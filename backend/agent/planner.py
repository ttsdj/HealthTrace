from __future__ import annotations

from dataclasses import dataclass, field

from backend.agent.state import AgentAction, EvidenceState
from backend.agent.intent_router import Intent, IntentRoute, route_intent
from backend.agent.tool_registry import build_execution_plan
from backend.db.models import User
from backend.infra.database import SessionLocal
from backend.medical_nlp.safety import analyze_medical_safety
from backend.patient.context import required_resource_types, should_query_patient_context
from backend.patient.facts import list_patient_facts
from backend.patient.scope import ensure_user_scope

_FIELD_BY_RESOURCE = {
    "AllergyIntolerance": "allergies",
    "MedicationStatement": "current_medications",
    "Condition": "conditions",
    "Observation": "observations",
    "DiagnosticReport": "diagnostic_reports",
}


@dataclass
class ConsultationPlan:
    intent: str
    risk_level: str
    route: IntentRoute | None = None
    execution_plan: dict = field(default_factory=dict)
    required_patient_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    required_evidence_sources: list[str] = field(default_factory=list)
    evidence_state: EvidenceState = EvidenceState.PARTIAL
    action: AgentAction = AgentAction.ANSWER
    action_reason: str = ""

    def trace_fields(self) -> dict:
        return {
            "consultation_orchestrator_enabled": True,
            "intent": self.intent,
            "route": self.route.model_dump() if self.route else {},
            "execution_plan": self.execution_plan,
            "risk_level": self.risk_level,
            "required_patient_fields": self.required_patient_fields,
            "missing_fields": self.missing_fields,
            "required_evidence_sources": self.required_evidence_sources,
            "evidence_state": self.evidence_state.value,
            "action": self.action.value,
            "action_reason": self.action_reason,
        }


def plan_consultation(username: str, query: str) -> ConsultationPlan:
    route = route_intent(query)
    intent = route.primary_intent.value
    safety = analyze_medical_safety(query)
    execution_plan = build_execution_plan(route)
    if route.primary_intent == Intent.URGENT_CARE or safety["high_risk_medical"]:
        return ConsultationPlan(
            intent=intent,
            risk_level="high",
            route=route,
            execution_plan=execution_plan,
            required_evidence_sources=[],
            evidence_state=EvidenceState.HIGH_RISK,
            action=AgentAction.ESCALATE_URGENT,
            action_reason="deterministic_high_risk_guard",
        )

    is_personal = route.needs_patient_context or should_query_patient_context(query)
    resource_types = required_resource_types(query) if is_personal else []
    required_fields = [_FIELD_BY_RESOURCE[item] for item in resource_types]
    sources = ["public_rag"]
    if route.primary_intent in {
        Intent.SYMPTOM_ASSESSMENT,
        Intent.MEDICATION_SAFETY,
        Intent.REPORT_INTERPRETATION,
        Intent.GENERAL_MEDICAL_QA,
    }:
        sources.append("medical_kg")
    if is_personal:
        sources.insert(0, "patient_facts")
        if any(marker in query for marker in ("报告", "病历", "检查单")):
            sources.insert(1, "patient_record")

    missing_fields: list[str] = []
    if resource_types:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == username).first()
            if user is None:
                missing_fields = required_fields
            else:
                scope = ensure_user_scope(db, user)
                db.commit()
                for resource_type in resource_types:
                    if not list_patient_facts(db, scope, resource_type):
                        missing_fields.append(_FIELD_BY_RESOURCE[resource_type])
        except Exception:
            db.rollback()
            missing_fields = required_fields
        finally:
            db.close()

    dosage_or_medication = safety["dosage_guard_triggered"] or route.primary_intent == Intent.MEDICATION_SAFETY
    if route.confidence < 0.60:
        return ConsultationPlan(
            intent=intent,
            risk_level="guarded",
            route=route,
            execution_plan=execution_plan,
            required_patient_fields=required_fields,
            missing_fields=missing_fields,
            required_evidence_sources=sources,
            evidence_state=EvidenceState.LOW_CONFIDENCE_INPUT,
            action=AgentAction.ASK,
            action_reason="intent_route_low_confidence",
        )
    if is_personal and required_fields and missing_fields:
        return ConsultationPlan(
            intent=intent,
            risk_level="guarded",
            route=route,
            execution_plan=execution_plan,
            required_patient_fields=required_fields,
            missing_fields=missing_fields,
            required_evidence_sources=sources,
            evidence_state=EvidenceState.PATIENT_DATA_MISSING,
            action=AgentAction.ASK,
            action_reason=(
                "required_medication_safety_fields_missing"
                if dosage_or_medication
                else "required_patient_context_missing"
            ),
        )

    if route.primary_intent == Intent.HEALTH_TASK:
        return ConsultationPlan(
            intent=intent,
            risk_level="normal",
            route=route,
            execution_plan=execution_plan,
            required_patient_fields=required_fields,
            missing_fields=missing_fields,
            required_evidence_sources=[],
            evidence_state=EvidenceState.PARTIAL,
            action=AgentAction.CREATE_REMINDER,
            action_reason="confirmation_required_health_task",
        )

    if route.primary_intent == Intent.CARE_NAVIGATION:
        return ConsultationPlan(
            intent=intent,
            risk_level="normal",
            route=route,
            execution_plan=execution_plan,
            required_patient_fields=required_fields,
            missing_fields=missing_fields,
            required_evidence_sources=sources,
            evidence_state=EvidenceState.PARTIAL,
            action=AgentAction.RECOMMEND_ROUTINE_VISIT,
            action_reason="care_navigation_requested",
        )

    return ConsultationPlan(
        intent=intent,
        risk_level="guarded" if dosage_or_medication else "normal",
        route=route,
        execution_plan=execution_plan,
        required_patient_fields=required_fields,
        missing_fields=missing_fields,
        required_evidence_sources=sources,
        evidence_state=EvidenceState.PARTIAL,
        action=AgentAction.ANSWER,
        action_reason="preflight_passed",
    )


