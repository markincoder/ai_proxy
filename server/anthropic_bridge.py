"""Anthropic Messages API → внутренний чат (OpenRouter через chat.completions). Для Claude Code и совместимых клиентов."""

from __future__ import annotations

import json
import secrets
from typing import Any, AsyncIterator

# Путь к /messages с телом Anthropic: model + max_tokens, без modelSlug (наш формат).
def is_anthropic_messages_request(raw: dict[str, Any]) -> bool:
    if raw.get("modelSlug") is not None:
        return False
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        return False
    if "max_tokens" not in raw:
        return False
    if raw.get("max_tokens") is None:
        return False
    return True


def _anthropic_content_to_openai_msg_content(content: Any) -> Any:
    """Anthropic message content → то, что ждёт OpenRouter в messages[].content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[dict[str, Any]] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                t = b.get("text")
                if isinstance(t, str):
                    out.append({"type": "text", "text": t})
            elif bt == "image":
                src = b.get("source") if isinstance(b.get("source"), dict) else {}
                u = src.get("url") if isinstance(src, dict) else None
                if isinstance(u, str) and u.startswith("data:"):
                    out.append({"type": "image_url", "image_url": {"url": u}})
            else:
                raise ValueError(
                    f"Неподдерживаемый блок контента Anthropic в bridge: {bt!r}. "
                    "Используйте текст или data-URL изображения."
                )
        if not out:
            raise ValueError("Пустой content в сообщении Anthropic")
        return out
    raise ValueError("message.content должен быть строкой или массивом блоков")


def anthropic_request_to_internal_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Тело POST …/messages (Anthropic) → dict для ChatJsonBody.model_validate.
    """
    model_slug = str(raw.get("model") or "").strip()
    if not model_slug:
        raise ValueError("model is required")

    max_raw = raw.get("max_tokens")
    if max_raw is None:
        raise ValueError("max_tokens is required")
    try:
        max_tokens = int(max_raw)
    except (TypeError, ValueError) as e:
        raise ValueError("max_tokens must be an integer") from e
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")

    stream = bool(raw.get("stream", False))

    messages_out: list[dict[str, Any]] = []

    sys_raw = raw.get("system")
    if sys_raw is not None:
        if isinstance(sys_raw, str) and sys_raw.strip():
            messages_out.append({"role": "system", "content": sys_raw.strip()})
        elif isinstance(sys_raw, list):
            parts: list[dict[str, Any]] = []
            for b in sys_raw:
                if isinstance(b, dict) and b.get("type") == "text":
                    t = b.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append({"type": "text", "text": t.strip()})
            if parts:
                messages_out.append({"role": "system", "content": parts})

    msgs = raw.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise ValueError("messages must be a non-empty array")

    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            raise ValueError(f"messages[{i}] must be an object")
        role = str(m.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            raise ValueError(f"messages[{i}].role must be user or assistant")
        content = _anthropic_content_to_openai_msg_content(m.get("content"))
        messages_out.append({"role": role, "content": content})

    temperature = raw.get("temperature")
    tout: dict[str, Any] = {
        "model_slug": model_slug,
        "messages": messages_out,
        "stream": stream,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        try:
            tout["temperature"] = float(temperature)
        except (TypeError, ValueError) as e:
            raise ValueError("temperature must be a number") from e
    return tout


def openai_completion_to_anthropic_message(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Ответ OpenAI chat.completions (один JSON) → тело Anthropic message."""
    msg_id = "msg_" + secrets.token_urlsafe(18).replace("-", "")[:24]
    choices = data.get("choices") or []
    text = ""
    stop_reason = "end_turn"
    if choices and isinstance(choices[0], dict):
        ch0 = choices[0]
        if isinstance(ch0.get("message"), dict):
            mc = ch0["message"].get("content")
            if isinstance(mc, str):
                text = mc
            elif isinstance(mc, list):
                for p in mc:
                    if isinstance(p, dict) and p.get("type") == "text":
                        t = p.get("text")
                        if isinstance(t, str):
                            text += t
        fr = ch0.get("finish_reason")
        if fr == "length":
            stop_reason = "max_tokens"
        elif fr == "stop":
            stop_reason = "end_turn"

    usage_in = data.get("usage") or {}
    in_tok = int(usage_in.get("prompt_tokens") or usage_in.get("input_tokens") or 0)
    out_tok = int(usage_in.get("completion_tokens") or usage_in.get("output_tokens") or 0)

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        },
    }


async def iter_openai_sse_to_anthropic_sse(
    lines_async: AsyncIterator[bytes],
    *,
    model: str,
    acc: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """
    Поток байтов OpenAI-style SSE (data: …) → Anthropic SSE (event: …).
    Не трогает кастомные event: billing в конце — их нужно отфильтровать в вызывающем коде или не добавлять.
    """
    msg_id = "msg_" + secrets.token_urlsafe(18).replace("-", "")[:24]
    line_carry = ""
    started = False
    block_open = False
    last_usage: dict[str, Any] | None = None
    any_delta = False

    def _emit(event: str, payload: dict[str, Any]) -> bytes:
        return (
            f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        )

    async for chunk in lines_async:
        line_carry += chunk.decode("utf-8", errors="replace")
        parts = line_carry.split("\n")
        line_carry = parts.pop() if parts else ""

        for line in parts:
            line = line.rstrip("\r")
            if not line.startswith("data:"):
                continue
            payload_s = line[5:].strip()
            if payload_s == "[DONE]":
                continue
            if not payload_s:
                continue
            try:
                j = json.loads(payload_s)
            except json.JSONDecodeError:
                continue

            if acc is not None:
                if j.get("id"):
                    acc["id"] = j["id"]
                if j.get("usage"):
                    acc["usage"] = j["usage"]

            if j.get("usage"):
                last_usage = j["usage"]
            if j.get("error"):
                if not started:
                    yield _emit(
                        "message_start",
                        {
                            "type": "message_start",
                            "message": {
                                "id": msg_id,
                                "type": "message",
                                "role": "assistant",
                                "model": model,
                                "content": [],
                                "stop_reason": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            },
                        },
                    )
                    started = True
                err = j.get("error")
                msg = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
                yield _emit("error", {"type": "error", "error": {"type": "api_error", "message": msg}})
                yield _emit("message_stop", {"type": "message_stop"})
                return

            choices = j.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece is None:
                continue
            if not isinstance(piece, str):
                piece = str(piece)
            if not piece:
                continue

            if not started:
                yield _emit(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": msg_id,
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [],
                            "stop_reason": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    },
                )
                started = True
            if not block_open:
                yield _emit(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
                block_open = True

            any_delta = True
            yield _emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )

    in_tok = 0
    out_tok = 0
    if isinstance(last_usage, dict):
        in_tok = int(last_usage.get("prompt_tokens") or last_usage.get("input_tokens") or 0)
        out_tok = int(last_usage.get("completion_tokens") or last_usage.get("output_tokens") or 0)

    if not started:
        yield _emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
                },
            },
        )
        started = True

    if block_open:
        yield _emit("content_block_stop", {"type": "content_block_stop", "index": 0})

    stop_reason = "end_turn"
    yield _emit(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": out_tok},
        },
    )
    yield _emit(
        "message_stop",
        {"type": "message_stop"},
    )
