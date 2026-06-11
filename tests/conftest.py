"""
Shared fixtures and import bootstrap for the PixelPotion test suite.

app.py has import-time side effects that crash off-device: its logging setup
builds a FileHandler pointing at /home/pi/pixelpotion/pixelpotion.log, a path
that only exists on the Raspberry Pi. The handler is stubbed out BEFORE the
module is imported so the suite runs on any dev machine or CI runner.

All filesystem paths (config.json, photo directories) and mutable global
state (runtime config, pipeline status, cached Gemini client) are redirected
to per-test temporary locations by the autouse `isolated_state` fixture.

Deliberately NOT unit-tested — infinite loops and pure hardware/OS glue whose
tests would couple to implementation details without protecting refactors:
auto_retry_loop, gpio_button_listener, start_ap_mode, main.
"""

import copy
import logging
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# /home/pi/... does not exist off-device; FileHandler would raise on import.
_real_file_handler = logging.FileHandler
logging.FileHandler = lambda *args, **kwargs: logging.NullHandler()
try:
    import app as pixelpotion  # noqa: E402
finally:
    logging.FileHandler = _real_file_handler

import ai_provider  # noqa: E402

# Realistic runtime configuration mirroring the shape of default_config.json.
BASELINE_CONFIG = {
    "gemini_api_key": "AIzaSyDk3v9XbT7eW2qLpZ8mNc4RfYhUj6sQwE0",
    "telegram_bot_token": "7123456789:AAHk3mP9qRsT2uVwXyZ1bCdEfGhIjKlMnOp",
    "telegram_chat_id": "492817365",
    "wifi_ssid": "CasaOlmedo_5G",
    "wifi_password": "patagonia2024!",
    "wifi_connected": True,
    "gpio_pin": 17,
    "camera_module": "imx708",
    "camera_resolution": [2592, 1944],
    "ap_ssid": "PixelPotion-Setup",
    "ap_password": "pixelpotion123",
    "active_style_id": "pixar",
    "styles": [
        {
            "id": "pixar",
            "name": "Pixar 3D",
            "prompt": (
                "TASK: Transform this input photograph into a Pixar / Disney 3D "
                "animation style illustration. Keep every person recognizable."
            ),
        },
        {
            "id": "anime",
            "name": "Anime / Manga",
            "prompt": (
                "TASK: Transform this photograph into a Japanese anime "
                "illustration with clean linework and cel-shading."
            ),
        },
        {
            "id": "watercolor",
            "name": "Watercolor",
            "prompt": (
                "TASK: Transform this photograph into a watercolor painting "
                "with visible brush strokes and paint bleeding effects."
            ),
        },
    ],
}

SAMPLE_PHOTO_NAME = "photo_20260610_143052.jpg"


def make_jpeg_bytes(color=(120, 80, 200), size=(32, 32)) -> bytes:
    """Return a small but structurally valid JPEG payload."""
    buffer = BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="JPEG")
    return buffer.getvalue()


def make_gemini_response(image_bytes: bytes | None):
    """Build a response object shaped like google-genai's GenerateContentResponse.

    With image_bytes=None the single part carries no inline_data (text-only
    answer); with bytes it carries the generated image.
    """
    part = MagicMock()
    if image_bytes is None:
        part.inline_data = None
    else:
        part.inline_data.data = image_bytes
    candidate = MagicMock()
    candidate.content.parts = [part]
    response = MagicMock()
    response.candidates = [candidate]
    return response


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect all paths to tmp and reset every piece of global mutable state."""
    config_path = tmp_path / "config.json"
    originals = tmp_path / "photos" / "original"
    pending = tmp_path / "photos" / "pending"
    processed = tmp_path / "photos" / "processed"
    for directory in (originals, pending, processed):
        directory.mkdir(parents=True)

    monkeypatch.setattr(pixelpotion, "CONFIG_PATH", config_path)
    monkeypatch.setattr(pixelpotion, "PHOTOS_ORIGINAL", originals)
    monkeypatch.setattr(pixelpotion, "PHOTOS_PENDING", pending)
    monkeypatch.setattr(pixelpotion, "PHOTOS_PROCESSED", processed)
    monkeypatch.setattr(ai_provider, "PHOTOS_PROCESSED", processed)

    # Routes and helpers hold references to these exact dict objects, so they
    # must be mutated in place rather than replaced.
    pixelpotion.config.clear()
    pixelpotion.config.update(copy.deepcopy(BASELINE_CONFIG))
    pixelpotion.status.clear()
    pixelpotion.status.update({"last_action": "Waiting...", "processing": False})

    ai_provider._cached_client = None
    ai_provider._cached_api_key = ""

    yield SimpleNamespace(
        config_path=config_path,
        originals=originals,
        pending=pending,
        processed=processed,
    )


@pytest.fixture
def client():
    """Flask test client for route-level tests."""
    pixelpotion.app.config["TESTING"] = True
    return pixelpotion.app.test_client()


@pytest.fixture
def sample_photo(isolated_state) -> Path:
    """A real JPEG dropped into the originals directory, as the camera would."""
    photo_path = isolated_state.originals / SAMPLE_PHOTO_NAME
    photo_path.write_bytes(make_jpeg_bytes())
    return photo_path


@pytest.fixture
def fake_genai(monkeypatch):
    """Inject a fake google-genai SDK module tree into sys.modules.

    The real SDK is not installed on dev machines; ai_provider imports it
    lazily, so providing module mocks is enough to exercise the full logic.
    """
    google_module = MagicMock(name="google")
    genai_module = MagicMock(name="google.genai")
    types_module = MagicMock(name="google.genai.types")
    google_module.genai = genai_module
    genai_module.types = types_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)
    return SimpleNamespace(
        genai=genai_module,
        types=types_module,
        client=genai_module.Client.return_value,
    )
