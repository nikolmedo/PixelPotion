#!/bin/bash
# =============================================================================
# PixelPotion - Uninstall Script (v3.x installs)
#
# Usage: sudo bash /home/pi/pixelpotion/uninstall.sh [options]
#
# Removes the service, the sudo whitelist, the access point configuration and
# the app directory. Photos and config.json are moved to a private backup
# folder unless --purge is given. Installs from v2.0.2 or earlier need
# uninstall-legacy.sh instead (see README, "Uninstalling").
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SERVICE_USER="pi"
INSTALL_DIR="/home/pi/pixelpotion"
SERVICE_NAME="pixelpotion.service"
SERVICE_FILE="/etc/systemd/system/pixelpotion.service"
SERVICE_WANTS_LINK="/etc/systemd/system/multi-user.target.wants/pixelpotion.service"
SUDOERS_FILE="/etc/sudoers.d/pixelpotion"
HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
HOSTAPD_DEFAULTS="/etc/default/hostapd"
HOSTAPD_DAEMON_LINE='DAEMON_CONF="/etc/hostapd/hostapd.conf"'
AP_SSID_LINE="ssid=PixelPotion-Setup"
DNSMASQ_DROPIN="/etc/dnsmasq.d/pixelpotion.conf"
DHCPCD_CONF="/etc/dhcpcd.conf"
BOOT_CONFIG="/boot/firmware/config.txt"
# Never modified: the home WiFi connection and the untouched dnsmasq original.
WPA_SUPPLICANT_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
DNSMASQ_CONF="/etc/dnsmasq.conf"
DNSMASQ_CONF_BACKUP="/etc/dnsmasq.conf.bak"
# Runtime state worth keeping; everything else in INSTALL_DIR is reinstallable.
DATA_ITEMS=(photos config.json config.json.bak pixelpotion.log)
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/home/pi/pixelpotion-backup-${TIMESTAMP}"

ASSUME_YES=0
PURGE=0
REMOVE_PACKAGES=0
RESTORE_BOOT_CONFIG=0
FORCE=0

REMOVED=()
KEPT=()
WARNINGS=()
BACKUP_CREATED=""

usage() {
    cat <<EOF
Usage: sudo bash uninstall.sh [options]

Removes PixelPotion v3.x from this Raspberry Pi.

Options:
  --yes                   Do not ask for confirmation
  --purge                 Delete photos and config.json instead of backing them up
  --remove-packages       Also purge the hostapd and dnsmasq apt packages
  --restore-boot-config   Remove the camera lines PixelPotion added to
                          ${BOOT_CONFIG}
  --force                 Run even if this looks like a v2.0.2-or-earlier install
  -h, --help              Show this help

By default photos/ and config.json are moved to
/home/pi/pixelpotion-backup-<date>/ (readable only by ${SERVICE_USER}).
EOF
}

for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --purge) PURGE=1 ;;
        --remove-packages) REMOVE_PACKAGES=1 ;;
        --restore-boot-config) RESTORE_BOOT_CONFIG=1 ;;
        -f|--force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo -e "${RED}Error: unknown option: ${arg}${NC}"
            usage
            exit 1
            ;;
    esac
done

info() { echo -e "  $*"; }
removed() { REMOVED+=("$1"); info "Removed $1"; }
kept() { KEPT+=("$1"); }
warn() { WARNINGS+=("$1"); echo -e "  ${YELLOW}Warning: $1${NC}"; }

has_systemctl() { command -v systemctl >/dev/null 2>&1; }

