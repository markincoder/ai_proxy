import base64
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_user_id, user_public_identifier
from ..models import SupportMessage, SupportTicket, User
from ..services.captcha_image import generate_captcha_code, normalize_captcha_answer, render_captcha_png
from ..services.notify import schedule_support_message_notification

router = APIRouter(prefix="/api/support", tags=["support"])

_CAPTCHA_SESSION_KEY = "support_captcha"
_CAPTCHA_TTL_SEC = 600
_CAPTCHA_MIN_SOLVE_SEC = 3.0
_TICKET_MIN_INTERVAL_SEC = 180
_TICKET_MAX_PER_HOUR = 5


def _preview(text: str, limit: int = 220) -> str:
    t = (text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


def _touch_ticket(ticket: SupportTicket, message: SupportMessage) -> None:
    ticket.last_sender_role = message.sender_role
    ticket.last_message_preview = _preview(message.body)
    ticket.updated_at = datetime.utcnow()


def _verify_captcha(request: Request, token: str, answer: str) -> None:
    payload = request.session.get(_CAPTCHA_SESSION_KEY) or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Капча устарела, обновите.")
    if payload.get("token") != token:
        raise HTTPException(status_code=400, detail="Капча устарела, обновите.")
    if time.time() > float(payload.get("expires_at") or 0):
        request.session.pop(_CAPTCHA_SESSION_KEY, None)
        raise HTTPException(status_code=400, detail="Капча устарела, обновите.")
    issued_at = float(payload.get("issued_at") or 0)
    if time.time() - issued_at < _CAPTCHA_MIN_SOLVE_SEC:
        request.session.pop(_CAPTCHA_SESSION_KEY, None)
        raise HTTPException(status_code=400, detail="Подождите и введите символы с картинки.")
    expected = normalize_captcha_answer(str(payload.get("answer") or ""))
    if normalize_captcha_answer(answer) != expected:
        raise HTTPException(status_code=400, detail="Неверная капча.")
    request.session.pop(_CAPTCHA_SESSION_KEY, None)


def _enforce_ticket_rate_limit(db: Session, uid: str) -> None:
    now = datetime.utcnow()
    since_hour = now - timedelta(hours=1)
    hour_count = (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == uid, SupportTicket.created_at >= since_hour)
        .count()
    )
    if hour_count >= _TICKET_MAX_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="Слишком много обращений за последний час. Попробуйте позже.",
        )
    since_interval = now - timedelta(seconds=_TICKET_MIN_INTERVAL_SEC)
    recent = (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == uid, SupportTicket.created_at >= since_interval)
        .count()
    )
    if recent >= 1:
        raise HTTPException(
            status_code=429,
            detail="Подождите несколько минут перед новым обращением.",
        )


class CaptchaResponse(BaseModel):
    token: str
    imageDataUrl: str
    expiresInSec: int


@router.get("/captcha", response_model=CaptchaResponse)
def support_captcha(request: Request):
    answer = generate_captcha_code()
    png = render_captcha_png(answer)
    token = secrets.token_hex(8)
    now = time.time()
    request.session[_CAPTCHA_SESSION_KEY] = {
        "token": token,
        "answer": answer,
        "issued_at": now,
        "expires_at": now + _CAPTCHA_TTL_SEC,
    }
    b64 = base64.b64encode(png).decode("ascii")
    return {
        "token": token,
        "imageDataUrl": f"data:image/png;base64,{b64}",
        "expiresInSec": _CAPTCHA_TTL_SEC,
    }


class TicketCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subject: str = Field(..., min_length=3, max_length=255)
    message: str = Field(..., min_length=4, max_length=8000)
    captcha_token: str = Field(..., alias="captchaToken", min_length=4, max_length=64)
    captcha_answer: str = Field(..., alias="captchaAnswer", min_length=1, max_length=12)
    website: str = Field(default="", max_length=200)


class TicketReplyBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class TicketSummary(BaseModel):
    id: str
    subject: str
    status: str
    lastSenderRole: Optional[str]
    lastMessagePreview: Optional[str]
    userUnreadCount: int
    updatedAt: str
    createdAt: str


class TicketMessageOut(BaseModel):
    id: str
    senderRole: str
    body: str
    createdAt: str


class TicketDetailOut(BaseModel):
    id: str
    subject: str
    status: str
    userUnreadCount: int
    createdAt: str
    updatedAt: str
    messages: list[TicketMessageOut]


