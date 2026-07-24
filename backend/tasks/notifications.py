from __future__ import annotations

import json
import os
import smtplib
import hashlib
import hmac
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from backend.db.models import (
    HealthNotification,
    HealthNotificationDelivery,
    HealthTask,
)

EXTERNAL_CHANNELS = {"webhook", "email"}


def _masked_email(value: str) -> str:
    if "@" not in value:
        return ""
    local, domain = value.split("@", 1)
    return f"{local[:2]}***@{domain}"


def create_external_deliveries(
    db: Session,
    notification: HealthNotification,
    task: HealthTask,
) -> list[HealthNotificationDelivery]:
    payload = dict(task.payload_json or {})
    channels = {
        str(item).lower()
        for item in (payload.get("notification_channels") or ["in_app"])
    } & EXTERNAL_CHANNELS
    created: list[HealthNotificationDelivery] = []
    for channel in sorted(channels):
        existing = db.query(HealthNotificationDelivery).filter(
            HealthNotificationDelivery.notification_id == notification.id,
            HealthNotificationDelivery.channel == channel,
        ).first()
        if existing is not None:
            continue
        consent = payload.get("external_notification_consent") is True
        recipient_hint = _masked_email(str(payload.get("notification_email", ""))) if channel == "email" else "server-configured webhook"
        delivery = HealthNotificationDelivery(
            id=f"delivery-{uuid4()}",
            notification_id=notification.id,
            task_id=task.id,
            tenant_id=task.tenant_id,
            patient_id=task.patient_id,
            channel=channel,
            recipient_hint=recipient_hint,
            status="pending" if consent else "skipped",
            max_attempts=max(1, min(int(payload.get("notification_max_attempts", 3)), 5)),
            error_message="" if consent else "explicit_external_notification_consent_required",
        )
        db.add(delivery)
        created.append(delivery)
    return created


def _send_webhook(delivery, notification, task) -> None:
    url = os.getenv("HEALTHTRACE_NOTIFICATION_WEBHOOK_URL", "").strip()
    parsed = urlparse(url)
    local = parsed.hostname in {"127.0.0.1", "localhost"}
    allow_insecure = os.getenv("HEALTHTRACE_ALLOW_INSECURE_WEBHOOK", "false").lower() == "true"
    if not url or parsed.scheme not in ({"https"} if not (local and allow_insecure) else {"http", "https"}):
        raise ValueError("secure server-configured webhook URL is unavailable")
    payload = {
        "event": "healthtrace.notification",
        "notification_id": notification.id,
        "notification_type": notification.notification_type,
        "title": notification.title,
        "body": notification.body,
        "created_at": notification.created_at.isoformat(),
    }
    headers = {"Content-Type": "application/json"}
    secret = os.getenv("HEALTHTRACE_NOTIFICATION_WEBHOOK_SECRET", "").strip()
    if secret:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["X-HealthTrace-Signature"] = f"sha256={signature}"
    response = requests.post(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        timeout=float(os.getenv("HEALTHTRACE_NOTIFICATION_TIMEOUT_SECONDS", "5")),
    )
    response.raise_for_status()


def _send_email(delivery, notification, task) -> None:
    payload = dict(task.payload_json or {})
    recipient = str(payload.get("notification_email", "")).strip()
    host = os.getenv("HEALTHTRACE_SMTP_HOST", "").strip()
    sender = os.getenv("HEALTHTRACE_SMTP_FROM", "").strip()
    if not host or not sender or "@" not in recipient:
        raise ValueError("SMTP configuration or task recipient is unavailable")
    message = EmailMessage()
    message["Subject"] = f"[HealthTrace] {notification.title}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(notification.body)
    port = int(os.getenv("HEALTHTRACE_SMTP_PORT", "587"))
    timeout = float(os.getenv("HEALTHTRACE_NOTIFICATION_TIMEOUT_SECONDS", "5"))
    use_ssl = os.getenv("HEALTHTRACE_SMTP_SSL", "false").lower() == "true"
    client_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with client_cls(host, port, timeout=timeout) as client:
        if not use_ssl and os.getenv("HEALTHTRACE_SMTP_STARTTLS", "true").lower() == "true":
            client.starttls()
        username = os.getenv("HEALTHTRACE_SMTP_USERNAME", "")
        password = os.getenv("HEALTHTRACE_SMTP_PASSWORD", "")
        if username:
            client.login(username, password)
        client.send_message(message)


