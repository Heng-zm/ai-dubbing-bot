"""Text-to-speech generation service.

Default provider is edge-tts because the project goal is Microsoft Edge TTS.
Production fallback to official Azure Speech REST API is included for cloud IP
403/rate-limit cases.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import shutil
from pathlib import Path
from typing import Literal

import edge_tts
import httpx

from app.config import settings
from app.services.logger_service import logger
from app.services.runtime_settings import runtime_settings
from app.utils.text_utils import normalize_tts_text

ProviderName = Literal["edge", "azure"]

# edge-tts is unofficial and can 403/rate-limit cloud hosts. Serializing calls
# plus configurable delay greatly reduces failures on small Render instances.
_EDGE_TTS_LOCK = asyncio.Lock()
_CACHE_LOCK = asyncio.Lock()


def _is_edge_forbidden_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "403" in text or "forbidden" in text or "invalid response status" in text


def _cache_key(provider: str, voice: str, text: str) -> str:
    raw = "|".join(
        [
            provider,
            voice,
            settings.edge_tts_rate,
            settings.edge_tts_volume,
            settings.edge_tts_pitch,
            settings.azure_output_format,
            text,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(provider: str, voice: str, text: str) -> Path:
    return settings.tts_cache_dir / f"{_cache_key(provider, voice, text)}.mp3"


async def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copyfile, src, dst)


async def _try_cache_hit(provider: str, voice: str, text: str, output_path: Path) -> bool:
    if not bool(runtime_settings.cached().get("tts_cache_enabled", settings.tts_cache_enabled)):
        return False
    cache = _cache_path(provider, voice, text)
    if cache.exists() and cache.stat().st_size > 0:
        await _copy_file(cache, output_path)
        return True
    return False


async def _save_cache(provider: str, voice: str, text: str, output_path: Path) -> None:
    if not bool(runtime_settings.cached().get("tts_cache_enabled", settings.tts_cache_enabled)) or not output_path.exists() or output_path.stat().st_size <= 0:
        return
    async with _CACHE_LOCK:
        cache = _cache_path(provider, voice, text)
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            await _copy_file(output_path, cache)


def _edge_communicate(text: str, voice: str) -> edge_tts.Communicate:
    kwargs = {
        "rate": settings.edge_tts_rate,
        "volume": settings.edge_tts_volume,
        "pitch": settings.edge_tts_pitch,
    }
    if settings.edge_tts_proxy:
        kwargs["proxy"] = settings.edge_tts_proxy
    if settings.edge_tts_connect_timeout > 0:
        kwargs["connect_timeout"] = settings.edge_tts_connect_timeout
    if settings.edge_tts_receive_timeout > 0:
        kwargs["receive_timeout"] = settings.edge_tts_receive_timeout

    try:
        return edge_tts.Communicate(text, voice, **kwargs)
    except TypeError:
        fallback_kwargs = {
            "rate": settings.edge_tts_rate,
            "volume": settings.edge_tts_volume,
            "pitch": settings.edge_tts_pitch,
        }
        if settings.edge_tts_proxy:
            fallback_kwargs["proxy"] = settings.edge_tts_proxy
        try:
            return edge_tts.Communicate(text, voice, **fallback_kwargs)
        except TypeError:
            return edge_tts.Communicate(text, voice)


async def _generate_with_edge(text: str, voice: str, output_path: Path) -> Path:
    if await _try_cache_hit("edge", voice, text, output_path):
        return output_path

    async with _EDGE_TTS_LOCK:
        communicate = _edge_communicate(text, voice)
        await communicate.save(str(output_path))
        if output_path.exists() and output_path.stat().st_size > 0:
            await _save_cache("edge", voice, text, output_path)
        if settings.edge_tts_delay_seconds > 0:
            await asyncio.sleep(settings.edge_tts_delay_seconds)
    return output_path


def _azure_ssml(text: str, voice: str) -> str:
    escaped_text = html.escape(text, quote=False)
    escaped_voice = html.escape(voice, quote=True)
    return f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="km-KH">
  <voice name="{escaped_voice}">{escaped_text}</voice>
</speak>""".strip()


async def _generate_with_azure(text: str, voice: str, output_path: Path) -> Path:
    if await _try_cache_hit("azure", voice, text, output_path):
        return output_path

    if not settings.azure_speech_key or not settings.azure_speech_region:
        raise RuntimeError(
            "Azure Speech fallback is enabled but AZURE_SPEECH_KEY or AZURE_SPEECH_REGION is missing"
        )

    region = settings.azure_speech_region.strip()
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": settings.azure_output_format,
        "User-Agent": settings.azure_user_agent,
    }
    ssml = _azure_ssml(text, voice)
    timeout = httpx.Timeout(settings.azure_tts_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, headers=headers, content=ssml.encode("utf-8"))
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else response.reason_phrase
        raise RuntimeError(f"Azure Speech TTS failed HTTP {response.status_code}: {detail}")
    output_path.write_bytes(response.content)
    await _save_cache("azure", voice, text, output_path)
    return output_path


async def _generate_once(provider: ProviderName, text: str, voice: str, output_path: Path) -> Path:
    if provider == "azure":
        return await _generate_with_azure(text, voice, output_path)
    return await _generate_with_edge(text, voice, output_path)


async def generate_tts_audio(text: str, voice: str, output_path: Path) -> Path:
    """Generate one subtitle line with retry logic and optional fallback.

    TTS_PROVIDER values:
    - edge: use only edge-tts
    - azure: use only official Azure Speech REST API
    - auto: try edge-tts first, fallback to Azure when Edge returns 403/blocking
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clean_text = normalize_tts_text(text)
    if not clean_text:
        raise ValueError("Cannot generate TTS from empty subtitle text")

    runtime = await runtime_settings.load()
    tts_provider = str(runtime.get("tts_provider", settings.tts_provider)).lower()
    if tts_provider == "azure":
        providers: list[ProviderName] = ["azure"]
    elif tts_provider == "auto":
        providers = ["edge", "azure"]
    else:
        providers = ["edge"]

    last_error: Exception | None = None
    for provider_name in providers:
        for attempt in range(1, settings.tts_max_retries + 1):
            try:
                # Remove partial files from previous failed attempts before retrying.
                output_path.unlink(missing_ok=True)
                await _generate_once(provider_name, clean_text, voice, output_path)
                if output_path.exists() and output_path.stat().st_size > 0:
                    return output_path
                raise RuntimeError(f"{provider_name} TTS produced an empty file")
            except Exception as exc:  # pragma: no cover - network/service dependent
                last_error = exc
                logger.warning(
                    "%s TTS attempt %s/%s failed: %s",
                    provider_name,
                    attempt,
                    settings.tts_max_retries,
                    exc,
                )
                if provider_name == "edge" and tts_provider == "auto" and _is_edge_forbidden_error(exc):
                    logger.warning("edge-tts returned 403/blocking. Trying Azure Speech fallback if configured.")
                    break
                await asyncio.sleep(min(settings.tts_retry_base_delay_seconds * attempt, 15))

    raise RuntimeError(f"TTS generation failed after retries: {last_error}")
