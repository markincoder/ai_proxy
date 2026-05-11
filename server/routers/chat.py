import base64
import json
from decimal import Decimal
from typing import Any, Annotated, AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..billing import compute_spend_rub, estimate_min_spend_rub
from ..database import SessionLocal
from ..deps import resolve_user_id
from ..models import AiModel
from ..openrouter import (
    chat_completions,
    is_matroska_non_webm_ebml,
    openrouter_async_client,
    openrouter_base_url,
    openrouter_headers,
    transcribe_audio,
)
from ..openai_audio_input import reencode_audio_bytes_to_wav
from ..pricing_rub import rub_price_ceil_2
from ..services.spend import assert_balance_covers_estimate, record_spend

router = APIRouter(prefix="/api/v1", tags=["chat"])

_VOICE_PLACEHOLDER_CHARS = 4000
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_DEFAULT_STT_SLUG = "openai/whisper-1"
_DEFAULT_TTS_VOICE = "alloy"
_ALLOWED_TTS_VOICES = frozenset(
    {"alloy", "echo", "fable", "onyx", "nova", "shimmer"},
)


def _normalize_tts_voice(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return _DEFAULT_TTS_VOICE
    v = str(raw).strip().lower()
    if v in _ALLOWED_TTS_VOICES:
        return v
    return _DEFAULT_TTS_VOICE


def _messages_contain_input_audio(msg_dicts: list[dict[str, Any]]) -> bool:
    for m in msg_dicts:
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "input_audio":
                    return True
    return False


def _messages_for_estimate(msg_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """input_audio не должен раздувать оценку баланса (огромный base64)."""
    out: list[dict[str, Any]] = []
    for m in msg_dicts:
        c = m.get("content")
        if not isinstance(c, list):
            out.append(m)
            continue
        new_parts: list[Any] = []
        for part in c:
            if isinstance(part, dict) and part.get("type") == "input_audio":
                new_parts.append(
                    {"type": "text", "text": "…" * _VOICE_PLACEHOLDER_CHARS}
                )
            else:
                new_parts.append(part)
        m2 = dict(m)
        m2["content"] = new_parts
        out.append(m2)
    return out


def _audio_format_from_mime(mime: str, filename: str) -> str:
    m = (mime or "").lower()
    fn = (filename or "").lower()
    if "webm" in m or fn.endswith(".webm"):
        return "webm"
    if "wav" in m or fn.endswith(".wav"):
        return "wav"
    if "mpeg" in m or "mp3" in m or fn.endswith(".mp3"):
        return "mp3"
    if "ogg" in m or fn.endswith(".ogg"):
        return "ogg"
    if "flac" in m or fn.endswith(".flac"):
        return "flac"
    if "mp4" in m or "m4a" in m or fn.endswith((".m4a", ".mp4")):
        return "mp4"
    return "wav"


def _estimate_transcribe_floor_rub(stt: AiModel) -> Decimal:
    """STT в БД — ориентир ₽/мин аудио; доля минуты как нижняя оценка одного запроса."""
    pm = Decimal(str(stt.input_price_per_mn or 0))
    if pm <= 0:
        return rub_price_ceil_2(Decimal("0.01"))
    base = max(pm / Decimal("6"), Decimal("0.01"))
    return rub_price_ceil_2(base)


def _pick_stt_model_for_voice_input(db: Session) -> AiModel | None:
    stt = (
        db.query(AiModel)
        .filter(
            AiModel.slug == _DEFAULT_STT_SLUG,
            AiModel.is_active.is_(True),
            AiModel.supports_transcription.is_(True),
        )
        .first()
    )
    if not stt:
        stt = (
            db.query(AiModel)
            .filter(
                AiModel.supports_transcription.is_(True),
                AiModel.is_active.is_(True),
            )
            .order_by(AiModel.input_price_per_mn.asc())
            .first()
        )
    return stt


def _openrouter_http_error_detail(resp: httpx.Response) -> str:
    """
    Текст ошибки OpenRouter для HTTPException(detail=...).
    Часто приходит только error.message «Provider returned 400» без причины — тогда добавляем сырой JSON.
    """
    raw = (resp.text or "").strip()
    try:
        j = resp.json()
    except (json.JSONDecodeError, ValueError):
        return raw or f"HTTP {resp.status_code}"

    if not isinstance(j, dict):
        return raw or f"HTTP {resp.status_code}"

    parts: list[str] = []
    err = j.get("error")
    if isinstance(err, dict):
        if err.get("message") is not None:
            parts.append(str(err["message"]))
        md = err.get("metadata")
        if isinstance(md, dict) and md:
            parts.append(json.dumps(md, ensure_ascii=False))
        for k in ("type", "code", "param"):
            if err.get(k) is not None:
                parts.append(f"{k}={err[k]!r}")
        if not parts:
            parts.append(json.dumps(err, ensure_ascii=False)[:1200])
    elif isinstance(err, str) and err:
        parts.append(err)

    detail = ": ".join(parts) if parts else ""
    if not detail:
        detail = raw or f"HTTP {resp.status_code}"

    # Малоинформативно — показываем весь ответ (OpenRouter иногда кладёт суть не в .error.message)
    short_generic = detail.strip() in ("Provider returned 400", "Bad Request", "Bad Gateway")
    if short_generic or (len(detail) < 90 and len(raw) > len(detail) + 10):
        tail = raw.replace("\n", " ").strip()
        if len(tail) > 900:
            tail = tail[:900] + "…"
        if tail:
            return f"{detail} — полный ответ провайдера: {tail}"
    return detail


def _parse_optional_stt_language(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if len(s) >= 2 and s[0].isalpha() and s[1].isalpha():
        return s[:2]
    return None


def _needs_voice_then_tts(model: AiModel, raw_msgs: list[dict[str, Any]]) -> bool:
    """GPT Audio в каталоге (speech + music_generation) при голосе пользователя.

    Для `_handle_chat_json`: сначала STT, затем один streamed completion с audio (см. ниже).
    Для `_resolve_user_for_model`: к оценке чата добавляется нижняя граница STT.
    """
    return (
        model.supports_speech
        and model.supports_music_generation
        and _messages_contain_input_audio(raw_msgs)
    )


async def _transcribe_and_bill(
    user_id: str | None,
    stt: AiModel,
    content: bytes,
    filename: str,
    mime: str,
    *,
    language: str | None = None,
) -> tuple[str, Decimal]:
    try:
        tr = await transcribe_audio(
            content, filename, mime, stt.slug, language=language
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Провайдер недоступен (сеть/прокси). Попробуйте OPENROUTER_HTTPX_TRUST_ENV=false. {exc}",
        ) from exc
    if tr.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=_openrouter_http_error_detail(tr),
        )

    try:
        tr_json = tr.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Некорректный ответ провайдера транскрипции: {exc}",
        ) from exc

    if isinstance(tr_json, dict) and tr_json.get("error"):
        raise HTTPException(
            status_code=502,
            detail=_openrouter_http_error_detail(tr),
        )

    text = (tr_json.get("text") or "").strip()
    usage = tr_json.get("usage")
    cost_dec = compute_spend_rub(stt, usage if isinstance(usage, dict) else None)
    if cost_dec <= 0 and user_id:
        cost_dec = _estimate_transcribe_floor_rub(stt)
    if user_id and cost_dec and cost_dec > 0:
        rid = tr_json.get("id")
        rid_s = str(rid) if rid is not None else None
        with SessionLocal() as db:
            record_spend(
                db,
                user_id,
                cost_dec,
                rid_s,
                f"transcribe {stt.slug}",
            )
    return text, cost_dec


class Msg(BaseModel):
    role: str
    content: Any


class ChatJsonBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_slug: str = Field(alias="modelSlug")
    messages: list[Msg]
    stream: bool = True
    tts_voice: Annotated[str | None, Field(default=None, alias="ttsVoice")]
    temperature: float | None = Field(default=None, ge=0, le=4)
    max_tokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("max_tokens", "maxTokens"),
        ge=1,
    )


