"""Tests for src/services/ai/image/generator.py (Google and OpenAI image gen).

Provider SDKs and the global config are fully mocked.
"""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.services.ai.image.generator as gen
from src.services.ai.llm import AINotConfiguredError

pytestmark = pytest.mark.asyncio


def _cfg(provider="google", api_key="k", gemini_api_key=None, image_model=None):
    return SimpleNamespace(
        ai_config=SimpleNamespace(
            provider=provider,
            api_key=api_key,
            gemini_api_key=gemini_api_key,
            image_model=image_model,
        )
    )


def _image_response(data=b"PNGBYTES"):
    part = SimpleNamespace(inline_data=SimpleNamespace(data=data))
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _openai_image_response(data=b"PNGDATA"):
    encoded = base64.b64encode(data).decode("ascii")
    return SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])


# --- _resolve_image_config ---

def test_resolve_config_google_uses_api_key():
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg(provider="google", api_key="gk")):
        provider, key, model = gen._resolve_image_config()
    assert provider == "google"
    assert key == "gk" and model == gen.DEFAULT_IMAGE_MODEL


def test_resolve_config_openai_reuses_provider_key():
    cfg = _cfg(provider="openai", api_key="openai-key", gemini_api_key="gemk")
    with patch.object(gen, "get_learnhouse_config", return_value=cfg):
        provider, key, model = gen._resolve_image_config()
    assert provider == "openai"
    assert key == "openai-key"
    assert model == gen.DEFAULT_OPENAI_IMAGE_MODEL


def test_resolve_config_other_provider_retains_gemini_fallback():
    cfg = _cfg(provider="anthropic", api_key="anthropic-key", gemini_api_key="gemk")
    with patch.object(gen, "get_learnhouse_config", return_value=cfg):
        provider, key, model = gen._resolve_image_config()
    assert provider == "google"
    assert key == "gemk"
    assert model == gen.DEFAULT_IMAGE_MODEL


def test_resolve_config_custom_model():
    cfg = _cfg(image_model="my-image-model")
    with patch.object(gen, "get_learnhouse_config", return_value=cfg):
        _, _, model = gen._resolve_image_config()
    assert model == "my-image-model"


def test_resolve_config_missing_key_raises():
    cfg = _cfg(provider="anthropic", api_key=None, gemini_api_key=None)
    with patch.object(gen, "get_learnhouse_config", return_value=cfg):
        with pytest.raises(AINotConfiguredError, match="LEARNHOUSE_AI_PROVIDER=openai or google"):
            gen._resolve_image_config()


# --- _extract_image_bytes ---

def test_extract_image_bytes_found():
    assert gen._extract_image_bytes(_image_response(b"abc")) == b"abc"


def test_extract_image_bytes_none_when_no_inline():
    resp = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(inline_data=None)]))])
    assert gen._extract_image_bytes(resp) is None


def test_extract_image_bytes_empty_candidates():
    assert gen._extract_image_bytes(SimpleNamespace(candidates=[])) is None


# --- generate_image ---

async def test_generate_image_empty_prompt_raises():
    with pytest.raises(ValueError):
        await gen.generate_image("   ")


async def test_generate_image_success():
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=_image_response(b"IMG"))
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ):
        out = await gen.generate_image("a cat")
    assert out == b"IMG"


async def test_generate_image_with_input_images():
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=_image_response(b"EDITED"))
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ), patch("google.genai.types.Part.from_bytes", return_value="PART"):
        out = await gen.generate_image("make it darker", input_images=[b"orig"])
    assert out == b"EDITED"
    # image part FIRST, then an edit-framed instruction referencing the image
    sent = client.aio.models.generate_content.await_args.kwargs["contents"]
    assert len(sent) == 2
    assert sent[0] == "PART"
    assert isinstance(sent[1], str) and "provided image" in sent[1] and "make it darker" in sent[1]


async def test_generate_image_no_image_raises_runtime():
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(candidates=[]))
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ):
        with pytest.raises(RuntimeError):
            await gen.generate_image("x")


async def test_generate_image_sdk_error_raises_runtime():
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=Exception("boom"))
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ):
        with pytest.raises(RuntimeError):
            await gen.generate_image("x")


async def test_generate_image_requests_image_modality():
    """Regression: the call MUST pass response_modalities so the model returns an image."""
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=_image_response(b"IMG"))
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ):
        await gen.generate_image("a cat")
    cfg = client.aio.models.generate_content.await_args.kwargs["config"]
    assert list(cfg.response_modalities) == ["TEXT", "IMAGE"]


