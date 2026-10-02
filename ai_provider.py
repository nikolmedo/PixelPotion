"""
PixelPotion - AI provider abstraction layer.

Dispatches image generation to the configured provider (Gemini by default).
A provider is a function `_process_with_X(image_path, prompt, api_key)` that
returns an `AIResult` (a plain `str | None` is also accepted: a path means
success, None a transient failure).
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from constants import (
    AI_PROVIDER, GEMINI_MODELS, GEMINI_TIMEOUT_MS, MAX_RETRIES,
    MAX_RETRY_DELAY_SECONDS, PHOTOS_PROCESSED,
)

log = logging.getLogger("pixelpotion.ai")


@dataclass(frozen=True)
class AIResult:
    """Outcome of one processing request.

    `permanent` failures (bad key, safety block, rejected input) will fail the
    same way on every retry, so callers stop retrying them automatically.
    """
    path: str | None = None
    permanent: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
AUTH_ERRORS = {401, 403}          # wrong/blocked key: no other model will help
REJECTED_REQUEST_ERRORS = {400, 404}  # this model refused the input or is gone
TRANSIENT_ERRORS = {408, 429, 500, 502, 503, 504}

# google-genai exceptions carry `code`; their text starts with the code and
# the gRPC status name. Used when an exception exposes neither attribute.
_STATUS_NAMES = {
    "INVALID_ARGUMENT": 400, "UNAUTHENTICATED": 401, "PERMISSION_DENIED": 403,
    "NOT_FOUND": 404, "RESOURCE_EXHAUSTED": 429, "INTERNAL": 500,
    "UNAVAILABLE": 503, "DEADLINE_EXCEEDED": 504,
}
_LEADING_CODE = re.compile(r"^\s*(\d{3})\b")
_RETRY_DELAY_TEXT = re.compile(
    r"(?:retryDelay['\"]?\s*[:=]\s*['\"]?|retry after\s+)(\d+(?:\.\d+)?)", re.IGNORECASE
)
# Finish/block reasons that mean "the model will not draw this".
_SAFETY_REASONS = {
    "SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT",
    "BLOCKLIST", "SPII",
}


def error_status_code(exc: Exception) -> int | None:
    """HTTP-like status of an SDK error: attribute first, message text second."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    message = str(exc)
    match = _LEADING_CODE.match(message)
    if match:
        return int(match.group(1))
    for name, code in _STATUS_NAMES.items():
        if name in message:
            return code
    return None