# Print <file> without every exact occurrence of the three-line block
# <line1> <line2> <line3> (compared without surrounding whitespace). One blank
# line after the block goes with it, plus the blank line before it when the
# block was the end of the file, which undoes the way it was appended.
# Exits 0 when a block was found, 1 otherwise.
filter_exact_block() {
    awk -v l1="$2" -v l2="$3" -v l3="$4" '
        function trim(s) { gsub(/^[ \t]+|[ \t\r]+$/, "", s); return s }
        { raw[NR] = $0; line[NR] = trim($0) }
        END {
            found = 0
            for (i = 1; i + 2 <= NR; i++) {
                if (drop[i] || line[i] != trim(l1) || line[i + 1] != trim(l2) || line[i + 2] != trim(l3)) {
                    continue
                }
                found = 1
                drop[i] = drop[i + 1] = drop[i + 2] = 1
                next_line = i + 3
                if (next_line <= NR && line[next_line] == "") {
                    drop[next_line] = 1
                    next_line++
                }
                if (next_line > NR && i > 1 && line[i - 1] == "") {
                    drop[i - 1] = 1
                }
            }
            for (i = 1; i <= NR; i++) {
                if (!drop[i]) print raw[i]
            }
            exit (found ? 0 : 1)
        }' "$1"
}

has_exact_block() {
    [ -f "$1" ] && filter_exact_block "$@" >/dev/null
}

# Remove the block from <file> in place, keeping a timestamped copy of the
# original next to it. Writing through `cat >` keeps the file's owner and mode.
remove_exact_block() {
    local file="$1" tmp
    has_exact_block "$@" || return 1
    tmp="$(mktemp)"
    filter_exact_block "$@" > "${tmp}" || true
    cp -p "${file}" "${file}.pixelpotion-uninstall-${TIMESTAMP}"
    cat "${tmp}" > "${file}"
    rm -f "${tmp}"
}

has_ap_block() {
    has_exact_block "${DHCPCD_CONF}" "interface wlan0" \
        "static ip_address=192.168.4.1/24" "nohook wpa_supplicant"
}

hostapd_conf_is_ours() {
    [ -f "${HOSTAPD_CONF}" ] && grep -qxF "${AP_SSID_LINE}" "${HOSTAPD_CONF}"
}

has_daemon_conf_line() {
    [ -f "${HOSTAPD_DEFAULTS}" ] && grep -qxF "${HOSTAPD_DAEMON_LINE}" "${HOSTAPD_DEFAULTS}"
}

# v2.0.2 and earlier ran the service as root and had no sudo whitelist.
is_legacy_install() {
    [ ! -f "${SUDOERS_FILE}" ] && [ -f "${SERVICE_FILE}" ] && \
        grep -qE '^User=root[[:space:]]*$' "${SERVICE_FILE}"
}

anything_installed() {
    [ -e "${SERVICE_FILE}" ] || [ -e "${SUDOERS_FILE}" ] || [ -e "${INSTALL_DIR}" ] || \
        [ -e "${DNSMASQ_DROPIN}" ] || hostapd_conf_is_ours || has_daemon_conf_line || \
        has_ap_block
}

confirm() {
    [ "${ASSUME_YES}" -eq 1 ] && return 0
    if [ ! -t 0 ]; then
        echo -e "${RED}Error: no terminal to confirm on. Re-run with --yes.${NC}"
        exit 1
    fi
    echo "This will stop PixelPotion and remove its service, access point"
    echo "configuration and ${INSTALL_DIR}."
    if [ "${PURGE}" -eq 1 ]; then
        echo -e "${YELLOW}--purge: photos and config.json will be DELETED.${NC}"
    else
        echo "Photos and config.json will be moved to ${BACKUP_DIR}."
    fi
    local answer=""
    read -r -p "Continue? [y/N] " answer
    case "${answer}" in
        y|Y|yes|YES) ;;
        *) echo "Cancelled. Nothing was changed."; exit 1 ;;
    esac
}

remove_service() {
    if has_systemctl; then
        systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
        systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    fi
    if [ -e "${SERVICE_FILE}" ] || [ -L "${SERVICE_WANTS_LINK}" ]; then
        rm -f "${SERVICE_FILE}" "${SERVICE_WANTS_LINK}"
        removed "${SERVICE_FILE}"
    fi
    if has_systemctl; then
        systemctl daemon-reload 2>/dev/null || true
        systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true
    fi
}

