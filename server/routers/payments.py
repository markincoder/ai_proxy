import base64
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_db, resolve_user_id, user_public_identifier
from ..models import PaymentOrder, Transaction, User
from ..services.notify import schedule_payment_notification

router = APIRouter(prefix="/api/payments", tags=["payments"])


class CreatePaymentBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    amount_rub: str = Field(alias="amountRub")


_AMOUNT_RUB_RE = re.compile(r"^\d+(\.\d{1,2})?$")


def _parse_amount_rub(amount_rub: str) -> Decimal:
    if not _AMOUNT_RUB_RE.match(amount_rub):
        raise HTTPException(status_code=400, detail="Invalid amount")
    return Decimal(amount_rub)


def _yookassa_auth_header() -> str:
    s = get_settings()
    if not s.yookassa_shop_id or not s.yookassa_secret_key:
        raise HTTPException(status_code=500, detail="YooKassa keys not configured")
    raw = base64.b64encode(f"{s.yookassa_shop_id}:{s.yookassa_secret_key}".encode()).decode()
    return f"Basic {raw}"


def _amount_matches_order_paid(order_amount: Decimal, paid_raw: str) -> bool:
    paid = Decimal(str(paid_raw))
    qo = order_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    qp = paid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return abs(qo - qp) <= Decimal("0.02")


def _try_credit_order_from_yookassa(
    db: Session,
    order: PaymentOrder,
    payment_id: str,
    value_raw: str,
    user_id_meta: str | None,
) -> bool:
    """Зачисление по данным ЮKassa. user_id_meta — из metadata API, иначе None (Simple Pay). Возвращает True, если баланс изменён."""
    if order.status == "succeeded":
        return False
    if user_id_meta and order.user_id != str(user_id_meta):
        return False
    amount = Decimal(str(value_raw))
    if not _amount_matches_order_paid(order.amount, str(value_raw)):
        return False
    order.status = "succeeded"
    order.yookassa_payment_id = payment_id
    user = db.query(User).filter(User.id == order.user_id).first()
    if user:
        user.balance = user.balance + amount
    db.add(
        Transaction(
            user_id=order.user_id,
            type="DEPOSIT",
            amount=amount,
            description=f"YooKassa {payment_id}",
            metadata_={"orderId": str(order.id), "paymentId": payment_id},
        )
    )
    db.commit()
    label = user_public_identifier(user) if user else str(order.user_id)
    amount_display = format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
    schedule_payment_notification(
        str(order.user_id),
        label,
        amount_display,
        payment_id,
        str(order.id),
    )
    return True


async def _yookassa_get_payment(client: httpx.AsyncClient, auth: str, payment_id: str) -> dict | None:
    r = await client.get(
        f"https://api.yookassa.ru/v3/payments/{payment_id}",
        headers={"Authorization": auth},
    )
    if r.status_code != 200:
        return None
    return r.json()


def _payment_matches_order(pay: dict, order_id: str) -> bool:
    oid = str(order_id).strip()
    mcid = pay.get("merchant_customer_id")
    if mcid is not None and str(mcid).strip() == oid:
        return True
    meta = pay.get("metadata") or {}
    if str(meta.get("orderId") or "").strip() == oid:
        return True
    return False


def _order_created_utc(order: PaymentOrder) -> datetime:
    dt = order.created_at
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_yookassa_created_at(raw: str) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        s = raw.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _used_succeeded_yookassa_payment_ids(db: Session) -> set[str]:
    rows = (
        db.query(PaymentOrder.yookassa_payment_id)
        .filter(PaymentOrder.yookassa_payment_id.isnot(None))
        .filter(PaymentOrder.status == "succeeded")
        .all()
    )
    return {r[0] for r in rows if r[0]}


async def _find_succeeded_payment_fallback_by_amount(
    auth: str, db: Session, order: PaymentOrder, max_pages: int = 30, max_fetches: int = 200
) -> dict | None:
    """Если Simple Pay не заполнил merchant_customer_id: ищем succeeded с той же суммой, не ушедший в другой заказ.

    Берём самый поздний платёж во временном окне после создания заказа (ограниченный риск при совпадении сумм у разных людей — в проде лучше webhook).
    """
    used = _used_succeeded_yookassa_payment_ids(db)
    order_t = _order_created_utc(order)
    lo = order_t - timedelta(days=2)
    hi = order_t + timedelta(days=7)
    candidates: list[tuple[datetime, dict]] = []
    fetches = 0
    cursor = None
    async with httpx.AsyncClient(timeout=45.0) as client:
        for _ in range(max_pages):
            if fetches >= max_fetches:
                break
            params: dict[str, str] = {"limit": "50"}
            if cursor:
                params["cursor"] = cursor
            r = await client.get(
                "https://api.yookassa.ru/v3/payments",
                headers={"Authorization": auth},
                params=params,
            )
            if r.status_code >= 400:
                return None
            data = r.json()
            for item in data.get("items") or []:
                if item.get("status") != "succeeded":
                    continue
                pid = item.get("id")
                if not pid or pid in used:
                    continue
                if fetches >= max_fetches:
                    break
                fetches += 1
                full = await _yookassa_get_payment(client, auth, pid)
                if not full or full.get("status") != "succeeded":
                    continue
                val = _payment_amount_value(full)
                if val is None or not _amount_matches_order_paid(order.amount, val):
                    continue
                pt = _parse_yookassa_created_at(full.get("created_at") or "")
                if pt is None:
                    continue
                pt = pt.astimezone(timezone.utc)
                if not (lo <= pt <= hi):
                    continue
                candidates.append((pt, full))
            cursor = data.get("next_cursor")
            if not cursor:
                break
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


