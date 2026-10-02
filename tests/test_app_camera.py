"""Tests for app.py capture_photo — Picamera2 interaction and module profiles."""

import re
import sys
from unittest.mock import MagicMock

import pytest

import app as pixelpotion


@pytest.fixture
def fake_picamera2(monkeypatch):
    """Inject a fake picamera2 module (the real one only exists on the Pi)."""
    module = MagicMock(name="picamera2")
    module.Picamera2.global_camera_info.return_value = [
        {"Model": "imx708", "Location": 2, "Rotation": 180, "Num": 0}
    ]
    monkeypatch.setitem(sys.modules, "picamera2", module)
    monkeypatch.setattr(pixelpotion.time, "sleep", lambda seconds: None)
    return module.Picamera2


class TestCapturePhoto:
    def test_captures_into_originals_with_timestamped_name(
        self, fake_picamera2, isolated_state
    ):
        # Arrange
        camera = fake_picamera2.return_value

        # Act
        filepath = pixelpotion.capture_photo()

        # Assert
        assert filepath is not None
        assert filepath.startswith(str(isolated_state.originals))
        assert re.search(r"photo_\d{8}_\d{6}\.jpg$", filepath)
        camera.configure.assert_called_once()
        camera.start.assert_called_once()
        camera.capture_file.assert_called_once_with(filepath)
        camera.stop.assert_called_once()
        camera.close.assert_called_once()

    def test_uses_configured_resolution_for_still_capture(self, fake_picamera2):
        # Arrange
        pixelpotion.config["camera_resolution"] = [4608, 2592]
        camera = fake_picamera2.return_value

        # Act
        pixelpotion.capture_photo()

        # Assert
        _, kwargs = camera.create_still_configuration.call_args
        assert kwargs["main"] == {"size": (4608, 2592)}

    def test_returns_none_when_no_camera_is_detected(self, fake_picamera2):
        # Arrange — ribbon cable unplugged / camera disabled.
        fake_picamera2.global_camera_info.return_value = []

        # Act / Assert
        assert pixelpotion.capture_photo() is None
        fake_picamera2.assert_not_called()

    def test_returns_none_when_camera_initialization_fails(self, fake_picamera2):
        # Arrange
        fake_picamera2.side_effect = RuntimeError(
            "Camera __init__ sequence did not complete."
        )

        # Act / Assert — hardware failures must not crash the pipeline.
        assert pixelpotion.capture_photo() is None


    @pytest.mark.parametrize("failing_step", ["configure", "start", "capture_file"])
    def test_camera_is_released_when_capture_fails_midway(
        self, fake_picamera2, failing_step
    ):
        # Arrange
        camera = fake_picamera2.return_value
        getattr(camera, failing_step).side_effect = RuntimeError(
            "Failed to queue buffer: Device or resource busy"
        )

        # Act
        result = pixelpotion.capture_photo()

        # Assert — the handle is released so the next press can use the camera.
        assert result is None
        camera.stop.assert_called_once()
        camera.close.assert_called_once()

    def test_camera_is_closed_even_when_stop_fails(self, fake_picamera2):
        # Arrange
        camera = fake_picamera2.return_value
        camera.capture_file.side_effect = RuntimeError("Camera frontend has timed out!")
        camera.stop.side_effect = RuntimeError("Camera was not started")

        # Act
        result = pixelpotion.capture_photo()

        # Assert
        assert result is None
        camera.close.assert_called_once()

    def test_next_capture_works_after_a_failed_one(self, fake_picamera2):
        # Arrange — first capture times out, second one succeeds.
        camera = fake_picamera2.return_value
        camera.capture_file.side_effect = [
            RuntimeError("Camera frontend has timed out!"),
            None,
        ]

        # Act
        first = pixelpotion.capture_photo()
        second = pixelpotion.capture_photo()

        # Assert
        assert first is None
        assert second is not None
        assert camera.close.call_count == 2


class TestCameraModuleProfiles:
    def test_imx708_applies_fixed_white_balance_gains(self, fake_picamera2):
        # Arrange — IMX708 needs fixed AWB gains to avoid a reddish tint.
        pixelpotion.config["camera_module"] = "imx708"
        camera = fake_picamera2.return_value

        # Act
        pixelpotion.capture_photo()

        # Assert
        camera.set_controls.assert_called_once_with(
            {"AwbEnable": False, "ColourGains": (1.0, 2.5)}
        )

    def test_imx219_keeps_auto_white_balance(self, fake_picamera2):
        # Arrange
        pixelpotion.config["camera_module"] = "imx219"
        camera = fake_picamera2.return_value

        # Act
        pixelpotion.capture_photo()

        # Assert
        camera.set_controls.assert_not_called()
