from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Transaction, User
from ..pricing_rub import rub_price_ceil_2


def assert_positive_balance(db: Session, user_id: str) -> None:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.balance <= 0:
        raise HTTPException(
            status_code=402,
            detail={"error": "Payment Required", "code": "INSUFFICIENT_BALANCE"},
        )


def assert_balance_covers_estimate(db: Session, user_id: str, estimate: Decimal) -> None:
    """Платная модель: на балансе должно хватать на оценку минимальной стоимости запроса."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    need_raw = estimate if estimate > 0 else Decimal("0")
    need = rub_price_ceil_2(need_raw) if need_raw > 0 else Decimal("0")
    if need <= 0:
        assert_positive_balance(db, user_id)
        return
    if user.balance < need:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Payment Required",
                "code": "INSUFFICIENT_BALANCE",
                "estimatedMinRub": format(need, "f"),
            },
        )


def record_spend(
    db: Session,
    user_id: str,
    amount: Decimal,
    openrouter_request_id: Optional[str],
    description: str,
) -> None:
    if amount <= 0:
        return
    amount_rub = rub_price_ceil_2(amount)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    user.balance = user.balance - amount_rub
    db.add(
        Transaction(
            user_id=user_id,
            type="SPEND",
            amount=amount_rub,
            description=description,
            openrouter_request_id=openrouter_request_id,
        )
    )
    db.commit()
