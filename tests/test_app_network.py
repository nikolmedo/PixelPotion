"""Tests for app.py WiFi helpers — connectivity check and connection flow."""

import builtins
import io
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app as pixelpotion
from conftest import load_sudoers_commands

WLAN0_WITH_HOME_IP = """\
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    inet 192.168.1.47/24 brd 192.168.1.255 scope global dynamic noprefixroute wlan0
       valid_lft 85906sec preferred_lft 75106sec
"""

WLAN0_IN_AP_MODE = """\
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    inet 192.168.4.1/24 brd 192.168.4.255 scope global wlan0
       valid_lft forever preferred_lft forever
"""

WLAN0_NO_CARRIER = """\
3: wlan0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc fq_codel state DOWN
"""

DHCPCD_CONF_STOCK = """\
# A sample configuration for dhcpcd.
hostname
clientid
persistent
option rapid_commit
option domain_name_servers, domain_name, domain_search, host_name
slaac private
"""

AP_STANZA = """\
interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
"""

DHCPCD_CONF_IN_AP_MODE = DHCPCD_CONF_STOCK + "\n" + AP_STANZA + "\n"

WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
DHCPCD_CONF = "/etc/dhcpcd.conf"


@pytest.fixture
def fake_subprocess(monkeypatch):
    fake = MagicMock(name="subprocess")
    monkeypatch.setattr(pixelpotion, "subprocess", fake)
    return fake


class TestIsWifiConnected:
    def test_true_when_wlan0_has_a_lan_address(self, fake_subprocess):
        # Arrange
        fake_subprocess.check_output.return_value = WLAN0_WITH_HOME_IP

        # Act / Assert
        assert pixelpotion.is_wifi_connected() is True

    def test_false_when_running_in_access_point_mode(self, fake_subprocess):
        # Arrange — 192.168.4.1 is the device's own AP address, not internet.
        fake_subprocess.check_output.return_value = WLAN0_IN_AP_MODE

        # Act / Assert
        assert pixelpotion.is_wifi_connected() is False

    def test_false_when_interface_has_no_address(self, fake_subprocess):
        # Arrange
        fake_subprocess.check_output.return_value = WLAN0_NO_CARRIER

        # Act / Assert
        assert pixelpotion.is_wifi_connected() is False

    def test_false_when_ip_command_fails(self, fake_subprocess):
        # Arrange
        fake_subprocess.check_output.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["ip", "-4", "addr", "show", "wlan0"]
        )

        # Act / Assert — must never raise, callers treat it as a plain bool.
        assert pixelpotion.is_wifi_connected() is False


@pytest.fixture
def fake_system(monkeypatch, fake_subprocess):
    """Simulate the root-owned network files and the whitelisted sudo commands.

    `files` holds what is on disk under /etc; a `sudo tee <path>` call stores
    its stdin there. Commands listed in `failing` exit with status 1.
    """
    monkeypatch.setattr(pixelpotion, "time", MagicMock(name="time"))
    files = {DHCPCD_CONF: DHCPCD_CONF_IN_AP_MODE}
    calls, failing = [], set()

    def run(cmd, *args, **kwargs):
        calls.append(SimpleNamespace(argv=list(cmd), stdin=kwargs.get("input")))
        command = tuple(cmd[2:])
        if command in failing:
            return MagicMock(returncode=1, stderr="sudo: a password is required")
        if command[0] == pixelpotion.TEE:
            files[command[1]] = kwargs["input"]
        return MagicMock(returncode=0, stderr="")

    def fake_open(path, *args, **kwargs):
        if str(path).startswith("/etc/"):
            if str(path) not in files:
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return io.StringIO(files[str(path)])
        return builtins.open(path, *args, **kwargs)

    fake_subprocess.run.side_effect = run
    monkeypatch.setattr(pixelpotion, "open", fake_open, raising=False)
    return SimpleNamespace(files=files, calls=calls, failing=failing)


@pytest.fixture
def wifi_comes_up(monkeypatch):
    probe = MagicMock(return_value=True)
    monkeypatch.setattr(pixelpotion, "is_wifi_connected", probe)
    return probe


def tee_calls(fake_system):
    return [c for c in fake_system.calls if pixelpotion.TEE in c.argv]


