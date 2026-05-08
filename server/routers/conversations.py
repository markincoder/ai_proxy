from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import get_db, resolve_user_id
from ..models import ChatThread

router = APIRouter(prefix="/api/v1", tags=["conversations"])


class CreateConversationBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_slug: str = Field(alias="modelSlug")


class PatchConversationBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_slug: str | None = Field(default=None, alias="modelSlug")
    title: str | None = None
    messages: list | None = None


def _row_list_item(t: ChatThread) -> dict:
    return {"id": t.id, "title": t.title, "modelSlug": t.model_slug}


def _thread_has_messages(t: ChatThread) -> bool:
    m = t.messages
    return isinstance(m, list) and len(m) > 0


def _row_full(t: ChatThread) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "modelSlug": t.model_slug,
        "messages": t.messages if t.messages is not None else [],
    }


@router.get("/conversations")
def list_conversations(request: Request, db: Session = Depends(get_db)):
    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    rows = (
        db.query(ChatThread)
        .filter(ChatThread.user_id == user_id)
        .order_by(ChatThread.updated_at.desc())
        .all()
    )
    # Пустые диалоги не показываем (создаются под первое сообщение и удаляются при смене модели)
    return [_row_list_item(t) for t in rows if _thread_has_messages(t)]


@router.post("/conversations")
def create_conversation(
    request: Request,
    body: CreateConversationBody,
    db: Session = Depends(get_db),
):
    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = ChatThread(user_id=user_id, model_slug=body.model_slug, messages=[])
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "title": t.title, "modelSlug": t.model_slug}


@router.get("/conversations/{thread_id}")
def get_conversation(request: Request, thread_id: str, db: Session = Depends(get_db)):
    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = (
        db.query(ChatThread)
        .filter(ChatThread.id == thread_id, ChatThread.user_id == user_id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    return _row_full(t)


@router.patch("/conversations/{thread_id}")
def patch_conversation(
    request: Request,
    thread_id: str,
    body: PatchConversationBody,
    db: Session = Depends(get_db),
):
    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = (
        db.query(ChatThread)
        .filter(ChatThread.id == thread_id, ChatThread.user_id == user_id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if body.model_slug is not None:
        t.model_slug = body.model_slug
    if body.title is not None:
        t.title = (body.title or "Новый чат")[:512]
    if body.messages is not None:
        t.messages = body.messages
    db.commit()
    return {"ok": True}


@router.delete("/conversations/{thread_id}")
def delete_conversation(request: Request, thread_id: str, db: Session = Depends(get_db)):
    user_id = resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    t = (
        db.query(ChatThread)
        .filter(ChatThread.id == thread_id, ChatThread.user_id == user_id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(t)
    db.commit()
    return {"ok": True}
