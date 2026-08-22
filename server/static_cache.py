"""HTTP: Cache-Control для отдачи статики и фавиконки через приложение."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Основные ассеты версионируются query (?v=) — можно агрессивно кэшировать на CDN/браузере.
CACHE_STATIC_VERSIONED_ASSETS = (
    "public, max-age=31536000, immutable, stale-while-revalidate=604800"
)
# Иконки/картинки без ?v в URL — умеренный срок.
CACHE_STATIC_UNVERSIONED_MEDIA = "public, max-age=86400, stale-while-revalidate=3600"

HTML_PAGE_CACHE_CONTROL = (
    "private, max-age=60, stale-while-revalidate=300, stale-if-error=60"
)


class StaticCacheHeadersMiddleware(BaseHTTPMiddleware):
    """Добавляет Cache-Control там, где Starlette/FileResponse его не задаёт."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        resp = await call_next(request)
        if resp.status_code != 200 or "cache-control" in resp.headers:
            return resp

        path = request.url.path
        ct = (resp.media_type or resp.headers.get("content-type") or "").lower()

        if path.startswith("/static/"):
            if "javascript" in ct or ct.startswith("text/css"):
                resp.headers["Cache-Control"] = CACHE_STATIC_VERSIONED_ASSETS
            elif "image/" in ct or "font/" in ct or "svg+xml" in ct:
                resp.headers["Cache-Control"] = CACHE_STATIC_UNVERSIONED_MEDIA
            return resp

        if path.startswith("/media/news/"):
            resp.headers["Cache-Control"] = CACHE_STATIC_UNVERSIONED_MEDIA
            return resp

        if path == "/favicon.ico":
            resp.headers["Cache-Control"] = CACHE_STATIC_UNVERSIONED_MEDIA
            return resp

        if path in ("/robots.txt", "/sitemap.xml"):
            resp.headers["Cache-Control"] = "public, max-age=3600"
            return resp

        return resp
