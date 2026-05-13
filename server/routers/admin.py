import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import engine, get_default_model_specs, run_vacuum_and_analyze
from ..deps import get_db, require_admin
from ..models import (
    AiModel,
    ChatThread,
    ErrorLog,
    NewsPost,
    SiteBanner,
    SitePricingFactors,
    SupportMessage,
    SupportTicket,
    User,
)
from ..openrouter_price_sync import sync_chat_model_prices_to_db
from ..services.models_catalog_cache import invalidate_public_models_cache
from ..pricing_factors import (
    effective_factors_from_session,
    environment_usd_rub_markup,
    pricing_factors_source_from_session,
)


def _support_preview(text: str, limit: int = 220) -> str:
    t = (text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"

router = APIRouter(prefix="/api/admin", tags=["admin"])

_NEWS_MEDIA_PREFIX = "/media/news/"
_NEWS_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
_NEWS_UPLOAD_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

_ADMIN_DISPLAY_MONEY_Q = Decimal("0.01")


def _admin_display_money(val: Decimal) -> str:
    """Для UI админки: две десятичные цифры (₽, курс, коэф.)."""
    return str(Decimal(str(val)).quantize(_ADMIN_DISPLAY_MONEY_Q, rounding=ROUND_HALF_UP))


def _delete_news_upload_file_if_ours(image_url: Optional[str]) -> None:
    if not image_url:
        return
    raw = image_url.strip()
    if not raw.startswith(_NEWS_MEDIA_PREFIX):
        return
    base = raw[len(_NEWS_MEDIA_PREFIX) :].lstrip("/")
    if not base or "/" in base or "\\" in base or ".." in base:
        return
    root = get_settings().news_upload_path.resolve()
    path = (root / base).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return
    if path.is_file():
        path.unlink()


@router.post("/news/upload-image")
async def admin_upload_news_image(
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
):
    """Сохраняет картинку в каталог из NEWS_UPLOAD_DIR; публичный URL — /media/news/<имя>."""
    header_ct = (file.content_type or "").split(";")[0].strip().lower()
    if header_ct == "image/jpg":
        header_ct = "image/jpeg"
    ext = _NEWS_UPLOAD_TYPES.get(header_ct)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail="Допустимы только изображения: JPEG, PNG, GIF, WebP",
        )
    dest_dir = get_settings().news_upload_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = dest_dir / name
    total = 0
    try:
        with path.open("wb") as out:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _NEWS_UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=400, detail="Файл больше 5 МБ")
                out.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except OSError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл: {e}") from e
    url = f"{_NEWS_MEDIA_PREFIX}{name}"
    return {"url": url}


def _serialize_model(m: AiModel) -> dict:
    return {
        "id": m.id,
        "slug": m.slug,
        "displayName": m.display_name,
        "provider": m.provider,
        "inputPricePerMn": _admin_display_money(m.input_price_per_mn),
        "outputPricePerMn": _admin_display_money(m.output_price_per_mn),
        "fixedPrice": _admin_display_money(m.fixed_price) if m.fixed_price is not None else None,
        "isActive": m.is_active,
        "supportsVision": m.supports_vision,
        "supportsImageGeneration": m.supports_image_generation,
        "supportsMusicGeneration": m.supports_music_generation,
        "supportsVideoGeneration": m.supports_video_generation,
        "supportsSpeech": m.supports_speech,
        "supportsTranscription": m.supports_transcription,
        "supportsCoding": m.supports_coding,
        "supportsEmbeddings": m.supports_embeddings,
        "supportsChat": m.supports_chat,
        "isFree": m.is_free,
        "descriptionRu": m.description_ru,
        "pricingNoteRu": m.pricing_note_ru,
    }


