"""Прокси к OpenRouter Video Generation API + скачивание ролика для <video>."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..deps import resolve_user_id
from ..models import AiModel, Transaction
from ..openrouter import openrouter_headers_get, video_generation_create, video_generation_get
from ..pricing_rub import openrouter_usd_to_balance_rub
from ..services.spend import assert_positive_balance, record_spend

router = APIRouter(prefix="/api/v1/video", tags=["video"])

# job_id OpenRouter → владелец (in-memory; после рестарта воркера может не сработать)
_job_owners: dict[str, str] = {}
_charged_jobs: set[str] = set()
class CreateVideoJobBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_slug: str = Field(alias="modelSlug")
    prompt: str


def _maybe_record_video_spend(
    db,
    user_id: str | None,
    job_id: str,
    data: dict[str, Any],
) -> None:
    if not user_id:
        return
    if job_id in _charged_jobs:
        return
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    if cost is None:
        return
    desc = f"OpenRouter video job {job_id}"
    if db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.description == desc).first():
        _charged_jobs.add(job_id)
        return
    amount = openrouter_usd_to_balance_rub(cost)
    if amount <= 0:
        return
    record_spend(db, user_id, amount, None, desc)
    _charged_jobs.add(job_id)


@router.post("/jobs")
async def create_video_job(request: Request, body: CreateVideoJobBody):
    from ..database import SessionLocal

    with SessionLocal() as db:
        m = db.query(AiModel).filter(AiModel.slug == body.model_slug, AiModel.is_active.is_(True)).first()
        if not m or not m.supports_video_generation:
            raise HTTPException(status_code=400, detail="Not a video model")
        user_id = resolve_user_id(request, db)
        if not user_id:
            raise HTTPException(status_code=401, detail="LOGIN_REQUIRED")
        assert_positive_balance(db, user_id)

    resp = await video_generation_create({"model": body.model_slug, "prompt": body.prompt.strip()})
    if resp.status_code == 402:
        return JSONResponse(status_code=402, content={"error": "Payment Required"})
    if resp.status_code not in (200, 201, 202):
        try:
            err = resp.json()
        except Exception:
            err = {"detail": resp.text}
        raise HTTPException(status_code=resp.status_code, detail=err)

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid JSON from OpenRouter")

    jid = data.get("id")
    if isinstance(jid, str) and user_id:
        _job_owners[jid] = user_id

    return data


@router.get("/jobs/{job_id}")
async def get_video_job(request: Request, job_id: str):
    from ..database import SessionLocal

    resp = await video_generation_get(job_id)
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid JSON from OpenRouter")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=data)

    with SessionLocal() as db:
        user_id = resolve_user_id(request, db)
        owner = _job_owners.get(job_id)
        if owner:
            if not user_id or owner != user_id:
                raise HTTPException(status_code=403, detail="Forbidden")
        uid = user_id or owner
        if uid and data.get("status") == "completed":
            _maybe_record_video_spend(db, uid, job_id, data)

    # То же пересчёты, что при списании — для отображения в UI
    if isinstance(data, dict):
        usage_obj = data.get("usage")
        if isinstance(usage_obj, dict):
            raw_cost = usage_obj.get("cost")
            if raw_cost is not None:
                try:
                    rub = openrouter_usd_to_balance_rub(raw_cost)
                    data["costRub"] = format(rub, "f")
                except Exception:
                    pass

    return data


@router.get("/jobs/{job_id}/content")
async def get_video_job_content(
    request: Request,
    job_id: str,
    index: int = Query(0, ge=0),
):
    from ..database import SessionLocal

    with SessionLocal() as db:
        user_id = resolve_user_id(request, db)
    owner = _job_owners.get(job_id)
    if owner:
        if not user_id or owner != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

    st = await video_generation_get(job_id)
    try:
        data = st.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid JSON from OpenRouter")
    if st.status_code != 200:
        raise HTTPException(status_code=st.status_code, detail=data)

    urls = data.get("unsigned_urls") or []
    if not isinstance(urls, list) or index >= len(urls):
        raise HTTPException(status_code=404, detail="No content URL")
    url = urls[index]
    if not isinstance(url, str) or not url:
        raise HTTPException(status_code=404, detail="Invalid content URL")

    h = {}
    p = urlparse(url)
    if p.netloc.endswith("openrouter.ai") or p.netloc.endswith("openrouter.com"):
        h = openrouter_headers_get()

    async def stream_bytes():
        t = httpx.Timeout(600.0, connect=30.0)
        s = get_settings()
        async with httpx.AsyncClient(timeout=t, trust_env=s.openrouter_httpx_trust_env) as client:
            async with client.stream("GET", url, headers=h, follow_redirects=True) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    yield chunk

    media = "video/mp4"
    return StreamingResponse(stream_bytes(), media_type=media)
