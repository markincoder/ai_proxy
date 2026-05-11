"""
CLI: синхронизация цен чат-моделей с OpenRouter (как админское «Применить» на вкладке Модели).

Из корня репозитория:
  python scripts/sync_openrouter_prices.py           # запись в БД
  python scripts/sync_openrouter_prices.py --dry-run  # только печать

  python scripts/sync_openrouter_prices.py --no-copy # только цены, без текстов карточек из JSON

Эффективные курс и коэффициент — через resolve_usd_rub_markup()
(БД site_pricing_factors после админского пересчёта или .env).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_repo_env() -> None:
    from dotenv import load_dotenv

    for path in (
        ROOT / ".env",
        ROOT / "server" / ".env",
        ROOT / "web" / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-copy",
        action="store_true",
        help="Не обновлять тексты карточек из default_model_specs.json.",
    )
    args = ap.parse_args()

    _load_repo_env()
    from server.database import SessionLocal, apply_user_facing_from_specs
    from server.openrouter_price_sync import apply_rows_to_session, build_apply_rows, fetch_models_map
    from server.pricing_factors import resolve_usd_rub_markup

    usd_rub, mult = resolve_usd_rub_markup()

    mm = fetch_models_map()
    print(f"(effective) OPENROUTER_USD_RUB={usd_rub} PRICING_MARKUP_MULT={mult}\n")

    updates, missing = build_apply_rows(mm, usd_rub, mult)

    for u in updates:
        extras = ""
        if u.set_fixed_price is not None:
            extras = f" fixed_price={u.set_fixed_price} RUB"
        print(f"{u.slug}: in={u.input_price_per_mn} out={u.output_price_per_mn} RUB{extras}")

    for slug in missing:
        print(f"[skip] {slug} — no API/pricing/local fallback")

    if args.dry_run:
        if not args.no_copy:
            with SessionLocal() as db:
                n_copy, n_absent = apply_user_facing_from_specs(db, dry_run=True)
            print(f"\n[copy] dry-run: затронуло бы до {n_copy} строк (нет в БД: {n_absent} slug из сидов).")
        print(f"\ndry-run: записей цен было бы применено: {len(updates)}")
        if missing:
            print("Без строки расчёта:", ", ".join(missing))
        return

    with SessionLocal() as db:
        rows_touched = apply_rows_to_session(db, updates)
        n_copy, n_absent = (0, 0)
        if not args.no_copy:
            n_copy, n_absent = apply_user_facing_from_specs(db, dry_run=False)
        db.commit()
        print(
            f"\nSQLite/Postgres: обновлено строк ai_models из OpenRouter-синка: {rows_touched} "
            f"/ обработано slug: {len(updates)}.",
        )
        if n_copy:
            print(f"[copy] тексты карточек обновлены: {n_copy} строк.")
        if n_absent:
            print(f"[copy] в БД нет slug из каталога — тексты не применены: {n_absent}")

    if missing:
        print(
            "\nНет расчёта для slug; при необходимости расширьте VIDEO_USD_PER_SEC или TRANSCRIPTION:",
            ", ".join(missing),
        )
    print(
        "\nСемантика колонок: input/output = ₽ за 1M токенов (чат); видео: input = ₽/с; STT: input = ₽/мин; "
        "Lyria: fixed_price."
    )


if __name__ == "__main__":
    main()
