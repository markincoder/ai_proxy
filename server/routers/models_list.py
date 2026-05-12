from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_db
from ..services.models_catalog_cache import get_public_models_cached

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
def list_models(response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ttl = float(s.models_list_cache_ttl_sec)
    payload = get_public_models_cached(db, ttl)
    if ttl > 0:
        client_max = min(int(ttl), 300)
        response.headers["Cache-Control"] = f"public, max-age={client_max}"
    else:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return payload
