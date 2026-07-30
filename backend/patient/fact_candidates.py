from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from backend.db.models import (
    DocumentRecord,
    ParentChunk,
    PatientFact,
    PatientFactCandidate,
)
from backend.medical_nlp.safety import redact_sensitive_text
from backend.patient.facts import FHIR_LIKE_RESOURCE_TYPES, create_patient_fact
from backend.patient.scope import PatientScope
from backend.patient.time import utc_naive

_DATE_RE = re.compile(r"(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?")
_LINE_PATTERNS = (
    ("AllergyIntolerance", re.compile(r"(?:过敏史|药物过敏)\s*[:：]\s*([^\n。；;]{2,120})")),
    ("MedicationStatement", re.compile(r"(?:当前用药|服用药物|用药)\s*[:：]\s*([^\n。；;]{2,160})")),
    ("Condition", re.compile(r"(?:诊断|既往史|疾病史)\s*[:：]\s*([^\n。；;]{2,160})")),
    ("Procedure", re.compile(r"(?:手术史|操作)\s*[:：]\s*([^\n。；;]{2,160})")),
    ("Immunization", re.compile(r"(?:疫苗|免疫接种)\s*[:：]\s*([^\n。；;]{2,160})")),
)


def _scope_candidate_query(db: Session, scope: PatientScope):
    return db.query(PatientFactCandidate).filter(
        PatientFactCandidate.tenant_id == scope.tenant_id,
        PatientFactCandidate.patient_id == scope.patient_id,
        PatientFactCandidate.owner_user_id == scope.user_id,
    )


def _scoped_document(db: Session, scope: PatientScope, document_id: str) -> DocumentRecord:
    record = (
        db.query(DocumentRecord)
        .filter(
            DocumentRecord.id == document_id,
            DocumentRecord.document_domain == "patient_private",
            DocumentRecord.tenant_id == scope.tenant_id,
            DocumentRecord.patient_id == scope.patient_id,
            DocumentRecord.owner_user_id == scope.user_id,
        )
        .first()
    )
    if record is None:
        raise ValueError("Patient document not found in the authenticated scope")
    if record.status != "indexed":
        raise ValueError("Patient document must be indexed before fact extraction")
    return record


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return utc_naive(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return utc_naive(datetime.fromisoformat(text))
    except ValueError:
        match = _DATE_RE.search(text)
        if not match:
            return None
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )


def _candidate_key(document_id: str, item: dict) -> str:
    payload = "|".join(
        (
            document_id,
            str(item.get("resource_type") or ""),
            str(item.get("display") or "").strip().lower(),
            str(item.get("effective_start") or ""),
            str(item.get("source_chunk_id") or ""),
        )
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _normalize_candidate(item: dict, chunks_by_id: dict[str, ParentChunk]) -> dict | None:
    resource_type = str(item.get("resource_type") or "").strip()
    display = str(item.get("display") or "").strip()
    if resource_type not in FHIR_LIKE_RESOURCE_TYPES or not display:
        return None
    source_chunk_id = str(item.get("source_chunk_id") or "").strip() or None
    source = chunks_by_id.get(source_chunk_id or "")
    source_page = item.get("source_page")
    if source_page is None and source is not None:
        source_page = source.page_number
    try:
        source_page = int(source_page) if source_page is not None else None
    except (TypeError, ValueError):
        source_page = None
    try:
        confidence = max(0.0, min(float(item.get("confidence", 0.5)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.5
    value = item.get("value")
    if not isinstance(value, dict):
        value = {"text": str(value)} if value not in (None, "") else {}
    evidence = str(item.get("evidence_text") or "").strip()
    if not evidence and source is not None:
        evidence = source.text[:1000]
    return {
        "resource_type": resource_type,
        "display": display[:500],
        "value": value,
        "clinical_status": str(item.get("clinical_status") or "active")[:40],
        "effective_start": _parse_datetime(item.get("effective_start")),
        "effective_end": _parse_datetime(item.get("effective_end")),
        "time_precision": str(item.get("time_precision") or "approximate")[:20],
        "confidence": confidence,
        "source_page": source_page,
        "source_chunk_id": source_chunk_id,
        "evidence_text": evidence[:1200],
    }


def _extract_json_array(content: str) -> list[dict]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("LLM extraction did not return a JSON array")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("LLM extraction payload is not a list")
    return [item for item in payload if isinstance(item, dict)]


def _llm_extract(source_text: str) -> list[dict]:
    from backend.chat.runtime import fast_model

    system = (
        "You extract candidate facts from a patient-owned medical document. Return only a JSON array. "
        "Never diagnose or infer facts not explicitly present. Allowed resource_type values: "
        + ", ".join(sorted(FHIR_LIKE_RESOURCE_TYPES))
        + ". Each object must include resource_type, display, value (object), clinical_status, "
        "effective_start (ISO date or null), effective_end, time_precision, confidence (0-1), "
        "source_page, source_chunk_id, and a short verbatim evidence_text. "
        "If evidence is ambiguous, omit it. Do not include reasoning."
    )
    response = fast_model.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=source_text),
        ]
    )
    return _extract_json_array(str(response.content or ""))


def _rule_extract(chunks: list[ParentChunk]) -> list[dict]:
    candidates: list[dict] = []
    for chunk in chunks:
        text = chunk.text or ""
        date = _parse_datetime(text)
        for resource_type, pattern in _LINE_PATTERNS:
            for match in pattern.finditer(text):
                display = match.group(1).strip()
                candidates.append(
                    {
                        "resource_type": resource_type,
                        "display": display,
                        "value": {},
                        "effective_start": date,
                        "time_precision": "day" if date else "approximate",
                        "confidence": 0.58,
                        "source_page": chunk.page_number,
                        "source_chunk_id": chunk.chunk_id,
                        "evidence_text": match.group(0),
                    }
                )

        observations = (
            (
                "血压",
                re.compile(r"血压\s*[:：]?\s*(\d{2,3})\s*[/／]\s*(\d{2,3})\s*(mmHg)?", re.I),
                lambda match: {
                    "systolic": int(match.group(1)),
                    "diastolic": int(match.group(2)),
                    "unit": match.group(3) or "mmHg",
                },
            ),
            (
                "血糖",
                re.compile(r"(?:空腹)?血糖\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mmol/L)?", re.I),
                lambda match: {
                    "value": float(match.group(1)),
                    "unit": match.group(2) or "mmol/L",
                },
            ),
        )
        for display, pattern, renderer in observations:
            for match in pattern.finditer(text):
                candidates.append(
                    {
                        "resource_type": "Observation",
                        "display": display,
                        "value": renderer(match),
                        "effective_start": date,
                        "time_precision": "day" if date else "approximate",
                        "confidence": 0.72,
                        "source_page": chunk.page_number,
                        "source_chunk_id": chunk.chunk_id,
                        "evidence_text": match.group(0),
                    }
                )
    return candidates


def extract_candidates_for_document(
    db: Session,
    scope: PatientScope,
    document_id: str,
    *,
    use_llm: bool,
    consent_external_processing: bool,
) -> tuple[list[PatientFactCandidate], str, str]:
    if os.getenv("HEALTHTRACE_FACT_CANDIDATES_ENABLED", "true").lower() != "true":
        raise ValueError("Patient fact candidate extraction is disabled")
    _scoped_document(db, scope, document_id)
    chunks = (
        db.query(ParentChunk)
        .filter(
            ParentChunk.document_id == document_id,
            ParentChunk.tenant_id == scope.tenant_id,
            ParentChunk.patient_id == scope.patient_id,
            ParentChunk.chunk_level == 1,
        )
        .order_by(ParentChunk.page_number, ParentChunk.chunk_idx)
        .all()
    )
    if not chunks:
        raise ValueError("No patient document text is available for fact extraction")

    max_chars = max(2000, int(os.getenv("PATIENT_FACT_EXTRACTION_MAX_CHARS", "16000")))
    source_parts = []
    consumed = 0
    for chunk in chunks:
        part = (
            f"[SOURCE chunk_id={chunk.chunk_id} page={chunk.page_number}]\n"
            f"{chunk.text}\n"
        )
        if consumed + len(part) > max_chars:
            part = part[: max_chars - consumed]
        if not part:
            break
        source_parts.append(part)
        consumed += len(part)
        if consumed >= max_chars:
            break
    source_text = "\n".join(source_parts)

    warning = ""
    method = "local_rules"
    raw_items: list[dict] = []
    if use_llm:
        if not consent_external_processing:
            raise ValueError("Explicit consent is required before sending private text to the configured LLM")
        try:
            redacted_source_text, _ = redact_sensitive_text(source_text)
            raw_items = _llm_extract(redacted_source_text)
            method = "llm"
        except Exception as exc:
            warning = f"LLM extraction unavailable; local rules used: {str(exc)[:180]}"
            raw_items = _rule_extract(chunks)
            method = "local_rules_fallback"
    else:
        raw_items = _rule_extract(chunks)

    chunks_by_id = {item.chunk_id: item for item in chunks}
    normalized = []
    for raw in raw_items[: max(1, int(os.getenv("PATIENT_FACT_CANDIDATE_LIMIT", "50")))]:
        item = _normalize_candidate(raw, chunks_by_id)
        if item is not None:
            normalized.append(item)

    created: list[PatientFactCandidate] = []
    for item in normalized:
        key = _candidate_key(document_id, item)
        existing = (
            _scope_candidate_query(db, scope)
            .filter(PatientFactCandidate.candidate_key == key)
            .first()
        )
        if existing is not None:
            created.append(existing)
            continue
        candidate = PatientFactCandidate(
            id=f"candidate-{uuid4()}",
            tenant_id=scope.tenant_id,
            patient_id=scope.patient_id,
            owner_user_id=scope.user_id,
            document_id=document_id,
            candidate_key=key,
            resource_type=item["resource_type"],
            display=item["display"],
            value_json=item["value"],
            clinical_status=item["clinical_status"],
            effective_start=item["effective_start"],
            effective_end=item["effective_end"],
            time_precision=item["time_precision"],
            confidence=item["confidence"],
            source_page=item["source_page"],
            source_chunk_id=item["source_chunk_id"],
            evidence_text=item["evidence_text"],
            extraction_method=method,
            status="pending",
        )
        db.add(candidate)
        db.flush()
        created.append(candidate)
    return created, method, warning


def list_fact_candidates(
    db: Session,
    scope: PatientScope,
    *,
    status: str | None = None,
    document_id: str | None = None,
) -> list[PatientFactCandidate]:
    query = _scope_candidate_query(db, scope)
    if status:
        query = query.filter(PatientFactCandidate.status == status)
    if document_id:
        query = query.filter(PatientFactCandidate.document_id == document_id)
    return query.order_by(PatientFactCandidate.created_at.desc()).all()


def confirm_fact_candidate(
    db: Session,
    scope: PatientScope,
    candidate_id: str,
    updates: dict,
) -> tuple[PatientFactCandidate, PatientFact]:
    candidate = _scope_candidate_query(db, scope).filter(PatientFactCandidate.id == candidate_id).first()
    if candidate is None:
        raise ValueError("Fact candidate not found")
    if candidate.status == "rejected":
        raise ValueError("Rejected fact candidate cannot be confirmed")
    if candidate.status == "confirmed" and candidate.confirmed_fact_id:
        fact = (
            db.query(PatientFact)
            .filter(
                PatientFact.id == candidate.confirmed_fact_id,
                PatientFact.tenant_id == scope.tenant_id,
                PatientFact.patient_id == scope.patient_id,
            )
            .first()
        )
        if fact is not None:
            return candidate, fact

    for key, attribute in (
        ("resource_type", "resource_type"),
        ("display", "display"),
        ("value", "value_json"),
        ("clinical_status", "clinical_status"),
        ("effective_start", "effective_start"),
        ("effective_end", "effective_end"),
        ("time_precision", "time_precision"),
    ):
        if updates.get(key) is not None:
            setattr(candidate, attribute, updates[key])
    if candidate.effective_start is None:
        raise ValueError("Please provide the clinical event date before confirmation")

    fact = create_patient_fact(
        db,
        scope,
        resource_type=candidate.resource_type,
        display=candidate.display,
        value=candidate.value_json,
        clinical_status=candidate.clinical_status,
        verification_status="user_confirmed",
        confidence=1.0,
        source_type="document_extracted_user_confirmed",
        source_document_id=candidate.document_id,
        source_page=candidate.source_page,
        source_chunk_id=candidate.source_chunk_id,
        effective_start=candidate.effective_start,
        effective_end=candidate.effective_end,
        time_precision=candidate.time_precision,
    )
    candidate.status = "confirmed"
    candidate.confirmed_fact_id = fact.id
    candidate.reviewed_by_user_id = scope.user_id
    candidate.reviewed_at = datetime.utcnow()
    candidate.updated_at = datetime.utcnow()
    db.flush()
    return candidate, fact


def reject_fact_candidate(
    db: Session,
    scope: PatientScope,
    candidate_id: str,
) -> PatientFactCandidate:
    candidate = _scope_candidate_query(db, scope).filter(PatientFactCandidate.id == candidate_id).first()
    if candidate is None:
        raise ValueError("Fact candidate not found")
    if candidate.status == "confirmed":
        raise ValueError("Confirmed fact candidate cannot be rejected")
    candidate.status = "rejected"
    candidate.reviewed_by_user_id = scope.user_id
    candidate.reviewed_at = datetime.utcnow()
    candidate.updated_at = datetime.utcnow()
    db.flush()
    return candidate
