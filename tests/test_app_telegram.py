"""Tests for app.py send_telegram_photos — Telegram Bot API delivery."""

from unittest.mock import MagicMock

import pytest
import requests

import app as pixelpotion
from conftest import make_jpeg_bytes

BOT_TOKEN = "7123456789:AAHk3mP9qRsT2uVwXyZ1bCdEfGhIjKlMnOp"
CHAT_ID = "492817365"


@pytest.fixture
def photo_pair(isolated_state):
    """Original + processed JPEGs, as produced by a successful pipeline run."""
    original = isolated_state.originals / "photo_20260610_143052.jpg"
    original.write_bytes(make_jpeg_bytes())
    processed = isolated_state.processed / "styled_20260610_143187.jpg"
    processed.write_bytes(make_jpeg_bytes(color=(30, 200, 90)))
    return str(original), str(processed)


@pytest.fixture
def fake_post(monkeypatch):
    post = MagicMock(return_value=MagicMock(ok=True, text='{"ok":true}'))
    monkeypatch.setattr(requests, "post", post)
    return post


class TestSendTelegramPhotos:
    def test_sends_original_and_styled_photo_with_captions(
        self, photo_pair, fake_post
    ):
        # Arrange
        original, processed = photo_pair

        # Act
        result = pixelpotion.send_telegram_photos(original, processed, "Pixar 3D")

        # Assert
        assert result is True
        assert fake_post.call_count == 2
        first_call, second_call = fake_post.call_args_list
        expected_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        assert first_call.args[0] == expected_url
        assert first_call.kwargs["data"] == {
            "chat_id": CHAT_ID, "caption": "📷 Original photo"
        }
        assert second_call.kwargs["data"] == {
            "chat_id": CHAT_ID, "caption": "🎨 Style: Pixar 3D"
        }
        assert "photo" in first_call.kwargs["files"]

    def test_uses_generic_caption_when_style_name_is_empty(
        self, photo_pair, fake_post
    ):
        # Arrange
        original, processed = photo_pair

        # Act
        pixelpotion.send_telegram_photos(original, processed)

        # Assert
        _, second_call = fake_post.call_args_list
        assert second_call.kwargs["data"]["caption"] == "🎨 Styled version"

    def test_returns_false_without_calling_api_when_token_is_missing(
        self, photo_pair, fake_post
    ):
        # Arrange
        pixelpotion.config["telegram_bot_token"] = ""
        original, processed = photo_pair

        # Act / Assert
        assert pixelpotion.send_telegram_photos(original, processed, "Pixar 3D") is False
        fake_post.assert_not_called()

    def test_returns_false_without_calling_api_when_chat_id_is_missing(
        self, photo_pair, fake_post
    ):
        # Arrange
        pixelpotion.config["telegram_chat_id"] = ""
        original, processed = photo_pair

        # Act / Assert
        assert pixelpotion.send_telegram_photos(original, processed, "Pixar 3D") is False
        fake_post.assert_not_called()

    def test_returns_false_when_second_upload_is_rejected(
        self, photo_pair, fake_post
    ):
        # Arrange — Telegram rate-limits the second photo.
        original, processed = photo_pair
        fake_post.side_effect = [
            MagicMock(ok=True, text='{"ok":true}'),
            MagicMock(
                ok=False,
                text='{"ok":false,"error_code":429,'
                     '"description":"Too Many Requests: retry after 35"}',
            ),
        ]

        # Act / Assert
        assert pixelpotion.send_telegram_photos(original, processed, "Pixar 3D") is False

    def test_returns_false_on_network_failure(self, photo_pair, fake_post):
        # Arrange
        original, processed = photo_pair
        fake_post.side_effect = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='api.telegram.org', port=443): "
            "Max retries exceeded"
        )

        # Act / Assert — offline delivery must fail soft so the photo stays queued.
        assert pixelpotion.send_telegram_photos(original, processed, "Pixar 3D") is False
