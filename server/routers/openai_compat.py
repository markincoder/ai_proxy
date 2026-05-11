"""OpenAI-совместимые эндпоинты (/v1/…) для IDE и клиентов (Cline, Kilo Code и т.п.)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..deps import get_db
from ..models import AiModel
from .chat import (
    ChatJsonBody,
    Msg,
    _handle_chat_json,
    _messages_for_estimate,
    _resolve_user_for_model,
)

router = APIRouter(prefix="/v1", tags=["openai-compat"])


def _openai_error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": "invalid_request_error",
            }
        },
    )


def _http_exception_to_openai(exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str):
        msg = detail
    elif isinstance(detail, dict):
        raw = detail.get("message")
        msg = raw if isinstance(raw, str) else json.dumps(detail, ensure_ascii=False)
    else:
        msg = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": msg,
                "type": "invalid_request_error",
            }
        },
    )


class OpenAiChatCompletionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(..., min_length=1, max_length=512)
    messages: list[Any]
    stream: bool = True


@router.post("/chat/completions")
async def openai_chat_completions(request: Request):
    try:
        data = await request.json()
    except Exception:
        return _openai_error(400, "Invalid JSON body")
    try:
        body = OpenAiChatCompletionBody.model_validate(data)
    except ValidationError as e:
        errs = e.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        return _openai_error(400, f"Validation error at {loc}: {first.get('msg', errs)}")

    slug = body.model.strip()
    if not slug:
        return _openai_error(400, "model must be a non-empty string")

    try:
        msg_objs = []
        for i, raw in enumerate(body.messages):
            if not isinstance(raw, dict):
                return _openai_error(400, f"messages[{i}] must be an object")
            msg_objs.append(Msg.model_validate(raw))
    except ValidationError as e:
        return _openai_error(400, str(e.errors()))

    chat_body = ChatJsonBody(model_slug=slug, messages=msg_objs, stream=body.stream)

    msg_dicts = [m.model_dump() for m in chat_body.messages]
    with SessionLocal() as db:
        model_row = (
            db.query(AiModel)
            .filter(AiModel.slug == slug, AiModel.is_active.is_(True))
            .first()
        )
        if not model_row:
            return _openai_error(
                404,
                f"Unknown model {slug!r}. Use GET /v1/models (or GET /api/models) for slug values from our catalog.",
            )
        msgs_est = _messages_for_estimate(msg_dicts)
        try:
            user_id = _resolve_user_for_model(
                request, db, model_row, msgs_est, raw_messages=msg_dicts
            )
        except HTTPException as exc:
            return _http_exception_to_openai(exc)

    return await _handle_chat_json(chat_body, user_id, model_row)


@router.get("/models")
def openai_list_models(db: Session = Depends(get_db)):
    rows = (
        db.query(AiModel)
        .filter(AiModel.is_active.is_(True))
        .order_by(AiModel.display_name.asc())
        .all()
    )
    return {
        "object": "list",
        "data": [
            {
                "id": m.slug,
                "object": "model",
                "created": 1_700_000_000,
                "owned_by": (
                    (m.provider or "openrouter").split("/")[0]
                    if "/" in (m.provider or "")
                    else (m.provider or "openrouter")
                ),
            }
            for m in rows
        ],
    }
