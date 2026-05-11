from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import init_db
from .error_logging import install_exception_logging
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


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

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

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
        return FileResponse(STATIC_DIR / "tariffs.html")

    @app.get("/news")
    def news_page():
        return FileResponse(STATIC_DIR / "news.html")

    @app.get("/docs")
    def docs_deprecated_redirect():
        """Раньше отдавалась отдельная HTML-страница; содержание перенесено на /developers."""
        return RedirectResponse(url="/developers", status_code=307)

    @app.get("/developers")
    def developers_hub_page():
        return FileResponse(STATIC_DIR / "developers.html")

    @app.get("/terms")
    def terms_page():
        return FileResponse(STATIC_DIR / "terms.html")

    @app.get("/contact")
    def contact_page():
        return FileResponse(STATIC_DIR / "contact.html")

    @app.get("/settings")
    def settings_page():
        return FileResponse(STATIC_DIR / "settings.html")

    @app.get("/account")
    def account_redirect():
        return RedirectResponse(url="/settings", status_code=302)

    @app.get("/transcribe")
    def transcribe_redirect():
        """Раньше была отдельная страница; распознавание — в чате (вкладка «Транскрипция» в моделях)."""
        return RedirectResponse(url="/", status_code=302)

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC_DIR / "admin.html")

    install_exception_logging(app)
    return app


app = create_app()
