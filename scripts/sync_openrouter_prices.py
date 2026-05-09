"""
Подтягивает цены с OpenRouter (GET /models, поле pricing), переводит в ₽ так же для чата:
usd_per_unit * 1e6 * OPENROUTER_USD_RUB * PRICING_MARKUP_MULT.

Поддерживает:
- текст + completion;
- modality «audio», «image» в pricing (ориентир для входа/выхода, например GPT Audio);
- видеогенерацию без записи pricing в каталоге — см. VIDEO_USD_PER_SEC (USD за сек выходного ролика,
  см. страницы моделей на openrouter.ai; в БД input_price хранится как ₽/с для отображения);
- транскрипцию без строки в /models — ориентир USD/мин аудио → в БД input как ₽/мин;
- Lyria: API отдаёт 0/0 по токенам — обновляет fixed_price по USD за композицию (CLIP — отдельная ставка).

Запуск из корня репозитория:
  python scripts/sync_openrouter_prices.py           # печать + обновить БД
  python scripts/sync_openrouter_prices.py --dry-run   # только печать

После цен обновляет из сидов (`server/database.py`) карточки моделей:
display_name, provider, description_ru, pricing_note_ru (если ключ задан в сиде)
для строк, которые уже есть в `ai_models`.

  python scripts/sync_openrouter_prices.py --no-copy   # только цены, без текстов карточек

Перед чтением переменных подгружаются `.env`, `server/.env`, `web/.env`.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.database import SessionLocal, DEFAULT_MODEL_SPECS, _provider_from_seed_spec  # noqa: E402
from server.models import AiModel  # noqa: E402


# --- Benchmark USD / sec по карточкам OpenRouter (ориентир; при расхождениях обновите). ---
VIDEO_USD_PER_SEC: dict[str, Decimal] = {
    # Kling Pro: без звука — $0,112/с, со звуком — $0,168/с → берём верхнюю полосу как ориентир max.
    "kwaivgi/kling-v3.0-pro": Decimal("0.168"),
    "kwaivgi/kling-v3.0-std": Decimal("0.126"),
    "kwaivgi/kling-video-o1": Decimal("0.112"),
    # Veo Fast: разброс по разрешению/звуку; округлённое значение середины типичных 720p–1080p со звуком.
    "google/veo-3.1-fast": Decimal("0.12"),
    "google/veo-3.1-lite": Decimal("0.08"),
    "google/veo-3.1": Decimal("0.60"),
    "minimax/hailuo-2.3": Decimal("0.0817"),
    "openai/sora-2-pro": Decimal("0.50"),
    "alibaba/wan-2.7": Decimal("0.10"),
    "alibaba/wan-2.6": Decimal("0.15"),
    # Seedance: в API billing/video_tokens за единицу; эквивалент ~$/с около этих ставок как ориентир для вкладки.
    "bytedance/seedance-2.0": Decimal("0.055"),
    "bytedance/seedance-2.0-fast": Decimal("0.040"),
    "bytedance/seedance-1-5-pro": Decimal("0.025"),
}

# slug нет в /models или без цен STT → USD за минуту входного аудио (как выставляет провайдер, ориентир).
TRANSCRIPTION_USD_PER_MINUTE_FALLBACK: dict[str, Decimal] = {
    "openai/whisper-1": Decimal("0.006"),
    "openai/gpt-4o-transcribe": Decimal("0.018"),
    "openai/gpt-4o-mini-transcribe": Decimal("0.006"),
    "openai/whisper-large-v3": Decimal("0.009"),
    "openai/whisper-large-v3-turbo": Decimal("0.006"),
    "google/chirp-3": Decimal("0.012"),
    # Amazon Nova (ориентир; проверьте актуальный STT в кабинете OpenRouter перед продакшеном).
    "amazon/nova-micro-v1": Decimal("0.003"),
    "amazon/nova-lite-v1": Decimal("0.004"),
    "amazon/nova-pro-v1": Decimal("0.008"),
    "amazon/nova-premier-v1": Decimal("0.012"),
    "amazon/nova-2-lite-v1": Decimal("0.005"),
}

# Lyria — фактическая оплата «за результат», не текстовые токены каталога; обновляет только fixed_price.
LYRIA_USD_PER_MEDIA_UNIT: dict[str, Decimal] = {
    "google/lyria-3-pro-preview": Decimal("0.08"),  # за полную композицию (song)
    "google/lyria-3-clip-preview": Decimal("0.048"),  # ориентир пропорционально сидов 22/35 к Pro
}


@dataclass(frozen=True)
class ApplyRow:
    slug: str
    input_price_per_mn: Decimal  # см. interpretation в логе
    output_price_per_mn: Decimal
    set_fixed_price: Decimal | None = None  # None = не менять столбец


def _env_dec(name: str, default: str) -> Decimal:
    return Decimal(os.environ.get(name, default))


def _load_repo_env() -> None:
    from dotenv import load_dotenv

    for path in (
        ROOT / ".env",
        ROOT / "server" / ".env",
        ROOT / "web" / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=True)


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
    """Вход: max(prompt, audio, image); выход: max(completion, audio)."""
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
    r = httpx.get("https://openrouter.ai/api/v1/models", timeout=120.0)
    r.raise_for_status()
    return {m["id"]: m for m in r.json().get("data", [])}


def compute_token_rub(
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
    if not isinstance(pr, dict):
        return None
    u_in, u_out = _usd_in_out_from_modalities(pr)
    # fallback: просто legacy prompt/completion если только они
    if u_in is None and u_out is None:
        u_in = usd_per_million(pr.get("prompt"))
        u_out = usd_per_million(pr.get("completion"))
        if u_in is None and u_out is None:
            return None
    factor = usd_rub * mult
    rub_in = ((u_in or Decimal(0)) * factor).quantize(Decimal("0.01"))
    rub_out = ((u_out or Decimal(0)) * factor).quantize(Decimal("0.01"))
    return rub_in, rub_out


def _decimal_from_any(v: object) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _build_apply_for_slug(
    spec: dict[str, object],
    models: dict[str, dict],
    usd_rub: Decimal,
    mult: Decimal,
) -> ApplyRow | None:
    slug = str(spec["slug"])

    factor = usd_rub * mult

    if spec.get("is_free"):
        return None

    if slug == "openrouter/auto":
        return ApplyRow(
            slug=slug,
            input_price_per_mn=_decimal_from_any(spec.get("input_price_per_mn")),
            output_price_per_mn=_decimal_from_any(spec.get("output_price_per_mn")),
            set_fixed_price=None,
        )

    if spec.get("supports_video_generation"):
        usd_sec = VIDEO_USD_PER_SEC.get(slug)
        if usd_sec is None:
            return None
        rub_sec = (usd_sec * factor).quantize(Decimal("0.01"))
        return ApplyRow(slug=slug, input_price_per_mn=rub_sec, output_price_per_mn=Decimal("0"))

    pair = compute_token_rub(slug, models, usd_rub, mult)

    lyr = LYRIA_USD_PER_MEDIA_UNIT.get(slug)
    if lyr is not None:
        converted = (lyr * factor).quantize(Decimal("0.01"))
        sf = spec.get("fixed_price")
        if sf is None:
            fixed = converted
        else:
            fixed = max(converted, _decimal_from_any(sf))
        if pair is None or (pair[0] <= 0 and pair[1] <= 0):
            rin = _decimal_from_any(spec.get("input_price_per_mn"))
            rout = _decimal_from_any(spec.get("output_price_per_mn"))
        else:
            rin, rout = pair
        return ApplyRow(
            slug=slug,
            input_price_per_mn=rin,
            output_price_per_mn=rout,
            set_fixed_price=fixed,
        )

    # Транскрипция только из локального словаря, если каталог без цен или нули.
    if spec.get("supports_transcription"):
        tpm = TRANSCRIPTION_USD_PER_MINUTE_FALLBACK.get(slug)
        if tpm is not None:
            rub_fb = (tpm * factor).quantize(Decimal("0.01"))
            seed_floor = _decimal_from_any(spec.get("input_price_per_mn"))
            rub_min = max(rub_fb, seed_floor) if seed_floor > 0 else rub_fb
            stale = pair is None or (pair[0] <= 0 and pair[1] <= 0)
            if stale:
                return ApplyRow(slug=slug, input_price_per_mn=rub_min, output_price_per_mn=Decimal("0"))
        if pair is not None:
            return ApplyRow(slug=slug, input_price_per_mn=pair[0], output_price_per_mn=pair[1])
        return None

    if pair is None:
        return None
    rin, rout = pair
    if rin <= 0 and rout <= 0:
        return None
    return ApplyRow(slug=slug, input_price_per_mn=rin, output_price_per_mn=rout)


def apply_user_facing_from_specs(db, *, dry_run: bool) -> tuple[int, int]:
    """Подтянуть display_name, provider, description_ru, pricing_note_ru из сидов для существующих slug.

    Возвращает (число обновлённых строк, число slug в сидах без строки в БД).
    """
    updated = 0
    missing_slug = 0
    for spec in DEFAULT_MODEL_SPECS:
        slug = str(spec["slug"])
        row = db.query(AiModel).filter(AiModel.slug == slug).first()
        if row is None:
            missing_slug += 1
            continue
        dn = str(spec["display_name"])
        pr = _provider_from_seed_spec(spec)
        desc: str | None
        if "description_ru" in spec:
            dr = spec.get("description_ru")
            desc = str(dr) if dr is not None else None
        else:
            desc = row.description_ru
        note: str | None
        if "pricing_note_ru" in spec:
            pn = spec.get("pricing_note_ru")
            note = str(pn) if pn is not None else None
        else:
            note = row.pricing_note_ru
        changed = (
            row.display_name != dn
            or row.provider != pr
            or row.description_ru != desc
            or row.pricing_note_ru != note
        )
        if not changed:
            continue
        updated += 1
        if dry_run:
            print(f"[copy] {slug}: обновить display/provider/описание/пояснение тарифа")
            continue
        row.display_name = dn
        row.provider = pr
        row.description_ru = desc
        row.pricing_note_ru = note
    return updated, missing_slug


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-copy",
        action="store_true",
        help="Не обновлять тексты карточек (display_name, provider, description_ru, pricing_note_ru) из сидов.",
    )
    args = ap.parse_args()

    _load_repo_env()
    usd_rub = _env_dec("OPENROUTER_USD_RUB", "100")
    mult = _env_dec("PRICING_MARKUP_MULT", "2")

    models = fetch_models_map()
    print(f"OPENROUTER_USD_RUB={usd_rub} PRICING_MARKUP_MULT={mult}\n")

    updates: list[ApplyRow] = []
    missing: list[str] = []

    for spec in DEFAULT_MODEL_SPECS:
        slug = str(spec["slug"])
        if spec.get("is_free"):
            continue
        row = _build_apply_for_slug(spec, models, usd_rub, mult)
        if row is None:
            missing.append(slug)
            print(f"[skip] {slug} — no API/pricing/local fallback")
            continue
        extras = ""
        if spec.get("supports_video_generation"):
            extras = " [video input=RUB/sec output duration]"
        elif spec.get("supports_transcription") and slug in TRANSCRIPTION_USD_PER_MINUTE_FALLBACK:
            extras = " [STT input=RUB/min audio]"
        if row.set_fixed_price is not None:
            extras += f" fixed_price={row.set_fixed_price} RUB"
        print(f"{slug}: in={row.input_price_per_mn} out={row.output_price_per_mn} RUB{extras}")

        updates.append(row)

    if args.dry_run:
        if missing:
            print("\nDry-run skips DB; models without computed row:", ", ".join(missing))
        if not args.no_copy:
            with SessionLocal() as db:
                n_copy, n_absent = apply_user_facing_from_specs(db, dry_run=True)
            print(f"\n[copy] dry-run: затронуло бы до {n_copy} строк (нет в БД: {n_absent} slug из сидов).")
        return

    with SessionLocal() as db:
        for u in updates:
            r = db.query(AiModel).filter(AiModel.slug == u.slug).first()
            if r:
                r.input_price_per_mn = u.input_price_per_mn
                r.output_price_per_mn = u.output_price_per_mn
                if u.set_fixed_price is not None:
                    r.fixed_price = u.set_fixed_price
        n_copy = 0
        if not args.no_copy:
            n_copy, n_absent = apply_user_facing_from_specs(db, dry_run=False)
            if n_absent:
                print(
                    f"\n[copy] в БД нет {n_absent} slug из сидов — пропущены (создаются при init_db/seed_models)."
                )
        db.commit()
        print(f"\nSQLite/Postgres: updated ai_models rows: {len(updates)}.")
        if not args.no_copy:
            print(f"[copy] обновлены тексты карточек: {n_copy} строк.")

    if missing:
        print(
            "\nNo row updated (missing data); extend VIDEO_USD_PER_SEC or TRANSCRIPTION map:",
            ", ".join(missing),
        )
    print(
        "\nColumn semantics: Usually input/output = RUB per 1M text tokens. "
        "Video: input = RUB per second of output length. Local STT: input = RUB per minute audio. "
        "Lyria: fixed_price synced from USD per song/clip."
    )


if __name__ == "__main__":
    main()