@router.get("/users")
def admin_list_users(
    q: Optional[str] = None,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if q is not None and (t := q.strip()):
        pat = f"%{t}%"
        query = query.filter(
            or_(
                User.id.ilike(pat),
                User.username.ilike(pat),
                User.vk_user_id.ilike(pat),
                User.yandex_user_id.ilike(pat),
            )
        )
    rows = query.order_by(User.created_at.desc()).limit(500).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "balance": _admin_display_money(u.balance),
            "vkUserId": u.vk_user_id,
            "yandexUserId": u.yandex_user_id,
            "isAdmin": u.is_admin == 1,
            "isBlocked": u.is_blocked == 1,
            "createdAt": (u.created_at.isoformat() + "Z") if u.created_at else None,
            "lastLoginAt": (u.last_login_at.isoformat() + "Z") if u.last_login_at else None,
        }
        for u in rows
    ]


@router.get("/models")
def admin_list_models(_: str = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(AiModel).order_by(AiModel.display_name.asc()).all()
    return [_serialize_model(m) for m in rows]


class AdminModelPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: Optional[str] = Field(None, alias="displayName")
    provider: Optional[str] = None
    input_price_per_mn: Optional[Decimal] = Field(None, alias="inputPricePerMn")
    output_price_per_mn: Optional[Decimal] = Field(None, alias="outputPricePerMn")
    fixed_price: Optional[Decimal] = Field(None, alias="fixedPrice")
    is_active: Optional[bool] = Field(None, alias="isActive")
    supports_vision: Optional[bool] = Field(None, alias="supportsVision")
    supports_image_generation: Optional[bool] = Field(None, alias="supportsImageGeneration")
    supports_music_generation: Optional[bool] = Field(None, alias="supportsMusicGeneration")
    supports_video_generation: Optional[bool] = Field(None, alias="supportsVideoGeneration")
    supports_speech: Optional[bool] = Field(None, alias="supportsSpeech")
    supports_transcription: Optional[bool] = Field(None, alias="supportsTranscription")
    supports_coding: Optional[bool] = Field(None, alias="supportsCoding")
    supports_embeddings: Optional[bool] = Field(None, alias="supportsEmbeddings")
    supports_chat: Optional[bool] = Field(None, alias="supportsChat")
    is_free: Optional[bool] = Field(None, alias="isFree")
    description_ru: Optional[str] = Field(None, alias="descriptionRu")
    pricing_note_ru: Optional[str] = Field(None, alias="pricingNoteRu")


@router.patch("/models/{model_id}")
def admin_patch_model(
    model_id: str,
    body: AdminModelPatch,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(AiModel).filter(AiModel.id == model_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        return _serialize_model(row)
    for key, val in data.items():
        setattr(row, key, val)
    db.commit()
    db.refresh(row)
    invalidate_public_models_cache()
    return _serialize_model(row)


@router.get("/pricing-factors")
def admin_get_pricing_factors(_: str = Depends(require_admin), db: Session = Depends(get_db)):
    env_r, env_m = environment_usd_rub_markup()
    eff_r, eff_m = effective_factors_from_session(db)
    return {
        "usdRub": _admin_display_money(eff_r),
        "markupMult": _admin_display_money(eff_m),
        "source": pricing_factors_source_from_session(db),
        "envUsdRub": _admin_display_money(env_r),
        "envMarkupMult": _admin_display_money(env_m),
    }


class AdminPricingFactorsApply(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    usd_rub: Decimal = Field(..., alias="usdRub", gt=0)
    markup_mult: Decimal = Field(..., alias="markupMult", gt=0)


@router.post("/pricing-factors/apply")
def admin_apply_pricing_factors(
    body: AdminPricingFactorsApply,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Пересчёт: GET OpenRouter → ₽ для slug, где в API есть актуальные цены.
    Slug, которых **нет** в ответе `/models`, переводятся на новую наценку умножением хранимых ₽ на то же отношение;
    модели только эмбеддингов — тем же множителем. Сохраняются курс и коэффициент для USD-биллинга.
    """
    new_r = body.usd_rub
    new_m = body.markup_mult

    old_r, old_m = effective_factors_from_session(db)
    old_prod = old_r * old_m
    new_prod = new_r * new_m
    ratio_emb = Decimal("1")
    if old_prod > 0:
        ratio_emb = new_prod / old_prod

    try:
        sync_result = sync_chat_model_prices_to_db(db, usd_rub=new_r, mult=new_m, copy_card_texts=True)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось получить каталог OpenRouter (GET /models): {e}",
        ) from e

    synced_from_or = frozenset(sync_result.get("pricingSlugsUpdatedFromOpenRouter") or [])

    emb_rescaled = 0
    chat_rescaled = 0
    if ratio_emb != 1:
        q6 = Decimal("0.000001")
        q4 = Decimal("0.01")
        emb_rows = (
            db.query(AiModel)
            .filter(
                AiModel.supports_embeddings.is_(True),
                AiModel.supports_chat.is_(False),
                AiModel.is_free.is_(False),
            )
            .all()
        )
        for m in emb_rows:
            m.input_price_per_mn = (m.input_price_per_mn * ratio_emb).quantize(q6, rounding=ROUND_HALF_UP)
            m.output_price_per_mn = (m.output_price_per_mn * ratio_emb).quantize(q6, rounding=ROUND_HALF_UP)
            if m.fixed_price is not None:
                m.fixed_price = (m.fixed_price * ratio_emb).quantize(q4, rounding=ROUND_HALF_UP)
            emb_rescaled += 1

        for spec in get_default_model_specs():
            if spec.get("is_free"):
                continue
            slug = str(spec["slug"])
            if slug in synced_from_or:
                continue
            row = db.query(AiModel).filter(AiModel.slug == slug).first()
            if row is None or row.is_free:
                continue
            row.input_price_per_mn = (row.input_price_per_mn * ratio_emb).quantize(q6, rounding=ROUND_HALF_UP)
            row.output_price_per_mn = (row.output_price_per_mn * ratio_emb).quantize(q6, rounding=ROUND_HALF_UP)
            if row.fixed_price is not None:
                row.fixed_price = (row.fixed_price * ratio_emb).quantize(q4, rounding=ROUND_HALF_UP)
            chat_rescaled += 1

    pf = db.query(SitePricingFactors).filter(SitePricingFactors.id == 1).first()
    if pf is None:
        db.add(SitePricingFactors(id=1, usd_rub=new_r, markup_mult=new_m))
    else:
        pf.usd_rub = new_r
        pf.markup_mult = new_m
    db.commit()
    invalidate_public_models_cache()
    out: dict[str, Any] = {
        "ok": True,
        "usdRub": _admin_display_money(new_r),
        "markupMult": _admin_display_money(new_m),
        "embeddingsPricingRescaled": emb_rescaled,
        "chatCatalogPricingRescaled": chat_rescaled,
        "embeddingsScaleRatio": str(ratio_emb),
        **sync_result,
    }
    return out


@router.get("/catalog/openrouter-check")
def admin_catalog_openrouter_check(_: str = Depends(require_admin)):
    """
    Сводка: локальный чат-каталог и эмбеддинги против списка ``id`` в GET …/models OpenRouter.

    Флаг catalogLooksAligned учитывает только чат: именно по этим id синхронизируются цены из полей pricing в JSON.
    Модели только для эмбеддингов там часто отсутствуют; пояснение см. в embeddingsCatalog.noteRu ответа.
    Исключения: опциональный OPENROUTER_FREE_ROUTER_SLUG, локальные ориентиры видео/STT/Lyria.
    """
    try:
        from ..openrouter_catalog_check import check_catalog_vs_openrouter

        return check_catalog_vs_openrouter()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Не удалось запросить каталог OpenRouter: {e}") from e


class AdminUserPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    blocked: Optional[bool] = None


@router.patch("/users/{user_id}")
def admin_patch_user(
    user_id: str,
    body: AdminUserPatch,
    admin_uid: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if body.blocked is None:
        raise HTTPException(status_code=400, detail="blocked required")
    row = db.query(User).filter(User.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if body.blocked and admin_uid == user_id:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")
    row.is_blocked = 1 if body.blocked else 0
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "isBlocked": row.is_blocked == 1,
    }


@router.get("/users/{user_id}/threads")
def admin_user_threads(
    user_id: str,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    rows = (
        db.query(ChatThread)
        .filter(ChatThread.user_id == user_id)
        .order_by(ChatThread.updated_at.desc())
        .limit(200)
        .all()
    )
    out: list[dict[str, Any]] = []
    for t in rows:
        mc = 0
        if isinstance(t.messages, list):
            mc = len(t.messages)
        out.append(
            {
                "id": t.id,
                "title": t.title,
                "modelSlug": t.model_slug,
                "messageCount": mc,
                "createdAt": (t.created_at.isoformat() + "Z") if t.created_at else None,
                "updatedAt": (t.updated_at.isoformat() + "Z") if t.updated_at else None,
            }
        )
    return out


@router.get("/threads/{thread_id}")
def admin_thread_detail(
    thread_id: str,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {
        "id": t.id,
        "userId": t.user_id,
        "title": t.title,
        "modelSlug": t.model_slug,
        "messages": t.messages,
        "createdAt": (t.created_at.isoformat() + "Z") if t.created_at else None,
        "updatedAt": (t.updated_at.isoformat() + "Z") if t.updated_at else None,
    }


def _serialize_site_banner(row: SiteBanner) -> dict[str, Any]:
    return {
        "isActive": row.is_active,
        "message": row.message or "",
        "scheduleText": row.schedule_text or "",
    }


class SiteBannerPut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_active: bool = Field(..., alias="isActive")
    message: str = ""
    schedule_text: str = Field("", alias="scheduleText")


class PurgeStaleThreadsBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    months: Literal[1, 3, 6]


class DatabaseMaintainBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    clear_error_logs: bool = Field(False, alias="clearErrorLogs")
    purge_stale_months: Optional[Literal[1, 3, 6]] = Field(None, alias="purgeStaleMonths")
    vacuum: bool = Field(True, alias="vacuum")
    analyze: bool = Field(True, alias="analyze")


_ALLOWED_STALE_MONTHS = frozenset({1, 3, 6})
_STALE_INACTIVITY_DAYS = {1: 30, 3: 90, 6: 180}


def _stale_thread_cutoff_utc(months: int) -> datetime:
    return datetime.utcnow() - timedelta(days=_STALE_INACTIVITY_DAYS[months])


@router.get("/banner")
def admin_get_banner(_: str = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SiteBanner).filter(SiteBanner.id == 1).first()
    if not row:
        return {"isActive": False, "message": "", "scheduleText": ""}
    return _serialize_site_banner(row)


@router.put("/banner")
def admin_put_banner(
    body: SiteBannerPut,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(SiteBanner).filter(SiteBanner.id == 1).first()
    if not row:
        row = SiteBanner(id=1, is_active=False)
        db.add(row)
    row.is_active = body.is_active
    row.message = (body.message or "").strip() or None
    row.schedule_text = (body.schedule_text or "").strip() or None
    db.commit()
    db.refresh(row)
    return _serialize_site_banner(row)


@router.get("/error-logs")
def admin_error_logs(
    limit: int = Query(100, ge=1, le=500),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ErrorLog)
        .order_by(ErrorLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "createdAt": (r.created_at.isoformat() + "Z") if r.created_at else None,
            "context": r.context,
            "errorType": r.error_type,
            "message": r.message,
            "traceback": r.traceback,
        }
        for r in rows
    ]


@router.delete("/error-logs")
def admin_error_logs_clear(
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    n = db.query(ErrorLog).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": n}


@router.get("/chat-threads/stale-preview")
def admin_chat_threads_stale_preview(
    months: int = Query(..., description="1, 3 или 6"),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if months not in _ALLOWED_STALE_MONTHS:
        raise HTTPException(status_code=400, detail="months must be 1, 3 or 6")
    cutoff = _stale_thread_cutoff_utc(months)
    n = db.query(ChatThread).filter(ChatThread.updated_at < cutoff).count()
    return {
        "months": months,
        "cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cutoffHuman": cutoff.strftime("%Y-%m-%d %H:%M UTC"),
        "daysInactiveAtLeast": _STALE_INACTIVITY_DAYS[months],
        "count": n,
    }


@router.post("/chat-threads/purge-stale")
def admin_chat_threads_purge_stale(
    body: PurgeStaleThreadsBody,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cutoff = _stale_thread_cutoff_utc(body.months)
    n = db.query(ChatThread).filter(ChatThread.updated_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return {
        "ok": True,
        "deleted": n,
        "months": body.months,
        "cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.post("/database/maintain")
def admin_database_maintain(
    body: DatabaseMaintainBody,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if (
        not body.clear_error_logs
        and body.purge_stale_months is None
        and not body.vacuum
        and not body.analyze
    ):
        raise HTTPException(
            status_code=400,
            detail="Выберите хотя бы одно действие: очистка логов, удаление чатов, VACUUM или ANALYZE",
        )

    deleted_error_logs = 0
    deleted_chat_threads = 0

    if body.clear_error_logs:
        deleted_error_logs = db.query(ErrorLog).delete(synchronize_session=False)

    if body.purge_stale_months is not None:
        cutoff = _stale_thread_cutoff_utc(body.purge_stale_months)
        deleted_chat_threads = (
            db.query(ChatThread).filter(ChatThread.updated_at < cutoff).delete(synchronize_session=False)
        )

    db.commit()

    extra: dict[str, Any] = {}
    if body.vacuum or body.analyze:
        extra = run_vacuum_and_analyze(engine, vacuum=body.vacuum, analyze=body.analyze)

    out: dict[str, Any] = {
        "ok": True,
        "deletedErrorLogs": deleted_error_logs,
        "deletedChatThreads": deleted_chat_threads,
    }
    out.update(extra)
    return out


def _parse_extra_keywords(raw: Optional[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    out: list[str] = []
    for x in str(raw).split(","):
        t = x.strip().lower()
        if t:
            out.append(t)
    return list(dict.fromkeys(out))


def _flatten_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return " ".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _thread_plaintext(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    lines: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        body = _flatten_message_content(m.get("content"))
        lines.append(f"{role}: {body}")
    return "\n".join(lines)


def _snippet_at(hay: str, keyword: str, radius: int = 100) -> str:
    lo = hay.lower()
    kw = keyword.lower()
    i = lo.find(kw)
    if i < 0:
        t = hay[: min(200, len(hay))]
        return t + ("…" if len(hay) > 200 else "")
    a = max(0, i - radius)
    b = min(len(hay), i + len(keyword) + radius)
    s = hay[a:b].replace("\n", " ")
    if a > 0:
        s = "…" + s
    if b < len(hay):
        s = s + "…"
    return s


@router.get("/moderation/message-matches")
def admin_message_matches(
    keywords: Optional[str] = Query(None, description="Дополнительные слова через запятую"),
    limit_threads: int = Query(5000, ge=1, le=20000),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = get_settings()
    kws = list(s.forbidden_message_keyword_list)
    kws.extend(_parse_extra_keywords(keywords))
    kws = list(dict.fromkeys(kws))
    if not kws:
        raise HTTPException(
            status_code=400,
            detail="Задайте FORBIDDEN_MESSAGE_KEYWORDS в .env и/или параметр keywords",
        )
    rows = (
        db.query(ChatThread)
        .order_by(ChatThread.updated_at.desc())
        .limit(limit_threads)
        .all()
    )
    matches: list[dict[str, Any]] = []
    for t in rows:
        blob = _thread_plaintext(t.messages)
        if not blob.strip():
            continue
        lower = blob.lower()
        hit_kw: Optional[str] = None
        for kw in kws:
            if kw in lower:
                hit_kw = kw
                break
        if not hit_kw:
            continue
        matches.append(
            {
                "threadId": t.id,
                "userId": t.user_id,
                "title": t.title,
                "matchedKeyword": hit_kw,
                "snippet": _snippet_at(blob, hit_kw),
                "modelSlug": t.model_slug,
                "updatedAt": (t.updated_at.isoformat() + "Z") if t.updated_at else None,
            }
        )
    return {"keywordsUsed": kws, "matchCount": len(matches), "matches": matches}


# ——— Новости ———


def _serialize_news_admin(n: NewsPost) -> dict[str, Any]:
    return {
        "id": n.id,
        "publishedAt": (n.published_at.isoformat() + "Z") if n.published_at else None,
        "title": n.title,
        "imageUrl": n.image_url,
        "body": n.body,
    }


class AdminNewsCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    published_at: datetime = Field(..., alias="publishedAt")
    title: str = Field(..., min_length=1, max_length=512)
    image_url: Optional[str] = Field(None, alias="imageUrl", max_length=2048)
    body: str = Field(..., min_length=1)


@router.get("/news")
def admin_list_news(_: str = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(NewsPost).order_by(NewsPost.published_at.desc()).all()
    return [_serialize_news_admin(r) for r in rows]


@router.post("/news")
def admin_create_news(
    body: AdminNewsCreate,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    img = (body.image_url or "").strip() or None
    row = NewsPost(
        id=str(uuid.uuid4()),
        published_at=body.published_at,
        title=body.title.strip(),
        image_url=img,
        body=body.body,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_news_admin(row)


@router.delete("/news/{news_id}")
def admin_delete_news(
    news_id: str,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(NewsPost).filter(NewsPost.id == news_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="News not found")
    _delete_news_upload_file_if_ours(row.image_url)
    db.delete(row)
    db.commit()
    return {"ok": True}


# ——— Поддержка пользователей ———


class AdminSupportReplyBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


def _support_ticket_payload(ticket: SupportTicket, user: Optional[User]) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "status": ticket.status,
        "lastSenderRole": ticket.last_sender_role,
        "lastMessagePreview": ticket.last_message_preview,
        "userUnreadCount": ticket.user_unread_count,
        "adminUnreadCount": ticket.admin_unread_count,
        "createdAt": (ticket.created_at.isoformat() + "Z") if ticket.created_at else None,
        "updatedAt": (ticket.updated_at.isoformat() + "Z") if ticket.updated_at else None,
        "user": {
            "id": user.id if user else ticket.user_id,
            "username": user.username if user else None,
            "vkUserId": user.vk_user_id if user else None,
            "yandexUserId": user.yandex_user_id if user else None,
        },
    }


def _support_message_payload(msg: SupportMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "senderRole": msg.sender_role,
        "senderId": msg.sender_id,
        "body": msg.body,
        "createdAt": msg.created_at.isoformat() + "Z",
    }


@router.get("/support/tickets")
def admin_support_tickets(
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(SupportTicket, User)
        .join(User, SupportTicket.user_id == User.id)
        .order_by(SupportTicket.updated_at.desc())
        .limit(500)
        .all()
    )
    return [_support_ticket_payload(t, u) for t, u in rows]


@router.get("/support/tickets/{ticket_id}")
def admin_support_ticket_detail(
    ticket_id: str,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    user = db.query(User).filter(User.id == ticket.user_id).first()
    if ticket.admin_unread_count:
        ticket.admin_unread_count = 0
        db.commit()
        db.refresh(ticket)
    messages = (
        db.query(SupportMessage)
        .filter(SupportMessage.ticket_id == ticket_id)
        .order_by(SupportMessage.created_at.asc())
        .all()
    )
    return {
        **_support_ticket_payload(ticket, user),
        "messages": [_support_message_payload(m) for m in messages],
    }


@router.post("/support/tickets/{ticket_id}/messages")
def admin_support_reply(
    ticket_id: str,
    body: AdminSupportReplyBody,
    admin_uid: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    msg_body = body.message.strip()
    if not msg_body:
        raise HTTPException(status_code=400, detail="Сообщение пустое")
    msg = SupportMessage(
        ticket_id=ticket.id,
        sender_role="admin",
        sender_id=admin_uid,
        body=msg_body,
    )
    ticket.last_sender_role = "admin"
    ticket.last_message_preview = _support_preview(msg_body)
    ticket.user_unread_count = int(ticket.user_unread_count or 0) + 1
    ticket.admin_unread_count = 0
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return _support_message_payload(msg)
