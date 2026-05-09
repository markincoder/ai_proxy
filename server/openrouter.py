from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

from .config import get_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1"


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
        "HTTP-Referer": s.openrouter_site_url,
        "X-Title": s.openrouter_app_title,
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def openrouter_headers_get() -> dict[str, str]:
    """Заголовки для GET (без Content-Type)."""
    return openrouter_headers(False)


async def chat_completions(body: dict[str, Any]) -> httpx.Response:
    async with openrouter_async_client() as client:
        return await client.post(
            f"{OPENROUTER_URL}/chat/completions",
            headers=openrouter_headers(True),
            json=body,
        )


async def video_generation_create(body: dict[str, Any]) -> httpx.Response:
    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        return await client.post(
            f"{OPENROUTER_URL}/videos",
            headers=openrouter_headers(True),
            json=body,
        )


async def video_generation_get(job_id: str) -> httpx.Response:
    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        return await client.get(
            f"{OPENROUTER_URL}/videos/{job_id}",
            headers=openrouter_headers_get(),
        )


async def transcribe_audio(
    content: bytes,
    filename: str,
    mime: str,
    model: str = "openai/whisper-1",
) -> httpx.Response:
    s = get_settings()
    if not s.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    files = {"file": (filename, content, mime)}
    data = {"model": model}
    async with openrouter_async_client(timeout=_client_timeout(total=120.0)) as client:
        return await client.post(
            f"{OPENROUTER_URL}/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {s.openrouter_api_key}",
                "HTTP-Referer": s.openrouter_site_url,
                "X-Title": s.openrouter_app_title,
            },
            files=files,
            data=data,
        )
