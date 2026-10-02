#!/usr/bin/env python3
"""
PixelPotion - Raspberry Pi AI Style Camera
Captures photos, transforms them with Gemini AI, and delivers them via Telegram.
"""

import os
import sys
import copy
import hmac
import json
import time
import queue
import shutil
import uuid
import signal
import logging
import secrets
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory, session
)

from constants import (
    RETRY_INTERVAL_SECONDS,
    DEFAULT_CONFIG,
    PHOTOS_PROCESSED,
)
from ai_provider import AIResult, process_image_result

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/pi/pixelpotion/pixelpotion.log", mode="a"),
    ],
)
log = logging.getLogger("pixelpotion")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
PHOTOS_ORIGINAL = BASE_DIR / "photos" / "original"
PHOTOS_PENDING = BASE_DIR / "photos" / "pending"

for d in [PHOTOS_ORIGINAL, PHOTOS_PROCESSED, PHOTOS_PENDING]:
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
# Guards every read-modify-write of `config` and every write of config.json.
# Re-entrant so a route holding it can still call save_config().
config_lock = threading.RLock()


def load_config() -> dict:
    """Merge config.json over the factory defaults.

    A corrupt or truncated config.json (for example after a power cut on an
    old install) is moved aside as `config.json.corrupt-<timestamp>` and the
    app starts from defaults instead of crash-looping.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        return cfg
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError(f"expected a JSON object, got {type(saved).__name__}")
    except (ValueError, UnicodeDecodeError) as e:
        backup = CONFIG_PATH.with_name(
            f"{CONFIG_PATH.name}.corrupt-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        try:
            os.replace(CONFIG_PATH, backup)
            log.error("config.json is unreadable (%s); moved it to %s and loaded defaults",
                      e, backup.name)
        except OSError as move_error:
            log.error("config.json is unreadable (%s) and could not be moved aside: %s",
                      e, move_error)
        return cfg
    cfg.update(saved)
    if not cfg.get("styles"):
        cfg["styles"] = copy.deepcopy(DEFAULT_CONFIG["styles"])
    if not cfg.get("active_style_id"):
        cfg["active_style_id"] = cfg["styles"][0]["id"] if cfg["styles"] else "pixar"
    return cfg


def write_json_atomic(path: Path, data, mode: int = 0o600):
    """Write JSON so a crash leaves either the old or the new file, never half.

    The data goes to a temporary dot-file in the same directory (created with
    `mode`), is flushed to disk, and then replaces `path` in one rename.
    Raises OSError on failure, after removing the temporary file.
    """
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.chmod(path, mode)


def save_config(cfg: dict):
    """Persist config atomically. config.json holds API keys and the WiFi
    password, so it is owner-only (0600) from the moment it exists."""
    with config_lock:
        write_json_atomic(CONFIG_PATH, cfg, 0o600)


def get_active_prompt() -> str:
    style_id = config.get("active_style_id", "")
    for s in config.get("styles", []):
        if s["id"] == style_id:
            return s["prompt"]
    styles = config.get("styles", [])
    return styles[0]["prompt"] if styles else DEFAULT_CONFIG["styles"][0]["prompt"]


def get_active_style_name() -> str:
    style_id = config.get("active_style_id", "")
    for s in config.get("styles", []):
        if s["id"] == style_id:
            return s["name"]
    return "No style"


config = load_config()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))
app.secret_key = os.urandom(24)

# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------
CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
# POST endpoints called via fetch() that expect a JSON reply.
JSON_ENDPOINTS = {"capture_route", "set_active_style"}


def csrf_token() -> str:
    """Return this session's CSRF token, creating it on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _read_version() -> str:
    try:
        return (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


APP_VERSION = _read_version()


@app.context_processor
def inject_template_globals():
    return {"csrf_token": csrf_token, "app_version": APP_VERSION}


def _csrf_failure_response():
    if request.is_json or request.endpoint in JSON_ENDPOINTS:
        return jsonify({"ok": False, "error": "Invalid or missing CSRF token."}), 400
    flash("Your session expired — please try again.", "error")
    target = url_for("index")
    referrer = request.referrer
    if referrer:
        parsed = urlparse(referrer)
        # `//host` and `/\host` are protocol-relative to browsers: only a
        # single leading slash keeps the redirect on this device.
        if (parsed.netloc == request.host and parsed.path.startswith("/")
                and not parsed.path.startswith(("//", "/\\"))):
            target = parsed.path
    return redirect(target)


@app.before_request
def verify_csrf_token():
    if request.method != "POST":
        return None
    expected = session.get(CSRF_SESSION_KEY, "").encode()
    submitted = (
        request.form.get(CSRF_FORM_FIELD) or request.headers.get(CSRF_HEADER) or ""
    ).encode()
    if not expected or not hmac.compare_digest(expected, submitted):
        log.warning("Rejected POST %s: CSRF token mismatch", request.path)
        return _csrf_failure_response()
    return None


# ---------------------------------------------------------------------------
# WiFi helpers
# ---------------------------------------------------------------------------
# The service runs as `pi`. Network management goes through sudo, limited to
# the exact command lines whitelisted in config/pixelpotion.sudoers, so these
# absolute paths must stay in sync with that file. iwlist and wpa_cli live in
# /usr/sbin on current Raspberry Pi OS; /sbin is a symlink to it (merged /usr).
SUDO = "/usr/bin/sudo"
SYSTEMCTL = "/usr/bin/systemctl"
TEE = "/usr/bin/tee"
IWLIST = "/usr/sbin/iwlist"
WPA_CLI = "/usr/sbin/wpa_cli"
WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
DHCPCD_CONF = "/etc/dhcpcd.conf"

AP_ADDRESS = "192.168.4.1"
DHCPCD_AP_BLOCK = (
    "interface wlan0\n"
    f"    static ip_address={AP_ADDRESS}/24\n"
    "    nohook wpa_supplicant\n"
)


def is_wifi_connected() -> bool:
    try:
        out = subprocess.check_output(
            ["ip", "-4", "addr", "show", "wlan0"], text=True, timeout=5
        )
        if "inet " in out and "192.168.4.1" not in out:
            return True
    except Exception:
        pass
    return False


def is_valid_wifi_credential(value: str) -> bool:
    """True if the value can be quoted safely inside wpa_supplicant.conf.

    A double quote would end the quoted string early and a line break would
    start a new directive, so both (and any other control character) are
    refused rather than escaped.
    """
    return '"' not in value and not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def build_wpa_supplicant_conf(ssid: str, password: str) -> str:
    """Render wpa_supplicant.conf for one network; an empty password means open."""
    if not (is_valid_wifi_credential(ssid) and is_valid_wifi_credential(password)):
        raise ValueError("SSID and password cannot contain quotes or line breaks")
    if password:
        security = f'    psk="{password}"\n    key_mgmt=WPA-PSK\n'
    else:
        security = "    key_mgmt=NONE\n"
    return (
        "country=US\n"
        "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
        "update_config=1\n"
        "\n"
        "network={\n"
        f'    ssid="{ssid}"\n'
        f"{security}"
        "}\n"
    )


def with_ap_block(dhcpcd_conf: str) -> str:
    """Append the static-IP stanza that AP mode needs, unless already present."""
    if AP_ADDRESS in dhcpcd_conf:
        return dhcpcd_conf
    # Trailing empty lines are folded so repeated AP/WiFi switches never pile
    # up blank lines in the file.
    base = dhcpcd_conf.rstrip("\r\n")
    return (base + "\n\n" if base else "") + DHCPCD_AP_BLOCK + "\n"


def without_ap_block(dhcpcd_conf: str) -> str:
    """Drop every `interface wlan0` stanza, up to and including the next empty line.

    Same effect as `sed '/^interface wlan0/,/^$/d'`: a stanza with no empty
    line after it runs to the end of the file.
    """
    kept, skipping = [], False
    for line in dhcpcd_conf.splitlines(keepends=True):
        if skipping:
            if line.rstrip("\r\n") == "":
                skipping = False
            continue
        if line.startswith("interface wlan0"):
            skipping = True
            continue
        kept.append(line)
    return "".join(kept)


def _run_privileged(*command: str, content: str | None = None, timeout: int = 10) -> bool:
    """Run one command through `sudo -n`; True only if it exits with status 0.

    Every command line passed here must match an entry in
    config/pixelpotion.sudoers exactly. `-n` makes a missing sudoers rule fail
    at once instead of waiting for a password prompt that never comes.
    """
    result = subprocess.run(
        [SUDO, "-n", *command],
        input=content, text=True, timeout=timeout,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        log.error("Privileged command failed (%s): %s",
                  " ".join(command), (result.stderr or "").strip())
        return False
    return True


def _write_root_file(path: str, content: str) -> bool:
    """Replace a root-owned file by piping the content to `sudo tee <path>`."""
    return _run_privileged(TEE, path, content=content, timeout=5)


def _update_dhcpcd_conf(transform) -> bool:
    """Rewrite /etc/dhcpcd.conf through `transform`; skip the write if unchanged."""
    try:
        with open(DHCPCD_CONF, encoding="utf-8") as f:
            current = f.read()
    except OSError as e:
        # NetworkManager-based images have no dhcpcd.conf at all.
        log.warning("Could not read %s: %s", DHCPCD_CONF, e)
        return False
    updated = transform(current)
    if updated == current:
        return True
    return _write_root_file(DHCPCD_CONF, updated)


def connect_wifi(ssid: str, password: str) -> bool:
    log.info("Connecting to WiFi: %s", ssid)
    try:
        wpa_conf = build_wpa_supplicant_conf(ssid, password)
    except ValueError as e:
        log.error("Refusing to connect to WiFi %r: %s", ssid, e)
        return False
    try:
        _run_privileged(SYSTEMCTL, "stop", "hostapd")
        _run_privileged(SYSTEMCTL, "stop", "dnsmasq")
        # The PSK goes straight to tee's stdin: no plaintext copy on disk.
        if not _write_root_file(WPA_SUPPLICANT_CONF, wpa_conf):
            return False
        _update_dhcpcd_conf(without_ap_block)
        _run_privileged(SYSTEMCTL, "restart", "dhcpcd", timeout=15)
        _run_privileged(WPA_CLI, "-i", "wlan0", "reconfigure")
        for _ in range(20):
            time.sleep(1)
            if is_wifi_connected():
                log.info("Connected to WiFi: %s", ssid)
                return True
        log.warning("Failed to connect to WiFi: %s", ssid)
        return False
    except Exception as e:
        log.error("Error connecting to WiFi: %s", e)
        return False


def start_ap_mode():
    log.info("Starting Access Point mode: %s", config["ap_ssid"])
    try:
        _update_dhcpcd_conf(with_ap_block)
        _run_privileged(SYSTEMCTL, "restart", "dhcpcd", timeout=15)
        time.sleep(2)
        _run_privileged(SYSTEMCTL, "start", "dnsmasq")
        _run_privileged(SYSTEMCTL, "start", "hostapd")
        log.info("Access Point started.")
    except Exception as e:
        log.error("Error starting AP mode: %s", e)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
camera_lock = threading.Lock()

# Per-module capture controls. IMX708 (Arducam) needs fixed AWB gains to avoid
# a reddish tint with the default libcamera tuning file; IMX219 (Module 2.1)
# works correctly with the built-in auto white balance.
CAMERA_PROFILES = {
    "imx219": {"label": "Camera Module 2.1 (IMX219)", "controls": {}},
    "imx708": {
        "label": "Camera Module 3 (IMX708)",
        "controls": {"AwbEnable": False, "ColourGains": (1.0, 2.5)},
    },
}


def capture_photo() -> str | None:
    with camera_lock:
        cam = None
        try:
            from picamera2 import Picamera2
            available = Picamera2.global_camera_info()
            if not available:
                log.error("Error capturing photo: no cameras detected")
                return None
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"photo_{ts}.jpg"
            cam = Picamera2()
            cam_config = cam.create_still_configuration(
                main={"size": tuple(config["camera_resolution"])}
            )
            cam.configure(cam_config)
            module = config.get("camera_module", "imx708")
            controls = CAMERA_PROFILES.get(module, {}).get("controls", {})
            if controls:
                cam.set_controls(controls)
            cam.start()
            time.sleep(2)
            filepath = str(PHOTOS_ORIGINAL / filename)
            cam.capture_file(filepath)
            log.info("Photo captured: %s", filepath)
            return filepath
        except Exception as e:
            log.error("Error capturing photo: %s", e)
            return None
        finally:
            if cam is not None:
                _release_camera(cam)


def _release_camera(cam):
    """Stop and close the camera; a failing stop() must not skip close().

    A handle left open keeps libcamera busy and makes every later capture
    fail until the service restarts.
    """
    for step in ("stop", "close"):
        try:
            getattr(cam, step)()
        except Exception as e:
            log.warning("Camera %s() failed during cleanup: %s", step, e)


# ---------------------------------------------------------------------------
# AI processing
# ---------------------------------------------------------------------------
def process_with_ai(image_path, prompt=None) -> AIResult:
    """Style one photo. Never raises: failures come back as a failed AIResult,
    with `permanent` set when retrying cannot help."""
    api_key = config.get("gemini_api_key", "").strip()
    if not api_key:
        log.error("AI API key not configured")
        # Not permanent: the photo should go through once a key is saved.
        return AIResult(reason="AI API key not configured")
    log.debug("AI processing: key length=%d", len(api_key))
    if prompt is None:
        prompt = get_active_prompt()
    try:
        return process_image_result(image_path, prompt, api_key)
    except Exception as e:
        log.error("Error in AI processing: %s", e)
        return AIResult(reason=f"AI error: {e}")


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
ORIGINAL_CAPTION = "📷 Original photo"


def styled_caption(style_name=""):
    return f"🎨 Style: {style_name}" if style_name else "🎨 Styled version"


def send_telegram_photo(photo_path, caption) -> bool:
    """Send one photo to the configured chat; False (logged) on any failure."""
    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        log.error("Telegram not configured")
        return False
    try:
        import requests
        with open(photo_path, "rb") as photo:
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                                 data={"chat_id": chat_id, "caption": caption},
                                 files={"photo": photo}, timeout=30)
        if not resp.ok:
            log.error("Telegram error for %s: %s", Path(photo_path).name, resp.text)
            return False
        log.info("Sent via Telegram: %s", Path(photo_path).name)
        return True
    except Exception as e:
        # requests puts the request URL — which embeds the bot token — in its
        # exception text, so never log it verbatim.
        log.error("Error sending via Telegram: %s",
                  redact(str(e), token, quote(token, safe="")))
        return False


def send_telegram_photos(original_path, processed_path, style_name=""):
    """Send the original, then the styled photo; True only if both arrived."""
    return (send_telegram_photo(original_path, ORIGINAL_CAPTION)
            and send_telegram_photo(processed_path, styled_caption(style_name)))


def redact(message: str, *secrets_to_hide: str) -> str:
    """Replace every non-empty secret in `message` with `<redacted>`.

    Empty secrets are skipped: `str.replace("", ...)` would insert the
    marker between every character.
    """
    for secret in sorted({s for s in secrets_to_hide if s}, key=len, reverse=True):
        message = message.replace(secret, "<redacted>")
    return message


# ---------------------------------------------------------------------------
# Pipeline: capture → pending (durable) → queue → single worker
# ---------------------------------------------------------------------------
# `status` is read by templates and /status_api and written by the capture
# path and the worker thread. It stays one module-level dict mutated in place;
# every access goes through status_lock via the helpers below.
status_lock = threading.Lock()
status = {"last_action": "Waiting...", "processing": False, "capturing": False,
          "step": "idle", "failed_step": ""}

# `step` drives the portal's progress tracker. It names the latest pipeline
# event, so a capture made while another photo is processing moves it too.
# `failed_step` says which step a "failed" belongs to.
PIPELINE_STEPS = ("idle", "capturing", "queued", "brewing", "sending",
                  "done", "failed", "waiting_wifi")

# Processing runs on ONE long-lived worker thread fed by this queue, so a
# capture never waits for (or is dropped by) a photo that is being processed.
work_queue: "queue.Queue[str]" = queue.Queue()
_queued_names: set[str] = set()   # queued or in progress, for de-duplication
_queue_lock = threading.Lock()
_worker_thread: threading.Thread | None = None


def update_status(**fields):
    if fields.get("step", "failed") != "failed":
        fields.setdefault("failed_step", "")
    with status_lock:
        status.update(fields)


def fail_status(failed_step, last_action):
    update_status(step="failed", failed_step=failed_step, last_action=last_action)


def status_snapshot() -> dict:
    with status_lock:
        return dict(status)


# Each pending photo has a JSON sidecar (`<photo>.jpg.json`) recording how far
# its delivery got, so a retry resumes instead of starting over: the AI step is
# not paid for twice and Telegram never receives the same photo twice.
PHOTO_STATE_DEFAULTS = {
    "style_id": None,              # style chosen at capture time
    "processed_path": None,        # styled image, once the AI step succeeded
    "telegram_original_sent": False,
    "telegram_styled_sent": False,
    "attempts": 0,
    "failed": False,               # permanent error: auto-retry skips it
    "failed_reason": "",
}
# Serializes sidecar read-modify-writes with photo deletion.
_photo_state_lock = threading.Lock()


def _state_path(pending_path: Path) -> Path:
    return pending_path.with_name(f"{pending_path.name}.json")


def read_photo_state(pending_path: Path) -> dict:
    """Delivery state of a pending photo; defaults if it has no readable sidecar."""
    state = dict(PHOTO_STATE_DEFAULTS)
    try:
        with open(_state_path(pending_path), encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state.update(saved)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning("Ignoring unreadable state for %s: %s", pending_path.name, e)
    return state


def update_photo_state(pending_path: Path, **changes) -> dict | None:
    """Merge `changes` into the photo's sidecar; None if the photo is gone.

    The existence check runs under the same lock as deletion, so a photo
    deleted mid-processing never gets an orphan sidecar written back.
    """
    with _photo_state_lock:
        if not pending_path.exists():
            return None
        state = read_photo_state(pending_path)
        state.update(changes)
        write_json_atomic(_state_path(pending_path), state, 0o644)
        return state


def request_processing(pending_path: Path, style_id=None):
    """Prepare a pending photo for a manual (re)try from the portal.

    Clears a permanent-failure mark (the user is explicitly asking to try
    again), and a different style invalidates the styled image made with the
    old one.
    """
    state = read_photo_state(pending_path)
    changes = {}
    if state["failed"]:
        changes.update(failed=False, failed_reason="")
    if style_id and style_id != state["style_id"]:
        changes.update(style_id=style_id, processed_path=None, telegram_styled_sent=False)
    if changes:
        update_photo_state(pending_path, **changes)


def _resolve_style(style_id):
    """Return (prompt, name) for a style id, falling back to the active style."""
    for s in config.get("styles", []):
        if s["id"] == style_id:
            return s["prompt"], s["name"]
    return get_active_prompt(), get_active_style_name()


def ensure_in_pending(photo_path) -> bool:
    """Place the photo in the pending queue; True once it is safely there.

    The copy is written under a temporary dot-name (which never matches
    `*.jpg`), flushed to disk and renamed into place, so the queue never holds
    a half-written photo.
    """
    pending_path = PHOTOS_PENDING / Path(photo_path).name
    if pending_path.exists():
        return True
    tmp_path = PHOTOS_PENDING / f".{pending_path.name}.tmp"
    try:
        with open(photo_path, "rb") as src, open(tmp_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp_path, pending_path)
        return True
    except OSError as e:
        log.error("Could not copy %s to pending: %s", Path(photo_path).name, e)
        tmp_path.unlink(missing_ok=True)
        return False


def remove_from_pending(photo_path):
    """Photo has been delivered — remove it and its sidecar from the queue."""
    pending_path = PHOTOS_PENDING / Path(photo_path).name
    with _photo_state_lock:
        pending_path.unlink(missing_ok=True)
        _state_path(pending_path).unlink(missing_ok=True)


def enqueue_pending(filename) -> bool:
    """Queue a pending photo for the worker; False if it is already queued."""
    with _queue_lock:
        if filename in _queued_names:
            return False
        _queued_names.add(filename)
    work_queue.put(filename)
    return True


def capture_to_pending(style_id=None) -> str | None:
    """Capture a photo, make it durable in pending and queue it.

    Only the camera lock is held, so capturing works while another photo is
    being processed. Returns the pending file name, or None on failure.
    """
    update_status(capturing=True, step="capturing", last_action="Capturing pixels...")
    try:
        photo_path = capture_photo()
        if not photo_path:
            fail_status("capturing", "Error: could not capture photo")
            return None
        # A photo that is not durably pending must not go any further.
        if not ensure_in_pending(photo_path):
            fail_status("capturing", "Error: could not save photo")
            return None
        name = Path(photo_path).name
        try:
            update_photo_state(PHOTOS_PENDING / name, style_id=style_id)
        except OSError as e:
            # The photo itself is safe; a retry falls back to the active style.
            log.error("Could not record the style of %s: %s", name, e)
        enqueue_pending(name)
        update_status(step="queued", last_action=f"Captured {name} — queued for processing")
        return name
    finally:
        update_status(capturing=False)


def _resolve_pending(filename) -> Path | None:
    """Return the pending-queue path for a bare file name, or None if unsafe.

    Filenames come from the web portal, so anything that could escape
    PHOTOS_PENDING (absolute paths, separators, `..`) is rejected.
    """
    if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
        return None
    # Only photos are addressable: never the sidecars or temporary files.
    if not filename.endswith(".jpg"):
        return None
    try:
        if Path(filename).name != filename:
            return None
        pending_dir = PHOTOS_PENDING.resolve()
        candidate = (pending_dir / filename).resolve()
    except (ValueError, OSError):
        return None
    if candidate.parent != pending_dir:
        return None
    return candidate


def process_pending_photo(filename) -> bool:
    """Run AI + Telegram for one pending photo. Called by the worker thread.

    Resumes from the photo's sidecar: a styled image that already exists is
    reused, and only the Telegram messages not yet sent are sent. Returns
    False if the photo is not (or no longer) in the pending queue. The photo
    leaves pending only after both Telegram messages were delivered.
    """
    pending_path = _resolve_pending(filename)
    if pending_path is None or not pending_path.exists():
        return False
    name = pending_path.name
    state = read_photo_state(pending_path)
    prompt, style_name = _resolve_style(state["style_id"])
    update_status(processing=True)
    try:
        if not is_wifi_connected():
            update_status(step="waiting_wifi", last_action=f"No WiFi — kept in pending: {name}")
            return True
        state = update_photo_state(pending_path, attempts=state["attempts"] + 1)
        if state is None:
            return False  # deleted from the gallery meanwhile

        processed = state["processed_path"]
        if not state["telegram_styled_sent"] and not (processed and Path(processed).is_file()):
            update_status(step="brewing", last_action=f"Adding potion ({style_name})...")
            result = process_with_ai(str(pending_path), prompt)
            if not result.ok:
                if result.permanent:
                    # Retrying cannot help: auto-retry skips it from now on.
                    update_photo_state(pending_path, failed=True, failed_reason=result.reason)
                    fail_status("brewing", f"Failed: {result.reason} — "
                                           "kept in pending, retry it from the gallery")
                else:
                    fail_status("brewing", "AI processing failed — kept in pending for retry")
                return True
            processed = result.path
            if update_photo_state(pending_path, processed_path=processed) is None:
                return False  # deleted from the gallery during the AI call

        update_status(step="sending", last_action="Sending via Telegram...")
        if not state["telegram_original_sent"]:
            if not send_telegram_photo(str(pending_path), ORIGINAL_CAPTION):
                fail_status("sending", "Telegram failed — kept in pending for retry")
                return True
            update_photo_state(pending_path, telegram_original_sent=True)
        if not state["telegram_styled_sent"]:
            if not send_telegram_photo(processed, styled_caption(style_name)):
                fail_status("sending", "Telegram failed — kept in pending for retry")
                return True
            update_photo_state(pending_path, telegram_styled_sent=True)

        remove_from_pending(pending_path)
        update_status(step="done", last_action=f"Done ({style_name}): {name}")
        return True
    except Exception as e:
        current = status_snapshot()["step"]
        fail_status(current if current in ("brewing", "sending") else "brewing",
                    f"Error: {e} — kept in pending")
        log.error("Pipeline error for %s: %s", name, e)
        return True
    finally:
        update_status(processing=False)


def process_next(block=True, timeout=None) -> bool:
    """Process one queued photo; False if the queue was empty.

    The worker loop calls this forever; tests call it with block=False to
    run the pipeline synchronously.
    """
    try:
        filename = work_queue.get(block=block, timeout=timeout)
    except queue.Empty:
        return False
    try:
        process_pending_photo(filename)
    except Exception as e:
        log.error("Worker error for %s: %s", filename, e)
    finally:
        with _queue_lock:
            _queued_names.discard(filename)
        work_queue.task_done()
    return True


def _worker_loop():
    while True:
        process_next()


def start_worker():
    """Start the processing worker thread unless it is already running."""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(
            target=_worker_loop, name="pixelpotion-worker", daemon=True
        )
        _worker_thread.start()


def pending_photo_names() -> list[str]:
    return sorted(p.name for p in PHOTOS_PENDING.glob("*.jpg"))


def retry_candidates() -> list[str]:
    """Pending photos auto-retry may queue: every one not marked as failed."""
    return [name for name in pending_photo_names()
            if not read_photo_state(PHOTOS_PENDING / name)["failed"]]


def auto_retry_loop():
    """Periodically queue photos that are still in PHOTOS_PENDING."""
    while True:
        time.sleep(RETRY_INTERVAL_SECONDS)
        try:
            if not is_wifi_connected():
                continue
            # Each photo keeps the style it was captured with; permanently
            # failed photos wait for a manual retry from the gallery.
            queued = sum(enqueue_pending(name) for name in retry_candidates())
            if queued:
                log.info("Auto-retry: queued %d pending photo(s)", queued)
        except Exception as e:
            log.error("Auto-retry loop error: %s", e)


# ---------------------------------------------------------------------------
# GPIO button handler
# ---------------------------------------------------------------------------
def gpio_button_listener():
    try:
        import RPi.GPIO as GPIO
        pin = config.get("gpio_pin", 17)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        log.info("GPIO button on pin %d", pin)
        last_press = 0
        while True:
            GPIO.wait_for_edge(pin, GPIO.FALLING)
            now = time.time()
            if now - last_press < 2:
                continue
            last_press = now
            log.info("Button pressed! Style: %s", config.get("active_style_id"))
            # Capture inline (camera lock only); processing is queued.
            capture_to_pending(config.get("active_style_id"))
    except ImportError:
        log.warning("RPi.GPIO not available — physical button disabled")
        while True:
            time.sleep(60)
    except Exception as e:
        log.error("GPIO error: %s", e)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    pending_photos = sorted(PHOTOS_PENDING.glob("*.jpg"), reverse=True)
    camera_modules = [{"id": k, "label": v["label"]} for k, v in CAMERA_PROFILES.items()]
    return render_template(
        "index.html", config=config, status=status_snapshot(),
        wifi_connected=is_wifi_connected(),
        pending_count=len(list(pending_photos)),
        styles=config.get("styles", []),
        active_style_id=config.get("active_style_id", ""),
        camera_modules=camera_modules,
    )


@app.route("/save_config", methods=["POST"])
def save_config_route():
    # Secrets are never rendered back into the form, so a blank field means
    # "keep the stored value".
    with config_lock:
        for secret in ("gemini_api_key", "telegram_bot_token"):
            submitted = request.form.get(secret, "").strip()
            if submitted:
                config[secret] = submitted
        config["telegram_chat_id"] = request.form.get("telegram_chat_id", "").strip()
        module = request.form.get("camera_module", "").strip()
        if module in CAMERA_PROFILES:
            config["camera_module"] = module
        save_config(config)
    flash("Configuration saved.", "success")
    return redirect(url_for("index"))


@app.route("/save_wifi", methods=["POST"])
def save_wifi_route():
    ssid = request.form.get("wifi_ssid", "").strip()
    password = request.form.get("wifi_password", "").strip()
    if not ssid:
        flash("SSID cannot be empty.", "error")
        return redirect(url_for("index"))
    # The stored password is never rendered, so blank means "keep it" — but only
    # for the same network. A new SSID with a blank password is an open network.
    if not password and ssid == config.get("wifi_ssid"):
        password = config.get("wifi_password", "")
    if not (is_valid_wifi_credential(ssid) and is_valid_wifi_credential(password)):
        flash("SSID and password cannot contain quotes or line breaks.", "error")
        return redirect(url_for("index"))
    with config_lock:
        config["wifi_ssid"] = ssid
        config["wifi_password"] = password
        save_config(config)
    flash(f"Connecting to {ssid}...", "info")

    def async_connect():
        success = connect_wifi(ssid, password)
        with config_lock:
            config["wifi_connected"] = success
            save_config(config)
        if not success:
            start_ap_mode()

    threading.Thread(target=async_connect, daemon=True).start()
    return redirect(url_for("index"))


@app.route("/capture", methods=["POST"])
def capture_route():
    """AJAX capture endpoint — returns JSON, no redirect.

    The capture itself runs in this request (camera lock only); AI and
    Telegram are queued for the worker, so this works while another photo is
    still being processed.
    """
    style_id = request.form.get("style_id", config.get("active_style_id", ""))
    with config_lock:
        config["active_style_id"] = style_id
        save_config(config)
    name = capture_to_pending(style_id)
    if name is None:
        return jsonify({"ok": False, "error": status_snapshot()["last_action"]})
    return jsonify({"ok": True, "message": f"Captured {name} — processing queued",
                    "filename": name})


@app.route("/set_active_style", methods=["POST"])
def set_active_style():
    data = request.get_json() or {}
    style_id = data.get("style_id", "")
    if style_id:
        with config_lock:
            config["active_style_id"] = style_id
            save_config(config)
    return jsonify({"ok": True, "active_style_id": config["active_style_id"]})


# -- Styles CRUD --
@app.route("/styles")
def styles_page():
    return render_template(
        "styles.html", config=config,
        styles=config.get("styles", []),
        active_style_id=config.get("active_style_id", ""),
        pending_count=len(list(PHOTOS_PENDING.glob("*.jpg"))),
    )


@app.route("/add_style", methods=["POST"])
def add_style():
    name = request.form.get("style_name", "").strip()
    prompt = request.form.get("style_prompt", "").strip()
    if not name or not prompt:
        flash("Name and prompt are required.", "error")
        return redirect(url_for("styles_page"))
    style_id = f"custom_{uuid.uuid4().hex[:8]}"
    with config_lock:
        config.setdefault("styles", []).append({"id": style_id, "name": name, "prompt": prompt})
        save_config(config)
    flash(f"Style '{name}' created.", "success")
    return redirect(url_for("styles_page"))


@app.route("/edit_style/<style_id>", methods=["POST"])
def edit_style(style_id):
    name = request.form.get("style_name", "").strip()
    prompt = request.form.get("style_prompt", "").strip()
    if not name or not prompt:
        flash("Name and prompt are required.", "error")
        return redirect(url_for("styles_page"))
    with config_lock:
        for s in config.get("styles", []):
            if s["id"] == style_id:
                s["name"] = name
                s["prompt"] = prompt
                break
        save_config(config)
    flash(f"Style '{name}' updated.", "success")
    return redirect(url_for("styles_page"))


@app.route("/delete_style/<style_id>", methods=["POST"])
def delete_style(style_id):
    with config_lock:
        config["styles"] = [s for s in config.get("styles", []) if s["id"] != style_id]
        if config.get("active_style_id") == style_id:
            config["active_style_id"] = config["styles"][0]["id"] if config["styles"] else ""
        save_config(config)
    flash("Style deleted.", "success")
    return redirect(url_for("styles_page"))


# -- Gallery --
@app.route("/gallery")
def gallery():
    pending_photos = sorted(PHOTOS_PENDING.glob("*.jpg"), reverse=True)
    pending_list = []
    for p in pending_photos:
        state = read_photo_state(p)
        pending_list.append({
            "name": p.name,
            "date": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
            "size_kb": round(p.stat().st_size / 1024),
            "failed_reason": (state["failed_reason"] or "unknown error") if state["failed"] else "",
        })
    return render_template(
        "gallery.html", photos=pending_list,
        wifi_connected=is_wifi_connected(), config=config,
        styles=config.get("styles", []),
        active_style_id=config.get("active_style_id", ""),
        pending_count=len(pending_list),
    )


@app.route("/process_photo", methods=["POST"])
def process_photo_route():
    filename = request.form.get("filename", "")
    style_id = request.form.get("style_id", config.get("active_style_id", ""))
    if not filename:
        flash("No file specified.", "error")
        return redirect(url_for("gallery"))
    pending_path = _resolve_pending(filename)
    if pending_path is None:
        flash("Invalid file name.", "error")
        return redirect(url_for("gallery"))
    if not is_wifi_connected():
        flash("No WiFi connection.", "error")
        return redirect(url_for("gallery"))
    if not pending_path.exists():
        flash(f"{filename} was not found.", "error")
        return redirect(url_for("gallery"))
    request_processing(pending_path, style_id)
    if enqueue_pending(filename):
        flash(f"Queued {filename} for processing.", "info")
    else:
        flash(f"{filename} is already queued.", "info")
    return redirect(url_for("gallery"))


@app.route("/process_all", methods=["POST"])
def process_all_route():
    style_id = request.form.get("style_id", config.get("active_style_id", ""))
    if not is_wifi_connected():
        flash("No WiFi connection.", "error")
        return redirect(url_for("gallery"))
    queued = 0
    for name in pending_photo_names():
        request_processing(PHOTOS_PENDING / name, style_id)
        queued += enqueue_pending(name)
    flash(f"Queued {queued} photo(s) for processing.", "info")
    return redirect(url_for("gallery"))


@app.route("/delete_photo", methods=["POST"])
def delete_photo_route():
    fn = request.form.get("filename", "")
    if not fn:
        return redirect(url_for("gallery"))
    path = _resolve_pending(fn)
    if path is None:
        flash("Invalid file name.", "error")
        return redirect(url_for("gallery"))
    if not path.exists():
        flash(f"{fn} was not found.", "error")
    elif _delete_pending_file(path):
        flash(f"{fn} deleted.", "success")
    else:
        flash(f"Could not delete {fn}.", "error")
    return redirect(url_for("gallery"))


def _delete_pending_file(path: Path) -> bool:
    """Delete one pending photo and its sidecar; False (logged) if the
    filesystem refuses to delete the photo."""
    with _photo_state_lock:
        try:
            path.unlink()
        except OSError as e:
            log.error("Could not delete %s: %s", path.name, e)
            return False
        try:
            _state_path(path).unlink(missing_ok=True)
        except OSError as e:
            log.warning("Could not delete the state file of %s: %s", path.name, e)
        return True


@app.route("/delete_selected", methods=["POST"])
def delete_selected_route():
    fns = request.form.getlist("selected_photos")
    deleted, invalid, failed = 0, 0, 0
    for fn in fns:
        path = _resolve_pending(fn)
        if path is None:
            invalid += 1
            continue
        if path.exists():
            if _delete_pending_file(path):
                deleted += 1
            else:
                failed += 1
    if invalid:
        flash(f"Skipped {invalid} invalid file name(s).", "error")
    if failed:
        flash(f"Could not delete {failed} photo(s).", "error")
    flash(f"{deleted} photo(s) deleted.", "success")
    return redirect(url_for("gallery"))


@app.route("/pending_photo/<filename>")
def serve_pending_photo(filename):
    if _resolve_pending(filename) is None:
        return "Not found", 404
    return send_from_directory(str(PHOTOS_PENDING), filename)


@app.route("/status_api")
def status_api():
    return jsonify({
        **status_snapshot(),
        "wifi": is_wifi_connected(),
        "pending_count": len(list(PHOTOS_PENDING.glob("*.jpg"))),
        "active_style_id": config.get("active_style_id", ""),
        "active_style_name": get_active_style_name(),
    })


@app.route("/scan_wifi")
def scan_wifi():
    try:
        out = subprocess.check_output(
            [SUDO, "-n", IWLIST, "wlan0", "scan"], text=True, timeout=15
        )
        networks = set()
        for line in out.split("\n"):
            if "ESSID:" in line:
                ssid = line.split("ESSID:")[1].strip().strip('"')
                if ssid:
                    networks.add(ssid)
        return jsonify(sorted(networks))
    except Exception:
        return jsonify([])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    with config_lock:
        config.clear()
        config.update(load_config())
        save_config(config)
    log.info("=== PixelPotion starting ===")

    if config.get("wifi_ssid"):
        if not connect_wifi(config["wifi_ssid"], config["wifi_password"]):
            start_ap_mode()
    else:
        start_ap_mode()

    start_worker()
    threading.Thread(target=gpio_button_listener, daemon=True).start()
    threading.Thread(target=auto_retry_loop, daemon=True).start()
    log.info("Auto-retry loop running every %d seconds", RETRY_INTERVAL_SECONDS)
    log.info("Web server on 0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