async def _gpt_audio_voice_turn_stt_then_text_body(
    body: ChatJsonBody,
    user_id: str | None,
) -> ChatJsonBody:
    """Для GPT Audio через OpenRouter: STT из input_audio → текст в чат → один поток с TTS модели."""
    msgs_dump = [m.model_dump(mode="python") for m in body.messages]

    idx: int | None = None
    audio_fmt = "wav"
    audio_bytes: bytes | None = None
    text_hints: list[str] = []

    for i in range(len(msgs_dump) - 1, -1, -1):
        m = msgs_dump[i]
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        found_audio = False
        hints: list[str] = []
        for part in c:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                txt = part.get("text")
                if isinstance(txt, str) and txt.strip():
                    hints.append(txt.strip())
            if part.get("type") == "input_audio":
                ia = part.get("input_audio")
                if isinstance(ia, dict):
                    b64 = ia.get("data")
                    if isinstance(b64, str):
                        try:
                            audio_bytes = base64.b64decode(b64, validate=False)
                        except (ValueError, TypeError):
                            raise HTTPException(
                                status_code=400,
                                detail="Некорректный base64 в input_audio",
                            ) from None
                    audio_fmt = str(ia.get("format") or "wav").lower()
                    found_audio = True
        if found_audio and audio_bytes:
            idx = i
            text_hints = hints
            break

    if idx is None or not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="В сообщении пользователя отсутствует input_audio для голосового ввода.",
        )

    mime_map = {
        "wav": "audio/wav",
        "wave": "audio/wav",
        "mp3": "audio/mpeg",
        "mpeg": "audio/mpeg",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "mp4": "audio/mp4",
        "m4a": "audio/mp4",
    }
    mime = mime_map.get(audio_fmt, "application/octet-stream")
    safe_ext = audio_fmt if audio_fmt in mime_map else "bin"
    fname = f"voice.{safe_ext}"

    with SessionLocal() as db:
        stt = _pick_stt_model_for_voice_input(db)
        if not stt:
            raise HTTPException(
                status_code=503,
                detail="Нет активной модели STT для голосового ввода GPT Audio.",
            )
        est = _estimate_transcribe_floor_rub(stt)
        if est > 0:
            if not user_id:
                raise HTTPException(status_code=401, detail="LOGIN_REQUIRED")
            assert_balance_covers_estimate(db, user_id, est)

    transcript, _ = await _transcribe_and_bill(
        user_id, stt, audio_bytes, fname, mime, language=None
    )
    transcript = (transcript or "").strip()
    if not transcript:
        raise HTTPException(
            status_code=400,
            detail="Не удалось распознать речь; повторите запись или отправьте текстом.",
        )

    hint_join = "\n".join(text_hints).strip()
    combined = (f"{hint_join}\n\n{transcript}" if hint_join else transcript).strip()

    new_msgs_dump = list(msgs_dump)
    new_msgs_dump[idx] = {
        "role": "user",
        "content": [{"type": "text", "text": combined}],
    }

    new_body_dict = body.model_dump(mode="python")
    new_body_dict["messages"] = new_msgs_dump
    return ChatJsonBody.model_validate(new_body_dict)


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
    *,
    raw_messages: list[dict[str, Any]] | None = None,
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
    if raw_messages is not None and _needs_voice_then_tts(model, raw_messages):
        stt = _pick_stt_model_for_voice_input(db)
        if stt is not None:
            est = est + _estimate_transcribe_floor_rub(stt)
    assert_balance_covers_estimate(db, uid, est)
    return uid


