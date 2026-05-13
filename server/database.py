"""Инициализация БД и загрузка каталогов моделей: `default_model_specs.json` и `default_embedding_specs.json`."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import REPO_ROOT, get_settings
from . import models as _models  # noqa: F401 — регистрация таблиц в Base.metadata
from .models import AiModel, Base, SiteBanner, User


def _catalog_specs_path() -> Path:
    return REPO_ROOT / "server" / "default_model_specs.json"


def _embedding_catalog_specs_path() -> Path:
    return REPO_ROOT / "server" / "default_embedding_specs.json"


# В образе Docker копируется в `.seed/`; при пустом томе `data/` файл подставляется при первом старте.
_SEED_SPECS_PATH = REPO_ROOT / ".seed" / "default_model_specs.json"
_SEED_EMBEDDING_SPECS_PATH = REPO_ROOT / ".seed" / "default_embedding_specs.json"
_specs_cache: list[dict[str, object]] | None = None
_embedding_specs_cache: list[dict[str, object]] | None = None


def _ensure_default_model_specs_file() -> None:
    path = _catalog_specs_path()
    if path.is_file():
        return
    if not _SEED_SPECS_PATH.is_file():
        raise FileNotFoundError(
            f"Нет файла каталога: {path}. Восстановите JSON или создайте из бэкапа репозитория."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_SEED_SPECS_PATH, path)


def _ensure_default_embedding_specs_file() -> None:
    path = _embedding_catalog_specs_path()
    if path.is_file():
        return
    if not _SEED_EMBEDDING_SPECS_PATH.is_file():
        raise FileNotFoundError(
            f"Нет файла каталога эмбеддингов: {path}. Восстановите JSON или добавьте в образ `.seed/`."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_SEED_EMBEDDING_SPECS_PATH, path)


def _env_removed_slugs() -> frozenset[str]:
    """Дополнительно убрать slug из БД, даже если он ещё есть в JSON (админ задаёт в .env)."""
    raw = get_settings().removed_openrouter_slugs.strip()
    return frozenset(x.strip() for x in raw.split(",") if x.strip()) if raw else frozenset()


def _purge_models_not_in_catalog(db: Session, spec_slugs: set[str]) -> None:
    """
    Удаляет из ai_models записи, slug которых нет в объединённом каталоге (чат + эмбеддинги).
    Плюс slug из REMOVED_OPENROUTER_SLUGS — явное исключение без правки репозитория.
    Диалоги (chat_threads) не меняем: исторический model_slug сохраняется для отображения;
    активная модель для ответов выбирается на клиенте из текущего каталога.
    """
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


def _normalize_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite:") and not url.startswith("file:"):
        return url
    if url.startswith("file:"):
        raw = url[5:]
        while raw.startswith("/"):
            raw = raw[1:]
        p = Path(raw)
        if not p.is_absolute():
            p = (REPO_ROOT / raw).resolve()
        return f"sqlite:///{p.as_posix()}"
    rest = url.replace("sqlite:///", "", 1)
    p = Path(rest)
    if not p.is_absolute():
        p = (REPO_ROOT / rest).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p.as_posix()}"


_settings = get_settings()
_db_url = (
    _normalize_sqlite_url(_settings.database_url)
    if "sqlite" in _settings.database_url or _settings.database_url.startswith("file:")
    else _settings.database_url
)

_url_obj = make_url(_db_url)
_is_sqlite = _url_obj.drivername == "sqlite"
_is_mysql = _url_obj.drivername.startswith("mysql")
_is_postgresql = _url_obj.drivername.startswith("postgresql")

_connect_args: dict[str, object] = {}
_engine_kwargs: dict[str, object] = {}
if _is_sqlite:
    _connect_args = {"check_same_thread": False}
elif _is_mysql:
    _connect_args = {"charset": "utf8mb4"}
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20
    _engine_kwargs["pool_recycle"] = 3600
elif _is_postgresql:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20
    _engine_kwargs["pool_recycle"] = 1800

engine = create_engine(
    _db_url,
    connect_args=_connect_args,
    **_engine_kwargs,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def run_vacuum_and_analyze(engine: Any, *, vacuum: bool, analyze: bool) -> dict[str, Any]:
    """
    После крупных DELETE: VACUUM (SQLite) возвращает место в файле; ANALYZE обновляет статистику планировщика.
    VACUUM в SQLite нельзя выполнять внутри обычной транзакции — используем AUTOCOMMIT.
    MySQL: «vacuum» ≈ OPTIMIZE TABLE по таблицам приложения (InnoDB, может быть долго); ANALYZE — статистика.
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
    elif dialect == "mysql":
        tables = sorted(Base.metadata.tables.keys())
        if tables:
            quoted = ", ".join(f"`{n}`" for n in tables)
            if vacuum:
                with engine.begin() as conn:
                    for name in tables:
                        conn.execute(text(f"OPTIMIZE TABLE `{name}`"))
                steps.append("OPTIMIZE TABLE")
            if analyze:
                if not vacuum:
                    with engine.begin() as conn:
                        conn.execute(text(f"ANALYZE TABLE {quoted}"))
                    steps.append("ANALYZE TABLE")
                else:
                    steps.append("ANALYZE (статистика обновлена OPTIMIZE для InnoDB)")
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
    d.setdefault("supports_embeddings", False)
    d.setdefault("supports_chat", True)
    return d


