#!/usr/bin/env python3
"""
PixelPotion - Raspberry Pi AI Style Camera
Captures photos, transforms them with Gemini AI, and delivers them via Telegram.
"""

import os
import sys
import json
import time
import uuid
import signal
import base64
import logging
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory
)

from constants import GEMINI_MODELS, MAX_RETRIES, DEFAULT_CONFIG

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
PHOTOS_PROCESSED = BASE_DIR / "photos" / "processed"
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
# WiFi helpers
# ---------------------------------------------------------------------------
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


def connect_wifi(ssid: str, password: str) -> bool:
    log.info("Connecting to WiFi: %s", ssid)
    try:
        subprocess.run(["sudo", "systemctl", "stop", "hostapd"], timeout=10)
        subprocess.run(["sudo", "systemctl", "stop", "dnsmasq"], timeout=10)
        wpa_conf = f'''country=US
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
'''
        with open("/tmp/wpa_supplicant.conf", "w") as f:
            f.write(wpa_conf)
        subprocess.run(["sudo", "cp", "/tmp/wpa_supplicant.conf",
                        "/etc/wpa_supplicant/wpa_supplicant.conf"], timeout=5)
        subprocess.run(["sudo", "bash", "-c",
                        "sed -i '/^interface wlan0/,/^$/d' /etc/dhcpcd.conf"], timeout=5)
        subprocess.run(["sudo", "systemctl", "restart", "dhcpcd"], timeout=15)
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], timeout=10)
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
        dhcpcd_ap = "\ninterface wlan0\n    static ip_address=192.168.4.1/24\n    nohook wpa_supplicant\n"
        with open("/etc/dhcpcd.conf") as f:
            content = f.read()
        if "192.168.4.1" not in content:
            subprocess.run(["sudo", "bash", "-c",
                            f"echo '{dhcpcd_ap}' >> /etc/dhcpcd.conf"], timeout=5)
        subprocess.run(["sudo", "systemctl", "restart", "dhcpcd"], timeout=15)
        time.sleep(2)
        subprocess.run(["sudo", "systemctl", "start", "dnsmasq"], timeout=10)
        subprocess.run(["sudo", "systemctl", "start", "hostapd"], timeout=10)
        log.info("Access Point started.")
    except Exception as e:
        log.error("Error starting AP mode: %s", e)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
camera_lock = threading.Lock()


def capture_photo() -> str | None:
    with camera_lock:
        try:
            from picamera2 import Picamera2
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"photo_{ts}.jpg"
            cam = Picamera2()
            cam_config = cam.create_still_configuration(
                main={"size": tuple(config["camera_resolution"])}
            )
            cam.configure(cam_config)
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
# Gemini AI processing
# ---------------------------------------------------------------------------
def _try_generate_image(client, model, image_data, prompt):
    from google.genai import types
    image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

    if "2.5-flash-image" in model or "3-pro" in model:
        gen_config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="3:4"),
        )
    else:
        gen_config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        )

    response = client.models.generate_content(
        model=model, contents=[prompt, image_part], config=gen_config,
    )
    if response.candidates:
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data
    return None


def process_with_gemini(image_path, prompt=None):
    api_key = config.get("gemini_api_key", "")
    if not api_key:
        log.error("Gemini API key not configured")
        return None
    if prompt is None:
        prompt = get_active_prompt()

    try:
        from google import genai
        from PIL import Image
        client = genai.Client(api_key=api_key)
        with open(image_path, "rb") as f:
            image_data = f.read()

        for model in GEMINI_MODELS:
            for attempt in range(1, MAX_RETRIES + 1):
                log.info("Processing with %s (attempt %d/%d)", model, attempt, MAX_RETRIES)
                try:
                    img_bytes = _try_generate_image(client, model, image_data, prompt)
                    if img_bytes:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        out_path = str(PHOTOS_PROCESSED / f"styled_{ts}.jpg")
                        Image.open(BytesIO(img_bytes)).save(out_path, "JPEG", quality=95)
                        log.info("Image processed with %s (attempt %d): %s", model, attempt, out_path)
                        return out_path
                    log.warning("%s attempt %d: no image returned", model, attempt)
                except Exception as inner_e:
                    log.warning("%s attempt %d failed: %s", model, attempt, inner_e)
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
            log.warning("Model %s exhausted retries", model)
        log.error("All models failed")
        return None
    except Exception as e:
        log.error("Error processing with Gemini: %s", e)
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
        log.error("Error sending via Telegram: %s", e)
        return False


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
processing_lock = threading.Lock()
status = {"last_action": "Waiting...", "processing": False}


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

        # 2. Check WiFi
        if not is_wifi_connected():
            filename = Path(photo_path).name
            pending_path = PHOTOS_PENDING / filename
            if str(photo_path) != str(pending_path):
                import shutil
                shutil.copy2(photo_path, pending_path)
            status["last_action"] = f"No WiFi — saved to pending: {filename}"
            return

        # 3. Process
        status["last_action"] = f"Adding potion ({style_name})..."
        processed = process_with_gemini(photo_path, prompt)
        if not processed:
            status["last_action"] = "Error: Gemini could not process the image"
            return

        # 4. Send
        status["last_action"] = "Sending via Telegram..."
        success = send_telegram_photos(photo_path, processed, style_name)
        if success:
            status["last_action"] = f"✅ Done ({style_name}): {Path(photo_path).name}"
        else:
            status["last_action"] = "Error: could not send via Telegram"
    except Exception as e:
        status["last_action"] = f"Error: {e}"
        log.error("Pipeline error: %s", e)
    finally:
        status["processing"] = False
        processing_lock.release()


def process_pending_photo(filename, style_id=None):
    pending_path = PHOTOS_PENDING / filename
    if not pending_path.exists():
        return False
    import shutil
    orig_path = PHOTOS_ORIGINAL / filename
    shutil.copy2(pending_path, orig_path)
    full_pipeline(str(orig_path), style_id=style_id)
    if not status.get("processing"):
        pending_path.unlink(missing_ok=True)
    return True


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
    return render_template(
        "index.html", config=config, status=status,
        wifi_connected=is_wifi_connected(),
        pending_count=len(list(pending_photos)),
        styles=config.get("styles", []),
        active_style_id=config.get("active_style_id", ""),
    )


@app.route("/save_config", methods=["POST"])
def save_config_route():
    config["gemini_api_key"] = request.form.get("gemini_api_key", "").strip()
    config["telegram_bot_token"] = request.form.get("telegram_bot_token", "").strip()
    config["telegram_chat_id"] = request.form.get("telegram_chat_id", "").strip()
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
    if fn:
        (PHOTOS_PENDING / fn).unlink(missing_ok=True)
        flash(f"{fn} deleted.", "success")
    return redirect(url_for("gallery"))


@app.route("/delete_selected", methods=["POST"])
def delete_selected_route():
    fns = request.form.getlist("selected_photos")
    for fn in fns:
        (PHOTOS_PENDING / fn).unlink(missing_ok=True)
    flash(f"{len(fns)} photo(s) deleted.", "success")
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
            ["sudo", "iwlist", "wlan0", "scan"], text=True, timeout=15
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
    log.info("Web server on 0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
