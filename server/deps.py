from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal
from .models import User


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_id_optional(request: Request) -> Optional[str]:
    return request.session.get("user_id")


def require_user_id(request: Request) -> str:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return uid


def resolve_user_id(request: Request, db: Session) -> Optional[str]:
    """Сессия или внутренний вызов (бот)."""
    secret = get_settings().internal_api_secret
    h_secret = request.headers.get("x-internal-secret")
    h_user = request.headers.get("x-user-id")
    if secret and h_secret == secret and h_user:
        u = db.query(User).filter(User.id == h_user).first()
        if u:
            return h_user
    return request.session.get("user_id")


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
    return uid
