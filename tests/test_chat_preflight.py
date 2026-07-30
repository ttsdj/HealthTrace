from backend.chat import service
from langchain_core.messages import HumanMessage


class StubStorage:
    def __init__(self):
        self.saved = []

    def load_with_meta(self, user_id, session_id):
        return [], {}

    def save(self, user_id, session_id, messages, metadata=None, extra_message_data=None):
        self.saved.append((user_id, session_id, list(messages), metadata, extra_message_data))


def test_high_risk_preflight_bypasses_llm_and_returns_urgent_action(monkeypatch):
    storage = StubStorage()
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(
        service.memory_service,
        "retrieve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("memory retrieval must be skipped for guarded preflight")
        ),
    )
    monkeypatch.setattr(service.memory_service, "format_for_prompt", lambda memories: "")
    monkeypatch.setattr(service.memory_service, "store_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "build_verified_patient_context",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("patient retrieval must be skipped for guarded preflight")
        ),
    )
    monkeypatch.setattr(service, "get_last_rag_context", lambda clear=False: None)
    monkeypatch.setattr(service, "_generate_session_title_sync", lambda text: "高风险分流")
    monkeypatch.setattr(service, "_update_persistent_note_sync", lambda *args: "")

    def forbidden_llm_call(*args, **kwargs):
        raise AssertionError("LLM must not be called for deterministic high-risk preflight")

    monkeypatch.setattr(service.agent, "invoke", forbidden_llm_call)

    result = service.chat_with_agent(
        "我突然胸痛而且喘不过气",
        user_id="alice",
        session_id="urgent-1",
    )

    assert "120" in result["response"]
    assert result["rag_trace"]["evidence_state"] == "HIGH_RISK"
    assert result["rag_trace"]["action"] == "ESCALATE_URGENT"
    assert result["rag_trace"]["patient_context_accessed"] is False
    assert result["rag_trace"]["reason"] == "preflight_guarded"
    assert len(storage.saved) >= 2


def test_sensitive_text_is_redacted_before_agent_and_title_llm(monkeypatch):
    storage = StubStorage()
    captured = {}
    monkeypatch.setattr(service, "storage", storage)
    monkeypatch.setattr(service.memory_service, "retrieve", lambda *args, **kwargs: {})
    monkeypatch.setattr(service.memory_service, "format_for_prompt", lambda memories: "")
    monkeypatch.setattr(service.memory_service, "store_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "get_last_rag_context", lambda clear=False: None)
    monkeypatch.setattr(service, "_update_persistent_note_sync", lambda *args: "")
    monkeypatch.setattr(
        "backend.agent.orchestrator.build_verified_patient_context",
        lambda *args: ("", {"patient_context_accessed": False, "reason": "test"}),
    )

    def capture_agent(input_data, config=None):
        captured["agent_messages"] = input_data["messages"]
        return {"output": "已收到"}

    def capture_title(text):
        captured["title_text"] = text
        return "隐私测试"

    monkeypatch.setattr(service.agent, "invoke", capture_agent)
    monkeypatch.setattr(service, "_generate_session_title_sync", capture_title)

    raw_email = "alice@example.com"
    result = service.chat_with_agent(
        f"联系邮箱 {raw_email}，请介绍高血压",
        user_id="alice",
        session_id="privacy-1",
    )

    current_query = next(
        message.content
        for message in reversed(captured["agent_messages"])
        if isinstance(message, HumanMessage)
    )
    assert raw_email not in current_query
    assert "[邮箱]" in current_query
    assert raw_email not in captured["title_text"]
    assert "[邮箱]" in captured["title_text"]
    assert result["rag_trace"]["privacy_redaction_applied"] is True
