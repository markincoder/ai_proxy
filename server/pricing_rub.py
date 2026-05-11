"""Единый пересчёт USD (OpenRouter) → рубли на балансе пользователя."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Any

from .pricing_factors import resolve_usd_rub_markup

_RUB_TWO = Decimal("0.01")


def rub_price_ceil_2(amount: Decimal | str | Any) -> Decimal:
    """Сумма тарифа в ₽: ровно 2 знака после запятой, округление в большую сторону."""
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return d.quantize(_RUB_TWO, rounding=ROUND_CEILING)


def openrouter_usd_to_balance_rub(usd: Any) -> Decimal:
    """
    USD × курс × коэффициент: как для каталога; значения берутся из БД (админка «Модели»)
    или из OPENROUTER_USD_RUB / PRICING_MARKUP_MULT в .env.

    Используется для usage.cost (чат/транскрипция) и стоимости видео.
    """
    rate, mult = resolve_usd_rub_markup()
    raw = Decimal(str(usd)) * rate * mult
    return rub_price_ceil_2(raw)
