"""Tests for app.py Flask routes — config, capture, styles CRUD, gallery, status."""

import json
import re
from html.parser import HTMLParser
from unittest.mock import MagicMock

import pytest

import app as pixelpotion
from conftest import (
    BASELINE_CONFIG, CSRF_TEST_TOKEN, REPO_ROOT, make_jpeg_bytes, seed_csrf_session,
)

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
        assert response.headers["Location"] == "/settings"
        assert pixelpotion.config["gemini_api_key"] == (
            "AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv"
        )
        assert pixelpotion.config["telegram_chat_id"] == "581234902"
        assert pixelpotion.config["camera_module"] == "imx219"
        persisted = json.loads(isolated_state.config_path.read_text(encoding="utf-8"))
        assert persisted["camera_module"] == "imx219"

    def test_blank_secret_fields_keep_stored_values(self, client, isolated_state):
        # Arrange — the form never carries stored secrets, so they arrive blank.
        form = {
            "gemini_api_key": "",
            "telegram_bot_token": "   ",
            "telegram_chat_id": "581234902",
            "camera_module": "imx708",
        }

        # Act
        client.post("/save_config", data=form)

        # Assert
        assert pixelpotion.config["gemini_api_key"] == BASELINE_CONFIG["gemini_api_key"]
        assert pixelpotion.config["telegram_bot_token"] == (
            BASELINE_CONFIG["telegram_bot_token"]
        )
        assert pixelpotion.config["telegram_chat_id"] == "581234902"
        persisted = json.loads(isolated_state.config_path.read_text(encoding="utf-8"))
        assert persisted["gemini_api_key"] == BASELINE_CONFIG["gemini_api_key"]

    def test_new_secret_values_replace_stored_ones(self, client):
        # Act
        client.post("/save_config", data={
            "gemini_api_key": "AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv",
            "telegram_bot_token": "",
            "telegram_chat_id": "492817365",
        })

        # Assert
        assert pixelpotion.config["gemini_api_key"] == (
            "AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv"
        )
        assert pixelpotion.config["telegram_bot_token"] == (
            BASELINE_CONFIG["telegram_bot_token"]
        )

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
        assert response.headers["Location"] == "/settings"
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
        assert response.headers["Location"] == "/settings"
        assert pixelpotion.config["wifi_ssid"] == "FibraHogar-2.4G"
        assert pixelpotion.config["wifi_password"] == "mate&tostadas99"
        fake_thread.return_value.start.assert_called_once()

    def test_blank_password_keeps_stored_one_for_the_same_network(
        self, client, fake_thread, monkeypatch
    ):
        # Arrange — capture what the background connect would use.
        connect = MagicMock(return_value=True)
        monkeypatch.setattr(pixelpotion, "connect_wifi", connect)

        # Act
        client.post("/save_wifi", data={"wifi_ssid": "CasaOlmedo_5G", "wifi_password": ""})

        # Assert
        assert pixelpotion.config["wifi_password"] == "patagonia2024!"
        _, kwargs = fake_thread.call_args
        kwargs["target"]()
        connect.assert_called_once_with("CasaOlmedo_5G", "patagonia2024!")

    def test_blank_password_for_a_new_network_is_stored_as_open(
        self, client, fake_thread
    ):
        # Act
        client.post(
            "/save_wifi", data={"wifi_ssid": "CafeDelBarrio-Guest", "wifi_password": ""}
        )

        # Assert
        assert pixelpotion.config["wifi_ssid"] == "CafeDelBarrio-Guest"
        assert pixelpotion.config["wifi_password"] == ""

    def test_rejects_credentials_with_quotes_or_line_breaks(
        self, client, fake_thread
    ):
        # Act
        client.post("/save_wifi", data={
            "wifi_ssid": "FibraHogar-2.4G",
            "wifi_password": 'mate"\nnetwork={',
        })

        # Assert — nothing saved, no connection attempt.
        assert pixelpotion.config["wifi_ssid"] == "CasaOlmedo_5G"
        assert pixelpotion.config["wifi_password"] == "patagonia2024!"
        assert (
            "error", "SSID and password cannot contain quotes or line breaks."
        ) in get_flashes(client)
        fake_thread.assert_not_called()


