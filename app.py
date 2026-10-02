#!/usr/bin/env python3
"""
PixelPotion - Raspberry Pi AI Style Camera
Captures photos, transforms them with Gemini AI, and delivers them via Telegram.
"""

import os
import sys
import hmac
import json
import time
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
from ai_provider import process_image

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
def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
        cfg = {**DEFAULT_CONFIG, **saved}
        if not cfg.get("styles"):
            cfg["styles"] = DEFAULT_CONFIG["styles"]
        if not cfg.get("active_style_id"):
            cfg["active_style_id"] = cfg["styles"][0]["id"] if cfg["styles"] else "pixar"
    else:
        cfg = DEFAULT_CONFIG.copy()
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    # config.json holds API keys and the WiFi password: owner-only access.
    os.chmod(CONFIG_PATH, 0o600)


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


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


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
            cam.stop()
            cam.close()
            log.info("Photo captured: %s", filepath)
            return filepath
        except Exception as e:
            log.error("Error capturing photo: %s", e)
            return None


# ---------------------------------------------------------------------------
# AI processing
# ---------------------------------------------------------------------------
def process_with_ai(image_path, prompt=None):
    api_key = config.get("gemini_api_key", "").strip()
    if not api_key:
        log.error("AI API key not configured")
        return None
    log.debug("AI processing: key length=%d", len(api_key))
    if prompt is None:
        prompt = get_active_prompt()
    try:
        return process_image(image_path, prompt, api_key)
    except Exception as e:
        log.error("Error in AI processing: %s", e)
        return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram_photos(original_path, processed_path, style_name=""):
    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        log.error("Telegram not configured")
        return False
    try:
        import requests
        api_url = f"https://api.telegram.org/bot{token}"
        with open(original_path, "rb") as photo:
            resp1 = requests.post(f"{api_url}/sendPhoto",
                                  data={"chat_id": chat_id, "caption": "📷 Original photo"},
                                  files={"photo": photo}, timeout=30)
        caption = f"🎨 Style: {style_name}" if style_name else "🎨 Styled version"
        with open(processed_path, "rb") as photo:
            resp2 = requests.post(f"{api_url}/sendPhoto",
                                  data={"chat_id": chat_id, "caption": caption},
                                  files={"photo": photo}, timeout=30)
        ok = resp1.ok and resp2.ok
        if ok:
            log.info("Photos sent via Telegram")
        else:
            log.error("Telegram error: %s / %s", resp1.text, resp2.text)
        return ok
    except Exception as e:
        # requests puts the request URL — which embeds the bot token — in its
        # exception text, so never log it verbatim.
        log.error("Error sending via Telegram: %s",
                  redact(str(e), token, quote(token, safe="")))
        return False


def redact(message: str, *secrets_to_hide: str) -> str:
    """Replace every non-empty secret in `message` with `<redacted>`.

    Empty secrets are skipped: `str.replace("", ...)` would insert the
    marker between every character.
    """
    for secret in sorted({s for s in secrets_to_hide if s}, key=len, reverse=True):
        message = message.replace(secret, "<redacted>")
    return message


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
processing_lock = threading.Lock()
status = {"last_action": "Waiting...", "processing": False}


def ensure_in_pending(photo_path):
    """Mark the photo as pending so it survives a restart / can be retried."""
    import shutil
    pending_path = PHOTOS_PENDING / Path(photo_path).name
    if not pending_path.exists():
        try:
            shutil.copy2(photo_path, pending_path)
        except Exception as e:
            log.error("Could not copy to pending: %s", e)


def remove_from_pending(photo_path):
    """Photo has been delivered — remove it from the pending queue."""
    pending_path = PHOTOS_PENDING / Path(photo_path).name
    pending_path.unlink(missing_ok=True)


def full_pipeline(photo_path=None, style_id=None):
    if not processing_lock.acquire(blocking=False):
        log.warning("Pipeline already running, skipping")
        return
    try:
        status["processing"] = True
        # Resolve style
        prompt, style_name = None, ""
        if style_id:
            for s in config.get("styles", []):
                if s["id"] == style_id:
                    prompt, style_name = s["prompt"], s["name"]
                    break
        if prompt is None:
            prompt, style_name = get_active_prompt(), get_active_style_name()

        # 1. Capture
        if photo_path is None:
            status["last_action"] = "Capturing pixels..."
            photo_path = capture_photo()
            if not photo_path:
                status["last_action"] = "Error: could not capture photo"
                return

        # Every photo that enters the pipeline is pending until delivered.
        ensure_in_pending(photo_path)

        # 2. Check WiFi
        if not is_wifi_connected():
            status["last_action"] = f"No WiFi — kept in pending: {Path(photo_path).name}"
            return

        # 3. Process
        status["last_action"] = f"Adding potion ({style_name})..."
        processed = process_with_ai(photo_path, prompt)
        if not processed:
            status["last_action"] = "AI processing failed — kept in pending for retry"
            return

        # 4. Send
        status["last_action"] = "Sending via Telegram..."
        success = send_telegram_photos(photo_path, processed, style_name)
        if success:
            remove_from_pending(photo_path)
            status["last_action"] = f"✅ Done ({style_name}): {Path(photo_path).name}"
        else:
            status["last_action"] = "Telegram failed — kept in pending for retry"
    except Exception as e:
        status["last_action"] = f"Error: {e} — kept in pending"
        log.error("Pipeline error: %s", e)
    finally:
        status["processing"] = False
        processing_lock.release()


