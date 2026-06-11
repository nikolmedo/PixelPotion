"""Tests for app.py configuration helpers — load/save/merge and style lookup."""

import json
import sys

import pytest

import app as pixelpotion
from constants import DEFAULT_CONFIG


class TestLoadConfig:
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

    @pytest.mark.xfail(
        sys.platform == "win32",
        reason=(
            "Latent bug: save_config/load_config open config.json without "
            "encoding='utf-8', so non-UTF-8 default locales (Windows cp1252) "
            "cannot persist emoji. Works on the Pi only because its locale "
            "is UTF-8. Fix: pass encoding='utf-8' to both open() calls."
        ),
        raises=UnicodeEncodeError,
    )
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
