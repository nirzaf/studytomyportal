"""AI image generation through Azure Foundry, OpenAI or Google.

OpenAI image requests reuse ``ai_config.api_key`` when ``provider=openai``;
Google requests reuse it for Google providers and retain the legacy dedicated
Gemini-key fallback for deployments using another text provider.

Supports two modes with the same call:
- text-to-image (``prompt`` only)
- image editing / iterative refinement (``prompt`` + one or more ``input_images``)
  which powers the chatbot-like "make it darker / add a diagram" refine loop.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
from typing import Optional

import httpx

from config.config import AIConfig, get_learnhouse_config
from src.services.ai.llm import AINotConfiguredError
from src.services.ai.llm.errors import is_quota_error, is_spend_cap_error
from src.services.ai.llm.provider import _GOOGLE_ALIASES

logger = logging.getLogger(__name__)

# Google "nano banana" image model. Defaults to the generally-available Gemini 2.5
# Flash Image model so generation works out of the box on any Gemini API key.
# Newer/preview models (e.g. Nano Banana 2 / "gemini-3-pro-image-preview") require
# allowlist access and can 429/500 under capacity; set LEARNHOUSE_AI_IMAGE_MODEL
# to opt into one once it is enabled for your key.
DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2.5-flare"
DEFAULT_MAI_IMAGE_MODEL = "mai-image-2-6-flash"

# Both supported providers return PNG bytes in the format requested below.
OUTPUT_MIME = "image/png"
OUTPUT_EXT = "png"

# Guard against pathologically large prompts before we spend a credit / call out.
MAX_PROMPT_CHARS = 4000

# Bounded retry for transient upstream errors (rate limit / capacity).
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.5
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _sniff_mime(data: bytes) -> str:
    """Best-effort image MIME detection so edit/refine inputs aren't mislabeled."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return OUTPUT_MIME


def _is_retryable(exc: Exception) -> bool:
    """True for transient image-provider errors worth retrying."""
    codes = (getattr(exc, "code", None), getattr(exc, "status_code", None))
    if any(isinstance(code, int) and code in _RETRYABLE_STATUS for code in codes):
        return True
    # google.genai raises ServerError (5xx) / APIError with a numeric status.
    name = type(exc).__name__
    return name in ("ServerError", "ServiceUnavailable", "ResourceExhausted")


def _resolve_image_config(cfg: AIConfig | None = None) -> tuple[str, str, str]:
    """Return ``(provider, api_key, model)`` or raise if image generation is unconfigured.

    OpenAI uses the same provider key as text generation. Other text providers
    retain the previous Google-image fallback when a dedicated Gemini key exists.
    """
    if cfg is None:
        cfg = get_learnhouse_config().ai_config
    mai_endpoint = (getattr(cfg, "mai_image_endpoint", None) or "").strip()
    mai_key = (getattr(cfg, "mai_image_api_key", None) or "").strip()
    if mai_endpoint or mai_key:
        if not mai_key or not re.fullmatch(
            r"https://[a-z0-9-]+\.services\.ai\.azure\.com/?", mai_endpoint, re.IGNORECASE
        ):
            raise AINotConfiguredError(
                "MAI image generation requires LEARNHOUSE_MAI_IMAGE_API_KEY and an HTTPS "
                "Azure Foundry resource origin in LEARNHOUSE_MAI_IMAGE_ENDPOINT."
            )
        model = (getattr(cfg, "image_model", None) or "").strip() or DEFAULT_MAI_IMAGE_MODEL
        return "mai", mai_key, model

    provider_id = (getattr(cfg, "provider", None) or "").strip().lower()

    image_provider = "google"
    api_key = None
    default_model = DEFAULT_IMAGE_MODEL
    if provider_id in _GOOGLE_ALIASES:
        api_key = getattr(cfg, "api_key", None) or getattr(cfg, "gemini_api_key", None)
    elif provider_id == "openai" and getattr(cfg, "api_key", None):
        image_provider = "openai"
        api_key = getattr(cfg, "api_key", None)
        default_model = DEFAULT_OPENAI_IMAGE_MODEL
    else:
        api_key = getattr(cfg, "gemini_api_key", None)

    if not api_key:
        raise AINotConfiguredError(
            "AI image generation requires an API key. Set LEARNHOUSE_AI_API_KEY "
            "with LEARNHOUSE_AI_PROVIDER=openai or google, or set "
            "LEARNHOUSE_GEMINI_API_KEY for the legacy Google image path. For Azure Foundry, "
            "set LEARNHOUSE_MAI_IMAGE_ENDPOINT and LEARNHOUSE_MAI_IMAGE_API_KEY."
        )

    model = (getattr(cfg, "image_model", None) or "").strip() or default_model
    return image_provider, api_key, model


