"""
PixelPotion - Constants and defaults
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Gemini AI models (tried in order, first success wins)
# ---------------------------------------------------------------------------
GEMINI_MODELS = [
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
    "gemini-2.0-flash-exp-image-generation",
]

MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Default runtime configuration (loaded from default_config.json)
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_config.json"

with open(_DEFAULT_CONFIG_PATH) as _f:
    DEFAULT_CONFIG: dict = json.load(_f)
