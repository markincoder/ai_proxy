"""Инициализация БД и загрузка каталога моделей из `data/default_model_specs.json`."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from . import models as _models  # noqa: F401 — регистрация таблиц в Base.metadata
from .models import AiModel, Base, ChatThread, SiteBanner, User

PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_SPECS_PATH = PACKAGE_DIR / "data" / "default_model_specs.json"
_specs_cache: list[dict[str, object]] | None = None


def _env_removed_slugs() -> frozenset[str]:
    """Дополнительно убрать slug из БД, даже если он ещё есть в JSON (админ задаёт в .env)."""
    raw = get_settings().removed_openrouter_slugs.strip()
    return frozenset(x.strip() for x in raw.split(",") if x.strip()) if raw else frozenset()


def _purge_models_not_in_catalog(db: Session, spec_slugs: set[str]) -> None:
    """
    Удаляет из ai_models записи, которых нет в default_model_specs.json.
    Плюс slug из REMOVED_OPENROUTER_SLUGS — явное исключение без правки репозитория.
    Чаты с удалённой моделью перепривязываются на THREAD_MODEL_FALLBACK_SLUG.
    """
    fb = get_settings().thread_model_fallback_slug
    env_rm = _env_removed_slugs()
    rows = db.query(AiModel).all()
    db_slugs = {r.slug for r in rows}
    to_remove = (db_slugs - spec_slugs) | (db_slugs & env_rm)
    if not to_remove:
        return
    for row in rows:
        if row.slug in to_remove:
            db.delete(row)
    (
        db.query(User)
        .filter(User.last_chat_model_slug.in_(to_remove))
        .update({User.last_chat_model_slug: None}, synchronize_session=False)
    )
    (
        db.query(ChatThread)
        .filter(ChatThread.model_slug.in_(to_remove))
        .update({ChatThread.model_slug: fb}, synchronize_session=False)
    )


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


def run_vacuum_and_analyze(engine: Any, *, vacuum: bool, analyze: bool) -> dict[str, Any]:
    """
    После крупных DELETE: VACUUM (SQLite) возвращает место в файле; ANALYZE обновляет статистику планировщика.
    VACUUM в SQLite нельзя выполнять внутри обычной транзакции — используем AUTOCOMMIT.
    """
    dialect = engine.dialect.name
    steps: list[str] = []
    if not vacuum and not analyze:
        return {"dialect": dialect, "steps": steps}

    if dialect == "sqlite":
        if vacuum:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("VACUUM"))
            steps.append("VACUUM")
        if analyze:
            with engine.begin() as conn:
                conn.execute(text("ANALYZE"))
            steps.append("ANALYZE")
    elif dialect == "postgresql":
        if vacuum and analyze:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("VACUUM ANALYZE"))
            steps.append("VACUUM ANALYZE")
        elif vacuum:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("VACUUM"))
            steps.append("VACUUM")
        elif analyze:
            with engine.begin() as conn:
                conn.execute(text("ANALYZE"))
            steps.append("ANALYZE")
    else:
        if analyze:
            with engine.begin() as conn:
                conn.execute(text("ANALYZE"))
            steps.append("ANALYZE")

    return {"dialect": dialect, "steps": steps}


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


def apply_user_facing_from_specs(db: Session, *, dry_run: bool = False) -> tuple[int, int]:
    """Подтянуть display_name, provider, description_ru, pricing_note_ru из каталога для существующих slug.

    Возвращает (число обновлённых строк, число slug в каталоге без строки в БД).
    """
    updated = 0
    missing_slug = 0
    for spec in get_default_model_specs():
        slug = str(spec["slug"])
        row = db.query(AiModel).filter(AiModel.slug == slug).first()
        if row is None:
            missing_slug += 1
            continue
        dn = str(spec["display_name"])
        pr = _provider_from_seed_spec(spec)
        desc: str | None
        if "description_ru" in spec:
            dr = spec.get("description_ru")
            desc = str(dr) if dr is not None else None
        else:
            desc = row.description_ru
        note: str | None
        if "pricing_note_ru" in spec:
            pn = spec.get("pricing_note_ru")
            note = str(pn) if pn is not None else None
        else:
            note = row.pricing_note_ru
        changed = (
            row.display_name != dn
            or row.provider != pr
            or row.description_ru != desc
            or row.pricing_note_ru != note
        )
        if not changed:
            continue
        updated += 1
        if dry_run:
            print(f"[copy] {slug}: обновить display/provider/описание/пояснение тарифа")
            continue
        row.display_name = dn
        row.provider = pr
        row.description_ru = desc
        row.pricing_note_ru = note
    return updated, missing_slug


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
        row.is_free = bool(s.get("is_free", False))
        fp = s.get("fixed_price")
        if fp is not None and (row.fixed_price is None or row.fixed_price == 0):
            row.fixed_price = fp  # type: ignore[assignment]


def _ensure_users_username_password_columns(engine) -> None:
    """Для существующих БД: добавляем username и password_hash (OAuth-пользователи без пароля)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "username" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(64)"))
        if "password_hash" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
    insp = inspect(engine)
    idx_names = {ix["name"] for ix in insp.get_indexes("users")}
    with engine.begin() as conn:
        if "ix_users_username_unique" not in idx_names:
            if dialect in ("sqlite", "postgresql"):
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_unique ON users(username) "
                        "WHERE username IS NOT NULL"
                    )
                )


def _ensure_user_last_chat_model_slug_column(engine) -> None:
    """Существующие БД: last_chat_model_slug для запоминания последней модели в чате."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "last_chat_model_slug" in cols:
        return
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN last_chat_model_slug VARCHAR(255)"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_chat_model_slug VARCHAR(255)"))


def _ensure_user_is_blocked_column(engine) -> None:
    """Существующие БД: is_blocked (0 = активен, 1 = заблокирован)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "is_blocked" in cols:
        return
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0"))


def _drop_legacy_user_pii_columns(engine) -> None:
    """Удаляем неиспользуемые колонки: ФИО (name), телефон — не храним email/ФИО/контакты пользователя."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    dialect = engine.dialect.name
    for col in ("name", "phone", "phone_verified"):
        if col not in cols:
            continue
        try:
            with engine.begin() as conn:
                if dialect == "sqlite":
                    conn.execute(text(f'ALTER TABLE users DROP COLUMN "{col}"'))
                else:
                    conn.execute(text(f"ALTER TABLE users DROP COLUMN {col}"))
        except Exception:
            continue


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_users_username_password_columns(engine)
    _ensure_user_last_chat_model_slug_column(engine)
    _ensure_user_is_blocked_column(engine)
    _drop_legacy_user_pii_columns(engine)
    specs = get_default_model_specs()
    with SessionLocal() as db:
        known = {row[0] for row in db.query(AiModel.slug).all()}
        for s in specs:
            slug = str(s["slug"])
            if slug in known:
                continue
            db.add(_ai_model_from_spec(s))
        _purge_models_not_in_catalog(db, {str(s["slug"]) for s in specs})
        _sync_models_from_specs(db)
        _ensure_site_banner_row(db)
        db.commit()


def _ensure_site_banner_row(db: Session) -> None:
    row = db.query(SiteBanner).filter(SiteBanner.id == 1).first()
    if row is None:
        db.add(SiteBanner(id=1, is_active=False, message=None, schedule_text=None))
