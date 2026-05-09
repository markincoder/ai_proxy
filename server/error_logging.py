"""Сохранение неперехваченных исключений в БД (см. ErrorLog)."""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from .database import SessionLocal
from .models import ErrorLog

if TYPE_CHECKING:
    from fastapi import FastAPI

_LOG = logging.getLogger(__name__)
_TB_MAX = 100_000


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


def persist_request_exception(request: Request, exc: BaseException) -> None:
    try:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(tb) > _TB_MAX:
            tb = tb[-_TB_MAX:]
        msg = str(exc) if exc else ""
        if len(msg) > 4000:
            msg = msg[:4000] + "…"
        ctx = _build_context(request)
        with SessionLocal() as db:
            db.add(
                ErrorLog(
                    id=str(uuid.uuid4()),
                    context=ctx,
                    error_type=type(exc).__name__,
                    message=msg or None,
                    traceback=tb,
                )
            )
            db.commit()
    except Exception as inner:
        _LOG.warning("Could not persist ErrorLog: %s", inner, exc_info=True)


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
