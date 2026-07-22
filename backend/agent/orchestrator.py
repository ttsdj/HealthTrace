from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from langgraph.graph import END, StateGraph

from backend.agent.planner import finalize_evidence_state, plan_consultation, preflight_response
from backend.agent.state import AgentAction, ConsultationStage, EvidenceState, HealthAgentState
from backend.patient.context import build_verified_patient_context


def _transition(stage: ConsultationStage, status: str = "completed", **details) -> dict:
    item = {
        "stage": stage.value,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if details:
        item["details"] = details
    return {"current_stage": stage.value, "transition_trace": [item]}


def _receive(state: HealthAgentState) -> dict:
    return _transition(ConsultationStage.RECEIVE, request_id=state["request_id"])


def _classify_and_plan(state: HealthAgentState) -> dict:
    plan = plan_consultation(state["username"], state["query"])
    return {
        **_transition(
            ConsultationStage.CLASSIFY_AND_PLAN,
            intent=plan.intent,
            risk_level=plan.risk_level,
        ),
        "plan": plan,
        "intent": plan.intent,
        "risk_level": plan.risk_level,
        "required_patient_fields": plan.required_patient_fields,
        "missing_fields": plan.missing_fields,
        "required_evidence_sources": plan.required_evidence_sources,
        "evidence_state": plan.evidence_state.value,
        "action": plan.action.value,
        "action_reason": plan.action_reason,
    }


def _query_patient_context(state: HealthAgentState) -> dict:
    plan = state["plan"]
    guarded = bool(preflight_response(plan))
    if guarded:
        context = ""
        metadata = {"patient_context_accessed": False, "reason": "preflight_guarded"}
        status = "skipped"
    else:
        try:
            context, metadata = build_verified_patient_context(
                state["username"], state["query"]
            )
            status = "completed"
        except Exception as exc:
            context = ""
            metadata = {
                "patient_context_accessed": False,
                "patient_context_mode": "unavailable",
                "patient_context_error": type(exc).__name__,
            }
            status = "degraded"
    return {
        **_transition(
            ConsultationStage.QUERY_PATIENT_CONTEXT,
            status,
            accessed=bool(metadata.get("patient_context_accessed")),
            mode=metadata.get("patient_record_mode") or metadata.get("patient_context_mode"),
        ),
        "patient_context": context,
        "patient_context_meta": metadata,
    }


def _check_information(state: HealthAgentState) -> dict:
    guarded_response = preflight_response(state["plan"])
    return {
        **_transition(
            ConsultationStage.CHECK_INFORMATION,
            guarded=bool(guarded_response),
            missing_count=len(state.get("missing_fields") or []),
        ),
        "guarded_response": guarded_response,
    }


def _retrieve_and_grade(state: HealthAgentState) -> dict:
    trace = dict(state.get("rag_trace") or {})
    trace.setdefault("high_risk_medical", state.get("evidence_state") == EvidenceState.HIGH_RISK.value)
    trace.setdefault("missing_fields", state.get("missing_fields") or [])
    graded = finalize_evidence_state(trace)
    failure = state.get("fallback_error") or graded.get("retrieval_failure_reason") or ""
    return {
        **_transition(
            ConsultationStage.RETRIEVE_AND_GRADE_EVIDENCE,
            "degraded" if failure else "completed",
            evidence_state=graded["evidence_state"],
            retrieval_mode=graded.get("retrieval_mode", "not_invoked"),
            failure_type=(type(failure).__name__ if failure and not isinstance(failure, str) else "runtime" if failure else ""),
        ),
        "rag_trace": graded,
        "evidence_state": graded["evidence_state"],
    }


def _decide_action(state: HealthAgentState) -> dict:
    evidence_state = EvidenceState(state["evidence_state"])
    if evidence_state == EvidenceState.HIGH_RISK:
        action = AgentAction.ESCALATE_URGENT
        reason = "high_risk_requires_immediate_care"
    elif evidence_state == EvidenceState.PATIENT_DATA_MISSING:
        action = AgentAction.ASK
        reason = "required_patient_context_missing"
    elif evidence_state == EvidenceState.NO_EVIDENCE:
        action = AgentAction.REFUSE
        reason = "insufficient_retrieved_evidence"
    elif evidence_state == EvidenceState.CONFLICTING:
        action = AgentAction.ANSWER
        reason = "answer_with_explicit_evidence_conflict"
    else:
        action = AgentAction.ANSWER
        reason = "evidence_policy_allows_bounded_answer"
    return {
        **_transition(ConsultationStage.DECIDE_ACTION, action=action.value, reason=reason),
        "action": action.value,
        "action_reason": reason,
    }


def _execute_policy(state: HealthAgentState) -> dict:
    response = (state.get("candidate_response") or "").strip()
    guarded = (state.get("guarded_response") or "").strip()
    evidence_state = EvidenceState(state["evidence_state"])
    action = AgentAction(state["action"])
    policy = "pass_through"

    if action in {AgentAction.ESCALATE_URGENT, AgentAction.ASK} and guarded:
        response = guarded
        policy = "preflight_guard"
    elif evidence_state == EvidenceState.NO_EVIDENCE:
        response = (
            "当前知识库、患者资料和结构化医学证据均未检索到足以支持结论的内容，"
            "因此我不能据此给出确定诊断或个体化用药建议。你可以补充症状持续时间、"
            "伴随症状和相关检查；若症状持续、加重或出现危险信号，请及时线下就医。"
        )
        policy = "no_evidence_guard"
    elif evidence_state == EvidenceState.CONFLICTING:
        response = (
            "注意：当前结构化知识与文档证据存在限制条件或不一致，以下回答按保守原则解释，"
            "不能直接用于自行诊断或调整药物。\n\n" + response
        )
        policy = "conflict_disclosure"
    elif not response:
        response = "本轮未能生成可靠回答，请稍后重试；若症状紧急，请立即线下就医。"
        policy = "empty_response_guard"

    transition = _transition(
        ConsultationStage.EXECUTE_AND_PERSIST,
        policy=policy,
        action=action.value,
    )
    completed = _transition(ConsultationStage.COMPLETED, action=action.value)
    return {
        "current_stage": completed["current_stage"],
        "transition_trace": transition["transition_trace"] + completed["transition_trace"],
        "final_response": response,
        "answer_policy_applied": policy,
    }


def _build_prepare_graph():
    graph = StateGraph(HealthAgentState)
    graph.add_node("receive", _receive)
    graph.add_node("classify_and_plan", _classify_and_plan)
    graph.add_node("query_patient_context", _query_patient_context)
    graph.add_node("check_information", _check_information)
    graph.set_entry_point("receive")
    graph.add_edge("receive", "classify_and_plan")
    graph.add_edge("classify_and_plan", "query_patient_context")
    graph.add_edge("query_patient_context", "check_information")
    graph.add_edge("check_information", END)
    return graph.compile()


def _build_finalize_graph():
    graph = StateGraph(HealthAgentState)
    graph.add_node("retrieve_and_grade_evidence", _retrieve_and_grade)
    graph.add_node("decide_action", _decide_action)
    graph.add_node("execute_and_persist", _execute_policy)
    graph.set_entry_point("retrieve_and_grade_evidence")
    graph.add_edge("retrieve_and_grade_evidence", "decide_action")
    graph.add_edge("decide_action", "execute_and_persist")
    graph.add_edge("execute_and_persist", END)
    return graph.compile()


_PREPARE_GRAPH = _build_prepare_graph()
_FINALIZE_GRAPH = _build_finalize_graph()


def prepare_consultation(username: str, session_id: str, query: str) -> HealthAgentState:
    return _PREPARE_GRAPH.invoke(
        {
            "request_id": f"consult-{uuid4()}",
            "username": username,
            "session_id": session_id,
            "query": query,
            "transition_trace": [],
        }
    )


def finalize_consultation(
    state: HealthAgentState,
    rag_trace: dict | None,
    candidate_response: str,
    fallback_error: str = "",
) -> tuple[str, dict]:
    completed = _FINALIZE_GRAPH.invoke(
        {
            **state,
            "rag_trace": dict(rag_trace or {}),
            "candidate_response": candidate_response,
            "fallback_error": fallback_error,
        }
    )
    trace = dict(completed.get("rag_trace") or {})
    trace.update(
        {
            "consultation_orchestrator_enabled": True,
            "consultation_request_id": completed["request_id"],
            "consultation_stage": completed["current_stage"],
            "consultation_transitions": completed.get("transition_trace") or [],
            "intent": completed.get("intent", "unknown"),
            "risk_level": completed.get("risk_level", "normal"),
            "required_patient_fields": completed.get("required_patient_fields") or [],
            "missing_fields": completed.get("missing_fields") or [],
            "required_evidence_sources": completed.get("required_evidence_sources") or [],
            "evidence_state": completed["evidence_state"],
            "action": completed["action"],
            "action_reason": completed["action_reason"],
            "answer_policy_applied": completed["answer_policy_applied"],
        }
    )
    trace.update(completed.get("patient_context_meta") or {})
    return completed["final_response"], trace
