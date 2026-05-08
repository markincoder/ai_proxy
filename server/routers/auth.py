from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_db
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        return {
            "guest": True,
            "balance": None,
            "yookassa_enabled": get_settings().yookassa_enabled,
            "isAdmin": False,
        }
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "guest": False,
        "id": user.id,
        "phone": user.phone,
        "balance": str(user.balance),
        "yookassa_enabled": get_settings().yookassa_enabled,
        "isAdmin": bool(user.is_admin),
    }
