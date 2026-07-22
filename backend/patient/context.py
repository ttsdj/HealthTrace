from __future__ import annotations

import os

from backend.db.models import User
from backend.infra.database import SessionLocal
from backend.patient.facts import list_patient_facts
from backend.patient.scope import ensure_user_scope

_PERSONAL_MARKERS = ("我", "我的", "本人", "结合病历", "结合报告", "结合我的")


def should_query_patient_context(query: str) -> bool:
    return any(marker in (query or "") for marker in _PERSONAL_MARKERS)


def required_resource_types(query: str) -> list[str]:
    text = query or ""
    required: list[str] = []
    if any(
        marker in text
        for marker in ("药", "服用", "剂量", "停药", "能不能吃", "可以吃", "正在吃")
    ):
        required.extend(["AllergyIntolerance", "MedicationStatement", "Condition"])
    if any(marker in text for marker in ("检查", "指标", "血压", "血糖", "化验")):
        required.extend(["Observation", "DiagnosticReport"])
    if any(marker in text for marker in ("过敏", "禁忌")):
        required.append("AllergyIntolerance")
    if any(marker in text for marker in ("病史", "诊断", "疾病")):
        required.append("Condition")
    return list(dict.fromkeys(required))


def build_verified_patient_context(username: str, query: str) -> tuple[str, dict]:
    """Read only the minimum patient facts needed for a personal query."""
    if not should_query_patient_context(query):
        return "", {"patient_context_accessed": False, "reason": "general_query"}

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            return "", {"patient_context_accessed": False, "reason": "user_not_found"}
        scope = ensure_user_scope(db, user)
        db.commit()
        resource_types = required_resource_types(query)
        facts = []
        if resource_types:
            for resource_type in resource_types:
                facts.extend(list_patient_facts(db, scope, resource_type))
        else:
            facts = list_patient_facts(db, scope)[:10]

        facts = [item for item in facts if item.verification_status != "model_inferred"]
        max_chars = max(500, int(os.getenv("PATIENT_CONTEXT_MAX_CHARS", "3000")))
        lines = ["【患者结构化事实：按来源核验后使用】"]
        for item in facts:
            effective = item.effective_start.isoformat() if item.effective_start else "时间未知"
            rendered = f"；值={item.value_json}" if item.value_json else ""
            lines.append(
                f"- {item.resource_type}: {item.display}{rendered}；时间={effective}；"
                f"状态={item.verification_status}；来源={item.source_type}"
            )
        context = ("\n".join(lines) if facts else "")[:max_chars]
        return context, {
            "patient_context_accessed": True,
            "required_patient_fields": resource_types,
            "patient_fact_count": len(facts),
            "patient_context_chars": len(context),
        }
    except Exception as exc:
        db.rollback()
        return "", {
            "patient_context_accessed": False,
            "reason": "patient_context_unavailable",
            "error": str(exc)[:200],
        }
    finally:
        db.close()
