"""Tests for app.py Flask routes — config, capture, styles CRUD, gallery, status."""

import json
import re
from unittest.mock import MagicMock

import pytest

import app as pixelpotion
from conftest import make_jpeg_bytes

IWLIST_SCAN_OUTPUT = """\
wlan0     Scan completed :
          Cell 01 - Address: A4:2B:B0:9E:11:3F
                    ESSID:"CasaOlmedo_5G"
                    Quality=68/70  Signal level=-42 dBm
          Cell 02 - Address: 5C:A6:E6:21:8B:90
                    ESSID:"CafeDelBarrio-Guest"
          Cell 03 - Address: A4:2B:B0:9E:11:40
                    ESSID:"CasaOlmedo_5G"
          Cell 04 - Address: DE:AD:BE:EF:00:01
                    ESSID:""
"""


def get_flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


@pytest.fixture
def fake_thread(monkeypatch):
    """Replace app's threading module so routes never spawn real work."""
    fake_threading = MagicMock(name="threading")
    monkeypatch.setattr(pixelpotion, "threading", fake_threading)
    return fake_threading.Thread


class TestSaveConfigRoute:
    def test_persists_trimmed_credentials(self, client, isolated_state):
        # Arrange
        form = {
            "gemini_api_key": "  AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv  ",
            "telegram_bot_token": " 8011223344:BBGk2nQ8rStU3vWxYz2cDeFgHiJkLmNoPq ",
            "telegram_chat_id": " 581234902 ",
            "camera_module": "imx219",
        }

        # Act
        response = client.post("/save_config", data=form)

        # Assert
        assert response.status_code == 302
        assert pixelpotion.config["gemini_api_key"] == (
            "AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv"
        )
        assert pixelpotion.config["telegram_chat_id"] == "581234902"
        assert pixelpotion.config["camera_module"] == "imx219"
        persisted = json.loads(isolated_state.config_path.read_text(encoding="utf-8"))
        assert persisted["camera_module"] == "imx219"

    def test_rejects_unknown_camera_module(self, client):
        # Arrange
        form = {"camera_module": "imx999"}

        # Act
        client.post("/save_config", data=form)

        # Assert — unknown hardware id is ignored, baseline stays.
        assert pixelpotion.config["camera_module"] == "imx708"


class TestSaveWifiRoute:
    def test_rejects_empty_ssid(self, client, fake_thread):
        # Act
        response = client.post(
            "/save_wifi", data={"wifi_ssid": "  ", "wifi_password": "irrelevant"}
        )

        # Assert
        assert response.status_code == 302
        assert pixelpotion.config["wifi_ssid"] == "CasaOlmedo_5G"
        assert ("error", "SSID cannot be empty.") in get_flashes(client)
        fake_thread.assert_not_called()

    def test_saves_credentials_and_connects_asynchronously(
        self, client, fake_thread
    ):
        # Act
        response = client.post(
            "/save_wifi",
            data={"wifi_ssid": "FibraHogar-2.4G", "wifi_password": "mate&tostadas99"},
        )

        # Assert
        assert response.status_code == 302
        assert pixelpotion.config["wifi_ssid"] == "FibraHogar-2.4G"
        assert pixelpotion.config["wifi_password"] == "mate&tostadas99"
        fake_thread.return_value.start.assert_called_once()


class TestCaptureRoute:
    def test_starts_pipeline_with_requested_style(self, client, fake_thread):
        # Act
        response = client.post("/capture", data={"style_id": "anime"})

        # Assert
        payload = response.get_json()
        assert payload["ok"] is True
        assert pixelpotion.config["active_style_id"] == "anime"
        _, kwargs = fake_thread.call_args
        assert kwargs["kwargs"] == {"style_id": "anime"}
        fake_thread.return_value.start.assert_called_once()

    def test_rejects_capture_while_pipeline_is_running(self, client, fake_thread):
        # Arrange
        pixelpotion.status["processing"] = True

        # Act
        payload = client.post("/capture", data={"style_id": "anime"}).get_json()

        # Assert
        assert payload["ok"] is False
        assert "already running" in payload["error"]
        fake_thread.assert_not_called()


class TestSetActiveStyle:
    def test_updates_active_style_from_json_body(self, client):
        # Act
        payload = client.post(
            "/set_active_style", json={"style_id": "watercolor"}
        ).get_json()

        # Assert
        assert payload == {"ok": True, "active_style_id": "watercolor"}
        assert pixelpotion.config["active_style_id"] == "watercolor"

    def test_keeps_current_style_when_body_is_empty(self, client):
        # Act
        payload = client.post("/set_active_style", json={}).get_json()

        # Assert
        assert payload["active_style_id"] == "pixar"


