from pathlib import Path

from backend.agent import intent_router
from backend.agent.executor import PlannerExecutor, ToolExecutionDenied
from backend.agent.intent_router import Intent, _ModelRoute, route_intent
from backend.agent.resilience import invoke_with_resilience
from backend.agent.tool_registry import CORE_TOOLS, build_execution_plan, execution_plan_context, tool_is_allowed
from backend.evaluation.intent_router import (
    acceptance_passed,
    evaluate_intent_router,
    load_frozen_intent_cases,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "intent_router_v1.jsonl"
MANIFEST = ROOT / "evaluation" / "intent_router_v1.manifest.json"


def test_frozen_100_case_intent_acceptance(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "rules")
    cases = load_frozen_intent_cases(DATASET, MANIFEST)
    report = evaluate_intent_router(cases)

    assert report["case_count"] == 100
    assert report["accuracy"] >= 0.97
    assert report["macro_f1"] >= 0.97
    assert report["high_risk_recall"] == 1.0
    assert report["false_write_trigger_rate"] == 0.0
    assert acceptance_passed(report)


def test_safety_preflight_never_calls_fast_model(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "fastmodel")
    monkeypatch.setattr(
        intent_router,
        "_fast_model_route",
        lambda _: (_ for _ in ()).throw(AssertionError("FastModel must not see high risk input")),
    )
    result = route_intent("我突然胸痛而且喘不过气")
    assert result.primary_intent == Intent.URGENT_CARE
    assert result.route_source == "deterministic_safety"


def test_fast_model_structured_route_is_bounded_and_cannot_grant_write(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "fastmodel")
    monkeypatch.setattr(
        intent_router,
        "_fast_model_route",
        lambda _: _ModelRoute(
            primary_intent=Intent.GENERAL_MEDICAL_QA,
            complexity="simple",
            needs_patient_context=False,
            requires_write_confirmation=True,
            confidence=0.88,
        ),
    )
    result = route_intent("高血压是什么")
    assert result.route_source == "fastmodel"
    assert result.primary_intent == Intent.GENERAL_MEDICAL_QA
    assert result.requires_write_confirmation is False


def test_json_prompt_fast_model_output_is_validated(monkeypatch):
    class Response:
        content = (
            '{"primary_intent":"general_medical_qa","secondary_intents":[], '
            '"complexity":"simple","needs_patient_context":false, '
            '"requires_write_confirmation":false,"confidence":0.91,"reason_code":"fake"}'
        )

    class Model:
        def invoke(self, _):
            return Response()

    monkeypatch.setenv("MODEL", "fake-model")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    monkeypatch.setenv("BASE_URL", "http://fake.invalid/v1")
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_STRUCTURED_MODE", "json_prompt")
    monkeypatch.setattr(intent_router, "_FAST_MODEL", Model())
    result = intent_router._fast_model_route("高血压是什么")
    assert result.primary_intent == Intent.GENERAL_MEDICAL_QA
    assert result.confidence == 0.91


def test_eight_core_tools_and_planner_allowlist():
    assert len(CORE_TOOLS) == 8
    assert len({item.name for item in CORE_TOOLS}) == 8
    task_plan = build_execution_plan(route_intent("明天提醒我测血压"))
    assert task_plan["allowed_tools"] == ["create_health_reminder_draft"]
    assert task_plan["requires_confirmation"] is True
    with execution_plan_context(task_plan):
        assert tool_is_allowed("create_health_reminder_draft")
        assert not tool_is_allowed("search_knowledge_base")


def test_read_tools_use_full_jitter_retry_and_writes_do_not_retry(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_TOOL_RETRY_BASE_SECONDS", "0")
    attempts = {"read": 0, "write": 0}
    events = []

    def flaky_read():
        attempts["read"] += 1
        if attempts["read"] == 1:
            raise RuntimeError("connection timeout")
        return "ok"

    assert invoke_with_resilience("search_knowledge_base", flaky_read, on_event=lambda status, event: events.append((status, event))) == "ok"
    assert attempts["read"] == 2
    assert [status for status, _ in events] == ["retryable_error", "ok"]
    assert events[0][1].retry_delay_ms == 0

    def failing_write():
        attempts["write"] += 1
        raise RuntimeError("connection timeout")

    try:
        invoke_with_resilience("create_health_reminder_draft", failing_write)
    except RuntimeError:
        pass
    assert attempts["write"] == 1


def test_executor_enforces_allowlist_and_write_confirmation():
    plan = build_execution_plan(route_intent("提醒我明天测血压"))
    executor = PlannerExecutor(plan)
    try:
        executor.invoke("create_health_reminder_draft", lambda: "draft")
    except ToolExecutionDenied:
        pass
    else:
        raise AssertionError("write tool must require confirmation")
    assert executor.invoke("create_health_reminder_draft", lambda: "draft", confirmed=True) == "draft"
    try:
        executor.invoke("search_knowledge_base", lambda: "unexpected")
    except ToolExecutionDenied:
        pass
    else:
        raise AssertionError("executor must reject non-planned tools")