class SupportUnreadOut(BaseModel):
    unreadCount: int


def _serialize_ticket(ticket: SupportTicket) -> TicketSummary:
    return TicketSummary(
        id=ticket.id,
        subject=ticket.subject,
        status=ticket.status,
        lastSenderRole=ticket.last_sender_role,
        lastMessagePreview=ticket.last_message_preview,
        userUnreadCount=ticket.user_unread_count,
        updatedAt=ticket.updated_at.isoformat() + "Z",
        createdAt=ticket.created_at.isoformat() + "Z",
    )


@router.get("/tickets", response_model=list[TicketSummary])
def list_user_tickets(
    db: Session = Depends(get_db),
    uid: str = Depends(require_user_id),
):
    rows = (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == uid)
        .order_by(SupportTicket.updated_at.desc())
        .all()
    )
    return [_serialize_ticket(t) for t in rows]


@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut)
def get_user_ticket(
    ticket_id: str,
    db: Session = Depends(get_db),
    uid: str = Depends(require_user_id),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket or ticket.user_id != uid:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if ticket.user_unread_count:
        ticket.user_unread_count = 0
        db.commit()
        db.refresh(ticket)
    messages = [
        TicketMessageOut(
            id=m.id,
            senderRole=m.sender_role,
            body=m.body,
            createdAt=m.created_at.isoformat() + "Z",
        )
        for m in ticket.messages
    ]
    return TicketDetailOut(
        id=ticket.id,
        subject=ticket.subject,
        status=ticket.status,
        userUnreadCount=ticket.user_unread_count,
        createdAt=ticket.created_at.isoformat() + "Z",
        updatedAt=ticket.updated_at.isoformat() + "Z",
        messages=messages,
    )


@router.post("/tickets", response_model=TicketSummary)
def create_ticket(
    request: Request,
    body: TicketCreateBody,
    db: Session = Depends(get_db),
    uid: str = Depends(require_user_id),
):
    _verify_captcha(request, body.captcha_token, body.captcha_answer)
    if body.website.strip():
        raise HTTPException(status_code=400, detail="Не удалось отправить обращение.")
    _enforce_ticket_rate_limit(db, uid)
    subject = body.subject.strip()
    message = body.message.strip()
    if not subject or not message:
        raise HTTPException(status_code=400, detail="Заполните тему и сообщение")
    ticket = SupportTicket(
        user_id=uid,
        subject=subject,
        status="open",
        user_unread_count=0,
        admin_unread_count=1,
    )
    msg = SupportMessage(
        ticket=ticket,
        sender_role="user",
        sender_id=uid,
        body=message,
    )
    _touch_ticket(ticket, msg)
    db.add(ticket)
    db.add(msg)
    db.commit()
    db.refresh(ticket)

    user = db.query(User).filter(User.id == uid).first()
    public_label = user_public_identifier(user) if user else uid
    schedule_support_message_notification(ticket.id, uid, public_label, subject, message)

    return _serialize_ticket(ticket)


@router.post("/tickets/{ticket_id}/messages", response_model=TicketMessageOut)
def reply_ticket(
    ticket_id: str,
    body: TicketReplyBody,
    db: Session = Depends(get_db),
    uid: str = Depends(require_user_id),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket or ticket.user_id != uid:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    msg_body = body.message.strip()
    if not msg_body:
        raise HTTPException(status_code=400, detail="Сообщение пустое")
    msg = SupportMessage(
        ticket=ticket,
        sender_role="user",
        sender_id=uid,
        body=msg_body,
    )
    _touch_ticket(ticket, msg)
    ticket.admin_unread_count = int(ticket.admin_unread_count or 0) + 1
    ticket.user_unread_count = 0
    db.add(msg)
    db.commit()
    db.refresh(msg)

    user = db.query(User).filter(User.id == uid).first()
    public_label = user_public_identifier(user) if user else uid
    schedule_support_message_notification(ticket.id, uid, public_label, ticket.subject, msg_body)

    return TicketMessageOut(
        id=msg.id,
        senderRole=msg.sender_role,
        body=msg.body,
        createdAt=msg.created_at.isoformat() + "Z",
    )


@router.get("/unread", response_model=SupportUnreadOut)
def support_unread_count(
    db: Session = Depends(get_db),
    uid: str = Depends(require_user_id),
):
    count = (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == uid, SupportTicket.user_unread_count > 0)
        .count()
    )
    return SupportUnreadOut(unreadCount=count)