class TestStylesCrud:
    def test_add_style_appends_persisted_custom_style(self, client, isolated_state):
        # Arrange
        form = {
            "style_name": "Cyberpunk Neon",
            "style_prompt": (
                "TASK: Transform this photograph into a neon-lit cyberpunk "
                "scene with rain-soaked streets and holographic signs."
            ),
        }

        # Act
        client.post("/add_style", data=form)

        # Assert
        added = pixelpotion.config["styles"][-1]
        assert added["name"] == "Cyberpunk Neon"
        assert re.fullmatch(r"custom_[0-9a-f]{8}", added["id"])
        persisted = json.loads(isolated_state.config_path.read_text(encoding="utf-8"))
        assert persisted["styles"][-1]["name"] == "Cyberpunk Neon"

    def test_add_style_requires_name_and_prompt(self, client):
        # Arrange
        styles_before = len(pixelpotion.config["styles"])

        # Act
        client.post("/add_style", data={"style_name": "Cyberpunk Neon", "style_prompt": ""})

        # Assert
        assert len(pixelpotion.config["styles"]) == styles_before
        assert ("error", "Name and prompt are required.") in get_flashes(client)

    def test_edit_style_updates_only_the_matching_style(self, client):
        # Act
        client.post("/edit_style/anime", data={
            "style_name": "Anime Ghibli",
            "style_prompt": "TASK: Transform into a Studio Ghibli watercolor anime frame.",
        })

        # Assert
        styles = {s["id"]: s for s in pixelpotion.config["styles"]}
        assert styles["anime"]["name"] == "Anime Ghibli"
        assert styles["pixar"]["name"] == "Pixar 3D"

    def test_delete_style_reassigns_active_when_active_is_removed(self, client):
        # Arrange
        pixelpotion.config["active_style_id"] = "pixar"

        # Act
        client.post("/delete_style/pixar")

        # Assert
        remaining_ids = [s["id"] for s in pixelpotion.config["styles"]]
        assert "pixar" not in remaining_ids
        assert pixelpotion.config["active_style_id"] == remaining_ids[0]

    def test_deleting_last_style_clears_active_style(self, client):
        # Arrange
        pixelpotion.config["styles"] = [pixelpotion.config["styles"][0]]
        pixelpotion.config["active_style_id"] = "pixar"

        # Act
        client.post("/delete_style/pixar")

        # Assert
        assert pixelpotion.config["styles"] == []
        assert pixelpotion.config["active_style_id"] == ""


class TestGalleryActions:
    def test_delete_photo_removes_pending_file(self, client, isolated_state):
        # Arrange
        target = isolated_state.pending / "photo_20260609_201500.jpg"
        target.write_bytes(make_jpeg_bytes())

        # Act
        client.post("/delete_photo", data={"filename": target.name})

        # Assert
        assert not target.exists()

    def test_delete_selected_removes_only_chosen_files(self, client, isolated_state):
        # Arrange
        names = [
            "photo_20260608_110001.jpg",
            "photo_20260608_110002.jpg",
            "photo_20260608_110003.jpg",
        ]
        for name in names:
            (isolated_state.pending / name).write_bytes(make_jpeg_bytes())

        # Act
        client.post("/delete_selected", data={"selected_photos": names[:2]})

        # Assert
        remaining = [p.name for p in isolated_state.pending.glob("*.jpg")]
        assert remaining == [names[2]]

    def test_process_photo_requires_filename(self, client, fake_thread, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)

        # Act
        client.post("/process_photo", data={"filename": ""})

        # Assert
        assert ("error", "No file specified.") in get_flashes(client)
        fake_thread.assert_not_called()

    def test_process_photo_requires_wifi(self, client, fake_thread, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: False)

        # Act
        client.post(
            "/process_photo", data={"filename": "photo_20260609_201500.jpg"}
        )

        # Assert
        assert ("error", "No WiFi connection.") in get_flashes(client)
        fake_thread.assert_not_called()

    def test_process_photo_spawns_background_processing(
        self, client, fake_thread, monkeypatch
    ):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)

        # Act
        client.post("/process_photo", data={
            "filename": "photo_20260609_201500.jpg", "style_id": "watercolor",
        })

        # Assert
        _, kwargs = fake_thread.call_args
        assert kwargs["args"] == ("photo_20260609_201500.jpg",)
        assert kwargs["kwargs"] == {"style_id": "watercolor"}
        fake_thread.return_value.start.assert_called_once()


class TestStatusApi:
    def test_reports_status_wifi_pending_and_active_style(
        self, client, isolated_state, monkeypatch
    ):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        for name in ("photo_20260608_110001.jpg", "photo_20260608_110002.jpg"):
            (isolated_state.pending / name).write_bytes(make_jpeg_bytes())

        # Act
        payload = client.get("/status_api").get_json()

        # Assert
        assert payload["wifi"] is True
        assert payload["pending_count"] == 2
        assert payload["active_style_id"] == "pixar"
        assert payload["active_style_name"] == "Pixar 3D"
        assert payload["last_action"] == "Waiting..."
        assert payload["processing"] is False


class TestScanWifi:
    def test_returns_sorted_unique_network_names(self, client, monkeypatch):
        # Arrange
        fake_subprocess = MagicMock()
        fake_subprocess.check_output.return_value = IWLIST_SCAN_OUTPUT
        monkeypatch.setattr(pixelpotion, "subprocess", fake_subprocess)

        # Act
        payload = client.get("/scan_wifi").get_json()

        # Assert — duplicates collapsed, empty ESSIDs dropped, sorted output.
        assert payload == ["CafeDelBarrio-Guest", "CasaOlmedo_5G"]

    def test_returns_empty_list_when_scan_fails(self, client, monkeypatch):
        # Arrange
        fake_subprocess = MagicMock()
        fake_subprocess.check_output.side_effect = OSError("iwlist: not found")
        monkeypatch.setattr(pixelpotion, "subprocess", fake_subprocess)

        # Act / Assert
        assert client.get("/scan_wifi").get_json() == []
