"""Сверка локального чат-каталога с OpenRouter: проверка, удаление мёртвых slug, free→paid, цены.

Используется админкой («Проверить / Исправить каталог»), ночным заданием и
``scripts/sync_openrouter_catalog.py``.
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .database import (
    _ai_model_from_spec,
    _catalog_specs_path,
    _purge_models_not_in_catalog,
    apply_user_facing_from_specs,
    get_default_embedding_specs,
    get_default_model_specs,
    invalidate_specs_cache,
)
from .models import AiModel, User
from .openrouter import openrouter_base_url, openrouter_headers
from .openrouter_price_sync import (
    TRANSCRIPTION_USD_PER_MINUTE_FALLBACK,
    VIDEO_USD_PER_SEC,
    compute_token_rub,
    fetch_models_map,
    sync_chat_model_prices_to_db,
)
from .pricing_factors import resolve_usd_rub_markup
from .services.models_catalog_cache import invalidate_public_models_cache

_LOG = logging.getLogger(__name__)

_FREE_UNAVAILABLE_RE = re.compile(
    r"unavailable for free|paid version is available|no endpoints found|is not available",
    re.I,
)


def _exempt_openrouter_slug_slugs() -> frozenset[str]:
    s = get_settings()
    out: set[str] = set()
    r = (s.openrouter_free_router_slug or "").strip()
    if r:
        out.add(r)
    out.update(VIDEO_USD_PER_SEC.keys())
    out.update(TRANSCRIPTION_USD_PER_MINUTE_FALLBACK.keys())
    return frozenset(out)


def _paid_slug_from_free(slug: str) -> str | None:
    s = str(slug or "").strip()
    if s.endswith(":free"):
        return s[: -len(":free")]
    return None


def _model_listed(models_map: dict[str, dict], slug: str) -> bool:
    return slug in models_map


def _probe_free_chat_available(slug: str) -> tuple[bool, str]:
    """Мини-запрос completions для free-маршрута: ловит «unavailable for free»."""
    base = openrouter_base_url()
    trust = get_settings().openrouter_httpx_trust_env
    body = {
        "model": slug,
        "messages": [{"role": "user", "content": "."}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers=openrouter_headers(True),
            json=body,
            timeout=60.0,
            trust_env=trust,
        )
    except httpx.HTTPError as e:
        return False, f"request_error:{e}"
    if r.status_code < 400:
        return True, "ok"
    try:
        payload = r.json()
    except Exception:
        payload = {}
    err = ""
    if isinstance(payload, dict):
        e = payload.get("error")
        if isinstance(e, dict):
            err = str(e.get("message") or e.get("metadata") or "")
        elif isinstance(e, str):
            err = e
        if not err:
            err = str(payload.get("detail") or payload)
    else:
        err = (r.text or "")[:400]
    if _FREE_UNAVAILABLE_RE.search(err or ""):
        return False, err.strip() or f"http_{r.status_code}"
    low = (err or "").lower()
    if r.status_code in (404, 410) or "no endpoints" in low:
        return False, err.strip() or f"http_{r.status_code}"
    # Прочие ошибки (rate limit, credits) не трактуем как «модели нет»
    return True, err.strip() or f"http_{r.status_code}"


def _analyze_chat_spec(
    spec: dict[str, object],
    models_map: dict[str, dict],
    exempt: frozenset[str],
    *,
    probe_free: bool,
) -> dict[str, Any]:
    slug = str(spec["slug"])
    is_free = bool(spec.get("is_free"))
    out: dict[str, Any] = {
        "slug": slug,
        "isFree": is_free,
        "exempt": slug in exempt,
        "listedOnOpenRouter": _model_listed(models_map, slug),
        "action": "keep",
        "reason": "",
        "paidSlug": None,
    }
    if slug in exempt:
        return out

    paid = _paid_slug_from_free(slug) if is_free else None
    out["paidSlug"] = paid

    if not out["listedOnOpenRouter"]:
        if is_free and paid and _model_listed(models_map, paid):
            out["action"] = "convert_to_paid"
            out["reason"] = "free_missing_paid_available"
        else:
            out["action"] = "remove"
            out["reason"] = "missing_on_openrouter"
        return out

    if is_free and probe_free:
        ok, reason = _probe_free_chat_available(slug)
        out["chatProbe"] = reason
        if not ok:
            if paid and _model_listed(models_map, paid):
                out["action"] = "convert_to_paid"
                out["reason"] = "free_unavailable_paid_available"
            else:
                out["action"] = "remove"
                out["reason"] = "free_unavailable"
            return out

    return out


def check_catalog_vs_openrouter(*, probe_free: bool = True) -> dict[str, Any]:
    """
    Сводка по чат-каталогу (включая free) и эмбеддингам vs GET …/models.

    ``catalogLooksAligned`` — нет действий remove/convert для чата.
    """
    models_map = fetch_models_map()
    or_ids = frozenset(models_map.keys())
    exempt = _exempt_openrouter_slug_slugs()

    analyses: list[dict[str, Any]] = []
    for spec in get_default_model_specs():
        analyses.append(_analyze_chat_spec(spec, models_map, exempt, probe_free=probe_free))

    to_remove = [a["slug"] for a in analyses if a["action"] == "remove"]
    to_convert = [
        {"from": a["slug"], "to": a["paidSlug"], "reason": a["reason"]}
        for a in analyses
        if a["action"] == "convert_to_paid" and a.get("paidSlug")
    ]
    missing_listed = [
        a["slug"]
        for a in analyses
        if not a["exempt"] and not a["listedOnOpenRouter"] and a["action"] != "keep"
    ]

    emb_missing: list[str] = []
    emb_checked = 0
    for spec in get_default_embedding_specs():
        if spec.get("is_free"):
            continue
        slug = str(spec["slug"])
        if slug in exempt:
            continue
        emb_checked += 1
        if slug not in or_ids:
            emb_missing.append(slug)

    chat_checked = sum(1 for a in analyses if not a["exempt"])
    aligned = len(to_remove) == 0 and len(to_convert) == 0

    return {
        "openRouterSlugCount": len(or_ids),
        "probeFree": probe_free,
        "chatCatalog": {
            "slugsCompared": chat_checked,
            "slugsMissingOnPublicModelsEndpoint": missing_listed,
            "slugsMissingOnOpenRouter": missing_listed,
            "slugsToRemove": to_remove,
            "slugsToConvertFreeToPaid": to_convert,
            "analyses": analyses,
        },
        "embeddingsCatalog": {
            "slugsCompared": emb_checked,
            "slugsMissingOnPublicModelsEndpoint": emb_missing,
            "slugsMissingOnOpenRouter": emb_missing,
            "publicModelsIncomplete": True,
            "noteRu": (
                "Ответ GET …/models у OpenRouter чаще всего не содержит строки только для эмбеддингов "
                "(их нет среди этих id — норма). Доступность slug для Embeddings API на сайте OpenRouter "
                "с этим не связана."
            ),
        },
        "catalogLooksAligned": aligned,
    }


def _strip_free_label(name: str) -> str:
    n = str(name or "").strip()
    for suf in (" (free)", " (Free)", " free", " Free"):
        if n.endswith(suf):
            return n[: -len(suf)].strip()
    return n


def _convert_spec_to_paid(spec: dict[str, object], paid_slug: str, models_map: dict[str, dict]) -> dict[str, object]:
    out = deepcopy(spec)
    out["slug"] = paid_slug
    out["is_free"] = False
    out["display_name"] = _strip_free_label(str(out.get("display_name") or paid_slug))
    note = str(out.get("pricing_note_ru") or "")
    if "0 ₽" in note or "бесплат" in note.lower():
        out["pricing_note_ru"] = "Цены по OpenRouter; уточняются синхронизацией."
    usd_rub, mult = resolve_usd_rub_markup()
    pair = compute_token_rub(paid_slug, models_map, usd_rub, mult)
    if pair is not None:
        rin, rout = pair
        if rin > 0 or rout > 0:
            out["input_price_per_mn"] = str(rin)
            out["output_price_per_mn"] = str(rout)
    return out


def _write_chat_specs(specs: list[dict[str, object]]) -> Path:
    path = _catalog_specs_path()
    serializable: list[dict[str, Any]] = []
    for s in specs:
        row: dict[str, Any] = {}
        for k, v in s.items():
            if isinstance(v, Decimal):
                row[k] = str(v)
            else:
                row[k] = v
        serializable.append(row)
    text = json.dumps(serializable, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    invalidate_specs_cache()
    return path


def reconcile_catalog_with_openrouter(
    db: Session,
    *,
    dry_run: bool = False,
    probe_free: bool = True,
    sync_prices: bool = True,
) -> dict[str, Any]:
    """
    Исправляет несоответствия чат-каталога с OpenRouter:
    удаляет недоступные slug, конвертирует dead free → paid, пишет JSON, чистит БД, синкает цены.
    """
    report = check_catalog_vs_openrouter(probe_free=probe_free)
    models_map = fetch_models_map()
    chat = report["chatCatalog"]
    to_remove = set(chat.get("slugsToRemove") or [])
    converts = {
        c["from"]: c["to"]
        for c in (chat.get("slugsToConvertFreeToPaid") or [])
        if c.get("from") and c.get("to")
    }

    old_specs = get_default_model_specs()
    new_specs: list[dict[str, object]] = []
    converted: list[dict[str, str]] = []
    removed: list[str] = []

    for spec in old_specs:
        slug = str(spec["slug"])
        if slug in converts:
            paid = converts[slug]
            new_specs.append(_convert_spec_to_paid(spec, paid, models_map))
            converted.append({"from": slug, "to": paid})
            continue
        if slug in to_remove:
            removed.append(slug)
            continue
        new_specs.append(deepcopy(spec))

    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for s in new_specs:
        slug = str(s["slug"])
        if slug in seen:
            continue
        seen.add(slug)
        deduped.append(s)
    new_specs = deduped

    result: dict[str, Any] = {
        "ok": True,
        "dryRun": dry_run,
        "removedSlugs": removed,
        "convertedFreeToPaid": converted,
        "chatSpecsBefore": len(old_specs),
        "chatSpecsAfter": len(new_specs),
        "check": {
            "catalogLooksAligned": report.get("catalogLooksAligned"),
            "openRouterSlugCount": report.get("openRouterSlugCount"),
            "slugsToRemove": list(to_remove),
            "slugsToConvertFreeToPaid": chat.get("slugsToConvertFreeToPaid") or [],
        },
    }

    if dry_run:
        result["wouldWriteJson"] = bool(removed or converted)
        return result

    if removed or converted:
        _write_chat_specs(new_specs)

    known = {row[0] for row in db.query(AiModel.slug).all()}
    for s in new_specs:
        slug = str(s["slug"])
        if slug not in known:
            db.add(_ai_model_from_spec(s))
            known.add(slug)

    emb_slugs = {str(s["slug"]) for s in get_default_embedding_specs()}
    keep = {str(s["slug"]) for s in new_specs} | emb_slugs
    _purge_models_not_in_catalog(db, keep)

    gone = set(removed) | set(converts.keys())
    if gone:
        (
            db.query(User)
            .filter(User.last_chat_model_slug.in_(gone))
            .update({User.last_chat_model_slug: None}, synchronize_session=False)
        )

    apply_user_facing_from_specs(db, dry_run=False)

    if sync_prices:
        usd_rub, mult = resolve_usd_rub_markup()
        price_sync = sync_chat_model_prices_to_db(db, usd_rub=usd_rub, mult=mult, copy_card_texts=False)
        result["pricing"] = price_sync
        result["usdRub"] = str(usd_rub)
        result["markupMult"] = str(mult)

    invalidate_public_models_cache()
    result["jsonPath"] = str(_catalog_specs_path())
    return result
