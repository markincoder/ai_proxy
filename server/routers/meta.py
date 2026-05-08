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
    if s.vk_oauth_configured:
        try:
            out["vkAppId"] = int(s.vk_oauth_client_id.strip())
        except ValueError:
            pass
        title = s.vk_id_widget_app_name.strip() or s.openrouter_app_title
        out["vkOneTapAppName"] = title
    return out
