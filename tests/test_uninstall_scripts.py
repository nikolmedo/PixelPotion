"""Static and bash-level guards for the uninstall scripts.

uninstall.sh ships with v3.x installs and must undo what install.sh does.
These tests catch drift: when install.sh starts writing a new /etc path,
the consistency check fails until uninstall.sh handles it too. Paths the
uninstaller must never delete (home WiFi, the untouched dnsmasq original) are
pinned separately so "mentioned in the summary" cannot become "removed".
"""

import re
import shutil
import subprocess
import sys

import pytest

import app as pixelpotion
from conftest import REPO_ROOT

UNINSTALL = "uninstall.sh"
SYSTEM_PATH = re.compile(r"/(?:etc|boot)/[\w./-]*\w")
# Mentioned in the summary as kept, but never deleted or rewritten.
NEVER_REMOVED = {
    "WPA_SUPPLICANT_CONF": "/etc/wpa_supplicant/wpa_supplicant.conf",
    "DNSMASQ_CONF": "/etc/dnsmasq.conf",
    "DNSMASQ_CONF_BACKUP": "/etc/dnsmasq.conf.bak",
}
MUTATING_COMMAND = re.compile(r"\b(rm|mv|sed -i|tee|truncate)\b|cat\s.*>|>\s*\"?\$")
BOOT_CAMERA_BLOCK = "\n# PixelPotion - Camera enabled\ncamera_auto_detect=1\ngpu_mem=128\n"
DHCPCD_WITH_USER_STANZA = (
    "# A sample configuration for dhcpcd.\n"
    "hostname\n"
    "option rapid_commit\n"
    "\n"
    "interface eth0\n"
    "static ip_address=192.168.1.50/24\n"
    "\n"
    "# User-managed wlan0 stanza that must survive\n"
    "interface wlan0\n"
    "    static ip_address=192.168.1.60/24\n"
)


def read_script(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def manifest_entries() -> list[str]:
    lines = (REPO_ROOT / "deploy-files.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def shell_function(text: str, name: str) -> str:
    return re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.M | re.S).group(0)


def mutating_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") and MUTATING_COMMAND.search(line)
    ]


class TestUninstallScriptBasics:
    def test_runs_in_strict_mode_as_root_only(self):
        # Arrange
        text = read_script(UNINSTALL)

        # Act / Assert
        assert text.startswith("#!/bin/bash\n")
        assert re.search(r"^set -euo pipefail$", text, re.M)
        assert re.search(r'^if \[ "\$EUID" -ne 0 \]; then$', text, re.M)

    def test_offers_the_documented_flags(self):
        # Arrange
        text = read_script(UNINSTALL)

        # Act / Assert
        for flag in ("--yes", "--purge", "--remove-packages",
                     "--restore-boot-config", "--force", "--help"):
            assert re.search(rf"^\s*(-\w\|)?{flag}\)", text, re.M), flag

    def test_is_deployed_and_made_executable(self):
        # Act / Assert — runnable as /home/pi/pixelpotion/uninstall.sh.
        assert UNINSTALL in manifest_entries()
        for script in ("install.sh", "update.sh"):
            assert re.search(r'^\s*chmod 0755 .*"\$\{dest\}/uninstall\.sh"', read_script(script), re.M)

    def test_points_legacy_installs_to_the_legacy_script(self):
        # Arrange
        text = read_script(UNINSTALL)

        # Act
        detection = shell_function(text, "is_legacy_install")

        # Assert
        assert "SUDOERS_FILE" in detection and "User=root" in detection
        assert "uninstall-legacy.sh" in text