class TestConnectWifi:
    def test_returns_true_once_connection_is_detected(self, fake_system, monkeypatch):
        # Arrange — connection comes up on the third poll.
        wifi_probe = MagicMock(side_effect=[False, False, True])
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", wifi_probe)

        # Act
        result = pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        assert result is True
        assert wifi_probe.call_count == 3

    def test_writes_credentials_into_wpa_supplicant_config(
        self, fake_system, wifi_comes_up
    ):
        # Act
        pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        written = fake_system.files[WPA_SUPPLICANT_CONF]
        assert 'ssid="CasaOlmedo_5G"' in written
        assert 'psk="patagonia2024!"' in written
        assert "key_mgmt=WPA-PSK" in written

    def test_open_network_is_configured_without_a_psk(
        self, fake_system, wifi_comes_up
    ):
        # Act
        pixelpotion.connect_wifi("CafeDelBarrio-Guest", "")

        # Assert
        written = fake_system.files[WPA_SUPPLICANT_CONF]
        assert 'ssid="CafeDelBarrio-Guest"' in written
        assert "key_mgmt=NONE" in written
        assert "psk=" not in written
        assert "WPA-PSK" not in written

    def test_psk_is_piped_to_tee_and_never_written_to_a_temp_file(
        self, fake_system, wifi_comes_up, monkeypatch, tmp_path
    ):
        # Arrange
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))

        # Act
        pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert — stdin only: not on any command line, not on disk.
        first_tee = tee_calls(fake_system)[0]
        assert first_tee.argv[2:] == [pixelpotion.TEE, WPA_SUPPLICANT_CONF]
        assert "patagonia2024!" in first_tee.stdin
        assert all(
            "patagonia2024!" not in " ".join(c.argv) for c in fake_system.calls
        )
        assert list(scratch.iterdir()) == []

    def test_removes_the_access_point_stanza_from_dhcpcd_conf(
        self, fake_system, wifi_comes_up
    ):
        # Act
        pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        dhcpcd = fake_system.files[DHCPCD_CONF]
        assert "interface wlan0" not in dhcpcd
        assert dhcpcd.rstrip("\n") == DHCPCD_CONF_STOCK.rstrip("\n")

    def test_still_connects_when_dhcpcd_conf_does_not_exist(
        self, fake_system, wifi_comes_up
    ):
        # Arrange — NetworkManager-based images ship without dhcpcd.conf.
        del fake_system.files[DHCPCD_CONF]

        # Act
        result = pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        assert result is True
        assert DHCPCD_CONF not in fake_system.files

    @pytest.mark.parametrize("ssid, password", [
        ('Casa"Olmedo', "patagonia2024!"),
        ("CasaOlmedo_5G", 'pata"gonia'),
        ("CasaOlmedo_5G", "patagonia2024!\nnetwork={"),
        ("CafeDelBarrio\r\nGuest", ""),
    ])
    def test_rejects_credentials_that_would_break_out_of_the_quoted_value(
        self, fake_system, wifi_comes_up, ssid, password
    ):
        # Act
        result = pixelpotion.connect_wifi(ssid, password)

        # Assert — refused before the access point is torn down.
        assert result is False
        assert fake_system.calls == []
        assert WPA_SUPPLICANT_CONF not in fake_system.files

    def test_returns_false_when_wpa_config_cannot_be_written(
        self, fake_system, wifi_comes_up
    ):
        # Arrange — e.g. the sudoers whitelist is missing on this device.
        fake_system.failing.add((pixelpotion.TEE, WPA_SUPPLICANT_CONF))

        # Act
        result = pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        assert result is False
        assert not any(pixelpotion.WPA_CLI in c.argv for c in fake_system.calls)

    def test_returns_false_after_polling_window_expires(self, fake_system, monkeypatch):
        # Arrange — network never comes up.
        wifi_probe = MagicMock(return_value=False)
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", wifi_probe)

        # Act
        result = pixelpotion.connect_wifi("CafeDelBarrio-Guest", "cortado123")

        # Assert — 20 one-second polls, then give up.
        assert result is False
        assert wifi_probe.call_count == 20

    def test_returns_false_when_a_system_command_fails(
        self, fake_system, fake_subprocess, wifi_comes_up
    ):
        # Arrange
        fake_subprocess.run.side_effect = OSError("sudo: command not found")

        # Act / Assert — must degrade to False, never crash the caller.
        assert pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!") is False

    def test_returns_false_when_a_command_times_out(
        self, fake_system, fake_subprocess, wifi_comes_up
    ):
        # Arrange
        fake_subprocess.run.side_effect = subprocess.TimeoutExpired(
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", "hostapd"], 10
        )

        # Act / Assert
        assert pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!") is False


