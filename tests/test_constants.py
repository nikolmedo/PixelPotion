"""Tests for constants.py — defaults loading and sanity of tuning values."""

import constants


class TestDefaultConfig:
    def test_default_config_contains_required_keys(self):
        # Arrange
        required_keys = {
            "gemini_api_key", "telegram_bot_token", "telegram_chat_id",
            "wifi_ssid", "wifi_password", "gpio_pin", "camera_module",
            "camera_resolution", "ap_ssid", "ap_password",
            "active_style_id", "styles",
        }

        # Act
        present_keys = set(constants.DEFAULT_CONFIG.keys())

        # Assert
        assert required_keys.issubset(present_keys)

    def test_default_styles_are_well_formed(self):
        # Arrange
        styles = constants.DEFAULT_CONFIG["styles"]

        # Act / Assert
        assert len(styles) > 0
        for style in styles:
            assert style["id"], "every style needs a non-empty id"
            assert style["name"], "every style needs a non-empty name"
            assert style["prompt"], "every style needs a non-empty prompt"

    def test_active_style_id_points_to_an_existing_style(self):
        # Arrange
        style_ids = {s["id"] for s in constants.DEFAULT_CONFIG["styles"]}

        # Act
        active_id = constants.DEFAULT_CONFIG["active_style_id"]

        # Assert
        assert active_id in style_ids


class TestTuningValues:
    def test_gemini_model_fallback_chain_is_not_empty(self):
        assert len(constants.GEMINI_MODELS) >= 1
        assert all(model.startswith("gemini-") for model in constants.GEMINI_MODELS)

    def test_retry_and_timeout_values_are_positive(self):
        assert constants.MAX_RETRIES >= 1
        assert constants.GEMINI_TIMEOUT_MS > 0
        assert constants.RETRY_INTERVAL_SECONDS > 0

    def test_configured_provider_is_implemented(self):
        # Arrange
        import ai_provider

        # Act / Assert — a typo in AI_PROVIDER would break every capture.
        assert constants.AI_PROVIDER in ai_provider._PROVIDERS