def get_default_model_specs() -> list[dict[str, object]]:
    """Каталог моделей для сидов, синхронизации цен и флагов возможностей."""
    global _specs_cache
    if _specs_cache is not None:
        return _specs_cache
    _ensure_default_model_specs_file()
    path = _catalog_specs_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    _specs_cache = [_parse_spec_dict(x) for x in data]
    return _specs_cache


def get_default_embedding_specs() -> list[dict[str, object]]:
    """Каталог моделей только для POST …/embeddings (slug не пересекаются с чат-каталогом)."""
    global _embedding_specs_cache
    if _embedding_specs_cache is not None:
        return _embedding_specs_cache
    _ensure_default_embedding_specs_file()
    path = _embedding_catalog_specs_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    _embedding_specs_cache = [_parse_spec_dict(x) for x in data]
    return _embedding_specs_cache


def _merged_specs_by_slug() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for s in get_default_model_specs():
        slug = str(s["slug"])
        out[slug] = dict(s)
    for s in get_default_embedding_specs():
        slug = str(s["slug"])
        out[slug] = dict(s)
    return out


def get_all_catalog_specs_ordered() -> list[dict[str, object]]:
    """Порядок: сначала чат-каталог, затем эмбеддинги только с новыми slug."""
    by = _merged_specs_by_slug()
    out: list[dict[str, object]] = []
    chat_slugs: set[str] = set()
    for s in get_default_model_specs():
        slug = str(s["slug"])
        chat_slugs.add(slug)
        out.append(by[slug])
    for s in get_default_embedding_specs():
        slug = str(s["slug"])
        if slug in chat_slugs:
            continue
        out.append(by[slug])
    return out


def __getattr__(name: str) -> Any:
    if name == "DEFAULT_MODEL_SPECS":
        return get_default_model_specs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _provider_from_seed_spec(s: dict[str, object]) -> str:
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
        supports_embeddings=bool(s.get("supports_embeddings", False)),
        supports_chat=bool(s.get("supports_chat", True)),
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
    for spec in [*get_default_model_specs(), *get_default_embedding_specs()]:
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
    """Флаги возможностей из объединённого каталога; fixed_price подставляем только если в БД пусто/0."""
    by_slug = _merged_specs_by_slug()
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
        row.supports_embeddings = bool(s.get("supports_embeddings", False))
        row.supports_chat = bool(s.get("supports_chat", True))
        row.is_free = bool(s.get("is_free", False))
        fp = s.get("fixed_price")
        if fp is not None and (row.fixed_price is None or row.fixed_price == 0):
            row.fixed_price = fp  # type: ignore[assignment]