async def _fetch_payment_by_id(auth: str, payment_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=35.0) as client:
        return await _yookassa_get_payment(client, auth, payment_id)


def _payment_amount_value(pay: dict) -> str | None:
    a = pay.get("amount")
    if isinstance(a, dict) and a.get("value") is not None:
        return str(a["value"])
    return None


async def _find_succeeded_payment_for_simplepay_order(
    auth: str, order_id: str, max_pages: int = 15, max_detail_fetches: int = 48
) -> dict | None:
    """Ищет succeeded-платёж, у которого merchant_customer_id или metadata.orderId совпадает с заказом.

    В кратком списке ЮKassa часто нет merchant_customer_id — подгружаем платёж через GET /v3/payments/{id}.
    """
    cursor = None
    detail_budget = max_detail_fetches
    async with httpx.AsyncClient(timeout=45.0) as client:
        for _ in range(max_pages):
            params: dict[str, str] = {"limit": "50"}
            if cursor:
                params["cursor"] = cursor
            r = await client.get(
                "https://api.yookassa.ru/v3/payments",
                headers={"Authorization": auth},
                params=params,
            )
            data = r.json()
            if r.status_code >= 400:
                return None
            for item in data.get("items") or []:
                if item.get("status") != "succeeded":
                    continue
                pid = item.get("id")
                if not pid:
                    continue

                detail: dict | None = item
                if item.get("merchant_customer_id") is None or not _payment_matches_order(
                    item, order_id
                ):
                    if detail_budget <= 0:
                        continue
                    detail_budget -= 1
                    detail = await _yookassa_get_payment(client, auth, pid)
                    if not detail:
                        continue

                if detail.get("status") == "succeeded" and _payment_matches_order(
                    detail, order_id
                ):
                    return detail
            cursor = data.get("next_cursor")
            if not cursor:
                break
    return None


class SyncSimplePayBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    order_id: str = Field(alias="orderId")
    yookassa_payment_id: str | None = Field(default=None, alias="yookassaPaymentId")


@router.post("/create")
async def create_payment(request: Request, body: CreatePaymentBody, db: Session = Depends(get_db)):
    s = get_settings()
    if not s.yookassa_enabled:
        raise HTTPException(status_code=403, detail="Платежи отключены (YOOKASSA_ENABLED=false)")

    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    amount_dec = _parse_amount_rub(body.amount_rub)
    order = PaymentOrder(user_id=user_id, amount=amount_dec)
    db.add(order)
    db.commit()
    db.refresh(order)

    idempotence_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": body.amount_rub, "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"{s.public_app_url.rstrip('/')}/?payment=return",
        },
        "description": f"Пополнение баланса ({body.amount_rub} ₽)",
        "metadata": {"userId": user_id, "orderId": order.id},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://api.yookassa.ru/v3/payments",
            headers={
                "Idempotence-Key": idempotence_key,
                "Authorization": _yookassa_auth_header(),
                "Content-Type": "application/json",
            },
            json=payload,
        )
        data = r.json()
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=str(data))

    payment_id = data.get("id")
    order.yookassa_payment_id = payment_id
    order.status = "pending"
    db.commit()

    url = (data.get("confirmation") or {}).get("confirmation_url")
    if not url:
        raise HTTPException(status_code=502, detail="No confirmation URL")
    return {"confirmationUrl": url, "paymentId": payment_id}


@router.post("/simplepay-order")
async def create_simplepay_order(request: Request, body: CreatePaymentBody, db: Session = Depends(get_db)):
    """Заказ для формы Simple Pay: `customerNumber` = order.id, сумма совпадает с оплатой (проверка в webhook)."""
    s = get_settings()
    if not s.yookassa_enabled:
        raise HTTPException(status_code=403, detail="Платежи отключены (YOOKASSA_ENABLED=false)")
    if not s.yookassa_shop_id:
        raise HTTPException(status_code=500, detail="YOOKASSA_SHOP_ID not configured")

    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    amount_dec = _parse_amount_rub(body.amount_rub)
    order = PaymentOrder(user_id=user_id, amount=amount_dec, status="pending")
    db.add(order)
    db.commit()
    db.refresh(order)
    return {"orderId": order.id, "shopId": s.yookassa_shop_id}


