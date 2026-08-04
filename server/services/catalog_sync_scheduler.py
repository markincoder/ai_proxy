"""Ночная сверка каталога моделей с OpenRouter (по умолчанию 02:00 Europe/Moscow)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..database import SessionLocal

_LOG = logging.getLogger(__name__)


def _seconds_until_next_run(hour: int, minute: int, tz_name: str) -> float:
    from datetime import timezone as dt_timezone

    tz = None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        try:
            tz = ZoneInfo("Europe/Moscow")
        except Exception:
            # Windows без пакета tzdata: фиксированный UTC+3 (МСК без DST).
            tz = dt_timezone(timedelta(hours=3))
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def run_catalog_reconcile_once(*, probe_free: bool = True) -> dict:
    from ..openrouter_catalog_sync import reconcile_catalog_with_openrouter

    with SessionLocal() as db:
        result = reconcile_catalog_with_openrouter(
            db,
            dry_run=False,
            probe_free=probe_free,
            sync_prices=True,
        )
        db.commit()
        return result


async def catalog_sync_scheduler_loop() -> None:
    s = get_settings()
    if not s.catalog_sync_enabled:
        _LOG.info("catalog sync scheduler disabled")
        return
    hour = int(s.catalog_sync_hour)
    minute = int(s.catalog_sync_minute)
    tz = (s.catalog_sync_tz or "Europe/Moscow").strip() or "Europe/Moscow"
    _LOG.info("catalog sync scheduler: daily at %02d:%02d %s", hour, minute, tz)
    while True:
        delay = _seconds_until_next_run(hour, minute, tz)
        _LOG.info("catalog sync: next run in %.0f s", delay)
        await asyncio.sleep(delay)
        try:
            result = await asyncio.to_thread(run_catalog_reconcile_once, probe_free=True)
            _LOG.info(
                "catalog sync done: removed=%s converted=%s aligned_before=%s",
                result.get("removedSlugs"),
                result.get("convertedFreeToPaid"),
                (result.get("check") or {}).get("catalogLooksAligned"),
            )
        except Exception:
            _LOG.exception("catalog sync failed")