class TestSettingsPage:
    def test_never_renders_stored_secrets(self, client):
        # Act
        body = client.get("/settings").data

        # Assert — secrets stay server-side; non-secret settings still show.
        assert BASELINE_CONFIG["gemini_api_key"].encode() not in body
        assert BASELINE_CONFIG["telegram_bot_token"].encode() not in body
        assert BASELINE_CONFIG["wifi_password"].encode() not in body
        assert b'value="CasaOlmedo_5G"' in body
        assert b'value="492817365"' in body
        assert "Saved — leave blank to keep".encode() in body

    def test_shows_format_hints_when_secrets_are_unset(self, client):
        # Arrange
        pixelpotion.config["gemini_api_key"] = ""

        # Act
        body = client.get("/settings").data

        # Assert
        assert b'placeholder="AIzaSy..."' in body

    def test_camera_page_no_longer_renders_the_settings_forms(self, client):
        # Act
        body = client.get("/").get_data(as_text=True)

        # Assert
        assert 'action="/save_config"' not in body
        assert 'action="/save_wifi"' not in body


class TestCaptureRoute:
    @pytest.fixture
    def camera_shot(self, monkeypatch, isolated_state):
        photo = isolated_state.originals / "photo_20260610_143052.jpg"
        photo.write_bytes(make_jpeg_bytes())
        capture = MagicMock(return_value=str(photo))
        monkeypatch.setattr(pixelpotion, "capture_photo", capture)
        return capture

    def test_captures_into_pending_and_queues_requested_style(
        self, client, camera_shot, isolated_state
    ):
        # Act
        response = client.post("/capture", data={"style_id": "anime"})

        # Assert
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["filename"] == "photo_20260610_143052.jpg"
        assert pixelpotion.config["active_style_id"] == "anime"
        pending_photo = isolated_state.pending / "photo_20260610_143052.jpg"
        assert pending_photo.exists()
        assert pixelpotion.read_photo_state(pending_photo)["style_id"] == "anime"
        assert pixelpotion.work_queue.get_nowait() == "photo_20260610_143052.jpg"

    def test_captures_while_another_photo_is_processing(
        self, client, camera_shot, isolated_state
    ):
        # Arrange
        pixelpotion.status["processing"] = True

        # Act
        payload = client.post("/capture", data={"style_id": "anime"}).get_json()

        # Assert — processing no longer blocks the camera.
        assert payload["ok"] is True
        assert (isolated_state.pending / "photo_20260610_143052.jpg").exists()
        assert pixelpotion.work_queue.qsize() == 1

    def test_reports_capture_failure(self, client, camera_shot):
        # Arrange
        camera_shot.return_value = None

        # Act
        payload = client.post("/capture", data={"style_id": "anime"}).get_json()

        # Assert
        assert payload["ok"] is False
        assert payload["error"] == "Error: could not capture photo"
        assert pixelpotion.work_queue.qsize() == 0


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

    def test_delete_photo_also_removes_its_state_file(self, client, isolated_state):
        # Arrange
        target = isolated_state.pending / "photo_20260609_201500.jpg"
        target.write_bytes(make_jpeg_bytes())
        pixelpotion.update_photo_state(target, style_id="anime")

        # Act
        client.post("/delete_photo", data={"filename": target.name})

        # Assert
        assert list(isolated_state.pending.iterdir()) == []

    def test_delete_selected_also_removes_state_files(self, client, isolated_state):
        # Arrange
        names = ["photo_20260608_110001.jpg", "photo_20260608_110002.jpg"]
        for name in names:
            photo = isolated_state.pending / name
            photo.write_bytes(make_jpeg_bytes())
            pixelpotion.update_photo_state(photo, style_id="pixar")

        # Act
        client.post("/delete_selected", data={"selected_photos": names})

        # Assert
        assert list(isolated_state.pending.iterdir()) == []

    def test_state_file_cannot_be_deleted_or_served_by_name(
        self, client, isolated_state
    ):
        # Arrange
        photo = isolated_state.pending / "photo_20260609_201500.jpg"
        photo.write_bytes(make_jpeg_bytes())
        pixelpotion.update_photo_state(photo, style_id="anime")
        sidecar = "photo_20260609_201500.jpg.json"

        # Act
        client.post("/delete_photo", data={"filename": sidecar})
        served = client.get(f"/pending_photo/{sidecar}")

        # Assert
        assert (isolated_state.pending / sidecar).exists()
        assert served.status_code == 404

    def test_gallery_lists_photos_but_not_their_state_files(
        self, client, isolated_state
    ):
        # Arrange
        photo = isolated_state.pending / "photo_20260608_110001.jpg"
        photo.write_bytes(make_jpeg_bytes())
        pixelpotion.update_photo_state(photo, style_id="pixar")

        # Act
        body = client.get("/gallery").get_data(as_text=True)

        # Assert
        assert re.search(r'id="pendingBadge"[^>]*>1</span>', body)
        assert "photo_20260608_110001.jpg.json" not in body

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

    @pytest.mark.parametrize("hostile_name", ["../../config.json", "ABSOLUTE"])
    def test_delete_photo_never_touches_files_outside_pending(
        self, client, isolated_state, hostile_name
    ):
        # Arrange — the runtime config sits two levels above the pending queue.
        sentinel = isolated_state.config_path
        sentinel.write_text('{"gemini_api_key": "AIzaSyDk3v9XbT7eW2qLpZ8mNc4RfYhUj6sQwE0"}')
        if hostile_name == "ABSOLUTE":
            hostile_name = str(sentinel)

        # Act
        client.post("/delete_photo", data={"filename": hostile_name})

        # Assert
        assert sentinel.exists()
        assert ("error", "Invalid file name.") in get_flashes(client)

    def test_delete_photo_reports_missing_file(self, client):
        # Act
        client.post("/delete_photo", data={"filename": "photo_19990101_000000.jpg"})

        # Assert
        assert ("error", "photo_19990101_000000.jpg was not found.") in get_flashes(client)

    def test_delete_selected_skips_hostile_names_and_counts_real_deletions(
        self, client, isolated_state
    ):
        # Arrange
        sentinel = isolated_state.config_path
        sentinel.write_text('{"telegram_chat_id": "492817365"}')
        valid = isolated_state.pending / "photo_20260608_110001.jpg"
        valid.write_bytes(make_jpeg_bytes())
        selection = [
            valid.name,
            "../../config.json",
            str(sentinel),
            "photo_19990101_000000.jpg",  # valid name, already gone
        ]

        # Act
        client.post("/delete_selected", data={"selected_photos": selection})

        # Assert
        assert not valid.exists()
        assert sentinel.exists()
        flashes = get_flashes(client)
        assert ("error", "Skipped 2 invalid file name(s).") in flashes
        assert ("success", "1 photo(s) deleted.") in flashes

    def test_delete_photo_reports_a_file_the_system_refuses_to_delete(
        self, client, isolated_state, monkeypatch
    ):
        # Arrange
        target = isolated_state.pending / "photo_20260609_201500.jpg"
        target.write_bytes(make_jpeg_bytes())

        def refuse(self, *args, **kwargs):
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(type(target), "unlink", refuse)

        # Act
        response = client.post("/delete_photo", data={"filename": target.name})

        # Assert — a flash message, not a 500.
        assert response.status_code == 302
        assert target.exists()
        assert (
            "error", "Could not delete photo_20260609_201500.jpg."
        ) in get_flashes(client)

    def test_delete_selected_keeps_going_after_a_refused_file(
        self, client, isolated_state, monkeypatch
    ):
        # Arrange
        locked = isolated_state.pending / "photo_20260608_110001.jpg"
        removable = isolated_state.pending / "photo_20260608_110002.jpg"
        for photo in (locked, removable):
            photo.write_bytes(make_jpeg_bytes())
        real_unlink = type(locked).unlink

        def unlink_unless_locked(self, *args, **kwargs):
            if self.name == locked.name:
                raise PermissionError(13, "Permission denied", str(self))
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(type(locked), "unlink", unlink_unless_locked)

        # Act
        response = client.post(
            "/delete_selected", data={"selected_photos": [locked.name, removable.name]}
        )

        # Assert
        assert response.status_code == 302
        assert locked.exists()
        assert not removable.exists()
        flashes = get_flashes(client)
        assert ("error", "Could not delete 1 photo(s).") in flashes
        assert ("success", "1 photo(s) deleted.") in flashes

    def test_process_photo_rejects_paths_outside_pending(
        self, client, fake_thread, monkeypatch, isolated_state
    ):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        outside = isolated_state.config_path.parent / "shadow.jpg"
        outside.write_bytes(make_jpeg_bytes())

        # Act
        client.post("/process_photo", data={"filename": str(outside)})

        # Assert
        assert ("error", "Invalid file name.") in get_flashes(client)
        assert pixelpotion.work_queue.empty()
        assert list(isolated_state.originals.iterdir()) == []
        assert list(isolated_state.pending.iterdir()) == []

    def test_process_photo_requires_filename(self, client, fake_thread, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)

        # Act
        client.post("/process_photo", data={"filename": ""})

        # Assert
        assert ("error", "No file specified.") in get_flashes(client)
        assert pixelpotion.work_queue.empty()

    def test_process_photo_requires_wifi(self, client, fake_thread, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: False)

        # Act
        client.post(
            "/process_photo", data={"filename": "photo_20260609_201500.jpg"}
        )

        # Assert
        assert ("error", "No WiFi connection.") in get_flashes(client)
        assert pixelpotion.work_queue.empty()

    def test_process_photo_queues_the_photo_with_the_chosen_style(
        self, client, monkeypatch, isolated_state
    ):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        photo = isolated_state.pending / "photo_20260609_201500.jpg"
        photo.write_bytes(make_jpeg_bytes())

        # Act
        client.post("/process_photo", data={
            "filename": "photo_20260609_201500.jpg", "style_id": "watercolor",
        })

        # Assert
        assert pixelpotion.work_queue.get_nowait() == "photo_20260609_201500.jpg"
        assert pixelpotion.read_photo_state(photo)["style_id"] == "watercolor"
        assert (
            "info", "Queued photo_20260609_201500.jpg for processing."
        ) in get_flashes(client)

    def test_process_photo_retries_a_photo_marked_as_failed(
        self, client, monkeypatch, isolated_state
    ):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        photo = isolated_state.pending / "photo_20260609_201500.jpg"
        photo.write_bytes(make_jpeg_bytes())
        pixelpotion.update_photo_state(
            photo, style_id="anime", failed=True,
            failed_reason="Gemini rejected the API key (403)",
        )

        # Act
        client.post("/process_photo", data={
            "filename": photo.name, "style_id": "anime",
        })

        # Assert
        assert pixelpotion.read_photo_state(photo)["failed"] is False
        assert pixelpotion.work_queue.get_nowait() == photo.name

    def test_gallery_shows_the_failure_reason_escaped(self, client, isolated_state):
        # Arrange — reasons can carry API text; it must render as text only.
        photo = isolated_state.pending / "photo_20260609_201500.jpg"
        photo.write_bytes(make_jpeg_bytes())
        pixelpotion.update_photo_state(
            photo, failed=True,
            failed_reason='Gemini error 400: <img src=x onerror="alert(1)">',
        )

        # Act
        body = client.get("/gallery").get_data(as_text=True)

        # Assert
        assert (
            "Failed: Gemini error 400: &lt;img src=x onerror=&#34;alert(1)&#34;&gt;"
            in body
        )
        assert '<img src=x onerror="alert(1)">' not in body

    def test_gallery_shows_no_failure_badge_for_healthy_photos(
        self, client, isolated_state
    ):
        # Arrange
        (isolated_state.pending / "photo_20260609_201500.jpg").write_bytes(
            make_jpeg_bytes()
        )

        # Act
        body = client.get("/gallery").get_data(as_text=True)

        # Assert
        assert "Failed:" not in body

    def test_process_photo_reports_a_missing_photo(self, client, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)

        # Act
        client.post("/process_photo", data={"filename": "photo_19990101_000000.jpg"})

        # Assert
        assert (
            "error", "photo_19990101_000000.jpg was not found."
        ) in get_flashes(client)
        assert pixelpotion.work_queue.qsize() == 0

    def test_process_all_reports_only_what_it_actually_queued(
        self, client, monkeypatch, isolated_state
    ):
        # Arrange — one of three photos is already waiting in the queue.
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        names = [
            "photo_20260608_110001.jpg",
            "photo_20260608_110002.jpg",
            "photo_20260608_110003.jpg",
        ]
        for name in names:
            (isolated_state.pending / name).write_bytes(make_jpeg_bytes())
        pixelpotion.enqueue_pending(names[0])

        # Act
        client.post("/process_all", data={"style_id": "anime"})

        # Assert
        assert ("info", "Queued 2 photo(s) for processing.") in get_flashes(client)
        assert pixelpotion.work_queue.qsize() == 3

    def test_process_all_requires_wifi(self, client, monkeypatch, isolated_state):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: False)
        (isolated_state.pending / "photo_20260608_110001.jpg").write_bytes(
            make_jpeg_bytes()
        )

        # Act
        client.post("/process_all", data={"style_id": "anime"})

        # Assert
        assert ("error", "No WiFi connection.") in get_flashes(client)
        assert pixelpotion.work_queue.qsize() == 0


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
        assert payload["capturing"] is False
        assert payload["step"] == "idle"
        assert payload["failed_step"] == ""

    def test_reports_which_step_failed(self, client):
        # Arrange
        pixelpotion.fail_status("brewing", "AI processing failed — kept in pending for retry")

        # Act
        payload = client.get("/status_api").get_json()

        # Assert
        assert payload["step"] == "failed"
        assert payload["failed_step"] == "brewing"
        assert payload["step"] in pixelpotion.PIPELINE_STEPS


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


