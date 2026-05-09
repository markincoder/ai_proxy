"""Инициализация БД и загрузка каталога моделей из `data/default_model_specs.json`."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from . import models as _models  # noqa: F401 — регистрация таблиц в Base.metadata
from .models import AiModel, Base, ChatThread

PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_SPECS_PATH = PACKAGE_DIR / "data" / "default_model_specs.json"
_specs_cache: list[dict[str, object]] | None = None


def _normalize_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite:") and not url.startswith("file:"):
        return url
    if url.startswith("file:"):
        raw = url[5:]
        while raw.startswith("/"):
            raw = raw[1:]
        p = Path(raw)
        if not p.is_absolute():
            p = (PACKAGE_DIR / raw).resolve()
        return f"sqlite:///{p.as_posix()}"
    rest = url.replace("sqlite:///", "", 1)
    p = Path(rest)
    if not p.is_absolute():
        p = (PACKAGE_DIR / rest).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p.as_posix()}"


_settings = get_settings()
_db_url = (
    _normalize_sqlite_url(_settings.database_url)
    if "sqlite" in _settings.database_url or _settings.database_url.startswith("file:")
    else _settings.database_url
)

engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False} if _db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _parse_spec_dict(raw: dict[str, Any]) -> dict[str, object]:
    d: dict[str, object] = dict(raw)
    for key in ("input_price_per_mn", "output_price_per_mn", "fixed_price"):
        v = d.get(key)
        if v is None:
            continue
        d[key] = Decimal(str(v))
    d.setdefault("is_free", False)
    for k in (
        "supports_vision",
        "supports_image_generation",
        "supports_music_generation",
        "supports_video_generation",
        "supports_speech",
        "supports_transcription",
    ):
        d.setdefault(k, False)
    d.setdefault("supports_coding", True)
    return d


def get_default_model_specs() -> list[dict[str, object]]:
    """Каталог моделей для сидов, синхронизации цен и флагов возможностей."""
    global _specs_cache
    if _specs_cache is not None:
        return _specs_cache
    if not _DEFAULT_SPECS_PATH.is_file():
        raise FileNotFoundError(
            f"Нет файла каталога: {_DEFAULT_SPECS_PATH}. Восстановите JSON или создайте из бэкапа репозитория."
        )
    data = json.loads(_DEFAULT_SPECS_PATH.read_text(encoding="utf-8"))
    _specs_cache = [_parse_spec_dict(x) for x in data]
    return _specs_cache


def __getattr__(name: str) -> Any:
    if name == "DEFAULT_MODEL_SPECS":
        return get_default_model_specs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _removed_slugs_cfg() -> frozenset[str]:
    raw = get_settings().removed_openrouter_slugs.strip()
    if not raw:
        return frozenset()
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _purge_removed_models_and_rewire_threads(db: Session) -> None:
    removed = _removed_slugs_cfg()
    if not removed:
        return
    fb = get_settings().thread_model_fallback_slug
    for slug in removed:
        row = db.query(AiModel).filter(AiModel.slug == slug).first()
        if row:
            db.delete(row)
    (
        db.query(ChatThread)
        .filter(ChatThread.model_slug.in_(removed))
        .update({ChatThread.model_slug: fb}, synchronize_session=False)
    )


def _provider_from_seed_spec(s: dict[str, object]) -> str:
    if str(s["slug"]) == get_settings().openrouter_free_router_slug:
        return str(get_settings().openrouter_app_title)
    return str(s["provider"])


def _ai_model_from_spec(s: dict[str, object]) -> AiModel:
    return AiModel(
        slug=str(s["slug"]),
        display_name=str(s["display_name"]),
        provider=_provider_from_seed_spec(s),
        input_price_per_mn=s["input_price_per_mn"],  # type: ignore[arg-type]
        output_price_per_mn=s["output_price_per_mn"],  # type: ignore[arg-type]
        fixed_price=s["fixed_price"],  # type: ignore[arg-type]
        is_active=True,
        supports_vision=bool(s["supports_vision"]),
        supports_image_generation=bool(s["supports_image_generation"]),
        supports_music_generation=bool(s["supports_music_generation"]),
        supports_video_generation=bool(s["supports_video_generation"]),
        supports_speech=bool(s.get("supports_speech", False)),
        supports_transcription=bool(s.get("supports_transcription", False)),
        supports_coding=bool(s["supports_coding"]),
        is_free=bool(s.get("is_free", False)),
        description_ru=str(s["description_ru"]) if s.get("description_ru") is not None else None,
        pricing_note_ru=str(s["pricing_note_ru"]) if s.get("pricing_note_ru") is not None else None,
    )


def _sync_models_from_specs(db: Session) -> None:
    """Флаги возможностей из каталога; fixed_price подставляем только если в БД пусто/0."""
    by_slug = {str(x["slug"]): x for x in get_default_model_specs()}
    for row in db.query(AiModel).all():
        s = by_slug.get(row.slug)
        if not s:
            continue
        row.supports_vision = bool(s["supports_vision"])
        row.supports_image_generation = bool(s["supports_image_generation"])
        row.supports_music_generation = bool(s["supports_music_generation"])
        row.supports_video_generation = bool(s["supports_video_generation"])
        row.supports_speech = bool(s.get("supports_speech", False))
        row.supports_transcription = bool(s.get("supports_transcription", False))
        row.supports_coding = bool(s["supports_coding"])
        fp = s.get("fixed_price")
        if fp is not None and (row.fixed_price is None or row.fixed_price == 0):
            row.fixed_price = fp  # type: ignore[assignment]


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    specs = get_default_model_specs()
    with SessionLocal() as db:
        known = {row[0] for row in db.query(AiModel.slug).all()}
        for s in specs:
            slug = str(s["slug"])
            if slug in known:
                continue
            db.add(_ai_model_from_spec(s))
        _purge_removed_models_and_rewire_threads(db)
        _sync_models_from_specs(db)
        db.commit()