@router.post("/sync-simplepay")
async def sync_simplepay_order(request: Request, body: SyncSimplePayBody, db: Session = Depends(get_db)):
    """Без webhook: после return_url запрашиваем список платежей в ЮKassa и зачисляем по merchant_customer_id = orderId."""
    s = get_settings()
    if not s.yookassa_enabled:
        raise HTTPException(status_code=403, detail="Платежи отключены")
    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    oid = str(body.order_id).strip()
    if not oid:
        raise HTTPException(status_code=400, detail="orderId required")

    order = db.query(PaymentOrder).filter(PaymentOrder.id == oid).first()
    if not order or order.user_id != user_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Заказ не найден для этой сессии: проверьте orderId (UUID из URL после оплаты или из simplepay-order), "
                "и то, что в Swagger вы «залогинены» тем же пользователем (cookie после входа на сайте). "
                "Маршрут: POST /api/payments/sync-simplepay, тело: {\"orderId\": \"...\"}."
            ),
        )

    if order.status == "succeeded":
        return {"credited": False, "alreadyDone": True}

    auth = _yookassa_auth_header()
    pay: dict | None = None
    manual_pid = (body.yookassa_payment_id or "").strip()
    if manual_pid:
        pay = await _fetch_payment_by_id(auth, manual_pid)
        if not pay:
            return {
                "credited": False,
                "pending": True,
                "hint": "yookassa_payment_not_found",
                "tip": "Проверьте ID платежа в личном кабинете и что YOOKASSA_SHOP_ID/SECRET_KEY от того же магазина.",
            }
        if pay.get("status") != "succeeded":
            return {
                "credited": False,
                "pending": True,
                "hint": f"payment_status_{pay.get('status')}",
                "tip": "Дождитесь статуса succeeded или проверьте правильность ID.",
            }
        used = _used_succeeded_yookassa_payment_ids(db)
        if manual_pid in used:
            other = (
                db.query(PaymentOrder)
                .filter(
                    PaymentOrder.yookassa_payment_id == manual_pid,
                    PaymentOrder.status == "succeeded",
                )
                .first()
            )
            if other and other.id != order.id:
                return {
                    "credited": False,
                    "pending": True,
                    "hint": "payment_already_credited_elsewhere",
                }
        val = _payment_amount_value(pay)
        if not val or not _amount_matches_order_paid(order.amount, val):
            return {
                "credited": False,
                "pending": False,
                "matchedPayment": True,
                "detail": "amount_mismatch_order_vs_yookassa",
            }
    else:
        pay = await _find_succeeded_payment_for_simplepay_order(auth, oid)
        if not pay:
            pay = await _find_succeeded_payment_fallback_by_amount(auth, db, order)
    if not pay:
        return {
            "credited": False,
            "pending": True,
            "hint": "no_matching_payment_in_yookassa",
            "tip": "Укажите в теле yookassaPaymentId — UUID платежа из личного кабинета ЮKassa (раздел «Платежи»). Проверьте, что ключи API от того же магазина, через который принимали оплату.",
        }

    payment_id = pay.get("id")
    value = _payment_amount_value(pay)
    if not payment_id or not value:
        return {"credited": False, "pending": True}

    credited = _try_credit_order_from_yookassa(db, order, payment_id, str(value), None)
    if not credited:
        return {
            "credited": False,
            "pending": False,
            "matchedPayment": True,
            "detail": "order_already_paid_or_amount_mismatch",
        }
    return {"credited": True, "pending": False}


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    if not s.yookassa_enabled:
        return {"ok": True, "skipped": True}

    try:
        body = await request.json()
    except Exception:
        return {"ok": False}

    obj = body.get("object") or {}
    if obj.get("status") != "succeeded":
        return {"ok": True}

    meta = obj.get("metadata") or {}
    user_id_meta = meta.get("userId")
    order_id = meta.get("orderId")
    if not order_id:
        mcid = obj.get("merchant_customer_id")
        if mcid is not None and str(mcid).strip():
            order_id = str(mcid).strip()

    payment_id = obj.get("id")
    value = (obj.get("amount") or {}).get("value")

    if not order_id or not payment_id or not value:
        return {"ok": True}

    order = db.query(PaymentOrder).filter(PaymentOrder.id == str(order_id)).first()
    if not order or order.status == "succeeded":
        return {"ok": True}

    if user_id_meta and order.user_id != str(user_id_meta):
        return {"ok": True}

    _try_credit_order_from_yookassa(db, order, payment_id, str(value), str(user_id_meta) if user_id_meta else None)
    return {"ok": True}
