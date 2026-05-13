import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import (
    blocked_user_detail,
    get_db,
    require_user_id,
    touch_user_last_login,
    user_public_identifier,
)
from ..model_favorite_defaults import DEFAULT_FAVORITE_MODEL_SLUGS, MAX_USER_MODEL_FAVORITES
from ..models import AiModel, User
from ..services.notify import schedule_new_user_notification
from ..passwords import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,32}$")

_TERMS_REQUIRED_DETAIL = (
    "Требуется согласие с условиями пользовательского соглашения и Политикой конфиденциальности"
)


class LoginPasswordBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class RegisterBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=128)
    password_repeat: str = Field(..., alias="passwordRepeat", min_length=8, max_length=128)


class AcceptTermsBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    terms_accepted: bool = Field(True, alias="termsAccepted")


def _me_terms_accepted(user: User) -> bool:
    return user.terms_accepted_at is not None


def _active_model_slugs(db: Session) -> set[str]:
    return {
        str(r[0])
        for r in db.query(AiModel.slug).filter(AiModel.is_active.is_(True)).all()
    }


def _normalize_favorite_slugs_against_catalog(raw: list[Any], cat: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        s = str(x).strip()
        if not s or len(s) > 255 or s not in cat or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= MAX_USER_MODEL_FAVORITES:
            break
    return out


def _resolve_user_favorite_slugs(user: User, db: Session) -> list[str]:
    """Первый запрос при favorite_model_slugs IS NULL — записать популярные из каталога."""
    cat = _active_model_slugs(db)
    if user.favorite_model_slugs is None:
        seeded = [s for s in DEFAULT_FAVORITE_MODEL_SLUGS if s in cat]
        user.favorite_model_slugs = seeded
        db.commit()
        db.refresh(user)
        return list(seeded)
    raw = user.favorite_model_slugs
    if not isinstance(raw, list):
        raw = []
    normalized = _normalize_favorite_slugs_against_catalog(raw, cat)
    if normalized != raw:
        user.favorite_model_slugs = normalized
        db.commit()
        db.refresh(user)
    return normalized


class SetUsernameBody(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)


class PatchMeBody(BaseModel):
    """Частичное обновление профиля (сессия)."""

    model_config = ConfigDict(populate_by_name=True)

    last_model_slug: Optional[str] = Field(None, alias="lastModelSlug")


class PutModelFavoritesBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    slugs: list[str] = Field(default_factory=list, max_length=MAX_USER_MODEL_FAVORITES)


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: Optional[str] = Field(None, min_length=3, max_length=32)
    current_password: Optional[str] = Field(None, alias="currentPassword")
    new_password: str = Field(..., min_length=8, max_length=128, alias="newPassword")
    new_password_repeat: str = Field(..., alias="newPasswordRepeat", min_length=8, max_length=128)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        return {
            "guest": True,
            "balance": None,
            "yookassa_enabled": get_settings().yookassa_enabled,
            "isAdmin": False,
            "usernameLocked": False,
            "hasPassword": False,
            "lastModelSlug": None,
            "vkUserId": None,
            "yandexUserId": None,
            "lastLoginAt": None,
            "termsAccepted": False,
        }
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.is_blocked == 1:
        request.session.clear()
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    uname = (user.username or "").strip()
    return {
        "guest": False,
        "id": user.id,
        "username": user.username,
        "usernameLocked": bool(uname),
        "hasPassword": user.password_hash is not None and len(str(user.password_hash)) > 0,
        "balance": str(user.balance),
        "yookassa_enabled": get_settings().yookassa_enabled,
        "isAdmin": user.is_admin == 1,
        "isBlocked": user.is_blocked == 1,
        "lastModelSlug": (user.last_chat_model_slug or None),
        "vkUserId": user.vk_user_id,
        "yandexUserId": user.yandex_user_id,
        "lastLoginAt": (user.last_login_at.isoformat() + "Z") if user.last_login_at else None,
        "termsAccepted": _me_terms_accepted(user),
    }


@router.patch("/me")
def patch_me(
    request: Request,
    body: PatchMeBody,
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.is_blocked == 1:
        request.session.clear()
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))

    if "last_model_slug" in body.model_fields_set:
        if body.last_model_slug is None:
            user.last_chat_model_slug = None
        else:
            raw = str(body.last_model_slug).strip()
            if not raw:
                user.last_chat_model_slug = None
            else:
                ok = (
                    db.query(AiModel)
                    .filter(
                        AiModel.slug == raw,
                        AiModel.is_active.is_(True),
                    )
                    .first()
                )
                if not ok:
                    raise HTTPException(
                        status_code=400,
                        detail="Неизвестная или отключённая модель",
                    )
                user.last_chat_model_slug = raw
        db.commit()
        db.refresh(user)

    uname = (user.username or "").strip()
    return {
        "guest": False,
        "id": user.id,
        "username": user.username,
        "usernameLocked": bool(uname),
        "hasPassword": user.password_hash is not None and len(str(user.password_hash)) > 0,
        "balance": str(user.balance),
        "yookassa_enabled": get_settings().yookassa_enabled,
        "isAdmin": user.is_admin == 1,
        "isBlocked": user.is_blocked == 1,
        "lastModelSlug": (user.last_chat_model_slug or None),
        "vkUserId": user.vk_user_id,
        "yandexUserId": user.yandex_user_id,
        "lastLoginAt": (user.last_login_at.isoformat() + "Z") if user.last_login_at else None,
        "termsAccepted": _me_terms_accepted(user),
    }