def _resolve_pending(filename) -> Path | None:
    """Return the pending-queue path for a bare file name, or None if unsafe.

    Filenames come from the web portal, so anything that could escape
    PHOTOS_PENDING (absolute paths, separators, `..`) is rejected.
    """
    if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
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


def process_pending_photo(filename, style_id=None):
    pending_path = _resolve_pending(filename)
    if pending_path is None or not pending_path.exists():
        return False
    filename = pending_path.name
    orig_path = PHOTOS_ORIGINAL / filename
    if not orig_path.exists():
        import shutil
        shutil.copy2(pending_path, orig_path)
    full_pipeline(str(orig_path), style_id=style_id)
    return True


def auto_retry_loop():
    """Periodically retry photos that are still in PHOTOS_PENDING."""
    while True:
        time.sleep(RETRY_INTERVAL_SECONDS)
        try:
            if status.get("processing") or not is_wifi_connected():
                continue
            pending = sorted(PHOTOS_PENDING.glob("*.jpg"))
            if not pending:
                continue
            log.info("Auto-retry: %d pending photo(s)", len(pending))
            style_id = config.get("active_style_id")
            for p in pending:
                if status.get("processing") or not is_wifi_connected():
                    break
                process_pending_photo(p.name, style_id=style_id)
                time.sleep(2)
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
            threading.Thread(
                target=full_pipeline,
                kwargs={"style_id": config.get("active_style_id")},
                daemon=True,
            ).start()
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
        "index.html", config=config, status=status,
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
    config["wifi_ssid"] = ssid
    config["wifi_password"] = password
    save_config(config)
    flash(f"Connecting to {ssid}...", "info")

    def async_connect():
        success = connect_wifi(ssid, password)
        config["wifi_connected"] = success
        save_config(config)
        if not success:
            start_ap_mode()

    threading.Thread(target=async_connect, daemon=True).start()
    return redirect(url_for("index"))


@app.route("/capture", methods=["POST"])
def capture_route():
    """AJAX capture endpoint — returns JSON, no redirect."""
    style_id = request.form.get("style_id", config.get("active_style_id", ""))
    if status["processing"]:
        return jsonify({"ok": False, "error": "A process is already running."})
    config["active_style_id"] = style_id
    save_config(config)
    threading.Thread(target=full_pipeline, kwargs={"style_id": style_id}, daemon=True).start()
    return jsonify({"ok": True, "message": "Capture started..."})


@app.route("/set_active_style", methods=["POST"])
def set_active_style():
    data = request.get_json() or {}
    style_id = data.get("style_id", "")
    if style_id:
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
    pending_list = [{
        "name": p.name,
        "date": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
        "size_kb": round(p.stat().st_size / 1024),
    } for p in pending_photos]
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
    if _resolve_pending(filename) is None:
        flash("Invalid file name.", "error")
        return redirect(url_for("gallery"))
    if not is_wifi_connected():
        flash("No WiFi connection.", "error")
        return redirect(url_for("gallery"))
    threading.Thread(target=process_pending_photo, args=(filename,),
                     kwargs={"style_id": style_id}, daemon=True).start()
    flash(f"Processing {filename}...", "info")
    return redirect(url_for("gallery"))


@app.route("/process_all", methods=["POST"])
def process_all_route():
    style_id = request.form.get("style_id", config.get("active_style_id", ""))
    if not is_wifi_connected():
        flash("No WiFi connection.", "error")
        return redirect(url_for("gallery"))

    def run():
        for p in sorted(PHOTOS_PENDING.glob("*.jpg")):
            process_pending_photo(p.name, style_id=style_id)
            time.sleep(2)

    threading.Thread(target=run, daemon=True).start()
    flash("Processing all photos...", "info")
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
    """Delete one pending photo; False (logged) if the filesystem refuses."""
    try:
        path.unlink()
        return True
    except OSError as e:
        log.error("Could not delete %s: %s", path.name, e)
        return False


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
    return send_from_directory(str(PHOTOS_PENDING), filename)


@app.route("/status_api")
def status_api():
    return jsonify({
        **status,
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
    global config
    config = load_config()
    save_config(config)
    log.info("=== PixelPotion starting ===")

    if config.get("wifi_ssid"):
        if not connect_wifi(config["wifi_ssid"], config["wifi_password"]):
            start_ap_mode()
    else:
        start_ap_mode()

    threading.Thread(target=gpio_button_listener, daemon=True).start()
    threading.Thread(target=auto_retry_loop, daemon=True).start()
    log.info("Auto-retry loop running every %d seconds", RETRY_INTERVAL_SECONDS)
    log.info("Web server on 0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
