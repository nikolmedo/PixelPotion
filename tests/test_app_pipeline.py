"""Tests for app.py pipeline logic — AI gating, pending queue, capture, worker."""

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
    mocks.send_telegram_photo = MagicMock(return_value=True)
    mocks.capture_photo = MagicMock(return_value=None)
    mocks.processed_path = str(processed_path)

    monkeypatch.setattr(pixelpotion, "is_wifi_connected", mocks.is_wifi_connected)
    monkeypatch.setattr(pixelpotion, "process_with_ai", mocks.process_with_ai)
    monkeypatch.setattr(pixelpotion, "send_telegram_photo", mocks.send_telegram_photo)
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
        first = pixelpotion.ensure_in_pending(str(sample_photo))
        second = pixelpotion.ensure_in_pending(str(sample_photo))

        # Assert
        assert first is True and second is True
        assert pending_copy.exists()
        assert pending_copy.read_bytes() == sample_photo.read_bytes()
        assert sorted(p.name for p in isolated_state.pending.iterdir()) == [
            SAMPLE_PHOTO_NAME
        ]

    def test_ensure_in_pending_reports_failure_and_leaves_no_partial_file(
        self, sample_photo, isolated_state, monkeypatch
    ):
        # Arrange — the SD card fails while the copy is renamed into place.
        def disk_error(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(pixelpotion.os, "replace", disk_error)

        # Act
        result = pixelpotion.ensure_in_pending(str(sample_photo))

        # Assert
        assert result is False
        assert list(isolated_state.pending.iterdir()) == []

    def test_remove_from_pending_tolerates_missing_file(self, isolated_state):
        # Arrange — photo was already delivered and removed by another path.
        ghost = isolated_state.originals / "photo_20260601_090000.jpg"

        # Act / Assert — must not raise.
        pixelpotion.remove_from_pending(str(ghost))

    def test_enqueue_skips_a_photo_that_is_already_queued(self):
        # Act
        first = pixelpotion.enqueue_pending(SAMPLE_PHOTO_NAME)
        second = pixelpotion.enqueue_pending(SAMPLE_PHOTO_NAME)

        # Assert
        assert (first, second) == (True, False)
        assert pixelpotion.work_queue.qsize() == 1

    def test_photo_can_be_queued_again_after_it_was_processed(
        self, pipeline_mocks, isolated_state
    ):
        # Arrange — processing fails, so the photo stays pending.
        (isolated_state.pending / SAMPLE_PHOTO_NAME).write_bytes(make_jpeg_bytes())
        pipeline_mocks.process_with_ai.return_value = None
        pixelpotion.enqueue_pending(SAMPLE_PHOTO_NAME)
        pixelpotion.process_next(block=False)

        # Act
        requeued = pixelpotion.enqueue_pending(SAMPLE_PHOTO_NAME)

        # Assert
        assert requeued is True

    def test_process_next_returns_false_when_queue_is_empty(self):
        # Act / Assert
        assert pixelpotion.process_next(block=False) is False


class TestCaptureToPending:
    def test_capture_lands_in_pending_and_is_queued(
        self, pipeline_mocks, sample_photo, isolated_state
    ):
        # Arrange
        pipeline_mocks.capture_photo.return_value = str(sample_photo)

        # Act
        name = pixelpotion.capture_to_pending("anime")

        # Assert — durable before anything else happens; AI not run inline.
        assert name == SAMPLE_PHOTO_NAME
        pending_photo = isolated_state.pending / SAMPLE_PHOTO_NAME
        assert pending_photo.exists()
        assert pixelpotion.read_photo_state(pending_photo)["style_id"] == "anime"
        assert pixelpotion.work_queue.qsize() == 1
        pipeline_mocks.process_with_ai.assert_not_called()
        assert pixelpotion.status["capturing"] is False

    def test_reports_error_when_capture_fails(self, pipeline_mocks, isolated_state):
        # Arrange — button pressed but the camera returned nothing.
        pipeline_mocks.capture_photo.return_value = None

        # Act
        name = pixelpotion.capture_to_pending("pixar")

        # Assert
        assert name is None
        assert pixelpotion.status["last_action"] == "Error: could not capture photo"
        assert list(isolated_state.pending.iterdir()) == []
        assert pixelpotion.work_queue.qsize() == 0

    def test_aborts_when_photo_cannot_be_made_pending(
        self, pipeline_mocks, sample_photo, monkeypatch
    ):
        # Arrange
        pipeline_mocks.capture_photo.return_value = str(sample_photo)
        monkeypatch.setattr(pixelpotion, "ensure_in_pending", lambda path: False)

        # Act
        name = pixelpotion.capture_to_pending("pixar")

        # Assert — never continues towards AI without a durable copy.
        assert name is None
        assert pixelpotion.status["last_action"] == "Error: could not save photo"
        assert pixelpotion.work_queue.qsize() == 0

    def test_second_capture_during_processing_is_captured_and_queued(
        self, pipeline_mocks, isolated_state
    ):
        # Arrange — the worker is busy with a first photo.
        first = isolated_state.originals / "photo_20260610_143052.jpg"
        second = isolated_state.originals / "photo_20260610_143110.jpg"
        for photo in (first, second):
            photo.write_bytes(make_jpeg_bytes())
        pipeline_mocks.capture_photo.return_value = str(first)
        pixelpotion.capture_to_pending("pixar")
        captured_while_busy = []

        def slow_ai(image_path, prompt):
            pipeline_mocks.capture_photo.return_value = str(second)
            captured_while_busy.append(pixelpotion.capture_to_pending("pixar"))
            return None

        pipeline_mocks.process_with_ai.side_effect = slow_ai

        # Act
        pixelpotion.process_next(block=False)

        # Assert — the press during processing was not dropped.
        assert captured_while_busy == [second.name]
        assert (isolated_state.pending / second.name).exists()
        assert pixelpotion.work_queue.qsize() == 1


def styled_captions(send_mock):
    return [call.args[1] for call in send_mock.call_args_list]


class TestProcessPendingPhoto:
    @pytest.fixture
    def pending_photo(self, isolated_state):
        photo = isolated_state.pending / SAMPLE_PHOTO_NAME
        photo.write_bytes(make_jpeg_bytes())
        return photo

    def test_happy_path_delivers_both_photos_and_clears_pending(
        self, pipeline_mocks, pending_photo, isolated_state
    ):
        # Arrange
        pixelpotion.update_photo_state(pending_photo, style_id="pixar")

        # Act
        pixelpotion.enqueue_pending(SAMPLE_PHOTO_NAME)
        pixelpotion.process_next(block=False)

        # Assert — original first, then the styled image; photo + sidecar gone.
        pipeline_mocks.process_with_ai.assert_called_once()
        sent = [call.args for call in pipeline_mocks.send_telegram_photo.call_args_list]
        assert sent == [
            (str(pending_photo), "📷 Original photo"),
            (pipeline_mocks.processed_path, "🎨 Style: Pixar 3D"),
        ]
        assert list(isolated_state.pending.iterdir()) == []
        assert pixelpotion.status["last_action"].startswith("✅ Done")
        assert pixelpotion.status["processing"] is False

    def test_without_wifi_photo_stays_pending_and_ai_is_skipped(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange
        pipeline_mocks.is_wifi_connected.return_value = False

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert — durability contract: the photo survives in pending.
        assert pending_photo.exists()
        pipeline_mocks.process_with_ai.assert_not_called()
        assert "No WiFi" in pixelpotion.status["last_action"]

    def test_keeps_photo_pending_when_ai_fails(self, pipeline_mocks, pending_photo):
        # Arrange
        pipeline_mocks.process_with_ai.return_value = None

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        assert pending_photo.exists()
        pipeline_mocks.send_telegram_photo.assert_not_called()
        assert "kept in pending" in pixelpotion.status["last_action"]
        assert pixelpotion.read_photo_state(pending_photo)["attempts"] == 1

    def test_keeps_photo_pending_when_telegram_fails(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange
        pipeline_mocks.send_telegram_photo.return_value = False

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert — failed delivery must remain retryable.
        assert pending_photo.exists()
        assert "Telegram failed" in pixelpotion.status["last_action"]

    def test_keeps_photo_pending_when_a_step_raises(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange
        pipeline_mocks.send_telegram_photo.side_effect = RuntimeError("disk I/O error")

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        assert pending_photo.exists()
        assert "kept in pending" in pixelpotion.status["last_action"]
        assert pixelpotion.status["processing"] is False

    def test_retry_after_styled_send_failed_sends_only_the_styled_photo(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange — first run: original delivered, styled rejected (429).
        pipeline_mocks.send_telegram_photo.side_effect = [True, False]
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)
        pipeline_mocks.send_telegram_photo.reset_mock(side_effect=True)
        pipeline_mocks.send_telegram_photo.return_value = True

        # Act — the retry.
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert — no duplicate original in the chat, no second AI call.
        assert styled_captions(pipeline_mocks.send_telegram_photo) == ["🎨 Style: Pixar 3D"]
        pipeline_mocks.process_with_ai.assert_called_once()
        assert not pending_photo.exists()

    def test_retry_after_telegram_failure_reuses_the_styled_image(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange — AI succeeded, then Telegram was unreachable.
        pipeline_mocks.send_telegram_photo.return_value = False
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)
        pipeline_mocks.send_telegram_photo.return_value = True

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert — the AI step is not paid for twice.
        pipeline_mocks.process_with_ai.assert_called_once()
        assert pipeline_mocks.send_telegram_photo.call_args.args[0] == (
            pipeline_mocks.processed_path
        )
        assert not pending_photo.exists()

    def test_runs_ai_again_when_the_styled_image_was_lost(
        self, pipeline_mocks, pending_photo, isolated_state
    ):
        # Arrange — the sidecar points at a file that no longer exists.
        pixelpotion.update_photo_state(
            pending_photo,
            processed_path=str(isolated_state.processed / "styled_20260101_000000.jpg"),
        )

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        pipeline_mocks.process_with_ai.assert_called_once()
        assert not pending_photo.exists()

    def test_uses_the_style_recorded_at_capture_time(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange — captured with anime; the active style changed since.
        pixelpotion.update_photo_state(pending_photo, style_id="anime")
        pixelpotion.config["active_style_id"] = "pixar"

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        _, prompt_arg = pipeline_mocks.process_with_ai.call_args.args
        assert ANIME_PROMPT_FRAGMENT in prompt_arg
        assert styled_captions(pipeline_mocks.send_telegram_photo)[-1] == (
            "🎨 Style: Anime / Manga"
        )

    def test_deleted_capture_style_falls_back_to_active_style(
        self, pipeline_mocks, pending_photo
    ):
        # Arrange
        pixelpotion.update_photo_state(pending_photo, style_id="vaporwave_deleted")
        pixelpotion.config["active_style_id"] = "pixar"

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        assert styled_captions(pipeline_mocks.send_telegram_photo)[-1] == (
            "🎨 Style: Pixar 3D"
        )

    def test_legacy_photo_without_state_file_is_processed_with_active_style(
        self, pipeline_mocks, pending_photo, isolated_state
    ):
        # Arrange — queued by an older version that wrote no sidecar.
        pixelpotion.config["active_style_id"] = "watercolor"

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        assert styled_captions(pipeline_mocks.send_telegram_photo)[-1] == (
            "🎨 Style: Watercolor"
        )
        assert list(isolated_state.pending.iterdir()) == []

    def test_unreadable_state_file_is_treated_as_a_fresh_photo(
        self, pipeline_mocks, pending_photo, isolated_state
    ):
        # Arrange
        (isolated_state.pending / f"{SAMPLE_PHOTO_NAME}.json").write_text(
            '{"style_id": "ani', encoding="utf-8"
        )

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        assert pipeline_mocks.send_telegram_photo.call_count == 2
        assert list(isolated_state.pending.iterdir()) == []

    def test_photo_deleted_while_processing_leaves_no_orphan_state(
        self, pipeline_mocks, pending_photo, isolated_state
    ):
        # Arrange — the user deletes the photo from the gallery mid-AI.
        def delete_during_ai(image_path, prompt):
            pixelpotion._delete_pending_file(pending_photo)
            return pipeline_mocks.processed_path

        pipeline_mocks.process_with_ai.side_effect = delete_during_ai

        # Act
        pixelpotion.process_pending_photo(SAMPLE_PHOTO_NAME)

        # Assert
        assert list(isolated_state.pending.iterdir()) == []

    def test_returns_false_when_pending_file_is_missing(self, pipeline_mocks):
        # Act / Assert — e.g. deleted from the gallery while it was queued.
        assert pixelpotion.process_pending_photo("photo_19990101_000000.jpg") is False
        pipeline_mocks.process_with_ai.assert_not_called()

    @pytest.mark.parametrize("hostile_name", ["../../client_secrets.jpg", "ABSOLUTE"])
    def test_refuses_files_outside_the_pending_queue(
        self, pipeline_mocks, isolated_state, tmp_path, hostile_name
    ):
        # Arrange — a photo-looking file that lives outside photos/pending.
        outside = tmp_path / "client_secrets.jpg"
        outside.write_bytes(make_jpeg_bytes())
        if hostile_name == "ABSOLUTE":
            hostile_name = str(outside)

        # Act
        result = pixelpotion.process_pending_photo(hostile_name)

        # Assert — the pipeline never touched it.
        assert result is False
        pipeline_mocks.process_with_ai.assert_not_called()
        assert outside.exists()


class TestRequestProcessing:
    def test_new_style_discards_the_image_made_with_the_old_one(self, isolated_state):
        # Arrange — styled with pixar, original already delivered.
        photo = isolated_state.pending / SAMPLE_PHOTO_NAME
        photo.write_bytes(make_jpeg_bytes())
        pixelpotion.update_photo_state(
            photo, style_id="pixar", telegram_original_sent=True,
            processed_path=str(isolated_state.processed / "styled_20260610_143187.jpg"),
        )

        # Act
        pixelpotion.request_processing(photo, "anime")

        # Assert
        state = pixelpotion.read_photo_state(photo)
        assert state["style_id"] == "anime"
        assert state["processed_path"] is None
        assert state["telegram_original_sent"] is True

    def test_same_style_keeps_the_existing_styled_image(self, isolated_state):
        # Arrange
        photo = isolated_state.pending / SAMPLE_PHOTO_NAME
        photo.write_bytes(make_jpeg_bytes())
        processed = str(isolated_state.processed / "styled_20260610_143187.jpg")
        pixelpotion.update_photo_state(photo, style_id="pixar", processed_path=processed)

        # Act
        pixelpotion.request_processing(photo, "pixar")

        # Assert
        assert pixelpotion.read_photo_state(photo)["processed_path"] == processed


class TestResolvePending:
    @pytest.mark.parametrize("name", [
        f"{SAMPLE_PHOTO_NAME}.json",
        f".{SAMPLE_PHOTO_NAME}.tmp",
        "../config.json",
    ])
    def test_only_photos_are_addressable(self, isolated_state, name):
        # Act / Assert — sidecars and temp files are not portal-addressable.
        assert pixelpotion._resolve_pending(name) is None

    def test_accepts_a_plain_photo_name(self, isolated_state):
        # Act / Assert
        assert pixelpotion._resolve_pending(SAMPLE_PHOTO_NAME) == (
            isolated_state.pending / SAMPLE_PHOTO_NAME
        ).resolve()