@router.get("/me/model-favorites")
def get_me_model_favorites(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.is_blocked == 1:
        request.session.clear()
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    slugs = _resolve_user_favorite_slugs(user, db)
    return {"slugs": slugs}


@router.put("/me/model-favorites")
def put_me_model_favorites(
    request: Request,
    body: PutModelFavoritesBody,
    db: Session = Depends(get_db),
):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.is_blocked == 1:
        request.session.clear()
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    cat = _active_model_slugs(db)
    normalized = _normalize_favorite_slugs_against_catalog(list(body.slugs), cat)
    user.favorite_model_slugs = normalized
    db.commit()
    db.refresh(user)
    return {"slugs": normalized}


@router.post("/login-password")
def login_password(request: Request, body: LoginPasswordBody, db: Session = Depends(get_db)):
    login = body.username.strip()
    if not login:
        raise HTTPException(status_code=400, detail="Укажите логин")
    user = db.query(User).filter(User.username == login).first()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if user.is_blocked == 1:
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    request.session["user_id"] = user.id
    touch_user_last_login(db, user.id)
    return {"ok": True, "userId": user.id, "termsAccepted": _me_terms_accepted(user)}


@router.post("/register")
def register(request: Request, body: RegisterBody, db: Session = Depends(get_db)):
    login = body.username.strip()
    if not _USERNAME_RE.match(login):
        raise HTTPException(
            status_code=400,
            detail="Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание",
        )
    if body.password != body.password_repeat:
        raise HTTPException(status_code=400, detail="Пароли не совпадают")

    existing = db.query(User).filter(User.username == login).first()
    if existing:
        raise HTTPException(status_code=409, detail="Этот логин уже занят")

    s = get_settings()
    try:
        initial = Decimal(s.oauth_new_user_balance)
    except Exception:
        initial = Decimal("0")

    user = User(
        username=login,
        password_hash=hash_password(body.password),
        balance=initial,
        terms_accepted_at=None,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Этот логин уже занят") from None
    db.refresh(user)
    request.session["user_id"] = user.id
    touch_user_last_login(db, user.id)
    schedule_new_user_notification(
        str(user.id),
        user_public_identifier(user),
        "форма (логин и пароль)",
        str(user.balance),
    )
    return {"ok": True, "userId": user.id, "termsAccepted": _me_terms_accepted(user)}


@router.post("/accept-terms")
def accept_terms(request: Request, body: AcceptTermsBody, db: Session = Depends(get_db)):
    """Фиксация согласия с офертой и политикой после входа (страница /consent)."""
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.is_blocked == 1:
        request.session.clear()
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    if not body.terms_accepted:
        raise HTTPException(status_code=400, detail=_TERMS_REQUIRED_DETAIL)
    if user.terms_accepted_at is None:
        user.terms_accepted_at = datetime.utcnow()
        db.commit()
        db.refresh(user)
    return {"ok": True, "termsAccepted": True}


@router.get("/username-available")
def username_available(u: str, db: Session = Depends(get_db)):
    """Подсказка для формы регистрации (true = можно использовать)."""
    login = (u or "").strip()
    if len(login) < 3:
        return {"available": False, "reason": "short"}
    if not _USERNAME_RE.match(login):
        return {"available": False, "reason": "format"}
    taken = db.query(User).filter(User.username == login).first() is not None
    return {"available": not taken, "reason": "taken" if taken else None}


@router.post("/set-username")
def set_username(
    request: Request,
    body: SetUsernameBody,
    db: Session = Depends(get_db),
):
    uid = require_user_id(request, db)
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if (user.username or "").strip():
        raise HTTPException(status_code=403, detail="Логин уже задан и не может быть изменён")
    login = body.username.strip()
    if not _USERNAME_RE.match(login):
        raise HTTPException(
            status_code=400,
            detail="Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание",
        )
    existing = db.query(User).filter(User.username == login).first()
    if existing:
        raise HTTPException(status_code=409, detail="Этот логин уже занят")
    user.username = login
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Этот логин уже занят") from None
    return {"ok": True, "username": login}


@router.post("/change-password")
def change_password(
    request: Request,
    body: ChangePasswordBody,
    db: Session = Depends(get_db),
):
    uid = require_user_id(request, db)
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    raw_login = (body.username or "").strip()
    if (user.username or "").strip():
        if raw_login and raw_login != (user.username or "").strip():
            raise HTTPException(status_code=403, detail="Логин уже задан и не может быть изменён")
    else:
        if not raw_login:
            raise HTTPException(status_code=400, detail="Укажите логин")
        if not _USERNAME_RE.match(raw_login):
            raise HTTPException(
                status_code=400,
                detail="Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание",
            )
        existing = db.query(User).filter(User.username == raw_login).first()
        if existing:
            raise HTTPException(status_code=409, detail="Этот логин уже занят")
        user.username = raw_login
    if body.new_password != body.new_password_repeat:
        raise HTTPException(status_code=400, detail="Пароли не совпадают")
    if user.password_hash:
        cur = (body.current_password or "").strip()
        if not cur:
            raise HTTPException(status_code=400, detail="Укажите текущий пароль")
        if not verify_password(cur, user.password_hash):
            raise HTTPException(status_code=401, detail="Неверный текущий пароль")
    user.password_hash = hash_password(body.new_password)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Этот логин уже занят") from None
    return {"ok": True}