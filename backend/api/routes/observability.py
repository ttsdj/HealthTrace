from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.db.models import User
from backend.infra.auth import get_db, require_admin
from backend.observability.service import build_observability_summary

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/summary")
async def observability_summary(
    hours: int = Query(default=24, ge=1, le=720),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return build_observability_summary(db, hours=hours)
