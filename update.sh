#!/bin/bash
# =============================================================================
# PixelPotion - Update Script
# Checks GitHub releases and updates to the latest version
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

REPO="nikolmedo/PixelPotion"
SERVICE_USER="pi"
INSTALL_DIR="/home/pi/pixelpotion"
VENV_DIR="${INSTALL_DIR}/venv"
SUDOERS_FILE="/etc/sudoers.d/pixelpotion"
WPA_SUPPLICANT_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"
CURL_OPTS=(--fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 120)
FORCE=0
SERVICE_STOPPED=0
TMP_DIR=""

for arg in "$@"; do
    case "$arg" in
        -f|--force) FORCE=1 ;;
        -h|--help)
            echo "Usage: sudo bash update.sh [--force]"
            echo "  --force   Reinstall even if already on the latest version"
            exit 0
            ;;
        *)
            echo -e "${RED}Error: unknown option: ${arg}${NC}"
            exit 1
            ;;
    esac
done

# Copy every runtime file listed in <src>/deploy-files.txt into <dest>.
# Same loop as install.sh; the file list itself lives only in the manifest.
# install(1) unlinks before writing, so replacing this running script is safe.
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

# Strip a leading "v" so tags (v2.1.0) and VERSION contents (2.1.0) compare.
normalize_version() {
    local version="${1#v}"
    printf '%s' "${version#V}"
}

# True when version $1 is strictly newer than version $2.
version_is_newer() {
    [ "$1" != "$2" ] && \
        [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]
}

cleanup() {
    local status=$?
    if [ -n "${TMP_DIR}" ]; then
        rm -rf "${TMP_DIR}"
    fi
    # Never leave the camera dead because an update step failed halfway.
    if [ "${status}" -ne 0 ] && [ "${SERVICE_STOPPED}" -eq 1 ]; then
        echo -e "${YELLOW}Update failed — starting the service again...${NC}"
        systemctl start pixelpotion.service || true
    fi
}
trap cleanup EXIT

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

TMP_DIR="$(mktemp -d /tmp/pixelpotion-update.XXXXXX)"

HTTP_CODE=""
if ! HTTP_CODE="$(curl "${CURL_OPTS[@]}" -H "Accept: application/vnd.github+json" \
        -w '%{http_code}' -o "${TMP_DIR}/release.json" "${API_URL}" \
        2>"${TMP_DIR}/curl.err")"; then
    # Keep only the final status code if retries printed several.
    HTTP_CODE="${HTTP_CODE: -3}"
    case "${HTTP_CODE}" in
        404) echo -e "${RED}Error: no releases have been published for ${REPO} yet.${NC}" ;;
        403|429) echo -e "${RED}Error: GitHub API rate limit reached (HTTP ${HTTP_CODE}). Try again later.${NC}" ;;
        ""|000) echo -e "${RED}Error: could not reach GitHub (check the internet connection).${NC}" ;;
        *) echo -e "${RED}Error: GitHub API request failed (HTTP ${HTTP_CODE}).${NC}" ;;
    esac
    sed 's/^/  /' "${TMP_DIR}/curl.err" >&2 || true
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

if [ -z "${LATEST:-}" ] || [ -z "${TARBALL:-}" ]; then
    echo -e "${RED}Error: could not parse the GitHub API response.${NC}"
    exit 1
fi
echo -e "${GREEN}Latest release:${NC}    ${LATEST}"

# ---- Compare versions ----
if [ "${FORCE}" -eq 1 ]; then
    echo -e "${YELLOW}Forcing reinstall of ${LATEST}...${NC}"
elif [ "${CURRENT}" = "unknown" ]; then
    echo -e "${YELLOW}Installed version unknown — installing ${LATEST}...${NC}"
elif version_is_newer "$(normalize_version "${LATEST}")" "$(normalize_version "${CURRENT}")"; then
    echo -e "${YELLOW}New version available: ${CURRENT} → ${LATEST}${NC}"
else
    echo ""
    echo -e "${GREEN}✅ Already up to date. Nothing to do.${NC}"
    echo "  Use --force to reinstall ${LATEST}."
    exit 0
fi

# ---- Download tarball ----
echo ""
echo -e "${GREEN}[1/6] Downloading release...${NC}"
if ! curl "${CURL_OPTS[@]}" -o "${TMP_DIR}/release.tar.gz" "${TARBALL}"; then
    echo -e "${RED}Error: could not download ${TARBALL}${NC}"
    exit 1
fi

# ---- Extract ----
echo -e "${GREEN}[2/6] Extracting...${NC}"
tar -xzf "${TMP_DIR}/release.tar.gz" -C "${TMP_DIR}"
SRC_DIR=$(find "${TMP_DIR}" -maxdepth 1 -mindepth 1 -type d | head -n 1)
if [ -z "${SRC_DIR}" ] || [ ! -f "${SRC_DIR}/app.py" ] || [ ! -f "${SRC_DIR}/deploy-files.txt" ]; then
    echo -e "${RED}Error: release tarball does not contain a valid PixelPotion source.${NC}"
    exit 1
fi

# ---- Backup config.json ----
echo -e "${GREEN}[3/6] Backing up config.json...${NC}"
if [ -f "${INSTALL_DIR}/config.json" ]; then
    cp -pv "${INSTALL_DIR}/config.json" "${INSTALL_DIR}/config.json.bak"
fi

# ---- Stop service ----
echo -e "${GREEN}[4/6] Updating files...${NC}"
SERVICE_WAS_ACTIVE=0
if systemctl is-active --quiet pixelpotion.service; then
    SERVICE_WAS_ACTIVE=1
    systemctl stop pixelpotion.service
    SERVICE_STOPPED=1
fi

# Code files only — never config.json or photos/.
deploy_runtime_files "${SRC_DIR}" "${INSTALL_DIR}"

# Privileges and service definition can change between releases too.
install_sudoers "${SRC_DIR}/config/pixelpotion.sudoers"
install -m 0644 "${SRC_DIR}/config/pixelpotion.service" /etc/systemd/system/pixelpotion.service
if [ -f "${WPA_SUPPLICANT_CONF}" ]; then
    chmod 0600 "${WPA_SUPPLICANT_CONF}"
fi

# Update VERSION marker
echo "${LATEST}" > "${INSTALL_DIR}/VERSION"

# ---- Install dependencies ----
echo -e "${GREEN}[5/6] Installing Python dependencies...${NC}"
if [ ! -x "${VENV_DIR}/bin/python3" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv --system-site-packages "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# Re-apply ownership (the venv and copied files were written as root).
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
if [ -f "${INSTALL_DIR}/config.json" ]; then
    chmod 0600 "${INSTALL_DIR}/config.json"
fi

# ---- Restart ----
echo -e "${GREEN}[6/6] Restarting service...${NC}"
systemctl daemon-reload
if [ "${SERVICE_WAS_ACTIVE}" -eq 1 ]; then
    systemctl start pixelpotion.service
    SERVICE_STOPPED=0
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