def _ensure_ai_model_embedding_columns(engine: Any) -> None:
    """Существующие БД: supports_embeddings и supports_chat (чат-only vs только эмбеддинги)."""
    insp = inspect(engine)
    if not insp.has_table("ai_models"):
        return
    cols = {c["name"] for c in insp.get_columns("ai_models")}
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "supports_embeddings" not in cols:
            if dialect in ("postgresql", "mysql"):
                conn.execute(
                    text(
                        "ALTER TABLE ai_models ADD COLUMN supports_embeddings BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE ai_models ADD COLUMN supports_embeddings INTEGER NOT NULL DEFAULT 0"
                    )
                )
        if "supports_chat" not in cols:
            if dialect in ("postgresql", "mysql"):
                conn.execute(
                    text(
                        "ALTER TABLE ai_models ADD COLUMN supports_chat BOOLEAN NOT NULL DEFAULT TRUE"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE ai_models ADD COLUMN supports_chat INTEGER NOT NULL DEFAULT 1"
                    )
                )


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
            elif dialect == "mysql":
                # При create_all на свежей БД уже есть UNIQUE(slug-подобное) из модели; не дублируем индекс.
                has_uq_username = False
                try:
                    for uq in insp.get_unique_constraints("users") or []:
                        cols = tuple(uq.get("column_names") or ())
                        if "username" in cols:
                            has_uq_username = True
                            break
                    if not has_uq_username:
                        for ix in insp.get_indexes("users") or []:
                            cn = list(ix.get("column_names") or [])
                            if ix.get("unique") and cn == ["username"]:
                                has_uq_username = True
                                break
                except Exception:
                    pass
                if not has_uq_username:
                    conn.execute(
                        text(
                            "CREATE UNIQUE INDEX ix_users_username_unique ON users(username)"
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


def _ensure_user_last_login_at_column(engine) -> None:
    """Существующие БД: last_login_at (последний успешный вход)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "last_login_at" in cols:
        return
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME"))
        elif dialect == "postgresql":
            conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP NULL"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL"))


def _ensure_user_terms_accepted_at_column(engine) -> None:
    """Существующие БД: terms_accepted_at (согласие с пользовательским соглашением)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "terms_accepted_at" in cols:
        return
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN terms_accepted_at DATETIME"))
        elif dialect == "postgresql":
            conn.execute(text("ALTER TABLE users ADD COLUMN terms_accepted_at TIMESTAMP NULL"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN terms_accepted_at DATETIME NULL"))


def _ensure_user_favorite_model_slugs_column(engine) -> None:
    """Существующие БД: favorite_model_slugs (JSON-список slug избранных моделей)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "favorite_model_slugs" in cols:
        return
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN favorite_model_slugs TEXT"))
        elif dialect == "postgresql":
            conn.execute(text("ALTER TABLE users ADD COLUMN favorite_model_slugs JSONB"))
        elif dialect == "mysql":
            conn.execute(text("ALTER TABLE users ADD COLUMN favorite_model_slugs JSON"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN favorite_model_slugs TEXT"))


def _sanitize_users_datetime_sentinels(engine) -> None:
    """SQLite и др.: исправить '' в колонках даты у users (ORM иначе падает на чтении строки)."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols_meta = {c["name"] for c in insp.get_columns("users")}
    dialect = engine.dialect.name

    dt_cols = ("last_login_at", "terms_accepted_at", "created_at", "updated_at")
    present = [c for c in dt_cols if c in cols_meta]
    if not present:
        return

    nullable = frozenset(("last_login_at", "terms_accepted_at"))

    if dialect == "sqlite":
        # Через pool не использовать raw_connection()+commit(): возможны откаты при возврате в пул.
        # Массовые UPDATE надёжнее построчного обхода и чистят все '' / пробельные «пустые» текстовые ячейки.
        with engine.begin() as conn:
            for col in present:
                qcol = '"' + col.replace('"', "") + '"'
                bad_empty = (
                    f"(trim(cast({qcol} AS TEXT)) = '' OR "
                    f"(typeof({qcol}) = 'text' AND trim({qcol}) = ''))"
                )
                if col in nullable:
                    conn.execute(text(f"UPDATE users SET {qcol} = NULL WHERE {bad_empty}"))
                else:
                    conn.execute(
                        text(f"UPDATE users SET {qcol} = CURRENT_TIMESTAMP WHERE {bad_empty}")
                    )
        return

    with engine.begin() as conn:
        if dialect == "mysql":
            # Нельзя писать `col = '0000-00-00 00:00:00'` — в strict / NO_ZERO_DATE литерал недопустим (1292).
            for col in present:
                q = "`" + col.replace("`", "") + "`"
                bad = (
                    f"(TRIM(CAST({q} AS CHAR(64))) IN "
                    f"('', '0000-00-00 00:00:00', '0000-00-00'))"
                )
                if col in nullable:
                    conn.execute(text(f"UPDATE users SET {q} = NULL WHERE {bad}"))
                else:
                    conn.execute(
                        text(f"UPDATE users SET {q} = CURRENT_TIMESTAMP WHERE {bad}")
                    )
        elif dialect == "postgresql":
            for col in present:
                if col not in nullable:
                    continue
                conn.execute(
                    text(f'UPDATE users SET "{col}" = NULL WHERE CAST("{col}" AS TEXT) = \'\'')
                )
        else:
            for col in present:
                if col in nullable:
                    conn.execute(
                        text(f'UPDATE users SET "{col}" = NULL WHERE "{col}" = \'\' OR trim(cast("{col}" as text)) = \'\'')
                    )


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
                elif dialect == "mysql":
                    conn.execute(text(f"ALTER TABLE users DROP COLUMN `{col}`"))
                else:
                    conn.execute(text(f"ALTER TABLE users DROP COLUMN {col}"))
        except Exception:
            continue


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_users_username_password_columns(engine)
    _ensure_user_last_chat_model_slug_column(engine)
    _ensure_user_is_blocked_column(engine)
    _ensure_user_last_login_at_column(engine)
    _ensure_user_terms_accepted_at_column(engine)
    _ensure_user_favorite_model_slugs_column(engine)
    _sanitize_users_datetime_sentinels(engine)
    _ensure_ai_model_embedding_columns(engine)
    _drop_legacy_user_pii_columns(engine)
    specs = get_all_catalog_specs_ordered()
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
