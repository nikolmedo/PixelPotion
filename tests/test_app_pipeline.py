"""Tests for app.py pipeline logic — AI gating, pending queue, full_pipeline."""

from unittest.mock import MagicMock

import pytest

import app as pixelpotion
from conftest import SAMPLE_PHOTO_NAME, make_jpeg_bytes

ANIME_PROMPT_FRAGMENT = "Japanese anime"


@pytest.fixture
def pipeline_mocks(monkeypatch, isolated_state):
    """Stub the pipeline's external boundaries: WiFi, AI, Telegram, camera."""
    processed_path = isolated_state.processed / "styled_20260610_143187.jpg"
    processed_path.write_bytes(make_jpeg_bytes(color=(30, 200, 90)))

    mocks = MagicMock()
    mocks.is_wifi_connected = MagicMock(return_value=True)
    mocks.process_with_ai = MagicMock(return_value=str(processed_path))
    mocks.send_telegram_photos = MagicMock(return_value=True)
    mocks.capture_photo = MagicMock(return_value=None)

    monkeypatch.setattr(pixelpotion, "is_wifi_connected", mocks.is_wifi_connected)
    monkeypatch.setattr(pixelpotion, "process_with_ai", mocks.process_with_ai)
    monkeypatch.setattr(
        pixelpotion, "send_telegram_photos", mocks.send_telegram_photos
    )
    monkeypatch.setattr(pixelpotion, "capture_photo", mocks.capture_photo)
    return mocks


class TestProcessWithAi:
    def test_returns_none_when_api_key_is_blank(self, monkeypatch, sample_photo):
        # Arrange
        pixelpotion.config["gemini_api_key"] = "   "
        process_image = MagicMock()
        monkeypatch.setattr(pixelpotion, "process_image", process_image)

        # Act / Assert — no key, no spend: the provider is never invoked.
        assert pixelpotion.process_with_ai(str(sample_photo)) is None
        process_image.assert_not_called()

    def test_uses_active_style_prompt_when_none_is_given(
        self, monkeypatch, sample_photo
    ):
        # Arrange
        pixelpotion.config["active_style_id"] = "anime"
        process_image = MagicMock(return_value="photos/processed/styled_x.jpg")
        monkeypatch.setattr(pixelpotion, "process_image", process_image)

        # Act
        pixelpotion.process_with_ai(str(sample_photo))

        # Assert
        path_arg, prompt_arg, key_arg = process_image.call_args.args
        assert path_arg == str(sample_photo)
        assert ANIME_PROMPT_FRAGMENT in prompt_arg
        assert key_arg == pixelpotion.config["gemini_api_key"]

    def test_returns_none_when_provider_raises(self, monkeypatch, sample_photo):
        # Arrange
        process_image = MagicMock(
            side_effect=RuntimeError("400 INVALID_ARGUMENT: API key not valid")
        )
        monkeypatch.setattr(pixelpotion, "process_image", process_image)

        # Act / Assert
        assert pixelpotion.process_with_ai(str(sample_photo)) is None


class TestPendingQueue:
    def test_ensure_in_pending_copies_photo_once(self, sample_photo, isolated_state):
        # Arrange
        pending_copy = isolated_state.pending / SAMPLE_PHOTO_NAME

        # Act — called twice, as happens when a photo is retried.
        pixelpotion.ensure_in_pending(str(sample_photo))
        pixelpotion.ensure_in_pending(str(sample_photo))

        # Assert
        assert pending_copy.exists()
        assert pending_copy.read_bytes() == sample_photo.read_bytes()
        assert len(list(isolated_state.pending.glob("*.jpg"))) == 1

    def test_remove_from_pending_tolerates_missing_file(self, isolated_state):
        # Arrange — photo was already delivered and removed by another path.
        ghost = isolated_state.originals / "photo_20260601_090000.jpg"

        # Act / Assert — must not raise.
        pixelpotion.remove_from_pending(str(ghost))


