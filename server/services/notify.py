"""Уведомления администратору по почте и в Telegram (.env: MAIL_*, TELEGRAM_*)."""

from __future__ import annotations

import logging
import smtplib
import threading
from collections.abc import Callable
from email.message import EmailMessage

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 3900


def _send_mail(subject: str, body: str) -> None:
    s = get_settings()
    server = (s.mail_server or "").strip()
    user = (s.mail_username or "").strip()
    password = (s.mail_password or "").strip()
    to_addr = (s.mail_to or "").strip()
    if not (server and user and password and to_addr):
        return
    frm = (s.mail_from or "").strip() or user
    port = int(s.mail_port)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to_addr
    msg.set_content(body)
    if port == 465:
        with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(server, port, timeout=30) as smtp:
            if s.mail_starttls:
                smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)


def _send_telegram(text: str) -> None:
    s = get_settings()
    token = (s.telegram_bot_token or "").strip()
    chat_id = (s.telegram_chat_id or "").strip()
    if not token or not chat_id:
        return
    t = text if len(text) <= TELEGRAM_MAX else text[: TELEGRAM_MAX - 3] + "..."
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": t, "disable_web_page_preview": True},
        timeout=25.0,
    )
    r.raise_for_status()


def _notify_admin(subject: str, body: str) -> None:
    try:
        _send_mail(subject, body)
    except Exception:
        logger.exception("mail notify failed")
    try:
        _send_telegram(body)
    except Exception:
        logger.exception("telegram notify failed")


def _fire_and_forget(fn: Callable[[], None]) -> None:
    def runner() -> None:
        try:
            fn()
        except Exception:
            logger.exception("admin notify task failed")

    threading.Thread(target=runner, daemon=True).start()


def schedule_new_user_notification(user_id: str, public_label: str, source_ru: str, balance: str) -> None:
    def run() -> None:
        subject = f"[II Proxy] Новый пользователь: {public_label}"
        body = (
            "Зарегистрирован новый пользователь.\n\n"
            f"Способ: {source_ru}\n"
            f"ID: {user_id}\n"
            f"Идентификатор: {public_label}\n"
            f"Начальный баланс: {balance}\n"
        )
        _notify_admin(subject, body)

    _fire_and_forget(run)


def schedule_payment_notification(
    user_id: str,
    public_label: str,
    amount_rub: str,
    payment_id: str,
    order_id: str,
) -> None:
    def run() -> None:
        subject = f"[II Proxy] Оплата {amount_rub} ₽ — {public_label}"
        body = (
            "Успешное зачисление оплаты (ЮKassa).\n\n"
            f"Пользователь: {public_label}\n"
            f"ID пользователя: {user_id}\n"
            f"Сумма: {amount_rub} ₽\n"
            f"Платёж ЮKassa: {payment_id}\n"
            f"Заказ: {order_id}\n"
        )
        _notify_admin(subject, body)

    _fire_and_forget(run)