class TestStartApMode:
    def test_restores_the_access_point_stanza_and_starts_the_ap(self, fake_system):
        # Arrange
        fake_system.files[DHCPCD_CONF] = DHCPCD_CONF_STOCK

        # Act
        pixelpotion.start_ap_mode()

        # Assert
        assert AP_STANZA in fake_system.files[DHCPCD_CONF]
        commands = [c.argv[2:] for c in fake_system.calls]
        assert [pixelpotion.SYSTEMCTL, "start", "dnsmasq"] in commands
        assert [pixelpotion.SYSTEMCTL, "start", "hostapd"] in commands

    def test_leaves_dhcpcd_conf_alone_when_stanza_is_present(self, fake_system):
        # Act
        pixelpotion.start_ap_mode()

        # Assert
        assert tee_calls(fake_system) == []
        assert fake_system.files[DHCPCD_CONF] == DHCPCD_CONF_IN_AP_MODE


class TestDhcpcdApStanza:
    def test_adding_the_stanza_is_idempotent(self):
        # Act
        once = pixelpotion.with_ap_block(DHCPCD_CONF_STOCK)
        twice = pixelpotion.with_ap_block(once)

        # Assert
        assert once.count("interface wlan0") == 1
        assert once.startswith(DHCPCD_CONF_STOCK)
        assert twice == once

    def test_removing_the_stanza_keeps_every_other_line(self):
        # Arrange — another interface stanza follows the AP one.
        content = (
            DHCPCD_CONF_STOCK + "\n" + AP_STANZA + "\n"
            + "interface eth0\n    static ip_address=10.0.0.5/24\n"
        )

        # Act
        result = pixelpotion.without_ap_block(content)

        # Assert
        assert "interface wlan0" not in result
        assert "192.168.4.1" not in result
        assert "interface eth0\n    static ip_address=10.0.0.5/24\n" in result
        assert result.startswith(DHCPCD_CONF_STOCK)

    def test_removes_a_stanza_at_end_of_file_without_trailing_blank_line(self):
        # Act
        result = pixelpotion.without_ap_block(DHCPCD_CONF_STOCK + "\n" + AP_STANZA)

        # Assert
        assert result == DHCPCD_CONF_STOCK + "\n"

    def test_switching_back_and_forth_does_not_grow_the_file(self):
        # Arrange
        ap_mode = pixelpotion.with_ap_block(DHCPCD_CONF_STOCK)

        # Act
        for _ in range(3):
            ap_mode = pixelpotion.with_ap_block(pixelpotion.without_ap_block(ap_mode))

        # Assert
        assert ap_mode == pixelpotion.with_ap_block(DHCPCD_CONF_STOCK)


class TestSudoersWhitelist:
    def test_every_privileged_command_the_app_runs_is_whitelisted(
        self, fake_system, fake_subprocess, wifi_comes_up, plain_client
    ):
        # Arrange
        allowed = set(load_sudoers_commands())
        fake_subprocess.check_output.return_value = ""
        fake_system.files[DHCPCD_CONF] = DHCPCD_CONF_STOCK

        # Act — exercise every code path that escalates.
        pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")
        pixelpotion.start_ap_mode()
        plain_client.get("/scan_wifi")

        # Assert
        invoked = [c.argv for c in fake_system.calls]
        invoked += [call.args[0] for call in fake_subprocess.check_output.call_args_list]
        sudo_calls = [argv for argv in invoked if argv[0] == pixelpotion.SUDO]
        assert len(sudo_calls) >= 10
        for argv in sudo_calls:
            assert argv[1] == "-n"
            assert " ".join(argv[2:]) in allowed