def retry_delay_seconds(exc: Exception) -> float | None:
    """Server-suggested wait (Retry-After header or RetryInfo.retryDelay)."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            value = headers.get("Retry-After")
            if value is not None:
                return float(value)
        except (AttributeError, TypeError, ValueError):
            pass
    match = _RETRY_DELAY_TEXT.search(f"{getattr(exc, 'details', '')} {exc}")
    return float(match.group(1)) if match else None


def _reason_name(value) -> str | None:
    """Name of an SDK enum or plain string reason; None for anything else."""
    if isinstance(value, str):
        return value.rsplit(".", 1)[-1].upper()
    name = getattr(value, "name", None)
    return name.upper() if isinstance(name, str) else None


class SafetyBlocked(Exception):
    """The model refused to produce an image for safety reasons."""


def _short(message: str, limit: int = 160) -> str:
    message = " ".join(message.split())
    return message if len(message) <= limit else message[:limit - 1] + "…"


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


def _extract_image(response) -> bytes | None:
    """Return the generated image bytes, None for a text-only answer.

    Raises SafetyBlocked when the prompt or the output was blocked: no
    candidates, a candidate without content, or a safety finish/block reason.
    """
    block_reason = _reason_name(
        getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    )
    if block_reason and block_reason != "BLOCK_REASON_UNSPECIFIED":
        raise SafetyBlocked(f"prompt blocked ({block_reason})")
    if not response.candidates:
        raise SafetyBlocked("no candidates returned")
    candidate = response.candidates[0]
    finish_reason = _reason_name(getattr(candidate, "finish_reason", None))
    if finish_reason in _SAFETY_REASONS:
        raise SafetyBlocked(f"finish reason {finish_reason}")
    if candidate.content is None:
        raise SafetyBlocked(f"empty content (finish reason {finish_reason or 'unknown'})")
    for part in candidate.content.parts or []:
        if part.inline_data is not None:
            return part.inline_data.data
    return None


def _try_generate_gemini(client, model: str, image_data: bytes, prompt: str) -> bytes | None:
    from google.genai import types

    image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    gen_config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="3:4"),
    )

    response = client.models.generate_content(
        model=model, contents=[prompt, image_part], config=gen_config
    )
    return _extract_image(response)


def _process_with_genkit(image_path: str, prompt: str, api_key: str) -> AIResult:
    from PIL import Image

    client = _get_or_create_client(api_key)
    with open(image_path, "rb") as f:
        image_data = f.read()

    PHOTOS_PROCESSED.mkdir(parents=True, exist_ok=True)

    rejected = []   # permanent per-model rejections (400/404)
    last_transient = "no image returned"
    for model in GEMINI_MODELS:
        for attempt in range(1, MAX_RETRIES + 1):
            log.info("Gemini: %s attempt %d/%d", model, attempt, MAX_RETRIES)
            delay = 2 ** attempt
            try:
                img_bytes = _try_generate_gemini(client, model, image_data, prompt)
                if img_bytes:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = str(PHOTOS_PROCESSED / f"styled_{ts}.jpg")
                    Image.open(BytesIO(img_bytes)).save(out_path, "JPEG", quality=95)
                    log.info("Gemini: image saved to %s", out_path)
                    return AIResult(path=out_path)
                log.warning("Gemini: %s attempt %d: no image returned", model, attempt)
                last_transient = "no image returned"
            except SafetyBlocked as e:
                # The same photo + prompt is blocked by every model, every time.
                log.warning("Gemini: %s: blocked by safety filters: %s", model, e)
                return AIResult(permanent=True, reason="blocked by safety filters")
            except Exception as e:
                code = error_status_code(e)
                detail = _short(str(e).replace(api_key, "<redacted>") if api_key else str(e))
                if code in AUTH_ERRORS:
                    log.error("Gemini: API key rejected (%s): %s", code, detail)
                    return AIResult(permanent=True,
                                    reason=f"Gemini rejected the API key ({code})")
                if code in REJECTED_REQUEST_ERRORS:
                    log.warning("Gemini: %s rejected the request (%s): %s", model, code, detail)
                    rejected.append(f"Gemini error {code}: {detail}")
                    break   # no retries; try the next model once
                log.warning("Gemini: %s attempt %d failed: %s", model, attempt, detail)
                last_transient = f"Gemini error {code}" if code else detail
                hint = retry_delay_seconds(e)
                if hint is not None:
                    delay = hint
            if attempt < MAX_RETRIES:
                time.sleep(min(delay, MAX_RETRY_DELAY_SECONDS))
        else:
            log.warning("Gemini: model %s exhausted retries", model)

    if len(rejected) == len(GEMINI_MODELS):
        log.error("Gemini: every model rejected the request")
        return AIResult(permanent=True, reason=rejected[-1])
    log.error("Gemini: all models failed")
    return AIResult(reason=last_transient)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_PROVIDERS = {
    "gemini": _process_with_genkit,
}


def process_image_result(image_path: str, prompt: str, api_key: str) -> AIResult:
    """Transform an image with the configured AI provider; never raises for
    provider failures (an unknown AI_PROVIDER is a programming error)."""
    provider_fn = _PROVIDERS.get(AI_PROVIDER)
    if provider_fn is None:
        raise ValueError(f"Unknown AI_PROVIDER: {AI_PROVIDER!r}")
    result = provider_fn(image_path, prompt, api_key)
    if isinstance(result, AIResult):
        return result
    return AIResult(path=result) if result else AIResult(reason="no image returned")


def process_image(image_path: str, prompt: str, api_key: str) -> str | None:
    """Transform an image with the configured AI provider. Returns output path or None."""
    return process_image_result(image_path, prompt, api_key).path