remove_access_point() {
    if has_systemctl; then
        systemctl stop hostapd 2>/dev/null || true
        systemctl stop dnsmasq 2>/dev/null || true
    fi

    if hostapd_conf_is_ours; then
        rm -f "${HOSTAPD_CONF}"
        removed "${HOSTAPD_CONF}"
    elif [ -f "${HOSTAPD_CONF}" ]; then
        warn "${HOSTAPD_CONF} does not use the PixelPotion network name; left in place"
        kept "${HOSTAPD_CONF} (not created by PixelPotion)"
    fi

    if has_daemon_conf_line; then
        sed -i '\|^DAEMON_CONF="/etc/hostapd/hostapd.conf"$|d' "${HOSTAPD_DEFAULTS}"
        removed "the DAEMON_CONF line from ${HOSTAPD_DEFAULTS}"
    fi

    if [ -e "${DNSMASQ_DROPIN}" ]; then
        rm -f "${DNSMASQ_DROPIN}"
        removed "${DNSMASQ_DROPIN}"
    fi
    kept "${DNSMASQ_CONF} and ${DNSMASQ_CONF_BACKUP} (PixelPotion never changed them)"

    if remove_exact_block "${DHCPCD_CONF}" "interface wlan0" \
            "static ip_address=192.168.4.1/24" "nohook wpa_supplicant"; then
        removed "the access point block from ${DHCPCD_CONF} (original saved as ${DHCPCD_CONF}.pixelpotion-uninstall-${TIMESTAMP})"
        if has_systemctl && systemctl cat dhcpcd.service >/dev/null 2>&1; then
            systemctl restart dhcpcd 2>/dev/null || warn "could not restart dhcpcd; reboot to apply"
        fi
    fi
    kept "${WPA_SUPPLICANT_CONF} (your home WiFi connection)"
}

remove_sudoers() {
    if [ -e "${SUDOERS_FILE}" ]; then
        rm -f "${SUDOERS_FILE}"
        removed "${SUDOERS_FILE}"
    fi
}

# Move the runtime state out of INSTALL_DIR into a folder only the owner can read
# (config.json holds API keys and the WiFi password).
back_up_data() {
    local item found=0
    for item in "${DATA_ITEMS[@]}"; do
        [ -e "${INSTALL_DIR}/${item}" ] && found=1
    done
    [ "${found}" -eq 1 ] || return 0

    if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
        install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${BACKUP_DIR}"
    else
        install -d -m 0700 "${BACKUP_DIR}"
        warn "user ${SERVICE_USER} not found; ${BACKUP_DIR} is owned by root"
    fi
    for item in "${DATA_ITEMS[@]}"; do
        if [ -e "${INSTALL_DIR}/${item}" ]; then
            mv "${INSTALL_DIR}/${item}" "${BACKUP_DIR}/"
        fi
    done
    for item in config.json config.json.bak; do
        if [ -f "${BACKUP_DIR}/${item}" ]; then
            chmod 0600 "${BACKUP_DIR}/${item}"
        fi
    done
    if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
        chown -R "${SERVICE_USER}:${SERVICE_USER}" "${BACKUP_DIR}" || \
            warn "could not hand ${BACKUP_DIR} to ${SERVICE_USER}"
    fi
    BACKUP_CREATED="${BACKUP_DIR}"
}

remove_install_dir() {
    [ -e "${INSTALL_DIR}" ] || return 0
    if [ "${PURGE}" -eq 0 ]; then
        back_up_data
    fi
    rm -rf -- "${INSTALL_DIR}"
    if [ "${PURGE}" -eq 1 ]; then
        removed "${INSTALL_DIR} (including photos and config.json)"
    else
        removed "${INSTALL_DIR}"
    fi
}

