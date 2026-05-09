"""Единый пересчёт USD (OpenRouter) → рубли на балансе пользователя."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Any

from .config import get_settings

_RUB_TWO = Decimal("0.01")


def rub_price_ceil_2(amount: Decimal | str | Any) -> Decimal:
    """Сумма тарифа в ₽: ровно 2 знака после запятой, округление в большую сторону."""
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return d.quantize(_RUB_TWO, rounding=ROUND_CEILING)


def openrouter_usd_to_balance_rub(usd: Any) -> Decimal:
    """
    Формула как в scripts/sync_openrouter_prices.py для цен моделей:
    USD × OPENROUTER_USD_RUB × PRICING_MARKUP_MULT.

    Используется для usage.cost (чат/транскрипция) и стоимости видео.
    """
    s = get_settings()
    try:
        rate = Decimal(str(s.openrouter_usd_rub).strip() or "100")
    except Exception:
        rate = Decimal("100")
    try:
        mult = Decimal(str(s.pricing_markup_mult).strip() or "1")
    except Exception:
        mult = Decimal("1")
    if rate <= 0:
        rate = Decimal("100")
    if mult <= 0:
        mult = Decimal("1")
    raw = Decimal(str(usd)) * rate * mult
    return rub_price_ceil_2(raw)
