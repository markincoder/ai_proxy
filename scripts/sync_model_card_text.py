"""
Копирует из каталога `server/data/default_model_specs.json` в БД только поля карточки:
display_name, provider, description_ru, pricing_note_ru для slug, уже присутствующих в `ai_models`.

Без запросов к OpenRouter и без изменения цен. Полезно после правки текстов в JSON.

Запуск из корня репозитория:
  python scripts/sync_model_card_text.py
  python scripts/sync_model_card_text.py --dry-run

Перед чтением переменных подгружаются `.env`, `server/.env`, `web/.env`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.database import SessionLocal, apply_user_facing_from_specs  # noqa: E402


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
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="Не писать в БД; список отличий в stdout.")
    args = ap.parse_args()
    _load_repo_env()

    prefix = "[dry-run] " if args.dry_run else ""
    action = "затронуло бы" if args.dry_run else "обновлено"
    with SessionLocal() as db:
        updated, absent = apply_user_facing_from_specs(db, dry_run=args.dry_run)
        print(f"\n{prefix}Карточки ({action}): {updated}")
        print(f"{prefix}Slug из каталога без строки в БД (нужен запуск приложения/init_db): {absent}")

        if not args.dry_run:
            db.commit()


if __name__ == "__main__":
    main()
