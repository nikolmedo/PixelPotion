"""
PixelPotion - AI provider abstraction layer.

Dispatches image generation to the configured provider (Gemini by default).
Future providers (OpenAI, Anthropic) only need to implement the same
_process_with_X(image_path, prompt, api_key) -> str | None signature.
"""

import logging
import time
from datetime import datetime
from io import BytesIO

from constants import AI_PROVIDER, GEMINI_MODELS, GEMINI_TIMEOUT_MS, MAX_RETRIES, PHOTOS_PROCESSED

log = logging.getLogger("pixelpotion.ai")

# ---------------------------------------------------------------------------
# Gemini provider (google-genai SDK directly)
#
# GenKit Python's google-genai plugin (alpha) does not reliably forward the
# api_key to its internal genai.Client, causing auth failures. We use the
# google-genai SDK directly here — the ai_provider abstraction layer still
# enables multi-provider support when other providers are added.
# ---------------------------------------------------------------------------
_cached_client = None
_cached_api_key: str = ""


def _get_or_create_client(api_key: str):
    global _cached_client, _cached_api_key
    if _cached_client is None or api_key != _cached_api_key:
        from google import genai
        from google.genai import types as genai_types
        _cached_client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
        )
        _cached_api_key = api_key
        log.debug("Gemini: created new client")
    return _cached_client


def _try_generate_gemini(client, model: str, image_data: bytes, prompt: str) -> bytes | None:
    from google.genai import types

    image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

    if "2.5-flash-image" in model or "3.1-flash" in model:
        gen_config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="3:4"),
        )
    else:
        gen_config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        )

    response = client.models.generate_content(
        model=model, contents=[prompt, image_part], config=gen_config
    )

    if not response.candidates:
        return None
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return part.inline_data.data
    return None


def _process_with_genkit(image_path: str, prompt: str, api_key: str) -> str | None:
    from PIL import Image

    client = _get_or_create_client(api_key)
    image_data = open(image_path, "rb").read()

    PHOTOS_PROCESSED.mkdir(parents=True, exist_ok=True)

    for model in GEMINI_MODELS:
        for attempt in range(1, MAX_RETRIES + 1):
            log.info("Gemini: %s attempt %d/%d", model, attempt, MAX_RETRIES)
            try:
                img_bytes = _try_generate_gemini(client, model, image_data, prompt)
                if img_bytes:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = str(PHOTOS_PROCESSED / f"styled_{ts}.jpg")
                    Image.open(BytesIO(img_bytes)).save(out_path, "JPEG", quality=95)
                    log.info("Gemini: image saved to %s", out_path)
                    return out_path
                log.warning("Gemini: %s attempt %d: no image returned", model, attempt)
            except Exception as e:
                log.warning("Gemini: %s attempt %d failed: %s", model, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
        log.warning("Gemini: model %s exhausted retries", model)

    log.error("Gemini: all models failed")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_PROVIDERS = {
    "gemini": _process_with_genkit,
}


def process_image(image_path: str, prompt: str, api_key: str) -> str | None:
    """Transform an image with the configured AI provider. Returns output path or None."""
    provider_fn = _PROVIDERS.get(AI_PROVIDER)
    if provider_fn is None:
        raise ValueError(f"Unknown AI_PROVIDER: {AI_PROVIDER!r}")
    return provider_fn(image_path, prompt, api_key)