class FormCsrfAudit(HTMLParser):
    """Collect every <form> and whether it carries a csrf_token field."""

    def __init__(self):
        super().__init__()
        self.forms = []  # [action, has_token]
        self._open = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._open = [attrs.get("action"), False]
            self.forms.append(self._open)
        elif tag == "input" and self._open and attrs.get("name") == "csrf_token":
            self._open[1] = bool(attrs.get("value"))

    def handle_endtag(self, tag):
        if tag == "form":
            self._open = None


class TestCsrfProtection:
    def test_form_post_without_token_is_rejected_and_config_unchanged(
        self, plain_client, isolated_state
    ):
        # Arrange — a valid session exists, but the forged form carries no token.
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post("/save_config", data={
            "gemini_api_key": "AIzaSyAttackerControlledKey00000000000",
            "telegram_chat_id": "666000666",
        })

        # Assert
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        assert pixelpotion.config["telegram_chat_id"] == "492817365"
        assert pixelpotion.config["gemini_api_key"] == BASELINE_CONFIG["gemini_api_key"]
        assert not isolated_state.config_path.exists()
        assert ("error", "Your session expired — please try again.") in (
            get_flashes(plain_client)
        )

    def test_post_without_any_session_is_rejected(self, plain_client, isolated_state):
        # Arrange — e.g. the service restarted and the old session is gone.
        target = isolated_state.pending / "photo_20260609_201500.jpg"
        target.write_bytes(make_jpeg_bytes())

        # Act
        plain_client.post("/delete_photo", data={
            "filename": target.name, "csrf_token": CSRF_TEST_TOKEN,
        })

        # Assert
        assert target.exists()

    def test_wrong_token_is_rejected_and_file_survives(self, client, isolated_state):
        # Arrange
        target = isolated_state.pending / "photo_20260609_201500.jpg"
        target.write_bytes(make_jpeg_bytes())

        # Act
        client.post(
            "/delete_photo",
            data={"filename": target.name},
            headers={"X-CSRF-Token": "forged-token-from-another-site"},
        )

        # Assert
        assert target.exists()

    def test_rejection_redirects_back_to_same_origin_referrer(self, plain_client):
        # Arrange
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post(
            "/add_style",
            data={"style_name": "Cyberpunk Neon", "style_prompt": "TASK: neon city"},
            headers={"Referer": "http://localhost/styles"},
        )

        # Assert
        assert response.headers["Location"] == "/styles"
        assert [s["name"] for s in pixelpotion.config["styles"]] == [
            "Pixar 3D", "Anime / Manga", "Watercolor",
        ]

    def test_rejection_ignores_foreign_referrer(self, plain_client):
        # Arrange
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post(
            "/delete_style/pixar",
            headers={"Referer": "http://evil.example.com/styles"},
        )

        # Assert
        assert response.headers["Location"] == "/"
        assert "pixar" in [s["id"] for s in pixelpotion.config["styles"]]

    @pytest.mark.parametrize("referrer", [
        "http://localhost//evil.example.com/styles",
        "http://localhost/\\evil.example.com/styles",
    ])
    def test_rejection_ignores_protocol_relative_referrer_path(
        self, plain_client, referrer
    ):
        # Arrange — same host, but browsers read `//host` as another site.
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post(
            "/delete_style/pixar", headers={"Referer": referrer}
        )

        # Assert
        assert response.headers["Location"] == "/"

    def test_form_field_token_is_accepted(self, plain_client):
        # Arrange
        seed_csrf_session(plain_client)

        # Act
        plain_client.post("/save_config", data={
            "telegram_chat_id": "581234902", "csrf_token": CSRF_TEST_TOKEN,
        })

        # Assert
        assert pixelpotion.config["telegram_chat_id"] == "581234902"

    def test_json_route_without_token_returns_400(self, plain_client):
        # Arrange
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post(
            "/set_active_style", json={"style_id": "watercolor"}
        )

        # Assert
        assert response.status_code == 400
        assert response.get_json()["ok"] is False
        assert pixelpotion.config["active_style_id"] == "pixar"

    def test_capture_without_token_returns_400_json(self, plain_client, fake_thread):
        # Arrange
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post("/capture", data={"style_id": "anime"})

        # Assert
        assert response.status_code == 400
        assert response.get_json()["ok"] is False
        assert pixelpotion.work_queue.empty()

    def test_json_route_accepts_header_token(self, plain_client):
        # Arrange
        seed_csrf_session(plain_client)

        # Act
        response = plain_client.post(
            "/set_active_style",
            json={"style_id": "watercolor"},
            headers={"X-CSRF-Token": CSRF_TEST_TOKEN},
        )

        # Assert
        assert response.status_code == 200
        assert pixelpotion.config["active_style_id"] == "watercolor"

    def test_rendered_token_matches_session_token(self, plain_client):
        # Act
        body = plain_client.get("/settings").get_data(as_text=True)

        # Assert — the page mints a token and embeds the one stored in the session.
        with plain_client.session_transaction() as session:
            token = session["_csrf_token"]
        assert f'name="csrf_token" value="{token}"' in body
        assert f'<meta name="csrf-token" content="{token}">' in body

    @pytest.mark.parametrize("page", ["/settings", "/styles", "/gallery"])
    def test_every_form_carries_a_csrf_token(
        self, client, isolated_state, monkeypatch, page
    ):
        # Arrange — render every conditional block (pending photos, WiFi up).
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        (isolated_state.pending / "photo_20260609_201500.jpg").write_bytes(
            make_jpeg_bytes()
        )
        audit = FormCsrfAudit()

        # Act
        audit.feed(client.get(page).get_data(as_text=True))

        # Assert
        assert audit.forms, f"expected forms on {page}"
        missing = [action for action, has_token in audit.forms if not has_token]
        assert missing == []


