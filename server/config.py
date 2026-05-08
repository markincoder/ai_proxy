from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    openrouter_site_url: str = Field(default="http://localhost:8000", validation_alias="OPENROUTER_SITE_URL")
    openrouter_app_title: str = Field(default="AI Proxy", validation_alias="OPENROUTER_APP_TITLE")
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
    # Подпись в виджете VK ID Floating One Tap; если пусто — OPENROUTER_APP_TITLE
    vk_id_widget_app_name: str = Field(default="", validation_alias="VK_ID_WIDGET_APP_NAME")
    oauth_new_user_balance: str = Field(default="0", validation_alias="OAUTH_NEW_USER_BALANCE")

    internal_api_secret: str = Field(default="", validation_alias="INTERNAL_API_SECRET")

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


def get_settings() -> Settings:
    """Без кэша: после правок .env достаточно сохранить файл; следующий запрос увидит новые значения."""
    return Settings()
