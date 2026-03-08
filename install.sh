#!/bin/bash
# =============================================================================
# PixelPotion - Installation Script
# Raspberry Pi Zero 2 W + Camera v2.1
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="/home/pi/pixelpotion"

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

# ---- Check Raspberry Pi ----
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo -e "${YELLOW}Warning: Raspberry Pi not detected. Continuing anyway...${NC}"
fi

echo ""
echo -e "${GREEN}[1/7] Updating system...${NC}"
apt-get update -y
apt-get upgrade -y

echo ""
echo -e "${GREEN}[2/7] Installing system dependencies...${NC}"
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
    libcap-dev

echo ""
echo -e "${GREEN}[3/7] Configuring camera...${NC}"
# Enable camera in config.txt if not already done
if ! grep -q "^start_x=1" /boot/firmware/config.txt 2>/dev/null && \
   ! grep -q "^camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null; then
    echo "" >> /boot/firmware/config.txt
    echo "# PixelPotion - Camera enabled" >> /boot/firmware/config.txt
    echo "camera_auto_detect=1" >> /boot/firmware/config.txt
    echo "gpu_mem=128" >> /boot/firmware/config.txt
    echo -e "${YELLOW}  Camera enabled in config.txt${NC}"
else
    echo -e "  Camera already enabled"
fi

echo ""
echo -e "${GREEN}[4/7] Setting up project directory...${NC}"
mkdir -p "${INSTALL_DIR}"/{templates,static,photos/{original,processed,pending},scripts}

# Copy project files
cp -v app.py "${INSTALL_DIR}/"
cp -v constants.py "${INSTALL_DIR}/"
cp -v default_config.json "${INSTALL_DIR}/"
cp -v templates/*.html "${INSTALL_DIR}/templates/"
cp -v requirements.txt "${INSTALL_DIR}/"

echo ""
echo -e "${GREEN}[5/7] Installing Python dependencies...${NC}"
pip3 install --break-system-packages flask requests google-genai Pillow

echo ""
echo -e "${GREEN}[6/7] Configuring Access Point and services...${NC}"

# -- hostapd --
cp -v config/hostapd.conf /etc/hostapd/hostapd.conf
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
cp -v config/dnsmasq.conf /etc/dnsmasq.d/pixelpotion.conf

# Don't start AP services on boot (PixelPotion manages them)
systemctl unmask hostapd 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# -- PixelPotion service --
cp -v config/pixelpotion.service /etc/systemd/system/pixelpotion.service
systemctl daemon-reload
systemctl enable pixelpotion.service

echo ""
echo -e "${GREEN}[7/7] Setting permissions...${NC}"
chown -R pi:pi "${INSTALL_DIR}"
chmod +x "${INSTALL_DIR}/app.py"

# Create initial config
if [ ! -f "${INSTALL_DIR}/config.json" ]; then
    cat > "${INSTALL_DIR}/config.json" << 'CONFIGEOF'
{
  "gemini_api_key": "",
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "wifi_ssid": "",
  "wifi_password": "",
  "wifi_connected": false,
  "gpio_pin": 17,
  "camera_resolution": [2592, 1944],
  "ap_ssid": "PixelPotion-Setup",
  "ap_password": "pixelpotion123"
}
CONFIGEOF
    chown pi:pi "${INSTALL_DIR}/config.json"
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
echo "       Network: PixelPotion-Setup"
echo "       Password: pixelpotion123"
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
echo ""
