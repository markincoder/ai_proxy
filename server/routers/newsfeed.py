"""Публичный список новостей (без авторизации)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import NewsPost

router = APIRouter(prefix="/api", tags=["news"])


def _serialize(n: NewsPost) -> dict[str, object]:
    return {
        "id": n.id,
        "publishedAt": (n.published_at.isoformat() + "Z") if n.published_at else None,
        "title": n.title,
        "imageUrl": n.image_url,
        "body": n.body,
    }


@router.get("/news")
def public_news_list(db: Session = Depends(get_db)):
    rows = (
        db.query(NewsPost)
        .order_by(NewsPost.published_at.desc())
        .all()
    )
    return [_serialize(r) for r in rows]
