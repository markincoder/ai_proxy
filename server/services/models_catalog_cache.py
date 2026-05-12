"""Кэш ответа GET /api/models (активные строки ai_models) для снижения нагрузки на БД."""

from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from ..models import AiModel

_lock = threading.Lock()
_cache_payload: list[dict[str, Any]] | None = None
_expires_at_mono: float = 0.0


def invalidate_public_models_cache() -> None:
    """Сбросить кэш после любых правок каталога моделей в БД."""
    global _cache_payload, _expires_at_mono
    with _lock:
        _cache_payload = None
        _expires_at_mono = 0.0


def _serialize_active_model(m: AiModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "slug": m.slug,
        "displayName": m.display_name,
        "provider": m.provider,
        "inputPricePerMn": str(m.input_price_per_mn),
        "outputPricePerMn": str(m.output_price_per_mn),
        "fixedPrice": str(m.fixed_price) if m.fixed_price is not None else None,
        "supportsVision": m.supports_vision,
        "supportsImageGeneration": m.supports_image_generation,
        "supportsVideoGeneration": m.supports_video_generation,
        "supportsMusicGeneration": m.supports_music_generation,
        "supportsSpeech": m.supports_speech,
        "supportsTranscription": m.supports_transcription,
        "supportsEmbeddings": m.supports_embeddings,
        "supportsChat": m.supports_chat,
        "isFree": m.is_free,
        "descriptionRu": m.description_ru,
        "pricingNoteRu": m.pricing_note_ru,
        "capabilities": {
            "visionInput": m.supports_vision,
            "imageGeneration": m.supports_image_generation,
            "musicGeneration": m.supports_music_generation,
            "videoGeneration": m.supports_video_generation,
            "speech": m.supports_speech,
            "transcription": m.supports_transcription,
            "coding": m.supports_coding,
            "embeddings": m.supports_embeddings,
            "chat": m.supports_chat,
            "free": m.is_free,
        },
    }


def _load_active_models_from_db(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(AiModel)
        .filter(AiModel.is_active.is_(True))
        .order_by(AiModel.display_name.asc())
        .all()
    )
    return [_serialize_active_model(m) for m in rows]


def get_public_models_cached(db: Session, ttl_sec: float) -> list[dict[str, Any]]:
    """
    Возвращает тот же JSON, что и GET /api/models.
    ttl_sec <= 0 — без кэша, каждый раз запрос в БД.
    """
    global _cache_payload, _expires_at_mono
    if ttl_sec <= 0:
        return _load_active_models_from_db(db)

    now = time.monotonic()
    with _lock:
        if _cache_payload is not None and now < _expires_at_mono:
            return _cache_payload

    snapshot = _load_active_models_from_db(db)

    with _lock:
        _cache_payload = snapshot
        _expires_at_mono = time.monotonic() + ttl_sec
        return _cache_payload
