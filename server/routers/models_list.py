from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import AiModel

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
def list_models(db: Session = Depends(get_db)):
    rows = (
        db.query(AiModel)
        .filter(AiModel.is_active.is_(True))
        .order_by(AiModel.display_name.asc())
        .all()
    )
    return [
        {
            "id": m.id,
            "slug": m.slug,
            "displayName": m.display_name,
            "provider": m.provider,
            "inputPricePerMn": str(m.input_price_per_mn),
            "outputPricePerMn": str(m.output_price_per_mn),
            "fixedPrice": str(m.fixed_price) if m.fixed_price is not None else None,
            "supportsVision": m.supports_vision,
            "supportsImageGeneration": m.supports_image_generation,
            "supportsVideoGeneration": m.supports_video_generation,
            "supportsMusicGeneration": m.supports_music_generation,
            "supportsSpeech": m.supports_speech,
            "supportsTranscription": m.supports_transcription,
            "supportsEmbeddings": m.supports_embeddings,
            "supportsChat": m.supports_chat,
            "isFree": m.is_free,
            "descriptionRu": m.description_ru,
            "pricingNoteRu": m.pricing_note_ru,
            "capabilities": {
                "visionInput": m.supports_vision,
                "imageGeneration": m.supports_image_generation,
                "musicGeneration": m.supports_music_generation,
                "videoGeneration": m.supports_video_generation,
                "speech": m.supports_speech,
                "transcription": m.supports_transcription,
                "coding": m.supports_coding,
                "embeddings": m.supports_embeddings,
                "chat": m.supports_chat,
                "free": m.is_free,
            },
        }
        for m in rows
    ]