class TestUninstallMatchesInstall:
    def test_handles_every_system_path_install_writes(self):
        # Arrange
        installed = set(SYSTEM_PATH.findall(read_script("install.sh")))
        uninstall = read_script(UNINSTALL)

        # Act
        missing = {path for path in installed if path not in uninstall}

        # Assert
        assert "/etc/sudoers.d/pixelpotion" in installed
        assert not missing, f"install.sh writes paths uninstall.sh ignores: {sorted(missing)}"

    def test_never_deletes_the_home_wifi_or_dnsmasq_original(self):
        # Arrange
        text = read_script(UNINSTALL)

        # Act
        offending = [
            line for line in mutating_lines(text)
            if any(path in line or f"${{{name}}}" in line for name, path in NEVER_REMOVED.items())
        ]

        # Assert
        for name, path in NEVER_REMOVED.items():
            assert f'{name}="{path}"' in text
        assert not offending, offending

    def test_removes_the_exact_ap_block_the_app_writes(self):
        # Arrange — the three lines of app.DHCPCD_AP_BLOCK, as the script passes them.
        block_lines = [line.strip() for line in pixelpotion.DHCPCD_AP_BLOCK.splitlines()]
        text = read_script(UNINSTALL)

        # Act
        call = re.search(r'remove_exact_block "\$\{DHCPCD_CONF\}"(.*?)nohook wpa_supplicant"',
                         text, re.S).group(0)

        # Assert
        for line in block_lines:
            assert f'"{line}"' in call


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs a POSIX bash (runs in CI)",
)
class TestExactBlockRemoval:
    AP_LINES = ("interface wlan0", "static ip_address=192.168.4.1/24", "nohook wpa_supplicant")
    BOOT_LINES = ("# PixelPotion - Camera enabled", "camera_auto_detect=1", "gpu_mem=128")

    @staticmethod
    def run_filter(script: str, path, lines) -> subprocess.CompletedProcess:
        helper = shell_function(read_script(script), "filter_exact_block")
        return subprocess.run(
            ["bash", "-c", helper + 'filter_exact_block "$@"', script, str(path), *lines],
            capture_output=True, text=True, check=False,
        )

    def test_restores_dhcpcd_conf_byte_exact_and_keeps_user_stanzas(self, tmp_path):
        # Arrange
        conf = tmp_path / "dhcpcd.conf"
        conf.write_bytes(pixelpotion.with_ap_block(DHCPCD_WITH_USER_STANZA).encode())

        # Act
        result = self.run_filter(UNINSTALL, conf, self.AP_LINES)

        # Assert
        assert result.returncode == 0
        assert result.stdout == DHCPCD_WITH_USER_STANZA

    def test_keeps_blank_line_between_neighbours_when_block_is_in_the_middle(self, tmp_path):
        # Arrange
        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(
            "hostname\n\ninterface wlan0\n    static ip_address=192.168.4.1/24\n"
            "    nohook wpa_supplicant\n\ninterface eth0\nstatic ip_address=10.0.0.5/24\n"
        )

        # Act
        result = self.run_filter(UNINSTALL, conf, self.AP_LINES)

        # Assert
        assert result.stdout == "hostname\n\ninterface eth0\nstatic ip_address=10.0.0.5/24\n"

    def test_reports_absent_block_and_changes_nothing(self, tmp_path):
        # Arrange
        conf = tmp_path / "dhcpcd.conf"
        conf.write_text(DHCPCD_WITH_USER_STANZA)

        # Act
        result = self.run_filter(UNINSTALL, conf, self.AP_LINES)

        # Assert
        assert result.returncode == 1
        assert result.stdout == DHCPCD_WITH_USER_STANZA

    def test_removes_only_the_camera_lines_install_appended(self, tmp_path):
        # Arrange
        original = "dtparam=audio=on\ncamera_auto_detect=1\n[all]\n"
        boot = tmp_path / "config.txt"
        boot.write_text(original + BOOT_CAMERA_BLOCK)

        # Act
        result = self.run_filter(UNINSTALL, boot, self.BOOT_LINES)

        # Assert — the user's own camera_auto_detect line stays.
        assert result.returncode == 0
        assert result.stdout == original
