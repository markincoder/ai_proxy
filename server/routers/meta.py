from fastapi import APIRouter

from ..config import get_settings
from ..database import SessionLocal
from ..models import SiteBanner

router = APIRouter(prefix="/api", tags=["meta"])


def site_banner_public() -> dict:
    """Публичное тело баннера для /api/banner и /api/config."""
    with SessionLocal() as db:
        row = db.query(SiteBanner).filter(SiteBanner.id == 1).first()
        if not row or not row.is_active:
            return {
                "active": False,
                "message": None,
                "scheduleText": None,
            }
        return {
            "active": True,
            "message": (row.message or "").strip() or None,
            "scheduleText": (row.schedule_text or "").strip() or None,
        }


@router.get("/banner")
def public_banner():
    return site_banner_public()


@router.get("/config")
def public_config():
    s = get_settings()
    out: dict = {
        "yookassaEnabled": s.yookassa_enabled,
        "oauthYandex": s.yandex_oauth_configured,
        "oauthVk": s.vk_oauth_configured,
        "siteBanner": site_banner_public(),
    }
    base_url = s.public_app_url.rstrip("/")
    if s.yookassa_enabled or s.vk_oauth_configured:
        out["publicAppUrl"] = base_url
    if s.yookassa_enabled:
        if s.yookassa_shop_id:
            out["yookassaShopId"] = s.yookassa_shop_id
    return out
