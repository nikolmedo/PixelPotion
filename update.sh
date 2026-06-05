#!/bin/bash
# =============================================================================
# PixelPotion - Update Script
# Checks GitHub releases and updates to the latest version
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

REPO="nikolmedo/PixelPotion"
INSTALL_DIR="/home/pi/pixelpotion"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
TMP_DIR="/tmp/pixelpotion-update-$$"
FORCE=0

for arg in "$@"; do
    case "$arg" in
        -f|--force) FORCE=1 ;;
        -h|--help)
            echo "Usage: sudo bash update.sh [--force]"
            echo "  --force   Reinstall even if already on the latest version"
            exit 0
            ;;
    esac
done

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════╗"
echo "║          🧪 PixelPotion - Update             ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ---- Check root ----
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Run this script as root (sudo)${NC}"
    echo "  sudo bash update.sh"
    exit 1
fi

# ---- Check install dir ----
if [ ! -d "${INSTALL_DIR}" ]; then
    echo -e "${RED}Error: PixelPotion not installed at ${INSTALL_DIR}${NC}"
    echo "  Run install.sh first."
    exit 1
fi

# ---- Read current version ----
if [ -f "${INSTALL_DIR}/VERSION" ]; then
    CURRENT=$(tr -d '[:space:]' < "${INSTALL_DIR}/VERSION")
else
    CURRENT="unknown"
fi
echo -e "${GREEN}Installed version:${NC} ${CURRENT}"

# ---- Query GitHub API ----
echo -e "${GREEN}Checking for new releases...${NC}"
if ! command -v curl >/dev/null 2>&1; then
    echo -e "${RED}Error: curl not found. Install it with: apt-get install -y curl${NC}"
    exit 1
fi

mkdir -p "${TMP_DIR}"
trap 'rm -rf "${TMP_DIR}"' EXIT

if ! curl -fsSL -H "Accept: application/vnd.github+json" -o "${TMP_DIR}/release.json" "${API_URL}"; then
    echo -e "${RED}Error: could not reach GitHub API (no internet or no releases published yet).${NC}"
    exit 1
fi

# Parse with python3 (always available — installed by install.sh)
read -r LATEST TARBALL <<< "$(python3 - "${TMP_DIR}/release.json" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    print(data.get("tag_name", ""), data.get("tarball_url", ""))
except Exception:
    print("", "")
PYEOF
)"

if [ -z "${LATEST}" ]; then
    echo -e "${RED}Error: could not parse the GitHub API response.${NC}"
    exit 1
fi
echo -e "${GREEN}Latest release:${NC}    ${LATEST}"

# ---- Compare versions ----
if [ "${CURRENT}" = "${LATEST}" ] && [ "${FORCE}" -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Already on the latest version. Nothing to do.${NC}"
    echo "  Use --force to reinstall the same version."
    exit 0
fi

if [ "${FORCE}" -eq 1 ]; then
    echo -e "${YELLOW}Forcing reinstall of ${LATEST}...${NC}"
else
    echo -e "${YELLOW}New version available: ${CURRENT} → ${LATEST}${NC}"
fi

# ---- Download tarball ----
echo ""
echo -e "${GREEN}[1/6] Downloading release...${NC}"
if ! curl -fsSL -o "${TMP_DIR}/release.tar.gz" "${TARBALL}"; then
    echo -e "${RED}Error: could not download ${TARBALL}${NC}"
    exit 1
fi

# ---- Extract ----
echo -e "${GREEN}[2/6] Extracting...${NC}"
tar -xzf "${TMP_DIR}/release.tar.gz" -C "${TMP_DIR}"
SRC_DIR=$(find "${TMP_DIR}" -maxdepth 1 -mindepth 1 -type d | head -n 1)
if [ -z "${SRC_DIR}" ] || [ ! -f "${SRC_DIR}/app.py" ]; then
    echo -e "${RED}Error: release tarball does not contain a valid PixelPotion source.${NC}"
    exit 1
fi

# ---- Backup config.json ----
echo -e "${GREEN}[3/6] Backing up config.json...${NC}"
if [ -f "${INSTALL_DIR}/config.json" ]; then
    cp -v "${INSTALL_DIR}/config.json" "${INSTALL_DIR}/config.json.bak"
fi

# ---- Stop service ----
echo -e "${GREEN}[4/6] Updating files...${NC}"
SERVICE_WAS_ACTIVE=0
if systemctl is-active --quiet pixelpotion.service; then
    SERVICE_WAS_ACTIVE=1
    systemctl stop pixelpotion.service
fi

# Copy code files (NOT config.json, NOT photos/)
cp -v "${SRC_DIR}/app.py" "${INSTALL_DIR}/"
cp -v "${SRC_DIR}/constants.py" "${INSTALL_DIR}/"
cp -v "${SRC_DIR}/default_config.json" "${INSTALL_DIR}/"
cp -v "${SRC_DIR}/requirements.txt" "${INSTALL_DIR}/" 2>/dev/null || true
mkdir -p "${INSTALL_DIR}/templates"
cp -v "${SRC_DIR}"/templates/*.html "${INSTALL_DIR}/templates/"

# Optional: update systemd unit if changed
if [ -f "${SRC_DIR}/config/pixelpotion.service" ]; then
    if ! cmp -s "${SRC_DIR}/config/pixelpotion.service" /etc/systemd/system/pixelpotion.service; then
        cp -v "${SRC_DIR}/config/pixelpotion.service" /etc/systemd/system/pixelpotion.service
        systemctl daemon-reload
    fi
fi

# Update VERSION marker
echo "${LATEST}" > "${INSTALL_DIR}/VERSION"
chown pi:pi "${INSTALL_DIR}/VERSION"

# Re-apply ownership
chown -R pi:pi "${INSTALL_DIR}"

# ---- Install dependencies ----
echo -e "${GREEN}[5/6] Installing Python dependencies...${NC}"
VENV_DIR="${INSTALL_DIR}/venv"
if [ ! -d "${VENV_DIR}" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv --system-site-packages "${VENV_DIR}"
    chown -R pi:pi "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# ---- Restart ----
echo -e "${GREEN}[6/6] Restarting service...${NC}"
if [ "${SERVICE_WAS_ACTIVE}" -eq 1 ]; then
    systemctl start pixelpotion.service
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗"
echo -e "║         ✅ Update Complete: ${LATEST}"
echo -e "╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Check the service:${NC}"
echo "  sudo systemctl status pixelpotion"
echo "  sudo journalctl -u pixelpotion -f"
echo ""
if [ -f "${INSTALL_DIR}/config.json.bak" ]; then
    echo -e "${YELLOW}Backup of previous config:${NC} ${INSTALL_DIR}/config.json.bak"
fi