class TestFullPipeline:
    def test_happy_path_delivers_and_clears_pending(
        self, pipeline_mocks, sample_photo, isolated_state
    ):
        # Act
        pixelpotion.full_pipeline(str(sample_photo))

        # Assert
        pipeline_mocks.process_with_ai.assert_called_once()
        pipeline_mocks.send_telegram_photos.assert_called_once()
        assert list(isolated_state.pending.glob("*.jpg")) == []
        assert pixelpotion.status["last_action"].startswith("✅ Done")
        assert pixelpotion.status["processing"] is False

    def test_without_wifi_photo_is_queued_and_ai_is_skipped(
        self, pipeline_mocks, sample_photo, isolated_state
    ):
        # Arrange
        pipeline_mocks.is_wifi_connected.return_value = False

        # Act
        pixelpotion.full_pipeline(str(sample_photo))

        # Assert — durability contract: the photo survives in pending.
        assert (isolated_state.pending / SAMPLE_PHOTO_NAME).exists()
        pipeline_mocks.process_with_ai.assert_not_called()
        assert "No WiFi" in pixelpotion.status["last_action"]

    def test_reports_error_when_capture_fails(
        self, pipeline_mocks, isolated_state
    ):
        # Arrange — button pressed but the camera returned nothing.
        pipeline_mocks.capture_photo.return_value = None

        # Act
        pixelpotion.full_pipeline()

        # Assert
        assert pixelpotion.status["last_action"] == "Error: could not capture photo"
        assert list(isolated_state.pending.glob("*.jpg")) == []
        pipeline_mocks.process_with_ai.assert_not_called()

    def test_keeps_photo_pending_when_ai_fails(
        self, pipeline_mocks, sample_photo, isolated_state
    ):
        # Arrange
        pipeline_mocks.process_with_ai.return_value = None

        # Act
        pixelpotion.full_pipeline(str(sample_photo))

        # Assert
        assert (isolated_state.pending / SAMPLE_PHOTO_NAME).exists()
        pipeline_mocks.send_telegram_photos.assert_not_called()
        assert "kept in pending" in pixelpotion.status["last_action"]

    def test_keeps_photo_pending_when_telegram_fails(
        self, pipeline_mocks, sample_photo, isolated_state
    ):
        # Arrange
        pipeline_mocks.send_telegram_photos.return_value = False

        # Act
        pixelpotion.full_pipeline(str(sample_photo))

        # Assert — failed delivery must remain retryable.
        assert (isolated_state.pending / SAMPLE_PHOTO_NAME).exists()
        assert "Telegram failed" in pixelpotion.status["last_action"]

    def test_explicit_style_id_overrides_active_style(
        self, pipeline_mocks, sample_photo
    ):
        # Arrange
        pixelpotion.config["active_style_id"] = "pixar"

        # Act
        pixelpotion.full_pipeline(str(sample_photo), style_id="anime")

        # Assert
        _, prompt_arg = pipeline_mocks.process_with_ai.call_args.args
        assert ANIME_PROMPT_FRAGMENT in prompt_arg
        style_name_arg = pipeline_mocks.send_telegram_photos.call_args.args[2]
        assert style_name_arg == "Anime / Manga"

    def test_unknown_style_id_falls_back_to_active_style(
        self, pipeline_mocks, sample_photo
    ):
        # Arrange
        pixelpotion.config["active_style_id"] = "pixar"

        # Act
        pixelpotion.full_pipeline(str(sample_photo), style_id="vaporwave_deleted")

        # Assert
        style_name_arg = pipeline_mocks.send_telegram_photos.call_args.args[2]
        assert style_name_arg == "Pixar 3D"

    def test_skips_when_another_run_holds_the_lock(
        self, pipeline_mocks, sample_photo
    ):
        # Arrange — simulate a concurrent run (button + web capture).
        assert pixelpotion.processing_lock.acquire(blocking=False)
        try:
            # Act
            pixelpotion.full_pipeline(str(sample_photo))

            # Assert — second invocation must be a no-op.
            pipeline_mocks.process_with_ai.assert_not_called()
            pipeline_mocks.send_telegram_photos.assert_not_called()
        finally:
            pixelpotion.processing_lock.release()


class TestProcessPendingPhoto:
    def test_returns_false_when_pending_file_is_missing(self):
        # Act / Assert
        assert pixelpotion.process_pending_photo("photo_19990101_000000.jpg") is False

    def test_restores_original_and_runs_pipeline(
        self, monkeypatch, isolated_state
    ):
        # Arrange — pending survived a restart; original dir was cleaned.
        pending_file = isolated_state.pending / SAMPLE_PHOTO_NAME
        pending_file.write_bytes(make_jpeg_bytes())
        run_pipeline = MagicMock()
        monkeypatch.setattr(pixelpotion, "full_pipeline", run_pipeline)

        # Act
        result = pixelpotion.process_pending_photo(
            SAMPLE_PHOTO_NAME, style_id="watercolor"
        )

        # Assert
        assert result is True
        restored = isolated_state.originals / SAMPLE_PHOTO_NAME
        assert restored.exists()
        run_pipeline.assert_called_once_with(str(restored), style_id="watercolor")
