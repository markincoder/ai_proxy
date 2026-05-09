"""Генерация и проверка ключей доступа разработчика (Bearer iip_… в Authorization)."""

from __future__ import annotations

import hashlib
import secrets

_KEY_PREFIX = "iip_"


def generate_developer_api_key() -> tuple[str, str, str]:
    """
    Возвращает (полный_ключ_один_раз, sha256_hex, короткий_префикс_для_списка).
    Полный ключ начинается с iip_.
    """
    raw = _KEY_PREFIX + secrets.token_urlsafe(48)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    prefix = raw[:18] + "…"
    return raw, h, prefix


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
