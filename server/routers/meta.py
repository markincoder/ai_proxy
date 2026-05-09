from fastapi import APIRouter

from ..config import get_settings

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/config")
def public_config():
    s = get_settings()
    out: dict = {
        "yookassaEnabled": s.yookassa_enabled,
        "oauthYandex": s.yandex_oauth_configured,
        "oauthVk": s.vk_oauth_configured,
    }
    base_url = s.public_app_url.rstrip("/")
    if s.yookassa_enabled or s.vk_oauth_configured:
        out["publicAppUrl"] = base_url
    if s.yookassa_enabled:
        if s.yookassa_shop_id:
            out["yookassaShopId"] = s.yookassa_shop_id
    return out
