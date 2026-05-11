"""Сохранение неперехваченных исключений в БД (см. ErrorLog), уведомление админу."""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from .database import SessionLocal
from .models import ErrorLog
from .services.notify import schedule_error_log_notification

if TYPE_CHECKING:
    from fastapi import FastAPI

_LOG = logging.getLogger(__name__)
_TB_MAX = 100_000

# upstream:* — не чаще одного события в журнал/Telegram за интервал (несколько воркеров = лимит не общий)
_upstream_throttle: dict[str, float] = {}
_PREVIEW_LEN = 2800


def _build_context(request: Request) -> str:
    parts: list[str] = [f"{request.method} {request.url.path}"]
    q = request.url.query or ""
    if q:
        if len(q) > 800:
            q = q[:800] + "…"
        parts.append("?" + q)
    try:
        uid = request.session.get("user_id")
        if uid:
            parts.append(f"user={uid}")
    except Exception:
        pass
    client = request.client
    if client and client.host:
        parts.append(f"ip={client.host}")
    return " · ".join(parts)


def _commit_error_log(
    *,
    context: str,
    error_type: str,
    message: str | None,
    tb_full: str,
) -> str | None:
    """Возвращает id записи или None при сбое."""
    eid = str(uuid.uuid4())
    try:
        with SessionLocal() as db:
            db.add(
                ErrorLog(
                    id=eid,
                    context=context,
                    error_type=error_type,
                    message=message,
                    traceback=tb_full,
                )
            )
            db.commit()
    except Exception as inner:
        _LOG.warning("Could not persist ErrorLog: %s", inner, exc_info=True)
        return None
    preview = tb_full[:_PREVIEW_LEN] if len(tb_full) > _PREVIEW_LEN else tb_full
    schedule_error_log_notification(eid, context, error_type, message, preview)
    return eid


def persist_request_exception(request: Request, exc: BaseException) -> None:
    try:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(tb) > _TB_MAX:
            tb = tb[-_TB_MAX:]
        msg = str(exc) if exc else ""
        if len(msg) > 4000:
            msg = msg[:4000] + "…"
        ctx = _build_context(request)
        _commit_error_log(context=ctx, error_type=type(exc).__name__, message=msg or None, tb_full=tb)
    except Exception as inner:
        _LOG.warning("Could not persist ErrorLog: %s", inner, exc_info=True)


def log_upstream_request_failure(
    kind: str,
    exc: BaseException,
    *,
    min_interval_sec: float = 300.0,
) -> None:
    """
    Нештатная недоступность upstream (после исчерпания попыток на транспортном уровне).
    Пишется в error_logs и уходит уведомление; между событиями одного kind — не чаще min_interval_sec.
    """
    _LOG.warning("upstream failure [%s]: %s", kind, exc, exc_info=True)
    now = time.monotonic()
    key = f"upstream:{kind}"
    last = _upstream_throttle.get(key, 0.0)
    if now - last < min_interval_sec:
        return
    _upstream_throttle[key] = now
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(tb) > _TB_MAX:
        tb = tb[-_TB_MAX:]
    msg = str(exc) if exc else ""
    if len(msg) > 4000:
        msg = msg[:4000] + "…"
    ctx = f"upstream:{kind}"
    _commit_error_log(
        context=ctx,
        error_type=type(exc).__name__,
        message=msg or None,
        tb_full=tb,
    )


def install_exception_logging(app: "FastAPI") -> None:
    """Регистрирует middleware (добавлять после SessionMiddleware)."""

    @app.middleware("http")
    async def _save_errors_middleware(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            persist_request_exception(request, exc)
            raise
