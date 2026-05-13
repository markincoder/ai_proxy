"""Ответы OpenRouter вида «недостаточно баланса/кредитов на ключе» (не баланс пользователя на сайте).

Для клиента возвращаем общую формулировку; админу — полный текст в Telegram и на почту.
"""

from __future__ import annotations

import threading
import time

from .services.notify import schedule_openrouter_low_balance_notification

# Единый текст для UI (чат и прочее). Не раскрывать $ и счёт провайдера.
SANITIZED_PROVIDER_CONNECTION_MESSAGE_RU = (
    "Сейчас нет устойчивой связи с провайдером моделей. Повторите запрос позже. "
    "Если ошибка повторяется, напишите в поддержку — раздел «Контакты»."
)

_DEBOUNCE_SEC = 600.0
_notify_lock = threading.Lock()
_last_notify_monotonic = 0.0


def is_openrouter_balance_or_credit_wall(body_text: str, http_status: int | None) -> bool:
    """HTTP 402/сообщение про balance/credits/minimum USD на ключе OpenRouter."""
    raw = (body_text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    compact_no_space = low.replace(" ", "")

    if http_status == 402 and any(
        x in low for x in ("balance", "credit", "payment", "fund", "usd", "$")
    ):
        return True
    if "requires at least" in low and (
        "balance" in low or "credit" in low or "$" in raw or "usd" in low
    ):
        return True
    if '"code":402' in compact_no_space and ("balance" in low or "credit" in low):
        return True
    if ("insufficient" in low or "top up" in low) and (
        "credit" in low or "balance" in low
    ):
        return True
    return False


def notify_openrouter_balance_wall_maybe(body_text: str, context: str) -> None:
    """Не чаще одного раза в _DEBOUNCE_SEC на процесс (антиспам при лавине запросов)."""
    global _last_notify_monotonic
    with _notify_lock:
        now = time.monotonic()
        if now - _last_notify_monotonic < _DEBOUNCE_SEC:
            return
        _last_notify_monotonic = now
    schedule_openrouter_low_balance_notification(body_text, context)
