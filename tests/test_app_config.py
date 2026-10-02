"""Tests for app.py configuration helpers — load/save/merge and style lookup."""

import copy
import json
import os
import stat
import sys

import pytest

import app as pixelpotion
from constants import DEFAULT_CONFIG


class TestLoadConfig:
    @pytest.mark.parametrize("damaged_content", [
        '{"gemini_api_key": "AIzaSyDk3v9XbT7eW2qLpZ8mNc4R',  # truncated by a power cut
        "",
        '["not", "an", "object"]',
    ])
    def test_unreadable_config_is_moved_aside_and_defaults_are_loaded(
        self, isolated_state, damaged_content
    ):
        # Arrange
        isolated_state.config_path.write_text(damaged_content, encoding="utf-8")

        # Act
        cfg = pixelpotion.load_config()

        # Assert — the app starts instead of crash-looping, and nothing is lost.
        assert cfg == DEFAULT_CONFIG
        assert not isolated_state.config_path.exists()
        backups = list(isolated_state.config_path.parent.glob("config.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == damaged_content

    def test_editing_loaded_default_styles_does_not_corrupt_default_config(self):
        # Arrange
        pristine_styles = copy.deepcopy(DEFAULT_CONFIG["styles"])
        cfg = pixelpotion.load_config()

        # Act — what /edit_style does to the in-memory config.
        cfg["styles"][0]["name"] = "Pixar 3D (warmer)"
        cfg["styles"][0]["prompt"] = "Transform this photo into a warm Pixar scene."

        # Assert
        assert DEFAULT_CONFIG["styles"] == pristine_styles

    def test_editing_restored_default_styles_does_not_corrupt_default_config(
        self, isolated_state
    ):
        # Arrange — an empty style list falls back to the factory styles.
        pristine_styles = copy.deepcopy(DEFAULT_CONFIG["styles"])
        isolated_state.config_path.write_text(json.dumps({"styles": []}))
        cfg = pixelpotion.load_config()

        # Act
        cfg["styles"][0]["name"] = "Pixar 3D (warmer)"

        # Assert
        assert DEFAULT_CONFIG["styles"] == pristine_styles

    def test_returns_defaults_when_config_file_is_missing(self, isolated_state):
        # Arrange — isolated_state points CONFIG_PATH at an empty tmp dir.
        assert not isolated_state.config_path.exists()

        # Act
        cfg = pixelpotion.load_config()

        # Assert
        assert cfg == DEFAULT_CONFIG

    def test_mutating_loaded_defaults_does_not_corrupt_default_config(self):
        # Arrange
        cfg = pixelpotion.load_config()

        # Act
        cfg["gemini_api_key"] = "AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv"

        # Assert
        assert DEFAULT_CONFIG["gemini_api_key"] == ""

    def test_saved_values_override_defaults_and_missing_keys_fall_back(
        self, isolated_state
    ):
        # Arrange — partial config, as left behind by an older app version.
        isolated_state.config_path.write_text(json.dumps({
            "gemini_api_key": "AIzaSyDk3v9XbT7eW2qLpZ8mNc4RfYhUj6sQwE0",
            "gpio_pin": 27,
        }))

        # Act
        cfg = pixelpotion.load_config()

        # Assert
        assert cfg["gemini_api_key"] == "AIzaSyDk3v9XbT7eW2qLpZ8mNc4RfYhUj6sQwE0"
        assert cfg["gpio_pin"] == 27
        assert cfg["ap_ssid"] == "PixelPotion-Setup"  # untouched default
        assert cfg["camera_module"] == "imx708"

    def test_restores_default_styles_when_saved_styles_are_empty(
        self, isolated_state
    ):
        # Arrange
        isolated_state.config_path.write_text(json.dumps({"styles": []}))

        # Act
        cfg = pixelpotion.load_config()

        # Assert
        assert cfg["styles"] == DEFAULT_CONFIG["styles"]

    def test_assigns_first_style_when_active_style_id_is_missing(
        self, isolated_state
    ):
        # Arrange
        isolated_state.config_path.write_text(json.dumps({"active_style_id": ""}))

        # Act
        cfg = pixelpotion.load_config()

        # Assert
        assert cfg["active_style_id"] == cfg["styles"][0]["id"]


class TestSaveConfig:
    def test_round_trip_preserves_accented_content(self, isolated_state):
        # Arrange — custom style with Spanish accents, saved by a real user.
        cfg = pixelpotion.load_config()
        cfg["styles"] = [{
            "id": "custom_a1b2c3d4",
            "name": "Acuarela Mágica",
            "prompt": "Transformá la foto en una acuarela mágica con tonos cálidos.",
        }]

        # Act
        pixelpotion.save_config(cfg)
        reloaded = pixelpotion.load_config()

        # Assert
        assert reloaded["styles"] == cfg["styles"]
        raw = isolated_state.config_path.read_bytes()
        assert b"\\u00e1" not in raw  # ensure_ascii=False keeps text readable

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
    def test_config_file_is_readable_only_by_owner(self, isolated_state):
        # Arrange — a pre-existing world-readable config from an older install.
        isolated_state.config_path.write_text("{}")
        os.chmod(isolated_state.config_path, 0o644)

        # Act
        pixelpotion.save_config(pixelpotion.load_config())

        # Assert — it holds API keys and the WiFi password.
        mode = stat.S_IMODE(os.stat(isolated_state.config_path).st_mode)
        assert mode == 0o600

    def test_round_trip_preserves_emoji_content(self):
        # Arrange
        cfg = pixelpotion.load_config()
        cfg["styles"] = [{
            "id": "custom_e5f6a7b8",
            "name": "Acuarela Mágica 🎨",
            "prompt": "Transformá la foto en una acuarela mágica 🎨 con tonos cálidos.",
        }]

        # Act
        pixelpotion.save_config(cfg)
        reloaded = pixelpotion.load_config()

        # Assert
        assert reloaded["styles"] == cfg["styles"]


    def test_interrupted_write_leaves_previous_config_intact(
        self, isolated_state, monkeypatch
    ):
        # Arrange — a valid config already on disk.
        previous = pixelpotion.load_config()
        previous["telegram_chat_id"] = "492817365"
        pixelpotion.save_config(previous)
        previous_bytes = isolated_state.config_path.read_bytes()
        updated = copy.deepcopy(previous)
        updated["telegram_chat_id"] = "-1001234567890"

        def power_cut(src, dst):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(pixelpotion.os, "replace", power_cut)

        # Act
        with pytest.raises(OSError):
            pixelpotion.save_config(updated)

        # Assert — the old file is untouched and no temp file is left behind.
        assert isolated_state.config_path.read_bytes() == previous_bytes
        assert sorted(p.name for p in isolated_state.config_path.parent.iterdir()) == [
            "config.json", "photos",
        ]

    def test_written_file_is_utf8_encoded(self, isolated_state):
        # Arrange
        cfg = pixelpotion.load_config()
        cfg["styles"] = [{
            "id": "custom_c9d0e1f2",
            "name": "Ñandú al óleo",
            "prompt": "Pintá la foto como un óleo con un ñandú de fondo.",
        }]

        # Act
        pixelpotion.save_config(cfg)

        # Assert — readable as UTF-8 regardless of the machine's locale.
        saved = json.loads(isolated_state.config_path.read_text(encoding="utf-8"))
        assert saved["styles"][0]["name"] == "Ñandú al óleo"


class TestActiveStyleLookup:
    def test_returns_prompt_and_name_of_active_style(self):
        # Arrange
        pixelpotion.config["active_style_id"] = "anime"

        # Act / Assert
        assert "anime" in pixelpotion.get_active_prompt().lower()
        assert pixelpotion.get_active_style_name() == "Anime / Manga"

    def test_falls_back_to_first_style_prompt_for_unknown_id(self):
        # Arrange — active style was deleted but the id stayed behind.
        pixelpotion.config["active_style_id"] = "vaporwave_deleted"

        # Act
        prompt = pixelpotion.get_active_prompt()

        # Assert
        assert prompt == pixelpotion.config["styles"][0]["prompt"]

    def test_style_name_falls_back_to_placeholder_for_unknown_id(self):
        # Arrange
        pixelpotion.config["active_style_id"] = "vaporwave_deleted"

        # Act / Assert
        assert pixelpotion.get_active_style_name() == "No style"
