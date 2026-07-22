from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import PatientFact
from backend.patient.scope import PatientScope

_VERIFIED_STATUSES = {"user_confirmed", "clinician_verified"}
_ALIASES = {
    "blood_pressure": {"bloodpressure", "bp", "血压", "家庭血压", "收缩压", "舒张压"},
    "blood_glucose": {"bloodglucose", "glucose", "血糖", "空腹血糖", "餐后血糖"},
    "weight": {"weight", "bodyweight", "体重"},
    "heart_rate": {"heartrate", "pulse", "心率", "脉搏"},
    "temperature": {"temperature", "bodytemperature", "体温"},
}


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", (value or "").lower())


def _canonical_code(code: str) -> str:
    normalized = _normalize(code)
    for canonical, aliases in _ALIASES.items():
        if normalized in {_normalize(item) for item in aliases | {canonical}}:
            return canonical
    return normalized


def _matches(fact: PatientFact, requested_code: str) -> bool:
    target = _canonical_code(requested_code)
    candidates = {_canonical_code(fact.code), _canonical_code(fact.display)}
    return bool(target and target in candidates)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def _extract_value(value: dict, canonical: str, metric: str | None) -> tuple[float | None, str]:
    selected = metric or ""
    if canonical == "blood_pressure":
        selected = selected if selected in {"systolic", "diastolic"} else "systolic"
        number = _number(value.get(selected))
        if number is not None:
            return number, selected
        raw = value.get("value")
        if isinstance(raw, str) and "/" in raw:
            parts = raw.split("/", 1)
            return _number(parts[0 if selected == "systolic" else 1]), selected
    return _number(value.get(metric or "value")), selected or "value"


def calculate_observation_trend(
    db: Session,
    scope: PatientScope,
    code: str,
    *,
    metric: str | None = None,
    limit: int = 200,
) -> dict:
    """Calculate a scoped trend from verified numeric Observation facts."""
    facts = (
        db.query(PatientFact)
        .filter(
            PatientFact.tenant_id == scope.tenant_id,
            PatientFact.patient_id == scope.patient_id,
            PatientFact.resource_type == "Observation",
            PatientFact.active.is_(True),
            PatientFact.verification_status.in_(_VERIFIED_STATUSES),
            PatientFact.effective_start.is_not(None),
        )
        .order_by(PatientFact.effective_start.desc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    canonical = _canonical_code(code)
    points: list[dict] = []
    selected_metric = metric or ""
    for fact in facts:
        if not _matches(fact, code):
            continue
        number, resolved_metric = _extract_value(fact.value_json or {}, canonical, metric)
        if number is None:
            continue
        selected_metric = resolved_metric
        points.append(
            {
                "fact_id": fact.id,
                "effective_at": fact.effective_start.isoformat(),
                "value": number,
                "unit": str((fact.value_json or {}).get("unit", "")),
                "source_type": fact.source_type,
                "verification_status": fact.verification_status,
            }
        )
    points.sort(key=lambda item: item["effective_at"])
    if not points:
        return {
            "code": canonical or code,
            "metric": selected_metric or metric or "value",
            "count": 0,
            "points": [],
            "direction": "insufficient_data",
        }

    values = [float(item["value"]) for item in points]
    first = values[0]
    latest = values[-1]
    delta = latest - first
    percent_change = (delta / abs(first) * 100.0) if first else None
    direction = "stable"
    tolerance = max(abs(first) * 0.02, 0.01)
    if delta > tolerance:
        direction = "rising"
    elif delta < -tolerance:
        direction = "falling"

    start = datetime.fromisoformat(points[0]["effective_at"])
    end = datetime.fromisoformat(points[-1]["effective_at"])
    elapsed_days = max((end - start).total_seconds() / 86400.0, 0.0)
    return {
        "code": canonical or code,
        "metric": selected_metric or metric or "value",
        "count": len(points),
        "unit": points[-1]["unit"],
        "first": round(first, 4),
        "latest": round(latest, 4),
        "minimum": round(min(values), 4),
        "maximum": round(max(values), 4),
        "delta": round(delta, 4),
        "percent_change": round(percent_change, 2) if percent_change is not None else None,
        "direction": direction if len(points) >= 2 else "insufficient_data",
        "slope_per_day": round(delta / elapsed_days, 4) if elapsed_days > 0 else None,
        "start_at": points[0]["effective_at"],
        "end_at": points[-1]["effective_at"],
        "points": points,
    }
