"""Tests for ai_provider.py — Gemini adapter, client caching, retry/fallback."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

import ai_provider
from constants import GEMINI_MODELS, MAX_RETRIES
from conftest import make_gemini_response, make_jpeg_bytes

GEMINI_API_KEY = "AIzaSyDk3v9XbT7eW2qLpZ8mNc4RfYhUj6sQwE0"
PIXAR_PROMPT = (
    "TASK: Transform this input photograph into a Pixar / Disney 3D "
    "animation style illustration. Keep every person recognizable."
)


@pytest.fixture
def source_photo(tmp_path):
    photo_path = tmp_path / "photo_20260610_143052.jpg"
    photo_path.write_bytes(make_jpeg_bytes())
    return str(photo_path)


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    """Retry backoff sleeps up to 2**MAX_RETRIES seconds — skip them."""
    monkeypatch.setattr(ai_provider.time, "sleep", lambda seconds: None)


class TestProcessImage:
    def test_returns_processed_jpeg_path_on_success(
        self, fake_genai, source_photo, isolated_state
    ):
        # Arrange
        generated = make_jpeg_bytes(color=(30, 200, 90))
        fake_genai.client.models.generate_content.return_value = (
            make_gemini_response(generated)
        )

        # Act
        result = ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        assert result is not None
        output = Path(result)
        assert output.parent == isolated_state.processed
        assert output.exists()
        assert output.name.startswith("styled_")
        with Image.open(output) as img:
            assert img.format == "JPEG"

    def test_raises_value_error_for_unknown_provider(self, source_photo, monkeypatch):
        # Arrange
        monkeypatch.setattr(ai_provider, "AI_PROVIDER", "stable-diffusion")

        # Act / Assert
        with pytest.raises(ValueError, match="stable-diffusion"):
            ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)


class TestClientCaching:
    def test_reuses_client_for_same_api_key(self, fake_genai):
        # Arrange / Act
        first = ai_provider._get_or_create_client(GEMINI_API_KEY)
        second = ai_provider._get_or_create_client(GEMINI_API_KEY)

        # Assert
        assert first is second
        assert fake_genai.genai.Client.call_count == 1

    def test_creates_new_client_when_api_key_changes(self, fake_genai):
        # Arrange
        rotated_key = "AIzaSyB9pQw2eRt5yUi8oPa1sDf4gHj7kLz0xCv"

        # Act
        ai_provider._get_or_create_client(GEMINI_API_KEY)
        ai_provider._get_or_create_client(rotated_key)

        # Assert
        assert fake_genai.genai.Client.call_count == 2
        _, last_kwargs = fake_genai.genai.Client.call_args
        assert last_kwargs["api_key"] == rotated_key


class TestTryGenerateGemini:
    def test_returns_image_bytes_from_inline_data_part(self, fake_genai):
        # Arrange
        generated = make_jpeg_bytes(color=(255, 140, 0))
        client = MagicMock()
        client.models.generate_content.return_value = make_gemini_response(generated)

        # Act
        result = ai_provider._try_generate_gemini(
            client, "gemini-2.5-flash-image", make_jpeg_bytes(), PIXAR_PROMPT
        )

        # Assert
        assert result == generated

    def test_returns_none_when_response_has_no_candidates(self, fake_genai):
        # Arrange
        response = MagicMock()
        response.candidates = []
        client = MagicMock()
        client.models.generate_content.return_value = response

        # Act
        result = ai_provider._try_generate_gemini(
            client, "gemini-2.5-flash-image", make_jpeg_bytes(), PIXAR_PROMPT
        )

        # Assert
        assert result is None

    def test_returns_none_when_no_part_carries_inline_data(self, fake_genai):
        # Arrange — model answered with text only, no generated image.
        client = MagicMock()
        client.models.generate_content.return_value = make_gemini_response(None)

        # Act
        result = ai_provider._try_generate_gemini(
            client, "gemini-2.0-flash-exp-image-generation",
            make_jpeg_bytes(), PIXAR_PROMPT,
        )

        # Assert
        assert result is None

    def test_image_models_request_image_only_modality(self, fake_genai):
        # Arrange
        client = MagicMock()
        client.models.generate_content.return_value = make_gemini_response(
            make_jpeg_bytes()
        )

        # Act
        ai_provider._try_generate_gemini(
            client, "gemini-2.5-flash-image", make_jpeg_bytes(), PIXAR_PROMPT
        )

        # Assert — model-routing contract: dedicated image models get IMAGE-only.
        _, kwargs = fake_genai.types.GenerateContentConfig.call_args
        assert kwargs["response_modalities"] == ["IMAGE"]
        fake_genai.types.ImageConfig.assert_called_once_with(aspect_ratio="3:4")

    def test_legacy_models_request_text_and_image_modalities(self, fake_genai):
        # Arrange
        client = MagicMock()
        client.models.generate_content.return_value = make_gemini_response(
            make_jpeg_bytes()
        )

        # Act
        ai_provider._try_generate_gemini(
            client, "gemini-2.0-flash-exp-image-generation",
            make_jpeg_bytes(), PIXAR_PROMPT,
        )

        # Assert
        _, kwargs = fake_genai.types.GenerateContentConfig.call_args
        assert kwargs["response_modalities"] == ["TEXT", "IMAGE"]


class TestRetryAndFallback:
    def test_falls_back_to_next_model_after_retries_exhausted(
        self, fake_genai, source_photo
    ):
        # Arrange — first model rate-limited on every attempt, second succeeds.
        rate_limit_error = RuntimeError(
            "429 RESOURCE_EXHAUSTED: Quota exceeded for "
            "generate_content_free_tier_requests"
        )
        fake_genai.client.models.generate_content.side_effect = (
            [rate_limit_error] * MAX_RETRIES
            + [make_gemini_response(make_jpeg_bytes())]
        )

        # Act
        result = ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        assert result is not None
        assert (
            fake_genai.client.models.generate_content.call_count == MAX_RETRIES + 1
        )

    def test_returns_none_when_all_models_fail(self, fake_genai, source_photo):
        # Arrange
        fake_genai.client.models.generate_content.side_effect = RuntimeError(
            "503 UNAVAILABLE: The model is overloaded. Please try again later."
        )

        # Act
        result = ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert — exhausts every model without leaking the exception.
        assert result is None
        assert (
            fake_genai.client.models.generate_content.call_count
            == MAX_RETRIES * len(GEMINI_MODELS)
        )

    def test_retries_same_model_when_no_image_is_returned(
        self, fake_genai, source_photo
    ):
        # Arrange — empty answer first, valid image on the second attempt.
        empty_response = MagicMock()
        empty_response.candidates = []
        fake_genai.client.models.generate_content.side_effect = [
            empty_response,
            make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        result = ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        assert result is not None
        assert fake_genai.client.models.generate_content.call_count == 2
