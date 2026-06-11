"""Tests for app.py WiFi helpers — connectivity check and connection flow."""

import subprocess
from unittest.mock import MagicMock, mock_open

import pytest

import app as pixelpotion

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


class TestConnectWifi:
    @pytest.fixture
    def quiet_environment(self, monkeypatch, fake_subprocess):
        """Mute sleeps, capture the wpa_supplicant file, control wifi polling."""
        fake_time = MagicMock(name="time")
        monkeypatch.setattr(pixelpotion, "time", fake_time)
        opener = mock_open()
        monkeypatch.setattr("builtins.open", opener)
        return fake_subprocess, fake_time, opener

    def test_returns_true_once_connection_is_detected(
        self, quiet_environment, monkeypatch
    ):
        # Arrange — connection comes up on the third poll.
        wifi_probe = MagicMock(side_effect=[False, False, True])
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", wifi_probe)

        # Act
        result = pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        assert result is True
        assert wifi_probe.call_count == 3

    def test_writes_credentials_into_wpa_supplicant_config(
        self, quiet_environment, monkeypatch
    ):
        # Arrange
        _, _, opener = quiet_environment
        monkeypatch.setattr(
            pixelpotion, "is_wifi_connected", MagicMock(return_value=True)
        )

        # Act
        pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!")

        # Assert
        written = "".join(
            call.args[0] for call in opener().write.call_args_list
        )
        assert 'ssid="CasaOlmedo_5G"' in written
        assert 'psk="patagonia2024!"' in written
        assert "key_mgmt=WPA-PSK" in written

    def test_returns_false_after_polling_window_expires(
        self, quiet_environment, monkeypatch
    ):
        # Arrange — network never comes up.
        wifi_probe = MagicMock(return_value=False)
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", wifi_probe)

        # Act
        result = pixelpotion.connect_wifi("CafeDelBarrio-Guest", "cortado123")

        # Assert — 20 one-second polls, then give up.
        assert result is False
        assert wifi_probe.call_count == 20

    def test_returns_false_when_a_system_command_fails(
        self, quiet_environment, monkeypatch
    ):
        # Arrange
        fake_subprocess, _, _ = quiet_environment
        fake_subprocess.run.side_effect = OSError("sudo: command not found")
        monkeypatch.setattr(
            pixelpotion, "is_wifi_connected", MagicMock(return_value=True)
        )

        # Act / Assert — must degrade to False, never crash the caller.
        assert pixelpotion.connect_wifi("CasaOlmedo_5G", "patagonia2024!") is False
