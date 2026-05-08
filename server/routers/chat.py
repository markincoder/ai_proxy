import json
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..billing import compute_spend_rub, estimate_min_spend_rub
from ..database import SessionLocal
from ..deps import resolve_user_id
from ..models import AiModel
from ..openrouter import (
    OPENROUTER_URL,
    chat_completions,
    openrouter_async_client,
    openrouter_headers,
    transcribe_audio,
)
from ..services.spend import assert_balance_covers_estimate, record_spend

router = APIRouter(prefix="/api/v1", tags=["chat"])

_VOICE_PLACEHOLDER_CHARS = 4000


class Msg(BaseModel):
    role: str
    content: Any


class ChatJsonBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_slug: str = Field(alias="modelSlug")
    messages: list[Msg]
    stream: bool = True


def merge_usage_line(line: str, prev: dict[str, Any]) -> dict[str, Any]:
    t = line.strip()
    if not t.startswith("data:"):
        return prev
    payload = t[5:].strip()
    if payload == "[DONE]":
        return prev
    try:
        j = json.loads(payload)
        n = dict(prev)
        if j.get("id"):
            n["id"] = j["id"]
        if j.get("usage"):
            n["usage"] = j["usage"]
        return n
    except json.JSONDecodeError:
        return prev


def _sse_upstream_error(message: str) -> bytes:
    return (
        "data: "
        + json.dumps({"error": {"message": message}}, ensure_ascii=False)
        + "\n\n"
    ).encode("utf-8")


def _resolve_user_for_model(
    request: Request,
    db: Session,
    model: AiModel,
    messages_for_estimate: list[dict[str, Any]],
) -> str | None:
    """Бесплатные модели — без входа; платные — сессия и баланс ≥ оценки минимума."""
    if model.is_free:
        return resolve_user_id(request, db)
    uid = resolve_user_id(request, db)
    if not uid:
        raise HTTPException(
            status_code=401,
            detail="LOGIN_REQUIRED",
        )
    est = estimate_min_spend_rub(model, messages_for_estimate)
    assert_balance_covers_estimate(db, uid, est)
    return uid


@router.post("/messages")
async def post_messages(request: Request):
    ct = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ct:
        return await _handle_multipart(request)
    try:
        body = ChatJsonBody.model_validate(await request.json())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    msg_dicts = [m.model_dump() for m in body.messages]
    with SessionLocal() as db:
        model = (
            db.query(AiModel)
            .filter(AiModel.slug == body.model_slug, AiModel.is_active.is_(True))
            .first()
        )
        if not model:
            raise HTTPException(status_code=404, detail="Unknown or inactive model")
        user_id = _resolve_user_for_model(request, db, model, msg_dicts)
    return await _handle_chat_json(body, user_id, model)


async def _handle_multipart(request: Request) -> StreamingResponse | JSONResponse:
    form = await request.form()
    audio = form.get("audio")
    model_slug = str(form.get("modelSlug") or "")
    messages_raw = str(form.get("messages") or "[]")
    stream = str(form.get("stream") or "true").lower() != "false"

    try:
        msgs = json.loads(messages_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid messages JSON") from None

    with SessionLocal() as db:
        model = (
            db.query(AiModel)
            .filter(AiModel.slug == model_slug, AiModel.is_active.is_(True))
            .first()
        )
        if not model:
            raise HTTPException(status_code=404, detail="Unknown or inactive model")
        msgs_for_est = list(msgs) + [{"role": "user", "content": "…" * _VOICE_PLACEHOLDER_CHARS}]
        user_id = _resolve_user_for_model(request, db, model, msgs_for_est)

    if audio is None or not hasattr(audio, "read"):
        raise HTTPException(status_code=400, detail="audio file required")
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="audio file required")
    mime = getattr(audio, "content_type", None) or "audio/webm"

    try:
        tr = await transcribe_audio(content, "voice.webm", mime)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OpenRouter недоступен (сеть/прокси). Попробуйте OPENROUTER_HTTPX_TRUST_ENV=false. {exc}",
        ) from exc
    if tr.status_code >= 400:
        raise HTTPException(status_code=502, detail=tr.text)

    tr_json = tr.json()
    text = (tr_json.get("text") or "").strip()
    msgs.append({"role": "user", "content": text})
    body = ChatJsonBody(
        model_slug=model_slug,
        messages=[Msg.model_validate(m) for m in msgs],
        stream=stream,
    )
    return await _handle_chat_json(body, user_id, model)


async def _handle_chat_json(
    body: ChatJsonBody,
    user_id: str | None,
    model: AiModel,
) -> StreamingResponse | JSONResponse:
    payload: dict[str, Any] = {
        "model": body.model_slug,
        "messages": [m.model_dump() for m in body.messages],
        "stream": body.stream,
    }
    if model.supports_image_generation:
        payload["modalities"] = ["image", "text"]

    if not body.stream:
        try:
            upstream = await chat_completions(payload)
        except httpx.RequestError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "OpenRouter unavailable",
                    "detail": str(exc),
                },
            )
        if upstream.status_code >= 400:
            return JSONResponse(
                status_code=upstream.status_code,
                content={"error": "OpenRouter error", "detail": upstream.text},
            )
        data = upstream.json()
        usage = data.get("usage")
        cost = compute_spend_rub(model, usage)
        if user_id:
            with SessionLocal() as db:
                record_spend(
                    db,
                    user_id,
                    cost,
                    data.get("id"),
                    f"chat {body.model_slug}",
                )
        data["meta"] = {"costRub": str(cost), "modelSlug": body.model_slug}
        return JSONResponse(content=data)

    async def stream_with_billing() -> AsyncIterator[bytes]:
        line_carry = ""
        state: dict[str, Any] = {}
        try:
            async with openrouter_async_client() as client:
                async with client.stream(
                    "POST",
                    f"{OPENROUTER_URL}/chat/completions",
                    headers=openrouter_headers(True),
                    json=payload,
                ) as resp:
                    if resp.status_code >= 400:
                        err = await resp.aread()
                        text = err.decode("utf-8", errors="replace")
                        yield f"data: {text}\n\n".encode()
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                        line_carry += chunk.decode("utf-8", errors="replace")
                        parts = line_carry.split("\n")
                        line_carry = parts.pop() if parts else ""
                        for line in parts:
                            state = merge_usage_line(line, state)
                    if line_carry.strip():
                        state = merge_usage_line(line_carry, state)
        except httpx.RequestError as exc:
            yield _sse_upstream_error(
                "Нет соединения с OpenRouter (сеть, файрвол или прокси). "
                "Если используется корпоративный VPN/прокси, попробуйте в .env: "
                "OPENROUTER_HTTPX_TRUST_ENV=false. "
                f"Технически: {exc}"
            )
            return

        cost = compute_spend_rub(model, state.get("usage"))
        if user_id:
            with SessionLocal() as db:
                record_spend(
                    db,
                    user_id,
                    cost,
                    state.get("id"),
                    f"stream {body.model_slug}",
                )
        billing = json.dumps(
            {
                "costRub": str(cost),
                "openRouterRequestId": state.get("id"),
                "modelSlug": body.model_slug,
            },
            ensure_ascii=False,
        )
        yield f"event: billing\ndata: {billing}\n\n".encode("utf-8")

    return StreamingResponse(
        stream_with_billing(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )
