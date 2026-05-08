from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models import AiModel, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _serialize_model(m: AiModel) -> dict:
    return {
        "id": m.id,
        "slug": m.slug,
        "displayName": m.display_name,
        "provider": m.provider,
        "inputPricePerMn": str(m.input_price_per_mn),
        "outputPricePerMn": str(m.output_price_per_mn),
        "fixedPrice": str(m.fixed_price) if m.fixed_price is not None else None,
        "isActive": m.is_active,
        "supportsVision": m.supports_vision,
        "supportsImageGeneration": m.supports_image_generation,
        "supportsMusicGeneration": m.supports_music_generation,
        "supportsVideoGeneration": m.supports_video_generation,
        "supportsCoding": m.supports_coding,
        "isFree": m.is_free,
        "descriptionRu": m.description_ru,
        "pricingNoteRu": m.pricing_note_ru,
    }


@router.get("/users")
def admin_list_users(_: str = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.created_at.desc()).limit(500).all()
    return [
        {
            "id": u.id,
            "phone": u.phone,
            "name": u.name,
            "balance": str(u.balance),
            "vkUserId": u.vk_user_id,
            "yandexUserId": u.yandex_user_id,
            "createdAt": (u.created_at.isoformat() + "Z") if u.created_at else None,
        }
        for u in rows
    ]


@router.get("/models")
def admin_list_models(_: str = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(AiModel).order_by(AiModel.display_name.asc()).all()
    return [_serialize_model(m) for m in rows]


class AdminModelPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: Optional[str] = Field(None, alias="displayName")
    provider: Optional[str] = None
    input_price_per_mn: Optional[Decimal] = Field(None, alias="inputPricePerMn")
    output_price_per_mn: Optional[Decimal] = Field(None, alias="outputPricePerMn")
    fixed_price: Optional[Decimal] = Field(None, alias="fixedPrice")
    is_active: Optional[bool] = Field(None, alias="isActive")
    supports_vision: Optional[bool] = Field(None, alias="supportsVision")
    supports_image_generation: Optional[bool] = Field(None, alias="supportsImageGeneration")
    supports_music_generation: Optional[bool] = Field(None, alias="supportsMusicGeneration")
    supports_video_generation: Optional[bool] = Field(None, alias="supportsVideoGeneration")
    supports_coding: Optional[bool] = Field(None, alias="supportsCoding")
    is_free: Optional[bool] = Field(None, alias="isFree")
    description_ru: Optional[str] = Field(None, alias="descriptionRu")
    pricing_note_ru: Optional[str] = Field(None, alias="pricingNoteRu")


@router.patch("/models/{model_id}")
def admin_patch_model(
    model_id: str,
    body: AdminModelPatch,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(AiModel).filter(AiModel.id == model_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        return _serialize_model(row)
    for key, val in data.items():
        setattr(row, key, val)
    db.commit()
    db.refresh(row)
    return _serialize_model(row)
