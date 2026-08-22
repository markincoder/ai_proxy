from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import init_db
from .error_logging import install_exception_logging
from .static_cache import HTML_PAGE_CACHE_CONTROL, StaticCacheHeadersMiddleware
from .routers import admin as admin_router
from .routers import (
    auth,
    chat,
    conversations,
    developer,
    embeddings,
    meta,
    models_list,
    newsfeed,
    oauth,
    openai_compat,
    payments,
    support,
    video_jobs,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _html(path: Path) -> FileResponse:
    """Страницы HTML: короткий кэш + SWR при перезагрузке без лишней нагрузки на сервер."""
    return FileResponse(path, headers={"Cache-Control": HTML_PAGE_CACHE_CONTROL})


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio
    import logging

    init_db()
    sync_task = None
    try:
        from .services.catalog_sync_scheduler import catalog_sync_scheduler_loop

        sync_task = asyncio.create_task(catalog_sync_scheduler_loop())
    except Exception:
        logging.getLogger(__name__).exception("catalog sync scheduler failed to start")
    try:
        yield
    finally:
        if sync_task is not None:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    # Документация разработчиков — /developers; Swagger — /swagger (/docs как редирект).
    app = FastAPI(
        title="II Proxy",
        lifespan=lifespan,
        docs_url="/swagger",
        redoc_url="/redoc",
    )
    s = get_settings()
    app.add_middleware(
        SessionMiddleware,
        secret_key=s.session_secret,
        max_age=14 * 24 * 3600,
        same_site="lax",
        https_only=False,
    )
    app.add_middleware(StaticCacheHeadersMiddleware)

    app.include_router(auth.router)
    app.include_router(oauth.router)
    app.include_router(chat.router)
    app.include_router(embeddings.router)
    app.include_router(openai_compat.router)
    app.include_router(conversations.router)
    app.include_router(models_list.router)
    app.include_router(meta.router)
    app.include_router(payments.router)
    app.include_router(support.router)
    app.include_router(video_jobs.router)
    app.include_router(developer.router)
    app.include_router(newsfeed.router)
    app.include_router(admin_router.router)

    news_dir = get_settings().news_upload_path
    news_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media/news", StaticFiles(directory=str(news_dir)), name="news_media")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/.well-known/appspecific/com.chrome.devtools.json")
    def chrome_devtools_wellknown():
        """Chrome DevTools иногда запрашивает этот URL; без маршрута в логах лишний 404."""
        return {}

    @app.get("/favicon.ico")
    def favicon_ico():
        """Браузер всегда запрашивает /favicon.ico; отдаём SVG без отдельного .ico файла."""
        return FileResponse(
            STATIC_DIR / "favicon.svg",
            media_type="image/svg+xml",
        )

    @app.get("/yandex_{code}.html", include_in_schema=False)
    def yandex_webmaster_file(code: str):
        """HTML-файл подтверждения Яндекс.Вебмастера в корне сайта."""
        if not code.isalnum() or len(code) > 64:
            raise HTTPException(status_code=404)
        path = STATIC_DIR / f"yandex_{code}.html"
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt():
        return FileResponse(
            STATIC_DIR / "robots.txt",
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_xml():
        return FileResponse(
            STATIC_DIR / "sitemap.xml",
            media_type="application/xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/health")
    def health():
        """Живость процесса для балансировщика и мониторинга (БД не проверяется)."""
        return {"status": "ok"}

    @app.get("/")
    def index():
        return _html(STATIC_DIR / "index.html")

    @app.get("/consent")
    def consent_page():
        """Подтверждение оферты после входа, если в БД ещё нет отметки."""
        return HTMLResponse(
            (STATIC_DIR / "consent.html").read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/login")
    def login_page():
        """Подставляет флаги OAuth в HTML: fetch('/api/config') через ngrok часто не JSON."""
        s = get_settings()
        raw = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
        boot = {
            "oauthYandex": s.yandex_oauth_configured,
            "oauthVk": s.vk_oauth_configured,
        }
        snippet = f"<script>window.__LOGIN_BOOT__={json.dumps(boot)};</script>"
        html = raw.replace("</head>", snippet + "</head>", 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/tariffs")
    def tariffs_page():
        return _html(STATIC_DIR / "tariffs.html")

    @app.get("/news")
    def news_page():
        return _html(STATIC_DIR / "news.html")

    @app.get("/docs")
    def docs_deprecated_redirect():
        """Раньше отдавалась отдельная HTML-страница; содержание перенесено на /developers."""
        return RedirectResponse(url="/developers", status_code=301)

    @app.get("/developers")
    def developers_hub_page():
        return _html(STATIC_DIR / "developers.html")

    @app.get("/terms")
    def terms_page():
        return _html(STATIC_DIR / "terms.html")

    @app.get("/privacy")
    def privacy_page():
        return _html(STATIC_DIR / "privacy.html")

    @app.get("/contact")
    def contact_page():
        return _html(STATIC_DIR / "contact.html")

    @app.get("/settings")
    def settings_page():
        return _html(STATIC_DIR / "settings.html")

    @app.get("/account")
    def account_redirect():
        return RedirectResponse(url="/settings", status_code=301)

    @app.get("/transcribe")
    def transcribe_redirect():
        """Раньше была отдельная страница; распознавание — в чате (вкладка «Транскрипция» в моделях)."""
        return RedirectResponse(url="/", status_code=301)

    @app.get("/admin")
    def admin_page():
        return _html(STATIC_DIR / "admin.html")

    install_exception_logging(app)
    return app


app = create_app()