@router.post("/transcribe")
async def post_transcribe_only(request: Request) -> JSONResponse:
    """Распознавание аудио в текст без вызова чата."""
    form = await request.form()
    audio = form.get("audio")
    stt_slug = str(form.get("sttModel") or _DEFAULT_STT_SLUG).strip() or _DEFAULT_STT_SLUG
    if audio is None or not hasattr(audio, "read"):
        raise HTTPException(status_code=400, detail="audio file required")
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="audio file required")
    if len(content) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio file too large")
    if is_matroska_non_webm_ebml(content):
        raise HTTPException(
            status_code=400,
            detail="Формат не поддерживается: похоже на MKV/Matroska. Сохраните как WebM, WAV или MP3.",
        )
    mime = getattr(audio, "content_type", None) or "application/octet-stream"
    raw_name = getattr(audio, "filename", None) or "audio.bin"
    safe_name = str(raw_name).replace("\\", "/").split("/")[-1][:220] or "audio.bin"
    lang_opt = _parse_optional_stt_language(str(form.get("language") or ""))

    with SessionLocal() as db:
        stt = (
            db.query(AiModel)
            .filter(AiModel.slug == stt_slug, AiModel.is_active.is_(True))
            .first()
        )
        if not stt or not stt.supports_transcription:
            raise HTTPException(status_code=404, detail="Unknown or inactive STT model")
        est = _estimate_transcribe_floor_rub(stt)
        if est > 0:
            uid = resolve_user_id(request, db)
            if not uid:
                raise HTTPException(
                    status_code=401,
                    detail="LOGIN_REQUIRED",
                )
            assert_balance_covers_estimate(db, uid, est)
            user_id = uid
        else:
            user_id = resolve_user_id(request, db)

    text, cost_dec = await _transcribe_and_bill(
        user_id, stt, content, safe_name, mime, language=lang_opt
    )
    out: dict[str, Any] = {"text": text}
    if user_id is not None:
        out["costRub"] = str(cost_dec)
    return JSONResponse(content=out)


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
        if not model.supports_chat:
            raise HTTPException(
                status_code=400,
                detail="Модель только для эмбеддингов; используйте POST /api/v1/embeddings.",
            )
        if _messages_contain_input_audio(msg_dicts) and not model.supports_speech:
            raise HTTPException(
                status_code=400,
                detail="Аудио на входе доступно только для моделей с поддержкой речи в чате.",
            )
        msgs_est = _messages_for_estimate(msg_dicts)
        user_id = _resolve_user_for_model(
            request, db, model, msgs_est, raw_messages=msg_dicts
        )
    return await _handle_chat_json(body, user_id, model)