remove_packages() {
    if ! command -v apt-get >/dev/null 2>&1; then
        warn "apt-get not found; hostapd and dnsmasq were not purged"
        return 0
    fi
    if apt-get purge -y hostapd dnsmasq; then
        removed "the hostapd and dnsmasq packages"
    else
        warn "apt-get purge hostapd dnsmasq failed"
    fi
}

restore_boot_config() {
    if remove_exact_block "${BOOT_CONFIG}" "# PixelPotion - Camera enabled" \
            "camera_auto_detect=1" "gpu_mem=128"; then
        removed "the PixelPotion camera lines from ${BOOT_CONFIG} (original saved as ${BOOT_CONFIG}.pixelpotion-uninstall-${TIMESTAMP})"
    else
        info "No PixelPotion camera block in ${BOOT_CONFIG}; left unchanged"
    fi
}

print_summary() {
    local entry
    echo ""
    echo -e "${CYAN}Summary${NC}"
    if [ "${#REMOVED[@]}" -eq 0 ]; then
        echo "  Nothing to remove: PixelPotion is not installed."
    else
        echo -e "${GREEN}  Removed:${NC}"
        for entry in "${REMOVED[@]}"; do echo "    - ${entry}"; done
    fi
    if [ -n "${BACKUP_CREATED}" ]; then
        echo -e "${GREEN}  Backup of photos and config.json:${NC} ${BACKUP_CREATED}"
    fi
    if [ "${#KEPT[@]}" -gt 0 ]; then
        echo -e "${GREEN}  Kept:${NC}"
        for entry in "${KEPT[@]}"; do echo "    - ${entry}"; done
    fi
    if [ "${REMOVE_PACKAGES}" -eq 0 ]; then
        echo "    - apt packages (hostapd, dnsmasq, picamera2...); use --remove-packages for hostapd and dnsmasq"
    fi
    if [ "${#WARNINGS[@]}" -gt 0 ]; then
        echo -e "${YELLOW}  Warnings:${NC}"
        for entry in "${WARNINGS[@]}"; do echo "    - ${entry}"; done
    fi
    if [ "${#REMOVED[@]}" -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}Reboot to finish:${NC} sudo reboot"
    fi
}

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════╗"
echo "║        🧪 PixelPotion - Uninstall            ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ---- Check root ----
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Run this script as root (sudo)${NC}"
    echo "  sudo bash uninstall.sh"
    exit 1
fi

# ---- Check install version ----
if is_legacy_install; then
    echo -e "${YELLOW}This looks like PixelPotion v2.0.2 or earlier:${NC} the service runs as"
    echo "root and there is no ${SUDOERS_FILE}. Those installs also left a WiFi"
    echo "password copy in /tmp and system-wide pip packages, which this script does"
    echo "not handle. Use uninstall-legacy.sh instead (see README, \"Uninstalling\")."
    if [ "${FORCE}" -eq 0 ]; then
        echo "  Re-run with --force to use this script anyway."
        exit 2
    fi
    echo -e "${YELLOW}--force given: continuing.${NC}"
fi

if ! anything_installed && [ "${REMOVE_PACKAGES}" -eq 0 ] && [ "${RESTORE_BOOT_CONFIG}" -eq 0 ]; then
    echo "Nothing to remove: PixelPotion is not installed."
    exit 0
fi

confirm

echo -e "${GREEN}[1/5] Removing the service...${NC}"
remove_service

echo -e "${GREEN}[2/5] Removing the access point configuration...${NC}"
remove_access_point

echo -e "${GREEN}[3/5] Removing the sudo whitelist...${NC}"
remove_sudoers

echo -e "${GREEN}[4/5] Removing ${INSTALL_DIR}...${NC}"
remove_install_dir

echo -e "${GREEN}[5/5] Optional steps...${NC}"
if [ "${REMOVE_PACKAGES}" -eq 1 ]; then
    remove_packages
fi
if [ "${RESTORE_BOOT_CONFIG}" -eq 1 ]; then
    restore_boot_config
else
    kept "${BOOT_CONFIG} (use --restore-boot-config to remove the camera lines)"
fi

print_summary
