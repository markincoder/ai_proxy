"""
Подтягивание цен OpenRouter (GET /models) и запись в ai_models в ₽ по заданным курсу и коэффициенту.

Используется скриптом `scripts/sync_openrouter_prices.py` и админским «Пересчётом» (курс + коэфф. + синк).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any, Iterable

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .database import apply_user_facing_from_specs, get_default_model_specs
from .models import AiModel

_RUB_CENT = Decimal("0.01")


def _rub_ceil_2(d: Decimal) -> Decimal:
    return d.quantize(_RUB_CENT, rounding=ROUND_CEILING)


VIDEO_USD_PER_SEC: dict[str, Decimal] = {
    "kwaivgi/kling-v3.0-pro": Decimal("0.168"),
    "kwaivgi/kling-v3.0-std": Decimal("0.126"),
    "kwaivgi/kling-video-o1": Decimal("0.112"),
    "google/veo-3.1-fast": Decimal("0.12"),
    "google/veo-3.1-lite": Decimal("0.08"),
    "google/veo-3.1": Decimal("0.60"),
    "minimax/hailuo-2.3": Decimal("0.0817"),
    "openai/sora-2-pro": Decimal("0.50"),
    "alibaba/wan-2.7": Decimal("0.10"),
    "bytedance/seedance-2.0": Decimal("0.055"),
    "bytedance/seedance-2.0-fast": Decimal("0.040"),
    "bytedance/seedance-1-5-pro": Decimal("0.025"),
}

TRANSCRIPTION_USD_PER_MINUTE_FALLBACK: dict[str, Decimal] = {
    "openai/whisper-1": Decimal("0.006"),
    "openai/gpt-4o-transcribe": Decimal("0.018"),
    "openai/gpt-4o-mini-transcribe": Decimal("0.006"),
    "openai/whisper-large-v3": Decimal("0.009"),
    "openai/whisper-large-v3-turbo": Decimal("0.006"),
}


@dataclass(frozen=True)
class ApplyRow:
    slug: str
    input_price_per_mn: Decimal
    output_price_per_mn: Decimal
    set_fixed_price: Decimal | None = None


def usd_per_million(unit_price: str | None) -> Decimal | None:
    if unit_price is None or unit_price == "" or unit_price == "0":
        return None
    try:
        d = Decimal(str(unit_price))
    except Exception:
        return None
    if d <= 0:
        return None
    return d * Decimal(1_000_000)


def _usd_in_out_from_modalities(pr: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    in_parts: list[Decimal] = []
    out_parts: list[Decimal] = []
    u_p = usd_per_million(pr.get("prompt"))
    u_a = usd_per_million(pr.get("audio"))
    u_img = usd_per_million(pr.get("image"))
    u_c = usd_per_million(pr.get("completion"))
    if u_p is not None:
        in_parts.append(u_p)
    if u_img is not None:
        in_parts.append(u_img)
    if u_a is not None:
        in_parts.append(u_a)
        out_parts.append(u_a)
    if u_c is not None:
        out_parts.append(u_c)
    mx_in = max(in_parts) if in_parts else None
    mx_out = max(out_parts) if out_parts else None
    return mx_in, mx_out


def fetch_models_map() -> dict[str, dict]:
    from .openrouter import openrouter_base_url, openrouter_headers_get

    base = openrouter_base_url()
    trust = get_settings().openrouter_httpx_trust_env
    r = httpx.get(
        f"{base}/models",
        headers=openrouter_headers_get(),
        timeout=120.0,
        trust_env=trust,
    )
    r.raise_for_status()
    return {m["id"]: m for m in r.json().get("data", [])}


def compute_token_rub(
    slug: str,
    models: dict[str, dict],
    usd_rub: Decimal,
    mult: Decimal,
) -> tuple[Decimal, Decimal] | None:
    m = models.get(slug)
    if not m:
        return None
    pr = m.get("pricing") or {}
    if not isinstance(pr, dict):
        return None
    u_in, u_out = _usd_in_out_from_modalities(pr)
    if u_in is None and u_out is None:
        u_in = usd_per_million(pr.get("prompt"))
        u_out = usd_per_million(pr.get("completion"))
        if u_in is None and u_out is None:
            return None
    factor = usd_rub * mult
    rub_in = _rub_ceil_2((u_in or Decimal(0)) * factor)
    rub_out = _rub_ceil_2((u_out or Decimal(0)) * factor)
    return rub_in, rub_out


def _decimal_from_any(v: object) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def build_apply_rows(
    models: dict[str, dict],
    usd_rub: Decimal,
    mult: Decimal,
) -> tuple[list[ApplyRow], list[str]]:
    updates: list[ApplyRow] = []
    missing: list[str] = []
    factor = usd_rub * mult

    for spec in get_default_model_specs():
        slug = str(spec["slug"])
        if spec.get("is_free"):
            continue

        if spec.get("supports_video_generation"):
            usd_sec = VIDEO_USD_PER_SEC.get(slug)
            if usd_sec is None:
                missing.append(slug)
                continue
            rub_sec = _rub_ceil_2(usd_sec * factor)
            updates.append(ApplyRow(slug=slug, input_price_per_mn=rub_sec, output_price_per_mn=Decimal("0")))
            continue

        pair = compute_token_rub(slug, models, usd_rub, mult)

        if spec.get("supports_transcription"):
            tpm = TRANSCRIPTION_USD_PER_MINUTE_FALLBACK.get(slug)
            if tpm is not None:
                rub_fb = _rub_ceil_2(tpm * factor)
                seed_floor = _decimal_from_any(spec.get("input_price_per_mn"))
                rub_min_raw = max(rub_fb, seed_floor) if seed_floor > 0 else rub_fb
                rub_min = _rub_ceil_2(rub_min_raw)
                stale = pair is None or (pair[0] <= 0 and pair[1] <= 0)
                if stale:
                    updates.append(
                        ApplyRow(slug=slug, input_price_per_mn=rub_min, output_price_per_mn=Decimal("0"))
                    )
                    continue
            if pair is not None:
                updates.append(ApplyRow(slug=slug, input_price_per_mn=pair[0], output_price_per_mn=pair[1]))
                continue
            missing.append(slug)
            continue

        if pair is None:
            missing.append(slug)
            continue
        rin, rout = pair
        if rin <= 0 and rout <= 0:
            missing.append(slug)
            continue
        updates.append(ApplyRow(slug=slug, input_price_per_mn=rin, output_price_per_mn=rout))

    return updates, missing


def apply_rows_to_session(db: Session, updates: Iterable[ApplyRow]) -> int:
    n = 0
    for u in updates:
        r = db.query(AiModel).filter(AiModel.slug == u.slug).first()
        if r:
            r.input_price_per_mn = u.input_price_per_mn
            r.output_price_per_mn = u.output_price_per_mn
            if u.set_fixed_price is not None:
                r.fixed_price = u.set_fixed_price
            n += 1
    return n


def sync_chat_model_prices_to_db(
    db: Session,
    *,
    usd_rub: Decimal,
    mult: Decimal,
    copy_card_texts: bool = True,
) -> dict[str, Any]:
    """
    GET OpenRouter /models → пересчёт ₽ по usd_rub и mult → обновление строк ai_models для slug из chat JSON.
    Транзакцию коммитит вызывающий код.
    """
    models_map = fetch_models_map()
    updates, missing = build_apply_rows(models_map, usd_rub, mult)
    rows_touched = apply_rows_to_session(db, updates)
    n_copy, n_absent = (0, 0)
    if copy_card_texts:
        n_copy, n_absent = apply_user_facing_from_specs(db, dry_run=False)
    return {
        "pricingRowsUpdatedFromOpenRouter": rows_touched,
        "pricingSlugsProcessed": len(updates),
        "pricingSlugsUpdatedFromOpenRouter": [u.slug for u in updates],
        "pricingSlugsSkippedNoData": missing,
        "cardTextsRowsUpdated": n_copy,
        "cardTextsSlugsAbsentInDb": n_absent,
    }