def _extract_image_bytes(response) -> Optional[bytes]:
    """Pull the first inline image payload out of a GenAI response."""
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if data:
                return data
    return None


def _openai_input_files(input_images: list[bytes]) -> list[tuple[str, bytes, str]]:
    """Build supported multipart image inputs for the OpenAI edit endpoint."""
    files: list[tuple[str, bytes, str]] = []
    allowed = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }
    for index, image in enumerate(input_images, start=1):
        mime_type = _sniff_mime(image)
        extension = allowed.get(mime_type)
        if extension is None or not (
            image.startswith(b"\x89PNG\r\n\x1a\n")
            or image.startswith(b"\xff\xd8\xff")
            or (image[:4] == b"RIFF" and image[8:12] == b"WEBP")
        ):
            raise ValueError("OpenAI image edits support PNG, JPEG, and WebP input images.")
        files.append((f"reference-{index}.{extension}", image, mime_type))
    return files


def _extract_openai_image_bytes(response) -> Optional[bytes]:
    """Decode the base64 image returned by the OpenAI Images API."""
    data = getattr(response, "data", None) or []
    encoded = getattr(data[0], "b64_json", None) if data else None
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None


async def _generate_openai_image(
    prompt: str,
    api_key: str,
    model: str,
    input_images: list[bytes],
) -> bytes:
    """Generate or edit a PNG with the OpenAI Images API."""
    from openai import AsyncOpenAI

    files = _openai_input_files(input_images) if input_images else None
    client = AsyncOpenAI(api_key=api_key)
    response = None
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                if files:
                    response = await client.images.edit(
                        model=model,
                        image=files,
                        prompt=prompt,
                        output_format="png",
                    )
                else:
                    response = await client.images.generate(
                        model=model,
                        prompt=prompt,
                        output_format="png",
                    )
                break
            except Exception as e:  # noqa: BLE001 (surface a clean error to the router)
                # Do not log the SDK error message: it may include request details.
                if _is_retryable(e) and not is_spend_cap_error(e) and attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "Image generation transient error (%s), retry %d/%d",
                        type(e).__name__, attempt, _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                    continue
                (logger.warning if is_quota_error(e) else logger.error)(
                    "Image generation call failed: %s", type(e).__name__
                )
                raise RuntimeError("Image generation failed") from e
    finally:
        await client.close()

    image_bytes = _extract_openai_image_bytes(response)
    if not image_bytes:
        logger.warning("Image generation returned no image")
        raise RuntimeError("The model did not return an image. Try rephrasing your prompt.")
    return image_bytes


