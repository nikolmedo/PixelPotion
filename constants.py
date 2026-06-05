"""
PixelPotion - Constants and defaults
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# AI provider selection
# ---------------------------------------------------------------------------
AI_PROVIDER = "gemini"  # Options: "gemini" | "openai" | "anthropic"

# ---------------------------------------------------------------------------
# Gemini models (tried in order, first success wins)
# ---------------------------------------------------------------------------
GEMINI_MODELS = [
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
    "gemini-2.0-flash-exp-image-generation",
]

MAX_RETRIES = 3

# Timeout in milliseconds for HttpOptions. google-genai SDK uses ms.
GEMINI_TIMEOUT_MS = 120_000

# How often the background retry loop scans PHOTOS_PENDING (seconds).
RETRY_INTERVAL_SECONDS = 300

# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PHOTOS_PROCESSED = BASE_DIR / "photos" / "processed"

# ---------------------------------------------------------------------------
# Default runtime configuration (loaded from default_config.json)
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_config.json"

with open(_DEFAULT_CONFIG_PATH) as _f:
    DEFAULT_CONFIG: dict = json.load(_f)
