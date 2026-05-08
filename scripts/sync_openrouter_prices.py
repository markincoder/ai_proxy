"""
Подтягивает цены prompt/completion с OpenRouter (USD за токен в API),
переводит в ₽ за 1M токенов: usd_per_million * OPENROUTER_USD_RUB * PRICING_MARKUP_MULT.

Запуск из корня репозитория:
  python scripts/sync_openrouter_prices.py           # печать + обновить БД
  python scripts/sync_openrouter_prices.py --dry-run   # только печать
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.database import SessionLocal, DEFAULT_MODEL_SPECS  # noqa: E402
from server.models import AiModel  # noqa: E402


def _env_dec(name: str, default: str) -> Decimal:
    return Decimal(os.environ.get(name, default))


def usd_per_million(token_price: str | None) -> Decimal | None:
    if token_price is None or token_price == "" or token_price == "0":
        return None
    return Decimal(str(token_price)) * Decimal(1_000_000)


def fetch_models_map() -> dict[str, dict]:
    r = httpx.get("https://openrouter.ai/api/v1/models", timeout=120.0)
    r.raise_for_status()
    return {m["id"]: m for m in r.json().get("data", [])}


def compute_rub_prices(
    slug: str,
    models: dict[str, dict],
    usd_rub: Decimal,
    mult: Decimal,
) -> tuple[Decimal, Decimal] | None:
    if slug == "openrouter/free":
        return Decimal("0"), Decimal("0")
    m = models.get(slug)
    if not m:
        return None
    pr = m.get("pricing") or {}
    pi, po = pr.get("prompt"), pr.get("completion")
    u_in = usd_per_million(pi)
    u_out = usd_per_million(po)
    if u_in is None and u_out is None:
        return None
    factor = usd_rub * mult
    rub_in = ((u_in or Decimal(0)) * factor).quantize(Decimal("0.01"))
    rub_out = ((u_out or Decimal(0)) * factor).quantize(Decimal("0.01"))
    return rub_in, rub_out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    usd_rub = _env_dec("OPENROUTER_USD_RUB", "100")
    mult = _env_dec("PRICING_MARKUP_MULT", "3")

    models = fetch_models_map()
    print(f"OPENROUTER_USD_RUB={usd_rub} PRICING_MARKUP_MULT={mult}\n")

    updates: list[tuple[str, Decimal, Decimal]] = []
    missing: list[str] = []

    for spec in DEFAULT_MODEL_SPECS:
        slug = str(spec["slug"])
        if spec.get("is_free"):
            continue
        pair = compute_rub_prices(slug, models, usd_rub, mult)
        if pair is None:
            missing.append(slug)
            print(f"[skip] {slug} — нет в API или нет pricing")
            continue
        rin, rout = pair
        updates.append((slug, rin, rout))
        print(f"{slug}: in={rin} out={rout} RUB/1M")

    if args.dry_run:
        if missing:
            print("\nНе обновлено (требуется ручная цена):", ", ".join(missing))
        return

    with SessionLocal() as db:
        for slug, rin, rout in updates:
            row = db.query(AiModel).filter(AiModel.slug == slug).first()
            if row:
                row.input_price_per_mn = rin
                row.output_price_per_mn = rout
        db.commit()
        print(f"\nSQLite/Postgres: обновлено записей в ai_models: {len(updates)}.")

    if missing:
        print(
            "\nБез поля pricing в ответе API (видео, музыка, устаревшие slug) — цены в БД не менялись.",
            "Список:",
            ", ".join(missing),
        )
    print(
        "\nСиды `DEFAULT_MODEL_SPECS` в server/database.py должны совпадать; "
        "для сверки смотрите вывод dry-run или git diff."
    )


if __name__ == "__main__":
    main()
