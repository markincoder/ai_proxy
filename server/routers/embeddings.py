"""Эмбеддинги: POST /api/v1/embeddings + биллинг по usage."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..billing import compute_embedding_spend_rub, estimate_embedding_spend_rub
from ..database import SessionLocal
from ..deps import resolve_user_id
from ..error_logging import log_upstream_request_failure
from ..models import AiModel
from ..openrouter import embeddings_create
from ..services.spend import assert_balance_covers_estimate, record_spend

router = APIRouter(prefix="/api/v1", tags=["embeddings"])

_MAX_EMBED_CHARS_PER_ITEM = 400_000


def _openai_http_error_detail(body_text: str) -> str:
    t = body_text.strip()
    if len(t) > 800:
        return t[:800] + "…"
    return t or "(пустое тело ошибки)"


def _embedding_input_token_estimate(inp: Any) -> int:
    """Грубая оценка токенов до запроса (≈ символы/4)."""
    if isinstance(inp, str):
        if len(inp) > _MAX_EMBED_CHARS_PER_ITEM:
            raise HTTPException(status_code=400, detail="input string too large")
        return max(len(inp) // 4, 1)
    if isinstance(inp, list):
        if len(inp) > 2048:
            raise HTTPException(status_code=400, detail="too many items in input array")
        total = 0
        for item in inp:
            if not isinstance(item, str):
                raise HTTPException(
                    status_code=400,
                    detail="input array must contain only strings",
                )
            if len(item) > _MAX_EMBED_CHARS_PER_ITEM:
                raise HTTPException(status_code=400, detail="input item too large")
            total += max(len(item) // 4, 1)
        return max(total, 1)
    raise HTTPException(
        status_code=400,
        detail="input must be a non-empty string or array of strings",
    )


async def dispatch_embeddings(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
        )
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Expected JSON object",
                    "type": "invalid_request_error",
                },
            },
        )

    slug = str(raw.get("model") or "").strip()
    if not slug:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "model is required", "type": "invalid_request_error"}},
        )
    inp = raw.get("input")
    if inp is None:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "input is required", "type": "invalid_request_error"}},
        )

    # Проксируем OpenAI-совместимое тело (encoding_format, dimensions, user …)
    upstream: dict[str, Any] = {k: v for k, v in raw.items() if v is not None}
    upstream["model"] = slug

    tok_est = _embedding_input_token_estimate(inp)

    with SessionLocal() as db:
        model_row = (
            db.query(AiModel)
            .filter(AiModel.slug == slug, AiModel.is_active.is_(True))
            .first()
        )
        if not model_row:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "message": f"Unknown or inactive model: {slug!r}",
                        "type": "invalid_request_error",
                    },
                },
            )
        if not model_row.supports_embeddings:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "This model supports embeddings via POST /api/v1/embeddings or POST /v1/embeddings. Pick a slug with supportsEmbeddings from GET /api/models.",
                        "type": "invalid_request_error",
                    },
                },
            )
        uid = resolve_user_id(request, db)
        if not uid:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {"message": "Unauthorized", "type": "invalid_request_error"},
                },
            )
        est = estimate_embedding_spend_rub(model_row, tok_est)
        assert_balance_covers_estimate(db, uid, est)

    try:
        resp = await embeddings_create(upstream)
    except httpx.RequestError as exc:
        log_upstream_request_failure("embeddings.create", exc)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": (
                        "Сейчас нет связи с провайдером модели. Повторите запрос позже. "
                        "Если ошибка повторяется, напишите в поддержку — раздел «Контакты»."
                    ),
                    "type": "api_connection_error",
                },
            },
        )

    txt = resp.text
    if resp.status_code >= 400:
        return JSONResponse(
            status_code=resp.status_code,
            content={
                "error": {
                    "message": _openai_http_error_detail(txt),
                    "type": "invalid_request_error",
                },
            },
        )

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": "Некорректный ответ провайдера эмбеддингов (не JSON).",
                    "type": "invalid_request_error",
                },
            },
        )

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    rid = payload.get("id") if isinstance(payload.get("id"), str) else None
    cost = compute_embedding_spend_rub(model_row, usage)

    out = dict(payload)
    out["meta"] = {
        "costRub": str(cost),
        "modelSlug": slug,
        "openRouterRequestId": rid,
    }
    with SessionLocal() as db:
        record_spend(db, uid, cost, rid, f"embedding {slug}")
    return JSONResponse(content=out)


@router.post("/embeddings")
async def post_embeddings(request: Request):
    return await dispatch_embeddings(request)
