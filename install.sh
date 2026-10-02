#!/bin/bash
# =============================================================================
# PixelPotion - Installation Script
# Raspberry Pi Zero 2 W + Camera Module 2.1 / 3
#
# Usage: sudo bash install.sh [--upgrade-system]
#   --upgrade-system   Also run `apt-get upgrade` before installing.
#
# Safe to re-run: config.json, photos and a customized AP passphrase are kept.
# Requires a `pi` user with home /home/pi (see README, "Known limitations").
# =============================================================================

set -euo pipefail

# Every relative path below (app.py, config/...) is relative to the checkout.
cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SERVICE_USER="pi"
INSTALL_DIR="/home/pi/pixelpotion"
VENV_DIR="${INSTALL_DIR}/venv"
HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
SUDOERS_FILE="/etc/sudoers.d/pixelpotion"
WPA_SUPPLICANT_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
DEFAULT_AP_PASSPHRASE="pixelpotion123"
UPGRADE_SYSTEM=0

for arg in "$@"; do
    case "$arg" in
        --upgrade-system) UPGRADE_SYSTEM=1 ;;
        -h|--help)
            echo "Usage: sudo bash install.sh [--upgrade-system]"
            echo "  --upgrade-system   Also run apt-get upgrade before installing"
            exit 0
            ;;
        *)
            echo -e "${RED}Error: unknown option: ${arg}${NC}"
            exit 1
            ;;
    esac
done

# Copy every runtime file listed in <src>/deploy-files.txt into <dest>.
deploy_runtime_files() {
    local src="$1" dest="$2" rel
    while IFS= read -r rel || [ -n "${rel}" ]; do
        rel="${rel%$'\r'}"
        case "${rel}" in ''|'#'*) continue ;; esac
        install -D -m 0644 "${src}/${rel}" "${dest}/${rel}"
        echo "  ${rel}"
    done < "${src}/deploy-files.txt"
    chmod 0755 "${dest}/app.py" "${dest}/update.sh"
}

# Validate the sudoers whitelist before it goes live. The staged name contains
# a dot, so sudo ignores it until the final rename.
install_sudoers() {
    local src="$1" staged="${SUDOERS_FILE}.new"
    install -m 0440 -o root -g root "${src}" "${staged}"
    if ! visudo -cf "${staged}" >/dev/null; then
        rm -f "${staged}"
        echo -e "${RED}Error: ${src} failed visudo validation; sudoers left unchanged.${NC}"
        exit 1
    fi
    mv -f "${staged}" "${SUDOERS_FILE}"
}

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════╗"
echo "║        🧪 PixelPotion - Installation         ║"
echo "║     AI Style Camera for Raspberry Pi         ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ---- Check root ----
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Run this script as root (sudo)${NC}"
    echo "  sudo bash install.sh"
    exit 1
fi

# ---- Check service user ----
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1 || [ ! -d "/home/${SERVICE_USER}" ]; then
    echo -e "${RED}Error: PixelPotion needs a '${SERVICE_USER}' user with home /home/${SERVICE_USER}.${NC}"
    echo "  Recent Raspberry Pi OS images have no default 'pi' user. Create it in"
    echo "  Raspberry Pi Imager (username: pi) or with: sudo adduser pi"
    exit 1
fi

# ---- Check Raspberry Pi ----
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo -e "${YELLOW}Warning: Raspberry Pi not detected. Continuing anyway...${NC}"
fi

echo ""
echo -e "${GREEN}[1/8] Updating package lists...${NC}"
apt-get update -y
if [ "${UPGRADE_SYSTEM}" -eq 1 ]; then
    apt-get upgrade -y
fi

echo ""
echo -e "${GREEN}[2/8] Installing system dependencies...${NC}"
# picamera2 and RPi.GPIO come from apt; the venv sees them via
# --system-site-packages.
apt-get install -y \
    python3-pip \
    python3-venv \
    python3-picamera2 \
    python3-libcamera \
    python3-rpi.gpio \
    python3-pil \
    hostapd \
    dnsmasq \
    libcamera-apps \
    libcap-dev \
    wireless-tools \
    curl

echo ""
echo -e "${GREEN}[3/8] Configuring camera...${NC}"
# Enable camera in config.txt if not already done
if ! grep -q "^start_x=1" /boot/firmware/config.txt 2>/dev/null && \
   ! grep -q "^camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null; then
    {
        echo ""
        echo "# PixelPotion - Camera enabled"
        echo "camera_auto_detect=1"
        echo "gpu_mem=128"
    } >> /boot/firmware/config.txt
    echo -e "${YELLOW}  Camera enabled in config.txt${NC}"
else
    echo -e "  Camera already enabled"
fi

echo ""
echo -e "${GREEN}[4/8] Copying application files...${NC}"
mkdir -p "${INSTALL_DIR}"/photos/{original,processed,pending}
deploy_runtime_files . "${INSTALL_DIR}"

echo ""
echo -e "${GREEN}[5/8] Installing Python dependencies (venv)...${NC}"
if [ ! -x "${VENV_DIR}/bin/python3" ]; then
    python3 -m venv --system-site-packages "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo ""
