from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.db.models import User
from backend.infra.auth import get_db, require_admin
from backend.observability.metrics import render_process_metrics
from backend.observability.service import (
    build_observability_summary,
    build_operational_alerts,
    render_summary_prometheus,
)

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/summary")
async def observability_summary(
    hours: int = Query(default=24, ge=1, le=720),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return build_observability_summary(db, hours=hours)


@router.get("/alerts")
async def observability_alerts(
    hours: int = Query(default=24, ge=1, le=720),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return build_operational_alerts(build_observability_summary(db, hours=hours))


@router.get("/metrics", response_class=Response)
async def prometheus_metrics(
    hours: int = Query(default=24, ge=1, le=720),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = render_process_metrics() + render_summary_prometheus(
        build_observability_summary(db, hours=hours)
    )
    return Response(
        content=payload,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
