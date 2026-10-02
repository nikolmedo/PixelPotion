"""Tests for ai_provider.py — Gemini adapter, client caching, retry/fallback."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

import ai_provider
from constants import GEMINI_MODELS, MAX_RETRIES, MAX_RETRY_DELAY_SECONDS
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
            client, "gemini-3.1-flash-image", make_jpeg_bytes(), PIXAR_PROMPT
        )

        # Assert
        assert result == generated

    def test_response_without_candidates_is_a_safety_block(self, fake_genai):
        # Arrange
        response = MagicMock()
        response.candidates = []
        response.prompt_feedback = None
        client = MagicMock()
        client.models.generate_content.return_value = response

        # Act / Assert
        with pytest.raises(ai_provider.SafetyBlocked):
            ai_provider._try_generate_gemini(
                client, "gemini-3.1-flash-image", make_jpeg_bytes(), PIXAR_PROMPT
            )

    def test_candidate_without_content_is_a_safety_block(self, fake_genai):
        # Arrange — used to crash with AttributeError on `.parts`.
        response = make_gemini_response(make_jpeg_bytes())
        response.candidates[0].content = None
        response.candidates[0].finish_reason = "IMAGE_SAFETY"
        client = MagicMock()
        client.models.generate_content.return_value = response

        # Act / Assert
        with pytest.raises(ai_provider.SafetyBlocked):
            ai_provider._try_generate_gemini(
                client, "gemini-3.1-flash-image", make_jpeg_bytes(), PIXAR_PROMPT
            )

    def test_returns_none_when_no_part_carries_inline_data(self, fake_genai):
        # Arrange — model answered with text only, no generated image.
        client = MagicMock()
        client.models.generate_content.return_value = make_gemini_response(None)

        # Act
        result = ai_provider._try_generate_gemini(
            client, "gemini-3.1-flash-lite-image",
            make_jpeg_bytes(), PIXAR_PROMPT,
        )

        # Assert
        assert result is None

    @pytest.mark.parametrize("model", GEMINI_MODELS)
    def test_every_configured_model_requests_image_only_modality(
        self, fake_genai, model
    ):
        # Arrange
        client = MagicMock()
        client.models.generate_content.return_value = make_gemini_response(
            make_jpeg_bytes()
        )

        # Act
        ai_provider._try_generate_gemini(
            client, model, make_jpeg_bytes(), PIXAR_PROMPT
        )

        # Assert
        _, kwargs = fake_genai.types.GenerateContentConfig.call_args
        assert kwargs["response_modalities"] == ["IMAGE"]
        fake_genai.types.ImageConfig.assert_called_once_with(aspect_ratio="3:4")


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
        # Arrange — text-only answer first, valid image on the second attempt.
        fake_genai.client.models.generate_content.side_effect = [
            make_gemini_response(None),
            make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        result = ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        assert result is not None
        assert fake_genai.client.models.generate_content.call_count == 2


class FakeAPIError(Exception):
    """Shaped like google.genai.errors.APIError: numeric `code` + message."""

    def __init__(self, code, message, details=None, response=None):
        super().__init__(f"{code} {message}")
        self.code = code
        self.details = details
        self.response = response


def generate_calls(fake_genai):
    return fake_genai.client.models.generate_content.call_count


class TestErrorClassification:
    @pytest.mark.parametrize("code", [401, 403])
    def test_rejected_api_key_stops_immediately(self, fake_genai, source_photo, code):
        # Arrange
        fake_genai.client.models.generate_content.side_effect = FakeAPIError(
            code, "PERMISSION_DENIED. API key not valid. Please pass a valid API key."
        )

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert — one call, no retries, no other model.
        assert result.ok is False
        assert result.permanent is True
        assert str(code) in result.reason
        assert generate_calls(fake_genai) == 1

    @pytest.mark.parametrize("code", [400, 404])
    def test_rejected_request_tries_each_model_once_without_retries(
        self, fake_genai, source_photo, code
    ):
        # Arrange
        fake_genai.client.models.generate_content.side_effect = FakeAPIError(
            code, "INVALID_ARGUMENT. Unable to process input image."
        )

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert
        assert result.permanent is True
        assert generate_calls(fake_genai) == len(GEMINI_MODELS)

    def test_rejected_request_falls_back_to_a_model_that_works(
        self, fake_genai, source_photo
    ):
        # Arrange — first model retired (404), second one answers.
        fake_genai.client.models.generate_content.side_effect = [
            FakeAPIError(404, "NOT_FOUND. models/gemini-3.1-flash-image is not found."),
            make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert
        assert result.ok is True
        assert generate_calls(fake_genai) == 2

    @pytest.mark.parametrize("code", [429, 500, 503])
    def test_transient_errors_are_retried_and_not_permanent(
        self, fake_genai, source_photo, code
    ):
        # Arrange
        fake_genai.client.models.generate_content.side_effect = FakeAPIError(
            code, "UNAVAILABLE. The model is overloaded."
        )

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert
        assert result.permanent is False
        assert generate_calls(fake_genai) == MAX_RETRIES * len(GEMINI_MODELS)

    def test_status_is_read_from_message_text_when_no_code_attribute(self):
        # Arrange
        error = RuntimeError("403 PERMISSION_DENIED. Your API key was reported as leaked.")

        # Act / Assert
        assert ai_provider.error_status_code(error) == 403

    def test_numbers_inside_network_errors_are_not_mistaken_for_a_status(self):
        # Arrange
        error = ConnectionError(
            "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
            "Max retries exceeded"
        )

        # Act / Assert
        assert ai_provider.error_status_code(error) is None

    def test_safety_block_fails_permanently_without_retries(
        self, fake_genai, source_photo
    ):
        # Arrange
        response = make_gemini_response(None)
        response.candidates = []
        response.prompt_feedback = MagicMock(block_reason="SAFETY")
        fake_genai.client.models.generate_content.return_value = response

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert
        assert result.permanent is True
        assert result.reason == "blocked by safety filters"
        assert generate_calls(fake_genai) == 1

    def test_safety_finish_reason_fails_permanently(self, fake_genai, source_photo):
        # Arrange
        response = make_gemini_response(None)
        response.candidates[0].finish_reason = "PROHIBITED_CONTENT"
        fake_genai.client.models.generate_content.return_value = response

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert
        assert result.permanent is True
        assert generate_calls(fake_genai) == 1

    def test_process_image_keeps_returning_none_on_failure(
        self, fake_genai, source_photo
    ):
        # Arrange
        fake_genai.client.models.generate_content.side_effect = FakeAPIError(
            401, "UNAUTHENTICATED. API key not valid."
        )

        # Act / Assert — compatibility wrapper for str | None callers.
        assert ai_provider.process_image(source_photo, PIXAR_PROMPT, GEMINI_API_KEY) is None


class TestRetryDelay:
    @pytest.fixture
    def sleep(self, monkeypatch):
        sleep = MagicMock()
        monkeypatch.setattr(ai_provider.time, "sleep", sleep)
        return sleep

    def test_honors_retry_after_header(self, fake_genai, source_photo, sleep):
        # Arrange
        rate_limited = FakeAPIError(
            429, "RESOURCE_EXHAUSTED. Quota exceeded.",
            response=MagicMock(headers={"Retry-After": "17"}),
        )
        fake_genai.client.models.generate_content.side_effect = [
            rate_limited, make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        result = ai_provider.process_image_result(
            source_photo, PIXAR_PROMPT, GEMINI_API_KEY
        )

        # Assert
        assert result.ok is True
        sleep.assert_called_once_with(17.0)

    def test_honors_retry_delay_from_error_details(
        self, fake_genai, source_photo, sleep
    ):
        # Arrange — Gemini puts RetryInfo in the error details.
        rate_limited = FakeAPIError(
            429, "RESOURCE_EXHAUSTED. Quota exceeded.",
            details={"error": {"details": [{
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "35s",
            }]}},
        )
        fake_genai.client.models.generate_content.side_effect = [
            rate_limited, make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        ai_provider.process_image_result(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        sleep.assert_called_once_with(35.0)

    def test_caps_a_very_long_retry_delay(self, fake_genai, source_photo, sleep):
        # Arrange
        rate_limited = FakeAPIError(
            429, "RESOURCE_EXHAUSTED. Quota exceeded.",
            response=MagicMock(headers={"Retry-After": "3600"}),
        )
        fake_genai.client.models.generate_content.side_effect = [
            rate_limited, make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        ai_provider.process_image_result(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        sleep.assert_called_once_with(MAX_RETRY_DELAY_SECONDS)

    def test_uses_exponential_backoff_without_a_hint(
        self, fake_genai, source_photo, sleep
    ):
        # Arrange
        fake_genai.client.models.generate_content.side_effect = [
            FakeAPIError(503, "UNAVAILABLE. The model is overloaded."),
            make_gemini_response(make_jpeg_bytes()),
        ]

        # Act
        ai_provider.process_image_result(source_photo, PIXAR_PROMPT, GEMINI_API_KEY)

        # Assert
        sleep.assert_called_once_with(2)