class FormNestingAudit(HTMLParser):
    """Track the deepest <form> nesting seen in a document."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.max_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


class TestTemplateInjectionSafety:
    def test_style_name_with_quote_lives_only_in_escaped_data_attribute(self, client):
        # Arrange — an apostrophe used to break out of the inline confirm() string.
        pixelpotion.config["styles"].append({
            "id": "custom_1a2b3c4d",
            "name": "Van Gogh's Starry Night",
            "prompt": "TASK: Transform this photograph into a Van Gogh painting.",
        })

        # Act
        body = client.get("/styles").data

        # Assert
        assert b'data-style-name="Van Gogh&#39;s Starry Night"' in body
        assert b"Van Gogh's" not in body
        assert b"onsubmit=" not in body

    def test_active_style_id_is_embedded_only_as_an_escaped_attribute(self, client):
        # Arrange — a hostile id must not break out of the attribute.
        hostile_id = 'x"><script>alert(1)</script>'
        pixelpotion.config["styles"].append(
            {"id": hostile_id, "name": "Hostile", "prompt": "TASK: anything."}
        )
        pixelpotion.config["active_style_id"] = hostile_id

        # Act
        body = client.get("/").get_data(as_text=True)

        # Assert
        assert 'data-active-style="x&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in body
        assert "<script>alert(1)</script>" not in body

    @pytest.mark.parametrize("page", ["/", "/styles", "/gallery", "/settings"])
    def test_pages_never_build_markup_with_inner_html(self, client, page):
        # Act
        body = client.get(page).get_data(as_text=True)

        # Assert
        assert "innerHTML" not in body

    def test_static_scripts_never_build_markup_with_inner_html(self, client):
        # Arrange
        scripts = sorted((REPO_ROOT / "static").glob("*.js"))

        # Act / Assert
        assert scripts
        for script in scripts:
            source = client.get(f"/static/{script.name}").get_data(as_text=True)
            assert "innerHTML" not in source, script.name

    def test_wifi_scan_results_are_inserted_as_text(self, client):
        # Act
        source = client.get("/static/settings.js").get_data(as_text=True)

        # Assert — SSIDs are attacker-controlled; they must go through textContent.
        assert "item.textContent = ssid;" in source

    def test_gallery_has_no_nested_forms(self, client, isolated_state, monkeypatch):
        # Arrange
        monkeypatch.setattr(pixelpotion, "is_wifi_connected", lambda: True)
        for name in ("photo_20260608_110001.jpg", "photo_20260608_110002.jpg"):
            (isolated_state.pending / name).write_bytes(make_jpeg_bytes())
        audit = FormNestingAudit()

        # Act
        audit.feed(client.get("/gallery").get_data(as_text=True))

        # Assert
        assert audit.max_depth == 1
        assert audit.depth == 0

    def test_gallery_checkboxes_belong_to_the_bulk_form(
        self, client, isolated_state
    ):
        # Arrange
        (isolated_state.pending / "photo_20260608_110001.jpg").write_bytes(
            make_jpeg_bytes()
        )

        # Act
        body = client.get("/gallery").get_data(as_text=True)

        # Assert
        boxes = re.findall(r'<input type="checkbox"[^>]*>', body)
        assert len(boxes) == 1
        assert 'name="selected_photos"' in boxes[0]
        assert 'value="photo_20260608_110001.jpg"' in boxes[0]
        assert 'form="bulkForm"' in boxes[0]
        assert 'aria-label="Select photo_20260608_110001.jpg"' in boxes[0]
        assert "onclick=\"processOne(" not in body
        assert "openModal('" not in body


class TestStyleSelectionFeedback:
    def test_shared_post_helper_sends_the_csrf_header_and_checks_status(self, client):
        # Act
        source = client.get("/static/app.js").get_data(as_text=True)

        # Assert — browser behavior itself is not exercised by this suite.
        helper = re.search(r"function post\(.*?\n    \}", source, re.S).group(0)
        assert "'X-CSRF-Token': csrfToken()" in helper
        assert "if (!r.ok)" in helper

    @pytest.mark.parametrize("script", ["index.js", "styles.js"])
    def test_failed_style_save_is_reported_to_the_user(self, client, script):
        # Act
        source = client.get(f"/static/{script}").get_data(as_text=True)

        # Assert
        call = re.search(r"PP\.post\('/set_active_style'.*?;", source, re.S).group(0)
        assert ".catch(" in call
        assert "Could not save style" in call
