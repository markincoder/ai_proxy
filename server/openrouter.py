from contextlib import asynccontextmanager
import base64
import logging
from typing import Any, AsyncIterator

import httpx

from .config import get_settings
from .openai_audio_input import reencode_audio_bytes_to_wav

_LOG = logging.getLogger(__name__)


def openrouter_base_url() -> str:
    return get_settings().openrouter_api_base_url.rstrip("/")


def _client_timeout(*, connect: float = 30.0, total: float = 300.0) -> httpx.Timeout:
    return httpx.Timeout(total, connect=connect)


@asynccontextmanager
async def openrouter_async_client(*, timeout: httpx.Timeout | None = None) -> AsyncIterator[httpx.AsyncClient]:
    s = get_settings()
    t = timeout or _client_timeout()
    async with httpx.AsyncClient(timeout=t, trust_env=s.openrouter_httpx_trust_env) as client:
        yield client


def openrouter_headers(json_body: bool = True) -> dict[str, str]:
    s = get_settings()
    if not s.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    h: dict[str, str] = {
        "Authorization": f"Bearer {s.openrouter_api_key}",
        "HTTP-Referer": s.public_app_url.rstrip("/"),
        "X-Title": s.openrouter_app_title,
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def openrouter_headers_get() -> dict[str, str]:
    """Заголовки для GET (без Content-Type)."""
    return openrouter_headers(False)


async def chat_completions(
    body: dict[str, Any],
    *,
    timeout: httpx.Timeout | None = None,
) -> httpx.Response:
    async with openrouter_async_client(timeout=timeout) as client:
        return await client.post(
            f"{openrouter_base_url()}/chat/completions",
            headers=openrouter_headers(True),
            json=body,
        )


async def embeddings_create(body: dict[str, Any]) -> httpx.Response:
    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        return await client.post(
            f"{openrouter_base_url()}/embeddings",
            headers=openrouter_headers(True),
            json=body,
        )


async def video_generation_create(body: dict[str, Any]) -> httpx.Response:
    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        return await client.post(
            f"{openrouter_base_url()}/videos",
            headers=openrouter_headers(True),
            json=body,
        )


async def video_generation_get(job_id: str) -> httpx.Response:
    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        return await client.get(
            f"{openrouter_base_url()}/videos/{job_id}",
            headers=openrouter_headers_get(),
        )


def _stt_audio_format(mime: str, filename: str) -> str:
    """Формат для input_audio.format (как в chat._audio_format_from_mime)."""
    m = (mime or "").lower()
    fn = (filename or "").lower()
    if "webm" in m or fn.endswith(".webm"):
        return "webm"
    if "wav" in m or fn.endswith(".wav") or "audio/wave" in m or "audio/x-wav" in m:
        return "wav"
    if "mpeg" in m or "mp3" in m or fn.endswith(".mp3"):
        return "mp3"
    if "oga" in m or "ogg" in m or fn.endswith((".ogg", ".oga", ".opus")):
        return "ogg"
    if "flac" in m or fn.endswith(".flac"):
        return "flac"
    if "m4a" in m or "audio/mp4" in m or fn.endswith(".m4a"):
        return "m4a"
    if "mp4" in m or fn.endswith(".mp4"):
        return "mp4"
    if "aac" in m or fn.endswith(".aac"):
        return "aac"
    return "webm"


def is_matroska_non_webm_ebml(content: bytes) -> bool:
    """Контейнер EBML Matroska без DocType webm (часто .mkv) — Chirp/даунстрим часто дают 400."""
    if len(content) < 32 or content[:4] != b"\x1a\x45\xdf\xa3":
        return False
    low = content[: min(len(content), 65536)].lower()
    return b"matroska" in low and b"webm" not in low


def _sniff_audio_container_format(content: bytes) -> str | None:
    """
    Реальный контейнер по сигнатуре (Google Chirp / upstream дают 400, если format ≠ содержимое;
    браузер часто шлёт application/octet-stream).
    """
    if not content or len(content) < 12:
        return None
    if content[:4] == b"RIFF" and len(content) >= 12 and content[8:12] == b"WAVE":
        return "wav"
    if content[:4] == b"fLaC":
        return "flac"
    if content[:4] == b"OggS":
        return "ogg"
    if content[:4] == b"\x1a\x45\xdf\xa3":
        if is_matroska_non_webm_ebml(content):
            return None
        return "webm"
    # MP3: фрейм или ID3
    if content[:3] == b"ID3":
        return "mp3"
    b0, b1 = content[0], content[1]
    if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
        return "mp3"
    # AAC ADTS
    if b0 == 0xFF and b1 in (0xF1, 0xF9):
        return "aac"
    # MP4 / M4A (ISO BMFF)
    if content[4:8] == b"ftyp" and len(content) >= 12:
        return "m4a"
    return None


def _resolve_transcription_format(content: bytes, mime: str, filename: str) -> str:
    sniffed = _sniff_audio_container_format(content)
    if sniffed:
        return sniffed
    return _stt_audio_format(mime, filename)


def _chirp_language_attempts(language: str | None) -> list[str | None]:
    """Chirp часто отвечает 400: перебираем варианты, optional language — приоритетный hint."""
    base: list[str | None] = [None, "ru", "en"]
    if not language:
        return base
    seen: set[str | None] = set()
    out: list[str | None] = []
    for x in (language, *base):
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _stt_request_body(model: str, content: bytes, fmt: str, language: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(content).decode("ascii"),
            "format": fmt,
        },
    }
    if language:
        body["language"] = language
    return body


async def transcribe_audio(
    content: bytes,
    filename: str,
    mime: str,
    model: str = "openai/whisper-1",
    language: str | None = None,
) -> httpx.Response:
    """OpenRouter STT: POST /audio/transcriptions (документация OpenRouter)."""
    s = get_settings()
    if not s.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    fmt = _resolve_transcription_format(content, mime, filename)
    chirp = model.startswith("google/chirp")
    if chirp and fmt in ("webm", "ogg", "mp3"):
        try:
            content = reencode_audio_bytes_to_wav(content, fmt)
            fmt = "wav"
        except FileNotFoundError:
            _LOG.warning(
                "ffmpeg not found: Chirp transcription may fail on %s input",
                fmt,
            )
        except RuntimeError as exc:
            _LOG.warning(
                "Chirp: could not transcode %s to wav, sending as-is: %s",
                fmt,
                exc,
            )
    url = f"{openrouter_base_url()}/audio/transcriptions"
    hdrs = openrouter_headers(True)

    if chirp:
        attempt_langs = _chirp_language_attempts(language)
    elif language:
        attempt_langs = [language]
    else:
        attempt_langs = [None]

    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        last: httpx.Response | None = None
        for lang in attempt_langs:
            body = _stt_request_body(model, content, fmt, lang)
            last = await client.post(url, headers=hdrs, json=body)
            if last.status_code < 400:
                return last
            if not chirp or last.status_code != 400:
                return last
        assert last is not None
        return last
