from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .api_keys import hash_api_key
from .config import get_settings
from .database import SessionLocal
from .models import User, UserApiKey


def user_public_identifier(user: User) -> str:
    """Краткий идентификатор для сообщений об ошибках (логин или OAuth id)."""
    un = (user.username or "").strip()
    if un:
        return un
    if user.vk_user_id:
        return f"VK id {user.vk_user_id}"
    if user.yandex_user_id:
        return f"Яндекс id {user.yandex_user_id}"
    return user.id


def blocked_user_detail(user: User) -> dict[str, Any]:
    out: dict[str, Any] = {
        "code": "ACCOUNT_BLOCKED",
        "message": "Аккаунт заблокирован",
        "identifier": user_public_identifier(user),
        "userId": str(user.id),
    }
    un = (user.username or "").strip()
    if un:
        out["username"] = un
    return out


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def touch_user_last_login(db: Session, user_id: str) -> None:
    """Обновляет last_login_at (UTC naive) после успешной аутентификации."""
    n = datetime.utcnow()
    affected = (
        db.query(User).filter(User.id == user_id).update({"last_login_at": n}, synchronize_session=False)
    )
    if affected:
        db.commit()


def get_user_id_optional(request: Request) -> Optional[str]:
    return request.session.get("user_id")


def require_user_id(request: Request, db: Session = Depends(get_db)) -> str:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.is_blocked == 1:
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    return uid


def resolve_user_id(request: Request, db: Session) -> Optional[str]:
    """Сессия, внутренний вызов (бот) или ключ разработчика Authorization: Bearer iip_…"""
    secret = get_settings().internal_api_secret
    h_secret = request.headers.get("x-internal-secret")
    h_user = request.headers.get("x-user-id")
    if secret and h_secret == secret and h_user:
        u = db.query(User).filter(User.id == h_user).first()
        if u:
            if u.is_blocked == 1:
                raise HTTPException(status_code=403, detail=blocked_user_detail(u))
            return h_user
    auth_h = request.headers.get("authorization")
    if auth_h:
        parts = auth_h.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if token.startswith("iip_"):
                if len(token) < 20:
                    raise HTTPException(status_code=401, detail="Invalid API key")
                h = hash_api_key(token)
                row = db.query(UserApiKey).filter(UserApiKey.key_hash == h).first()
                if row and row.revoked_at is None:
                    u = db.query(User).filter(User.id == row.user_id).first()
                    if not u:
                        raise HTTPException(status_code=401, detail="Invalid API key")
                    if u.is_blocked == 1:
                        raise HTTPException(status_code=403, detail=blocked_user_detail(u))
                    return row.user_id
                raise HTTPException(status_code=401, detail="Invalid or revoked API key")
            raise HTTPException(status_code=401, detail="Invalid API key")
    uid = request.session.get("user_id")
    if not uid:
        return None
    u = db.query(User).filter(User.id == uid).first()
    if u and u.is_blocked == 1:
        raise HTTPException(status_code=403, detail=blocked_user_detail(u))
    return uid


def require_effective_user(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    uid = resolve_user_id(request, db)
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return uid


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """Доступ к /api/admin/*: пользователь в сессии с users.is_admin = 1."""
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == uid).first()
    if not user or user.is_admin != 1:
        raise HTTPException(status_code=403, detail="Admin only")
    if user.is_blocked == 1:
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    return uid