async def _handle_multipart(request: Request) -> StreamingResponse | JSONResponse:
    form = await request.form()
    audio = form.get("audio")
    model_slug = str(form.get("modelSlug") or "")
    voice_hint = str(form.get("voiceHint") or "").strip() or "Ответь на голосовое сообщение."
    messages_raw = str(form.get("messages") or "[]")
    stream = str(form.get("stream") or "true").lower() != "false"

    try:
        msgs = json.loads(messages_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid messages JSON") from None

    if audio is None or not hasattr(audio, "read"):
        raise HTTPException(status_code=400, detail="audio file required")
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="audio file required")
    if len(content) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio file too large")

    mime = getattr(audio, "content_type", None) or "audio/webm"
    raw_name = getattr(audio, "filename", None) or "voice.webm"
    safe_name = str(raw_name).replace("\\", "/").split("/")[-1][:220] or "voice.webm"
    fmt = _audio_format_from_mime(mime, safe_name)

    with SessionLocal() as db:
        model = (
            db.query(AiModel)
            .filter(AiModel.slug == model_slug, AiModel.is_active.is_(True))
            .first()
        )
        if not model:
            raise HTTPException(status_code=404, detail="Unknown or inactive model")
        if not model.supports_chat:
            raise HTTPException(
                status_code=400,
                detail="Модель только для эмбеддингов; используйте POST /api/v1/embeddings.",
            )
        if not model.supports_speech:
            raise HTTPException(
                status_code=400,
                detail="Загрузка аудио в чате доступна только для моделей с поддержкой речи в чате.",
            )
        if fmt not in ("wav", "mp3"):
            try:
                content = reencode_audio_bytes_to_wav(content, fmt)
                fmt = "wav"
            except FileNotFoundError:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Запись из браузера обычно в формате WebM (Opus); провайдеры вроде Mistral принимают только WAV или MP3. "
                        "Установите ffmpeg (https://ffmpeg.org) в PATH на сервере или добавьте его в образ; "
                        "либо отправьте готовый .wav или .mp3."
                    ),
                ) from None
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Не удалось перекодировать аудио в WAV (ffmpeg): {exc}",
                ) from exc
        user_content = [
            {"type": "text", "text": voice_hint},
            {
                "type": "input_audio",
                "input_audio": {"data": base64.b64encode(content).decode("ascii"), "format": fmt},
            },
        ]
        msgs.append({"role": "user", "content": user_content})
        msgs_est = _messages_for_estimate(msgs)
        user_id = _resolve_user_for_model(request, db, model, msgs_est, raw_messages=msgs)

    body = ChatJsonBody(
        model_slug=model_slug,
        messages=[Msg.model_validate(m) for m in msgs],
        stream=stream,
        tts_voice=str(form.get("ttsVoice") or "").strip() or None,
    )
    return await _handle_chat_json(body, user_id, model)


async def _handle_chat_json(
    body: ChatJsonBody,
    user_id: str | None,
    model: AiModel,
) -> StreamingResponse | JSONResponse:
    raw_msgs = [m.model_dump() for m in body.messages]
    if _messages_contain_input_audio(raw_msgs) and not model.supports_speech:
        raise HTTPException(
            status_code=400,
            detail="Аудио на входе доступно только для моделей с поддержкой речи в чате.",
        )
    if _needs_voice_then_tts(model, raw_msgs):
        # mini / OpenRouter: один streamed completion с native input_audio часто даёт отказ;
        # сначала Whisper (STT), затем текст в чат → тот же аудиовыход через modalities.
        body = await _gpt_audio_voice_turn_stt_then_text_body(body, user_id)
        raw_msgs = [m.model_dump() for m in body.messages]
    has_user_input_audio = _messages_contain_input_audio(raw_msgs)
    tts_v = _normalize_tts_voice(body.tts_voice)
    payload: dict[str, Any] = {
        "model": body.model_slug,
        "messages": [m.model_dump() for m in body.messages],
        "stream": body.stream,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature
    if body.max_tokens is not None:
        payload["max_tokens"] = body.max_tokens
    if model.supports_image_generation:
        payload["modalities"] = ["image", "text"]
    elif model.supports_speech and model.supports_music_generation:
        # GPT Audio: после голоса в multipart вопрос уже преобразован в текст (STT) — как обычный чат.
        if not has_user_input_audio:
            payload["modalities"] = ["text", "audio"]
            # При stream=true OpenAI принимает только audio.format=pcm16 (не wav).
            payload["audio"] = {
                "voice": tts_v,
                "format": "pcm16" if body.stream else "wav",
            }
    elif model.supports_music_generation and "lyria" in (model.slug or "").lower():
        # Google Lyria: без modalities в запросе провайдер может вернуть только текст;
        # аудио приходит в delta.audio (stream), формат см. OpenRouter.
        payload["modalities"] = ["text", "audio"]
        payload["audio"] = {
            "voice": "alloy",
            "format": "pcm16" if body.stream else "wav",
        }

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
                    f"{openrouter_base_url()}/chat/completions",
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
