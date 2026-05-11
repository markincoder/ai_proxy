"""Эффективные OPENROUTER_USD_RUB × PRICING_MARKUP_MULT: из БД или из .env."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def environment_usd_rub_markup() -> tuple[Decimal, Decimal]:
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
    return rate, mult


def effective_factors_from_session(db: Session) -> tuple[Decimal, Decimal]:
    from .models import SitePricingFactors

    row: SitePricingFactors | None = db.query(SitePricingFactors).filter(SitePricingFactors.id == 1).first()
    if row is not None and row.usd_rub is not None and row.markup_mult is not None:
        r, m = Decimal(str(row.usd_rub)), Decimal(str(row.markup_mult))
        if r > 0 and m > 0:
            return r, m
    return environment_usd_rub_markup()


def pricing_factors_source_from_session(db: Session) -> str:
    from .models import SitePricingFactors

    row: SitePricingFactors | None = db.query(SitePricingFactors).filter(SitePricingFactors.id == 1).first()
    if row is not None and row.usd_rub is not None and row.markup_mult is not None:
        r, m = Decimal(str(row.usd_rub)), Decimal(str(row.markup_mult))
        if r > 0 and m > 0:
            return "database"
    return "environment"


def resolve_usd_rub_markup() -> tuple[Decimal, Decimal]:
    from .database import SessionLocal

    with SessionLocal() as db:
        return effective_factors_from_session(db)