def dispatch_pending_deliveries(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 20,
    senders: dict | None = None,
) -> list[HealthNotificationDelivery]:
    timestamp = now or datetime.utcnow()
    query = db.query(HealthNotificationDelivery).filter(
        (HealthNotificationDelivery.status == "pending")
        | (
            (HealthNotificationDelivery.status == "retry_wait")
            & (HealthNotificationDelivery.next_retry_at.is_not(None))
            & (HealthNotificationDelivery.next_retry_at <= timestamp)
        )
    ).order_by(HealthNotificationDelivery.created_at.asc()).limit(max(1, min(limit, 100)))
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    deliveries = query.all()
    handlers = {"webhook": _send_webhook, "email": _send_email, **(senders or {})}
    handled: list[HealthNotificationDelivery] = []
    for delivery in deliveries:
        notification = db.query(HealthNotification).filter(HealthNotification.id == delivery.notification_id).first()
        task = db.query(HealthTask).filter(HealthTask.id == delivery.task_id).first()
        delivery.attempt_count += 1
        delivery.updated_at = timestamp
        try:
            if notification is None or task is None:
                raise LookupError("notification or task no longer exists")
            handler = handlers.get(delivery.channel)
            if handler is None:
                raise ValueError(f"unsupported notification channel: {delivery.channel}")
            handler(delivery, notification, task)
            delivery.status = "delivered"
            delivery.delivered_at = timestamp
            delivery.next_retry_at = None
            delivery.error_message = ""
        except Exception as exc:
            delivery.error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
            if delivery.attempt_count < delivery.max_attempts:
                delay = min(3600, 30 * (2 ** (delivery.attempt_count - 1)))
                delivery.status = "retry_wait"
                delivery.next_retry_at = timestamp + timedelta(seconds=delay)
            else:
                delivery.status = "failed"
                delivery.next_retry_at = None
        handled.append(delivery)
    db.flush()
    return handled


def notification_configuration_status() -> dict:
    webhook_url = os.getenv("HEALTHTRACE_NOTIFICATION_WEBHOOK_URL", "").strip()
    parsed = urlparse(webhook_url)
    webhook_secure = parsed.scheme == "https" and bool(parsed.hostname)
    smtp_host = os.getenv("HEALTHTRACE_SMTP_HOST", "").strip()
    smtp_sender = os.getenv("HEALTHTRACE_SMTP_FROM", "").strip()
    return {
        "external_dispatch_enabled": os.getenv(
            "HEALTHTRACE_EXTERNAL_NOTIFICATIONS_ENABLED", "false"
        ).lower()
        == "true",
        "webhook": {
            "configured": bool(webhook_url),
            "secure": webhook_secure,
            "signed": bool(
                os.getenv("HEALTHTRACE_NOTIFICATION_WEBHOOK_SECRET", "").strip()
            ),
            "host": parsed.hostname or "",
        },
        "email": {
            "configured": bool(smtp_host and smtp_sender),
            "host": smtp_host,
            "port": int(os.getenv("HEALTHTRACE_SMTP_PORT", "587")),
            "sender_hint": _masked_email(smtp_sender),
            "starttls": os.getenv("HEALTHTRACE_SMTP_STARTTLS", "true").lower()
            == "true",
            "ssl": os.getenv("HEALTHTRACE_SMTP_SSL", "false").lower() == "true",
        },
    }


def send_notification_probe(
    *,
    channel: str,
    recipient: str = "",
    senders: dict | None = None,
) -> dict:
    from types import SimpleNamespace

    handlers = {"webhook": _send_webhook, "email": _send_email, **(senders or {})}
    if channel not in handlers:
        raise ValueError("Notification probe channel must be webhook or email")
    now = datetime.utcnow()
    notification = SimpleNamespace(
        id=f"probe-{uuid4()}",
        notification_type="configuration_probe",
        title="HealthTrace notification configuration test",
        body="This is an explicitly confirmed HealthTrace delivery probe.",
        created_at=now,
    )
    task = SimpleNamespace(payload_json={"notification_email": recipient})
    delivery = SimpleNamespace(channel=channel)
    handlers[channel](delivery, notification, task)
    return {
        "channel": channel,
        "status": "delivered",
        "delivered_at": now.isoformat(),
        "recipient_hint": _masked_email(recipient) if channel == "email" else "server-configured webhook",
    }
