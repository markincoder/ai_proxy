"""API-ключи разработчика (доступ к OpenRouter через сервис по Bearer iip_…)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..api_keys import generate_developer_api_key
from ..deps import get_db, require_user_id
from ..models import UserApiKey

router = APIRouter(prefix="/api/developer", tags=["developer"])


def _serialize_key_row(
    r: UserApiKey,
    *,
    plaintext: Optional[str] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "keyPrefix": r.key_prefix,
        "label": r.label,
        "createdAt": (r.created_at.isoformat() + "Z") if r.created_at else None,
        "revoked": r.revoked_at is not None,
    }
    if plaintext is not None:
        out["key"] = plaintext
    return out


class CreateKeyBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: Optional[str] = Field(default=None, max_length=128)


@router.get("/keys")
def list_my_keys(
    user_id: str = Depends(require_user_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(UserApiKey)
        .filter(UserApiKey.user_id == user_id)
        .order_by(UserApiKey.created_at.desc())
        .all()
    )
    return [_serialize_key_row(r) for r in rows]


@router.post("/keys")
def create_key(
    body: CreateKeyBody,
    user_id: str = Depends(require_user_id),
    db: Session = Depends(get_db),
):
    raw, key_hash, prefix = generate_developer_api_key()
    label = (body.label or "").strip() or None
    row = UserApiKey(
        id=str(uuid.uuid4()),
        user_id=user_id,
        label=label,
        key_hash=key_hash,
        key_prefix=prefix,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_key_row(row, plaintext=raw)


@router.delete("/keys/{key_id}")
def revoke_key(
    key_id: str,
    user_id: str = Depends(require_user_id),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserApiKey)
        .filter(UserApiKey.id == key_id, UserApiKey.user_id == user_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    if row.revoked_at is not None:
        return {"ok": True, "alreadyRevoked": True}
    row.revoked_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
