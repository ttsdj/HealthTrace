from enum import StrEnum
from typing import Annotated, Any, TypedDict

import operator


class EvidenceState(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    CONFLICTING = "CONFLICTING"
    NO_EVIDENCE = "NO_EVIDENCE"
    PATIENT_DATA_MISSING = "PATIENT_DATA_MISSING"
    LOW_CONFIDENCE_INPUT = "LOW_CONFIDENCE_INPUT"
    HIGH_RISK = "HIGH_RISK"


class AgentAction(StrEnum):
    ANSWER = "ANSWER"
    ASK = "ASK"
    CREATE_REMINDER = "CREATE_REMINDER"
    RECOMMEND_ROUTINE_VISIT = "RECOMMEND_ROUTINE_VISIT"
    ESCALATE_URGENT = "ESCALATE_URGENT"
    REFUSE = "REFUSE"


class ConsultationStage(StrEnum):
    RECEIVE = "RECEIVE"
    CLASSIFY_AND_PLAN = "CLASSIFY_AND_PLAN"
    QUERY_PATIENT_CONTEXT = "QUERY_PATIENT_CONTEXT"
    CHECK_INFORMATION = "CHECK_INFORMATION"
    RETRIEVE_AND_GRADE_EVIDENCE = "RETRIEVE_AND_GRADE_EVIDENCE"
    DECIDE_ACTION = "DECIDE_ACTION"
    EXECUTE_AND_PERSIST = "EXECUTE_AND_PERSIST"
    COMPLETED = "COMPLETED"


class HealthAgentState(TypedDict, total=False):
    request_id: str
    username: str
    session_id: str
    query: str
    current_stage: str
    plan: Any
    intent: str
    risk_level: str
    required_patient_fields: list[str]
    missing_fields: list[str]
    required_evidence_sources: list[str]
    patient_context: str
    patient_context_meta: dict[str, Any]
    guarded_response: str
    rag_trace: dict[str, Any]
    candidate_response: str
    final_response: str
    fallback_error: str
    evidence_state: str
    action: str
    action_reason: str
    answer_policy_applied: str
    transition_trace: Annotated[list[dict[str, Any]], operator.add]