echo -e "${GREEN}[6/8] Configuring Access Point and privileges...${NC}"

# -- hostapd: per-device passphrase instead of the public default --
NEW_AP_PASSPHRASE=""
if [ ! -f "${HOSTAPD_CONF}" ] || \
   grep -qE "^wpa_passphrase=${DEFAULT_AP_PASSPHRASE}"$'\r?$' "${HOSTAPD_CONF}"; then
    NEW_AP_PASSPHRASE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
    install -D -m 0600 config/hostapd.conf "${HOSTAPD_CONF}"
    sed -i "s|^wpa_passphrase=.*|wpa_passphrase=${NEW_AP_PASSPHRASE}|" "${HOSTAPD_CONF}"
    echo "  Generated a new access point passphrase"
else
    chmod 0600 "${HOSTAPD_CONF}"
    echo "  Keeping the existing access point passphrase in ${HOSTAPD_CONF}"
fi
# Tell hostapd where to find its config
if ! grep -q "DAEMON_CONF=" /etc/default/hostapd 2>/dev/null; then
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd
else
    sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
fi

# -- dnsmasq --
# Backup original dnsmasq config
if [ -f /etc/dnsmasq.conf ] && [ ! -f /etc/dnsmasq.conf.bak ]; then
    cp /etc/dnsmasq.conf /etc/dnsmasq.conf.bak
fi
install -m 0644 config/dnsmasq.conf /etc/dnsmasq.d/pixelpotion.conf

# Don't start AP services on boot (PixelPotion manages them)
systemctl unmask hostapd 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# -- WiFi credentials file --
# The app rewrites it through `sudo tee`, which keeps the mode of an existing
# file but would create a missing one world-readable.
if [ -f "${WPA_SUPPLICANT_CONF}" ]; then
    chmod 0600 "${WPA_SUPPLICANT_CONF}"
else
    install -D -m 0600 /dev/null "${WPA_SUPPLICANT_CONF}"
fi

# -- sudo whitelist for the unprivileged service --
install_sudoers config/pixelpotion.sudoers
echo "  Installed ${SUDOERS_FILE}"

# -- PixelPotion service --
install -m 0644 config/pixelpotion.service /etc/systemd/system/pixelpotion.service
systemctl daemon-reload
systemctl enable pixelpotion.service

echo ""
echo -e "${GREEN}[7/8] Creating initial configuration...${NC}"
python3 - "${INSTALL_DIR}/config.json" "${NEW_AP_PASSPHRASE}" <<'PYEOF'
import json
import os
import sys

path, ap_password = sys.argv[1], sys.argv[2]
if os.path.exists(path):
    print("  Keeping existing config.json")
    sys.exit(0)
config = {
    "gemini_api_key": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "wifi_ssid": "",
    "wifi_password": "",
    "wifi_connected": False,
    "gpio_pin": 17,
    "camera_resolution": [2592, 1944],
    "ap_ssid": "PixelPotion-Setup",
}
if ap_password:
    config["ap_password"] = ap_password
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
print("  Created config.json")
PYEOF

echo ""
echo -e "${GREEN}[8/8] Setting permissions...${NC}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod 0600 "${INSTALL_DIR}/config.json"

# A re-run over a running install applies the new code and service user now.
if systemctl is-active --quiet pixelpotion.service; then
    systemctl restart pixelpotion.service
    echo "  Restarted pixelpotion.service"
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗"
echo -e "║          ✅ Installation Complete!            ║"
echo -e "╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Next steps:${NC}"
echo ""
echo -e "  1. ${YELLOW}Reboot the Raspberry Pi:${NC}"
echo "       sudo reboot"
echo ""
echo -e "  2. ${YELLOW}Connect to the WiFi access point:${NC}"
echo "       Network:  PixelPotion-Setup"
if [ -n "${NEW_AP_PASSPHRASE}" ]; then
    echo -e "       Password: ${YELLOW}${NEW_AP_PASSPHRASE}${NC}"
    echo "       (write it down — it is unique to this device; it is also stored"
    echo "        in ${HOSTAPD_CONF})"
else
    echo "       Password: unchanged — show it with:"
    echo "         sudo grep wpa_passphrase ${HOSTAPD_CONF}"
fi
echo ""
echo -e "  3. ${YELLOW}Open the web portal:${NC}"
echo "       http://192.168.4.1:8080"
echo ""
echo -e "  4. ${YELLOW}Configure:${NC}"
echo "       - Your home WiFi network"
echo "       - Google Gemini API Key"
echo "       - Telegram Bot Token and Chat ID"
echo ""
echo -e "  5. ${YELLOW}Wire the button:${NC}"
echo "       GPIO17 (Pin 11) ←→ GND (Pin 9)"
echo ""
echo -e "${CYAN}Useful commands:${NC}"
echo "  View logs:    sudo journalctl -u pixelpotion -f"
echo "  Restart:      sudo systemctl restart pixelpotion"
echo "  Stop:         sudo systemctl stop pixelpotion"
echo "  Status:       sudo systemctl status pixelpotion"
echo "  Update:       sudo bash ${INSTALL_DIR}/update.sh"
echo ""
