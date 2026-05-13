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
    base_url = s.public_app_url.rstrip("/")
    out: dict = {
        "yookassaEnabled": s.yookassa_enabled,
        "oauthYandex": s.yandex_oauth_configured,
        "oauthVk": s.vk_oauth_configured,
        "siteBanner": site_banner_public(),
        "publicAppUrl": base_url,
        "openrouterFreeRouterSlug": (s.openrouter_free_router_slug or "").strip(),
    }
    # Только публичный id магазина для Simple Pay. YOOKASSA_SECRET_KEY в ответ не включается.
    if s.yookassa_enabled and s.yookassa_shop_id:
        out["yookassaShopId"] = s.yookassa_shop_id
    return out
