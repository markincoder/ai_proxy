"""Совместимость входного аудио с OpenAI GPT Audio: только format wav|mp3; браузер шлёт webm/opus."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AiModel


def openai_gpt_audio_style_model(model: "AiModel") -> bool:
    """Каталог помечает семейство GPT Audio как speech + music_generation."""
    return bool(model.supports_music_generation and model.supports_speech)


def _ffmpeg_demuxer_for_format(fmt: str) -> list[str]:
    """Подсказка ffmpeg demuxer при чтении из stdin (иначе webm/opus не распознаётся)."""
    f = (fmt or "").strip().lower()
    if f == "webm":
        return ["-f", "webm"]
    if f == "ogg":
        return ["-f", "ogg"]
    if f == "mp3":
        return ["-f", "mp3"]
    if f in ("mp4", "m4a"):
        return ["-f", "mov"]
    if f == "flac":
        return ["-f", "flac"]
    return []


def reencode_audio_bytes_to_wav(src: bytes, source_format: str) -> bytes:
    """PCM WAV 24kHz mono через ffmpeg (stdin → stdout)."""
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("ffmpeg")
    pre = _ffmpeg_demuxer_for_format(source_format)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *pre,
        "-i",
        "pipe:0",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-f",
        "wav",
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        input=src,
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "ffmpeg failed")
    out = proc.stdout or b""
    if len(out) < 64:
        raise RuntimeError("ffmpeg produced empty output")
    return out