def preflight_response(plan: ConsultationPlan) -> str:
    if plan.action == AgentAction.ESCALATE_URGENT:
        return (
            "你描述的情况包含可能的急危重症信号。请立即拨打 120 或尽快前往急诊，"
            "不要等待在线回答，也不要自行调整药物。若身边有人，请让对方协助就医。"
        )
    if plan.action == AgentAction.ASK:
        if not plan.missing_fields:
            return "我还不能可靠判断你的具体需求。请补充症状、检查项目、既往病史或你希望完成的健康任务。"
        labels = {
            "allergies": "是否有药物或食物过敏",
            "current_medications": "目前正在使用哪些药物及剂量",
            "conditions": "已有疾病或重要病史",
            "observations": "最近相关检查指标",
            "diagnostic_reports": "相关检查报告",
        }
        questions = [labels.get(item, item) for item in plan.missing_fields]
        return (
            "为了避免给出不安全的个体化用药建议，我还需要确认："
            + "；".join(questions)
            + "。你可以逐项回答；如涉及处方调整，请以医生或药师意见为准。"
        )
    return ""


def finalize_evidence_state(trace: dict) -> dict:
    out = dict(trace)
    if out.get("high_risk_medical"):
        out["evidence_state"] = EvidenceState.HIGH_RISK.value
        out["action"] = AgentAction.ESCALATE_URGENT.value
    elif out.get("missing_fields"):
        out["evidence_state"] = EvidenceState.PATIENT_DATA_MISSING.value
        out["action"] = AgentAction.ASK.value
    elif out.get("conflict_detected") or out.get("kg_vector_conflict_detected"):
        out["evidence_state"] = EvidenceState.CONFLICTING.value
        out["action"] = AgentAction.ANSWER.value
    elif out.get("retrieved_chunks") or int(out.get("kg_hit_count") or 0) > 0:
        out["evidence_state"] = EvidenceState.SUFFICIENT.value
        out["action"] = AgentAction.ANSWER.value
    elif out.get("retrieval_mode") in {"no_results", "unavailable"}:
        out["evidence_state"] = EvidenceState.NO_EVIDENCE.value
        out["action"] = AgentAction.REFUSE.value
    else:
        out.setdefault("evidence_state", EvidenceState.PARTIAL.value)
        out.setdefault("action", AgentAction.ANSWER.value)
    return out
