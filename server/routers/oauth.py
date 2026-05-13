"""OAuth2 (authorization code): Yandex ID и VK ID; VK ID SDK (Floating One Tap) → session."""

import base64
import hashlib
import re
import secrets
from decimal import Decimal
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import blocked_user_detail, get_db, touch_user_last_login, user_public_identifier
from ..models import User
from ..services.notify import schedule_new_user_notification

router = APIRouter(prefix="/api/auth/oauth", tags=["auth"])

VK_API_VER = "5.199"
# id.vk.ru тянет скрипты/статику с vk.ru; у части пользователей это даёт пустую форму «Ошибка загрузки».
# Документация FAQ указывает обмен кода на id.vk.com/oauth2/auth — тот же хост для /authorize.
VK_ID_HOST = "https://id.vk.com"

_MERGE_VK_ID_RE = re.compile(r"^[1-9]\d{0,30}$")
_MERGE_YANDEX_ID_RE = re.compile(r"^[1-9]\d{0,62}$")


def _merge_hints_dict(merge_vk: str | None, merge_yandex: str | None) -> dict[str, str]:
    hints: dict[str, str] = {}
    if merge_vk:
        v = merge_vk.strip()
        if _MERGE_VK_ID_RE.fullmatch(v):
            hints["vk"] = v
    if merge_yandex:
        v = merge_yandex.strip()
        if _MERGE_YANDEX_ID_RE.fullmatch(v):
            hints["yandex"] = v
    return hints


