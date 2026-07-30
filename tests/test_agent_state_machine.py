from backend.agent import orchestrator
from backend.agent.orchestrator import finalize_consultation, prepare_consultation
from backend.agent.planner import ConsultationPlan
from backend.agent.intent_router import Intent, IntentRoute
from backend.agent.state import AgentAction, ConsultationStage, EvidenceState
from backend.agent.tool_audit import get_tool_audit, record_tool_call, reset_tool_audit


def _normal_plan() -> ConsultationPlan:
    return ConsultationPlan(
        intent="medical_qa",
        risk_level="normal",
        required_evidence_sources=["public_rag"],
        evidence_state=EvidenceState.PARTIAL,
        action=AgentAction.ANSWER,
        action_reason="preflight_passed",
    )


def test_consultation_state_machine_records_the_complete_lifecycle(monkeypatch):
    monkeypatch.setattr(orchestrator, "plan_consultation", lambda *_: _normal_plan())
    monkeypatch.setattr(
        orchestrator,
        "build_verified_patient_context",
        lambda *_: ("verified context", {"patient_context_accessed": True}),
    )

    prepared = prepare_consultation("alice", "session-1", "解释这份检查")
    response, trace = finalize_consultation(
        prepared,
        {"retrieved_chunks": [{"text": "evidence"}], "retrieval_mode": "hybrid"},
        "有证据支持的回答",
    )

    assert response == "有证据支持的回答"
    assert trace["consultation_stage"] == ConsultationStage.COMPLETED.value
    assert trace["evidence_state"] == EvidenceState.SUFFICIENT.value
    assert [item["stage"] for item in trace["consultation_transitions"]] == [
        ConsultationStage.RECEIVE.value,
        ConsultationStage.CLASSIFY_AND_PLAN.value,
        ConsultationStage.QUERY_PATIENT_CONTEXT.value,
        ConsultationStage.CHECK_INFORMATION.value,
        ConsultationStage.RETRIEVE_AND_GRADE_EVIDENCE.value,
        ConsultationStage.DECIDE_ACTION.value,
        ConsultationStage.EXECUTE_AND_PERSIST.value,
        ConsultationStage.COMPLETED.value,
    ]


def test_no_evidence_policy_replaces_an_unsupported_answer(monkeypatch):
    monkeypatch.setattr(orchestrator, "plan_consultation", lambda *_: _normal_plan())
    monkeypatch.setattr(orchestrator, "build_verified_patient_context", lambda *_: ("", {}))
    prepared = prepare_consultation("alice", "session-1", "罕见病怎么治疗")

    response, trace = finalize_consultation(
        prepared,
        {"retrieval_mode": "no_results", "retrieval_failure_reason": "empty"},
        "这是一个没有证据支持的确定答案",
    )

    assert "不能据此给出确定诊断" in response
    assert "没有证据支持的确定答案" not in response
    assert trace["action"] == AgentAction.REFUSE.value
    assert trace["answer_policy_applied"] == "no_evidence_guard"


def test_tool_audit_redacts_secret_fields_and_records_failure_type():
    reset_tool_audit()
    record_tool_call(
        tool_name="search_nearby_medical_care",
        arguments={"query": "附近医院", "latitude": 34.1, "longitude": 108.9},
        attempt=1,
        status="failed",
        latency_ms=12,
        error_type="TimeoutError",
    )
    audit = get_tool_audit()

    assert audit[0]["arguments"]["latitude"] == "[REDACTED]"
    assert audit[0]["arguments"]["longitude"] == "[REDACTED]"
    assert audit[0]["error_type"] == "TimeoutError"
    assert len(audit[0]["arguments_sha256"]) == 64


def test_all_evidence_states_map_to_the_six_declared_actions():
    expected = {
        EvidenceState.HIGH_RISK: AgentAction.ESCALATE_URGENT,
        EvidenceState.PATIENT_DATA_MISSING: AgentAction.ASK,
        EvidenceState.NO_EVIDENCE: AgentAction.REFUSE,
        EvidenceState.LOW_CONFIDENCE_INPUT: AgentAction.ASK,
        EvidenceState.CONFLICTING: AgentAction.ANSWER,
        EvidenceState.SUFFICIENT: AgentAction.ANSWER,
        EvidenceState.PARTIAL: AgentAction.ANSWER,
    }
    for evidence_state, action in expected.items():
        outcome = orchestrator._decide_action({"evidence_state": evidence_state.value})
        assert outcome["action"] == action.value

    reminder = orchestrator._decide_action(
        {
            "evidence_state": EvidenceState.PARTIAL.value,
            "requested_action": AgentAction.CREATE_REMINDER.value,
        }
    )
    routine_visit = orchestrator._decide_action(
        {
            "evidence_state": EvidenceState.PARTIAL.value,
            "requested_action": AgentAction.RECOMMEND_ROUTINE_VISIT.value,
        }
    )
    assert reminder["action"] == AgentAction.CREATE_REMINDER.value
    assert routine_visit["action"] == AgentAction.RECOMMEND_ROUTINE_VISIT.value


def test_low_confidence_route_is_asked_for_clarification(monkeypatch):
    low_confidence_plan = ConsultationPlan(
        intent=Intent.GENERAL_MEDICAL_QA.value,
        risk_level="guarded",
        route=IntentRoute(
            primary_intent=Intent.GENERAL_MEDICAL_QA,
            confidence=0.2,
            reason_code="test_low_confidence",
        ),
        evidence_state=EvidenceState.LOW_CONFIDENCE_INPUT,
        action=AgentAction.ASK,
        action_reason="intent_route_low_confidence",
    )
    monkeypatch.setattr(orchestrator, "plan_consultation", lambda *_: low_confidence_plan)
    monkeypatch.setattr(orchestrator, "build_verified_patient_context", lambda *_: ("", {}))
    prepared = prepare_consultation("alice", "session-1", "请帮我看看")
    response, trace = finalize_consultation(prepared, {}, "不应直接回答")
    assert trace["evidence_state"] == EvidenceState.LOW_CONFIDENCE_INPUT.value
    assert trace["action"] == AgentAction.ASK.value
    assert "补充" in response