async def _generate_mai_image(
    prompt: str, api_key: str, model: str, endpoint: str, input_images: list[bytes]
) -> bytes:
    payload = {"model": model, "prompt": prompt}
    if input_images:
        if len(input_images) > 5 or any(
            not image.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) for image in input_images
        ):
            raise ValueError("MAI image edits support up to five PNG or JPEG reference images.")
        path = "edits"
        request = {"data": payload, "files": [("image", file) for file in _openai_input_files(input_images)]}
    else:
        path = "generations"
        request = {"json": {**payload, "width": 1024, "height": 1024}}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), trust_env=False) as client:
            response = await client.post(
                f"{endpoint}/mai/v1/images/{path}", headers={"api-key": api_key}, **request
            )
            response.raise_for_status()
            data = response.json()["data"]
            image = base64.b64decode(data[0]["b64_json"], validate=True)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Expected PNG output")
            return image
    except httpx.HTTPStatusError as exc:
        logger.warning("MAI image request returned HTTP %d", exc.response.status_code)
        raise RuntimeError("Image generation failed") from None
    except (httpx.RequestError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("MAI image request failed: %s", type(exc).__name__)
        raise RuntimeError("Image generation failed") from None


async def generate_image(
    prompt: str,
    *,
    input_images: Optional[list[bytes]] = None,
) -> bytes:
    """Generate or edit an image with the configured provider. Returns PNG bytes.

    Raises ``AINotConfiguredError`` when no supported provider key is configured,
    or a plain ``RuntimeError`` when the model returns no image (e.g. a refusal).
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("A non-empty prompt is required for image generation.")
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]

    cfg = get_learnhouse_config().ai_config
    provider, api_key, model = _resolve_image_config(cfg)
    imgs = [img for img in (input_images or []) if img]
    if provider == "mai":
        endpoint = (cfg.mai_image_endpoint or "").strip().rstrip("/")
        return await _generate_mai_image(prompt, api_key, model, endpoint, imgs)
    if provider == "openai":
        return await _generate_openai_image(prompt, api_key, model, imgs)

    from google import genai
    from google.genai import types

    # Build multimodal contents.
    #   - EDIT/REFINE (input_images present): put the reference image(s) FIRST and
    #     frame the instruction as an edit of "the provided image". This matches
    #     Google's image-editing recipe and is what makes the model modify the
    #     attachment instead of generating a fresh image from the words alone.
    #   - TEXT-TO-IMAGE: prompt only.
    # Sniff each image's real MIME type; a JPEG/WebP mislabeled as PNG is rejected.
    contents: list = []
    if imgs:
        for img in imgs:
            contents.append(types.Part.from_bytes(data=img, mime_type=_sniff_mime(img)))
        contents.append(
            f"Using the provided image as the starting point, edit it as follows: {prompt}"
        )
    else:
        contents.append(prompt)

    # Request both output modalities, Google's documented image-editing config.
    # TEXT parts are skipped by _extract_image_bytes, so output handling is
    # unchanged; this only avoids a spurious "no image" 502 when an edit turn also
    # returns a text note. (response_modalities is OUTPUT-only; the edit framing
    # above, not this, is what makes the input image be used.)
    config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])

    client = genai.Client(api_key=api_key)
    response = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            break
        except Exception as e:  # noqa: BLE001 (surface a clean error to the router)
            # Log only the exception type: the underlying SDK error can embed the
            # API key (request URL/headers), so never log the message or traceback.
            # A spent budget is a 429 too, but it fails the same way until topped up.
            if _is_retryable(e) and not is_spend_cap_error(e) and attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "Image generation transient error (%s), retry %d/%d",
                    type(e).__name__, attempt, _MAX_ATTEMPTS,
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            (logger.warning if is_quota_error(e) else logger.error)(
                "Image generation call failed: %s", type(e).__name__
            )
            raise RuntimeError("Image generation failed") from e

    image_bytes = _extract_image_bytes(response)
    if not image_bytes:
        # Most commonly a safety block: no inline image part was returned.
        # `model` is intentionally not logged: it shares a return tuple with the
        # API key, so logging it trips clear-text-secret analysis.
        logger.warning("Image generation returned no image")
        raise RuntimeError(
            "The model did not return an image. Try rephrasing your prompt."
        )
    return image_bytes
