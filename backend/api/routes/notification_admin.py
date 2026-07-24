from fastapi import APIRouter, Depends, HTTPException

from backend.db.models import User
from backend.infra.auth import require_admin
from backend.schemas.notifications import NotificationProbeRequest
from backend.tasks.notifications import (
    notification_configuration_status,
    send_notification_probe,
)

router = APIRouter(prefix="/admin/notifications", tags=["notification-admin"])


@router.get("/diagnostics")
async def notification_diagnostics(_: User = Depends(require_admin)):
    return notification_configuration_status()


@router.post("/probe")
async def notification_probe(
    request: NotificationProbeRequest,
    _: User = Depends(require_admin),
):
    if not request.confirm_external_delivery:
        raise HTTPException(
            status_code=409,
            detail="Explicit confirmation is required before sending an external probe",
        )
    if request.channel == "email" and "@" not in request.recipient:
        raise HTTPException(status_code=400, detail="A valid probe email is required")
    try:
        return send_notification_probe(
            channel=request.channel,
            recipient=request.recipient,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Notification probe failed: {type(exc).__name__}: {str(exc)[:300]}",
        ) from exc

