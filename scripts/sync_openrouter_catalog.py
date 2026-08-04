"""
CLI: сверка чат-каталога с OpenRouter (проверка / исправление).

Из корня репозитория:
  python scripts/sync_openrouter_catalog.py --check
  python scripts/sync_openrouter_catalog.py --check --no-probe-free
  python scripts/sync_openrouter_catalog.py              # исправить + цены
  python scripts/sync_openrouter_catalog.py --dry-run
  python scripts/sync_openrouter_catalog.py --no-prices  # только JSON/БД slug, без пересчёта ₽

Ночной запуск в приложении: 02:00 Europe/Moscow (CATALOG_SYNC_* в .env).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_repo_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (
        ROOT / ".env",
        ROOT / "server" / ".env",
        ROOT / "web" / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="Только отчёт, без записи.")
    ap.add_argument("--dry-run", action="store_true", help="Показать, что было бы исправлено.")
    ap.add_argument(
        "--no-probe-free",
        action="store_true",
        help="Не слать мини-запросы к free-моделям (только список /models).",
    )
    ap.add_argument(
        "--no-prices",
        action="store_true",
        help="Не пересчитывать цены после правки slug.",
    )
    ap.add_argument("--json", action="store_true", help="Печатать результат как JSON.")
    args = ap.parse_args()
    _load_repo_env()

    from server.database import SessionLocal
    from server.openrouter_catalog_sync import (
        check_catalog_vs_openrouter,
        reconcile_catalog_with_openrouter,
    )

    probe_free = not args.no_probe_free

    if args.check:
        report = check_catalog_vs_openrouter(probe_free=probe_free)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        chat = report.get("chatCatalog") or {}
        print(f"OpenRouter ids: {report.get('openRouterSlugCount')}")
        print(f"aligned: {report.get('catalogLooksAligned')}")
        print("to remove:", ", ".join(chat.get("slugsToRemove") or []) or "(нет)")
        for c in chat.get("slugsToConvertFreeToPaid") or []:
            print(f"convert: {c.get('from')} → {c.get('to')} ({c.get('reason')})")
        return

    with SessionLocal() as db:
        result = reconcile_catalog_with_openrouter(
            db,
            dry_run=args.dry_run,
            probe_free=probe_free,
            sync_prices=not args.no_prices,
        )
        if not args.dry_run:
            db.commit()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"dry_run={result.get('dryRun')}")
    print("removed:", ", ".join(result.get("removedSlugs") or []) or "(нет)")
    for c in result.get("convertedFreeToPaid") or []:
        print(f"converted: {c.get('from')} → {c.get('to')}")
    print(f"specs: {result.get('chatSpecsBefore')} → {result.get('chatSpecsAfter')}")
    pricing = result.get("pricing") or {}
    if pricing:
        print(
            "pricing rows:",
            pricing.get("pricingRowsUpdatedFromOpenRouter"),
            "skipped:",
            len(pricing.get("pricingSlugsSkippedNoData") or []),
        )


if __name__ == "__main__":
    main()