async def test_default_model_is_ga_flash_image():
    assert gen.DEFAULT_IMAGE_MODEL == "gemini-2.5-flash-image"
    assert gen.DEFAULT_OPENAI_IMAGE_MODEL == "gpt-image-2.5-flare"


def test_sniff_mime():
    assert gen._sniff_mime(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert gen._sniff_mime(b"\xff\xd8\xff\xe0xxxx") == "image/jpeg"
    assert gen._sniff_mime(b"RIFF\x00\x00\x00\x00WEBPxxxx") == "image/webp"
    assert gen._sniff_mime(b"unknownbytes") == "image/png"  # safe fallback


async def test_generate_image_retries_transient_then_succeeds():
    class ServerError(Exception):
        code = 503

    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        side_effect=[ServerError("busy"), _image_response(b"IMG")]
    )
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ), patch("asyncio.sleep", new=AsyncMock()):
        out = await gen.generate_image("a cat")
    assert out == b"IMG"
    assert client.aio.models.generate_content.await_count == 2


async def test_generate_image_truncates_long_prompt():
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=_image_response(b"IMG"))
    long_prompt = "a" * (gen.MAX_PROMPT_CHARS + 500)
    with patch.object(gen, "get_learnhouse_config", return_value=_cfg()), patch(
        "google.genai.Client", return_value=client
    ):
        out = await gen.generate_image(long_prompt)
    assert out == b"IMG"
    sent_prompt = client.aio.models.generate_content.await_args.kwargs["contents"][0]
    assert len(sent_prompt) == gen.MAX_PROMPT_CHARS


async def test_generate_image_openai_reuses_configured_key_and_decodes_png():
    client = MagicMock()
    client.images.generate = AsyncMock(return_value=_openai_image_response(b"OPENAI-PNG"))
    client.close = AsyncMock()
    cfg = _cfg(provider="openai", api_key="same-openai-key")
    with patch.object(gen, "get_learnhouse_config", return_value=cfg), patch(
        "openai.AsyncOpenAI", return_value=client
    ) as openai_client:
        out = await gen.generate_image("a chemistry course thumbnail")

    assert out == b"OPENAI-PNG"
    openai_client.assert_called_once_with(api_key="same-openai-key")
    client.images.generate.assert_awaited_once_with(
        model=gen.DEFAULT_OPENAI_IMAGE_MODEL,
        prompt="a chemistry course thumbnail",
        output_format="png",
    )
    client.close.assert_awaited_once()


async def test_generate_image_openai_edits_reference_image():
    png = b"\x89PNG\r\n\x1a\noriginal-image"
    client = MagicMock()
    client.images.edit = AsyncMock(return_value=_openai_image_response(b"EDITED-PNG"))
    client.close = AsyncMock()
    cfg = _cfg(provider="openai", api_key="same-openai-key")
    with patch.object(gen, "get_learnhouse_config", return_value=cfg), patch(
        "openai.AsyncOpenAI", return_value=client
    ):
        out = await gen.generate_image("make it brighter", input_images=[png])

    assert out == b"EDITED-PNG"
    client.images.edit.assert_awaited_once()
    kwargs = client.images.edit.await_args.kwargs
    assert kwargs["prompt"] == "make it brighter"
    assert kwargs["output_format"] == "png"
    assert kwargs["image"] == [("reference-1.png", png, "image/png")]
    client.close.assert_awaited_once()


async def test_generate_image_openai_rejects_gif_edit_inputs():
    client = MagicMock()
    client.images.edit = AsyncMock()
    client.close = AsyncMock()
    cfg = _cfg(provider="openai", api_key="same-openai-key")
    with patch.object(gen, "get_learnhouse_config", return_value=cfg), patch(
        "openai.AsyncOpenAI", return_value=client
    ), pytest.raises(ValueError, match="PNG, JPEG, and WebP"):
        await gen.generate_image("edit", input_images=[b"GIF89a-frame"])

    client.images.edit.assert_not_awaited()


def test_extract_openai_image_bytes_returns_none_for_invalid_response():
    assert gen._extract_openai_image_bytes(SimpleNamespace(data=[])) is None
    assert gen._extract_openai_image_bytes(
        SimpleNamespace(data=[SimpleNamespace(b64_json="not-base64!")])
    ) is None


def test_openai_rate_limit_status_is_retryable_when_error_code_is_text():
    error = Exception("rate limit")
    error.code = "insufficient_quota"
    error.status_code = 429
    assert gen._is_retryable(error)
