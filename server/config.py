from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень репозитория (рядом с каталогом `server/`).
REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_file_paths() -> tuple[str, ...]:
    base = Path(__file__).resolve().parent
    # Сначала корень репозитория — обычный .env; затем server/.env может переопределить отдельные ключи.
    candidates = [base.parent / ".env", base / ".env", base.parent / "web" / ".env"]
    found = [str(p) for p in candidates if p.is_file()]
    return tuple(found) if found else (".env",)


def _env_is_true(s: str) -> bool:
    return s.strip().lower() not in ("false", "0", "no", "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_paths(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="sqlite:///./data/app.db", validation_alias="DATABASE_URL")
    session_secret: str = Field(
        default="change-me-in-production-use-openssl-rand",
        validation_alias=AliasChoices("SESSION_SECRET", "NEXTAUTH_SECRET"),
    )

    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    openrouter_app_title: str = Field(default="II Proxy", validation_alias="OPENROUTER_APP_TITLE")
    openrouter_api_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias="OPENROUTER_API_BASE_URL",
    )
    openrouter_free_router_slug: str = Field(default="openrouter/free", validation_alias="OPENROUTER_FREE_ROUTER_SLUG")
    thread_model_fallback_slug: str = Field(default="openai/gpt-4o", validation_alias="THREAD_MODEL_FALLBACK_SLUG")
    removed_openrouter_slugs: str = Field(default="", validation_alias="REMOVED_OPENROUTER_SLUGS")
    # То же, что для scripts/sync_openrouter_prices.py: коэффициенты для USD → баланс (чат usage.cost, видео, см. pricing_rub).
    openrouter_usd_rub: str = Field(default="100", validation_alias="OPENROUTER_USD_RUB")
    pricing_markup_mult: str = Field(default="1", validation_alias="PRICING_MARKUP_MULT")
    # true: httpx использует HTTP(S)_PROXY из окружения. false — прямой выход (если ConnectError/TLS через прокси).
    httpx_trust_env_openrouter: str = Field(default="true", validation_alias="OPENROUTER_HTTPX_TRUST_ENV")

    yookassa_env: str = Field(default="false", validation_alias="YOOKASSA_ENABLED")

    yookassa_shop_id: str = Field(default="", validation_alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", validation_alias="YOOKASSA_SECRET_KEY")
    public_app_url: str = Field(default="http://localhost:8000", validation_alias="NEXT_PUBLIC_APP_URL")

    yandex_oauth_client_id: str = Field(default="", validation_alias="YANDEX_OAUTH_CLIENT_ID")
    yandex_oauth_client_secret: str = Field(default="", validation_alias="YANDEX_OAUTH_CLIENT_SECRET")
    vk_oauth_client_id: str = Field(default="", validation_alias="VK_OAUTH_CLIENT_ID")
    vk_oauth_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("VK_OAUTH_CLIENT_SECRET", "VK_OAUTH_SECRET_KEY"),
    )
    oauth_new_user_balance: str = Field(default="0", validation_alias="OAUTH_NEW_USER_BALANCE")

    internal_api_secret: str = Field(default="", validation_alias="INTERNAL_API_SECRET")
    # Список через запятую: сканирование сохранённых чатов в админке (модерация).
    forbidden_message_keywords: str = Field(default="", validation_alias="FORBIDDEN_MESSAGE_KEYWORDS")

    # Каталог для загруженных картинок новостей (относительный путь — от корня репо; абсолютный — как есть).
    # В Docker: том на `/app/data` — БД, каталог моделей и `uploads/` в одном месте.
    news_upload_dir: str = Field(
        default="data/uploads/news",
        validation_alias=AliasChoices("NEWS_UPLOAD_DIR", "NEWS_IMAGE_UPLOAD_DIR"),
    )

    @property
    def yookassa_enabled(self) -> bool:
        return _env_is_true(self.yookassa_env)

    @property
    def yandex_oauth_configured(self) -> bool:
        return bool(self.yandex_oauth_client_id.strip() and self.yandex_oauth_client_secret.strip())

    @property
    def vk_oauth_configured(self) -> bool:
        return bool(self.vk_oauth_client_id.strip() and self.vk_oauth_client_secret.strip())

    @property
    def openrouter_httpx_trust_env(self) -> bool:
        return _env_is_true(self.httpx_trust_env_openrouter)

    @property
    def forbidden_message_keyword_list(self) -> list[str]:
        raw = self.forbidden_message_keywords.strip()
        if not raw:
            return []
        out: list[str] = []
        for x in raw.split(","):
            t = x.strip().lower()
            if t:
                out.append(t)
        return list(dict.fromkeys(out))

    @property
    def news_upload_path(self) -> Path:
        p = Path(self.news_upload_dir)
        return p.resolve() if p.is_absolute() else (REPO_ROOT / p).resolve()


def get_settings() -> Settings:
    """Без кэша: после правок .env достаточно сохранить файл; следующий запрос увидит новые значения."""
    return Settings()
