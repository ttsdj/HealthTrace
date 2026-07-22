from __future__ import annotations

import os

from backend.db.models import DocumentRecord, User
from backend.infra.database import SessionLocal
from backend.patient.facts import list_patient_facts
from backend.patient.retrieval import retrieve_patient_records
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
    if any(marker in text for marker in ("检查", "报告", "指标", "血压", "血糖", "化验")):
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
        indexed_document_exists = (
            db.query(DocumentRecord.id)
            .filter(
                DocumentRecord.document_domain == "patient_private",
                DocumentRecord.tenant_id == scope.tenant_id,
                DocumentRecord.patient_id == scope.patient_id,
                DocumentRecord.owner_user_id == scope.user_id,
                DocumentRecord.status == "indexed",
            )
            .first()
            is not None
        )
        patient_record_result = {"docs": [], "mode": "not_requested", "attempts": []}
        if indexed_document_exists:
            try:
                patient_record_result = retrieve_patient_records(query, scope, top_k=3)
            except Exception as exc:
                patient_record_result = {
                    "docs": [],
                    "mode": "unavailable",
                    "attempts": [
                        {
                            "mode": "patient_record",
                            "status": "error",
                            "error": str(exc)[:200],
                        }
                    ],
                }

        max_chars = max(500, int(os.getenv("PATIENT_CONTEXT_MAX_CHARS", "3000")))
        sections: list[str] = []
        if facts:
            fact_lines = ["【患者结构化事实：按来源核验后使用】"]
            for item in facts:
                effective = item.effective_start.isoformat() if item.effective_start else "时间未知"
                rendered = f"；值={item.value_json}" if item.value_json else ""
                fact_lines.append(
                    f"- {item.resource_type}: {item.display}{rendered}；时间={effective}；"
                    f"状态={item.verification_status}；来源={item.source_type}"
                )
            sections.append("\n".join(fact_lines))

        record_docs = patient_record_result.get("docs") or []
        if record_docs:
            record_lines = [
                "【患者私有文档检索证据：仅作来源证据，未结构化核验，不得直接当作确诊事实】"
            ]
            for index, item in enumerate(record_docs, start=1):
                record_lines.append(
                    f"[{index}] {item.get('filename', '患者文档')} 第"
                    f"{int(item.get('page_number', 0)) + 1}页：{item.get('text', '')}"
                )
            sections.append("\n".join(record_lines))

        context = "\n\n".join(sections)[:max_chars]
        return context, {
            "patient_context_accessed": True,
            "required_patient_fields": resource_types,
            "patient_fact_count": len(facts),
            "patient_record_accessed": indexed_document_exists,
            "patient_record_hits": len(record_docs),
            "patient_record_mode": patient_record_result.get("mode", "not_requested"),
            "patient_record_attempts": patient_record_result.get("attempts", []),
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