def _merge_hints_from_session_value(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    vk_raw = raw.get("vk")
    ya_raw = raw.get("yandex")
    return _merge_hints_dict(
        vk_raw if isinstance(vk_raw, str) else None,
        ya_raw if isinstance(ya_raw, str) else None,
    )


def _pick_merge_candidate(db: Session, hints: dict[str, str], *, attaching: str):
    """Подбор строки users: только второй oauth-id (VK«»Яндекс), без учёта логина/пароля."""
    if not hints:
        return None
    if attaching == "yandex":
        val = hints.get("vk")
        if val:
            return db.query(User).filter(User.vk_user_id == val).first()
        return None
    val = hints.get("yandex")
    if val:
        return db.query(User).filter(User.yandex_user_id == val).first()
    return None


def _ensure_yandex_user(db: Session, yid: str, hints: dict[str, str], *, initial: Decimal):
    existing = db.query(User).filter(User.yandex_user_id == yid).first()
    if existing:
        return existing, False
    cand = _pick_merge_candidate(db, hints, attaching="yandex")
    if cand is not None and cand.yandex_user_id is None:
        cand.yandex_user_id = yid
        db.commit()
        db.refresh(cand)
        return cand, False
    user = User(yandex_user_id=yid, balance=initial)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


def _ensure_vk_user(db: Session, vk_uid: str, hints: dict[str, str], *, initial: Decimal):
    existing = db.query(User).filter(User.vk_user_id == vk_uid).first()
    if existing:
        return existing, False
    cand = _pick_merge_candidate(db, hints, attaching="vk")
    if cand is not None and cand.vk_user_id is None:
        cand.vk_user_id = vk_uid
        db.commit()
        db.refresh(cand)
        return cand, False
    user = User(vk_user_id=vk_uid, balance=initial)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


def _public_base() -> str:
    return get_settings().public_app_url.rstrip("/")


def _yandex_callback_url() -> str:
    return f"{_public_base()}/api/auth/oauth/yandex/callback"


def _vk_callback_url() -> str:
    return f"{_public_base()}/api/auth/oauth/vk/callback"


def _vk_pkce_challenge(verifier: str) -> str:
    """S256 code_challenge для VK ID (id.vk.ru/authorize)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _login_redirect(error: str) -> RedirectResponse:
    return RedirectResponse(url=f"/login?oauth_error={error}", status_code=302)


def _login_blocked_redirect(user: User) -> RedirectResponse:
    ident = quote(user_public_identifier(user), safe="")
    uid_q = quote(str(user.id), safe="")
    return RedirectResponse(
        url=f"/login?oauth_error=blocked&blocked_id={uid_q}&blocked_uid={ident}",
        status_code=302,
    )


def _vk_user_id_from_access_token(access_token: str) -> str:
    """Только идентификатор VK (имя/ФИО не запрашиваем и не храним)."""
    r = httpx.get(
        "https://api.vk.com/method/users.get",
        params={
            "v": VK_API_VER,
            "access_token": access_token,
        },
        timeout=25.0,
    )
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise ValueError(str(payload.get("error")))
    arr = payload.get("response") or []
    if not arr:
        raise ValueError("empty response")
    return str(arr[0]["id"])


def _yandex_user_id(access_token: str) -> str:
    """Только числовой id Яндекса (без ФИО, email и т.д.)."""
    r = httpx.get(
        "https://login.yandex.ru/info",
        params={"format": "json"},
        headers={"Authorization": f"OAuth {access_token}"},
        timeout=25.0,
    )
    r.raise_for_status()
    data = r.json()
    raw_id = data.get("id")
    if raw_id is None:
        raise ValueError("yandex profile missing id")
    return str(raw_id)


class VkSdkSessionBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str
    merge_vk: str | None = None
    merge_yandex: str | None = None


@router.post("/vk/session")
def vk_session_from_sdk_token(request: Request, body: VkSdkSessionBody, db: Session = Depends(get_db)):
    """После VKID.Auth.exchangeCode на клиенте — создать cookie-сессию по access_token."""
    s = get_settings()
    if not s.vk_oauth_configured:
        raise HTTPException(status_code=404, detail="VK OAuth not configured")
    token = body.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="access_token required")
    try:
        uid = _vk_user_id_from_access_token(token)
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired VK access token")
    hints = _merge_hints_dict(body.merge_vk, body.merge_yandex)
    initial = Decimal(s.oauth_new_user_balance)
    user, created = _ensure_vk_user(db, uid, hints, initial=initial)
    if created:
        schedule_new_user_notification(
            str(user.id),
            user_public_identifier(user),
            "VK",
            str(user.balance),
        )
    if user.is_blocked == 1:
        raise HTTPException(status_code=403, detail=blocked_user_detail(user))
    request.session["user_id"] = user.id
    touch_user_last_login(db, user.id)
    return {"ok": True, "user_id": request.session.get("user_id")}


@router.get("/yandex/start")
def yandex_oauth_start(
    request: Request,
    merge_vk: str | None = None,
    merge_yandex: str | None = None,
):
    s = get_settings()
    if not s.yandex_oauth_configured:
        raise HTTPException(status_code=404, detail="Yandex OAuth not configured")
    request.session.pop("oauth_pending_merge_yandex", None)
    mh = _merge_hints_dict(merge_vk, merge_yandex)
    if mh:
        request.session["oauth_pending_merge_yandex"] = mh
    state = secrets.token_urlsafe(32)
    request.session["oauth_yandex_state"] = state
    # force_confirm=yes: всегда показать экран Яндекса с выбором аккаунта (не «тихий» вход в последний логин).
    qs = urlencode(
        {
            "response_type": "code",
            "client_id": s.yandex_oauth_client_id.strip(),
            "redirect_uri": _yandex_callback_url(),
            "state": state,
            "force_confirm": "yes",
        }
    )
    return RedirectResponse(url=f"https://oauth.yandex.ru/authorize?{qs}", status_code=302)


@router.get("/yandex/callback")
def yandex_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    s = get_settings()
    if not s.yandex_oauth_configured:
        raise HTTPException(status_code=404, detail="Yandex OAuth not configured")

    if error:
        return _login_redirect("yandex_denied")
    expected = request.session.pop("oauth_yandex_state", None)
    if not code or not state or expected != state:
        return _login_redirect("yandex_state")

    try:
        tok = httpx.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": s.yandex_oauth_client_id.strip(),
                "client_secret": s.yandex_oauth_client_secret.strip(),
                "redirect_uri": _yandex_callback_url(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=25.0,
        )
        tok.raise_for_status()
        body = tok.json()
        access = body.get("access_token")
        if not access:
            return _login_redirect("yandex_token")
        yid = _yandex_user_id(access)
    except (httpx.HTTPError, ValueError, KeyError):
        return _login_redirect("yandex_profile")

    hints = _merge_hints_from_session_value(request.session.pop("oauth_pending_merge_yandex", None))
    initial = Decimal(s.oauth_new_user_balance)
    user, created = _ensure_yandex_user(db, yid, hints, initial=initial)
    if created:
        schedule_new_user_notification(
            str(user.id),
            user_public_identifier(user),
            "Яндекс ID",
            str(user.balance),
        )

    if user.is_blocked == 1:
        return _login_blocked_redirect(user)
    request.session.pop("oauth_terms_ok", None)
    request.session["user_id"] = user.id
    touch_user_last_login(db, user.id)
    return RedirectResponse(url="/", status_code=302)


@router.get("/vk/start")
def vk_oauth_start(
    request: Request,
    merge_vk: str | None = None,
    merge_yandex: str | None = None,
):
    s = get_settings()
    if not s.vk_oauth_configured:
        raise HTTPException(status_code=404, detail="VK OAuth not configured")
    request.session.pop("oauth_pending_merge_vk", None)
    mh = _merge_hints_dict(merge_vk, merge_yandex)
    if mh:
        request.session["oauth_pending_merge_vk"] = mh
    # VK ID (приложения в кабинете id.vk.com): только id.vk.ru + PKCE; oauth.vk.com даёт Security Error.
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(32)
    request.session["oauth_vk_state"] = state
    request.session["oauth_vk_code_verifier"] = code_verifier
    qs = urlencode(
        {
            "response_type": "code",
            "client_id": s.vk_oauth_client_id.strip(),
            "redirect_uri": _vk_callback_url(),
            "state": state,
            "code_challenge": _vk_pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(url=f"{VK_ID_HOST}/authorize?{qs}", status_code=302)


@router.get("/vk/callback")
def vk_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    device_id: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    _ = error_description
    s = get_settings()
    if not s.vk_oauth_configured:
        raise HTTPException(status_code=404, detail="VK OAuth not configured")

    if error:
        return _login_redirect("vk_denied")
    expected = request.session.pop("oauth_vk_state", None)
    code_verifier = request.session.pop("oauth_vk_code_verifier", None)
    device_id = (device_id or "").strip()
    if not code or not state or expected != state or not code_verifier:
        return _login_redirect("vk_state")
    if not device_id:
        return _login_redirect("vk_token")

    try:
        r = httpx.post(
            f"{VK_ID_HOST}/oauth2/auth",
            data={
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
                "redirect_uri": _vk_callback_url(),
                "code": code,
                "client_id": s.vk_oauth_client_id.strip(),
                "device_id": device_id,
                "state": state,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=25.0,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return _login_redirect("vk_token")
        access_token = data.get("access_token")
        if not access_token:
            return _login_redirect("vk_token")
        vk_uid = _vk_user_id_from_access_token(access_token)
    except (httpx.HTTPError, KeyError, ValueError):
        return _login_redirect("vk_token")

    hints = _merge_hints_from_session_value(request.session.pop("oauth_pending_merge_vk", None))
    initial = Decimal(s.oauth_new_user_balance)
    user, created = _ensure_vk_user(db, vk_uid, hints, initial=initial)
    if created:
        schedule_new_user_notification(
            str(user.id),
            user_public_identifier(user),
            "VK",
            str(user.balance),
        )
    if user.is_blocked == 1:
        return _login_blocked_redirect(user)
    request.session.pop("oauth_terms_ok", None)
    request.session["user_id"] = user.id
    touch_user_last_login(db, user.id)
    return RedirectResponse(url="/", status_code=302)
