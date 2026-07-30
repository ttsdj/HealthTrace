from backend.agent.planner import finalize_evidence_state
from backend.rag import pipeline


class _RejectingBulkhead:
    def acquire(self, timeout):
        return False

    def release(self):
        raise AssertionError("a rejected bulkhead must not be released")


class _FailingSubgraph:
    def invoke(self, state):
        raise TimeoutError("synthetic branch timeout")


class _PassThroughBulkhead:
    def acquire(self, timeout):
        return True

    def release(self):
        return None


def test_sub_agent_bulkhead_rejection_becomes_mergeable_error(monkeypatch):
    monkeypatch.setattr(pipeline, "_RAG_SUB_AGENT_BULKHEAD", _RejectingBulkhead())

    result = pipeline.rag_sub_agent({"question": "branch-a"})

    branch = result["sub_results"][0]
    assert branch["status"] == "error"
    assert branch["error_type"] == "bulkhead_rejected"
    assert branch["docs"] == []


def test_sub_agent_failure_does_not_abort_other_send_branches(monkeypatch):
    monkeypatch.setattr(pipeline, "_RAG_SUB_AGENT_BULKHEAD", _PassThroughBulkhead())
    monkeypatch.setattr(pipeline, "_rag_sub_agent_graph", _FailingSubgraph())

    result = pipeline.rag_sub_agent({"question": "branch-timeout"})

    branch = result["sub_results"][0]
    assert branch["status"] == "error"
    assert branch["error_type"] == "TimeoutError"


def test_empty_failed_synthesis_is_mapped_to_no_evidence():
    result = pipeline.synthesis(
        {
            "question": "complex question",
            "complexity_reason": "multi-source",
            "sub_questions": ["branch-a", "branch-b"],
            "sub_results": [
                {
                    "question": "branch-a",
                    "docs": [],
                    "status": "error",
                    "error_type": "TimeoutError",
                },
                {
                    "question": "branch-b",
                    "docs": [],
                    "status": "error",
                    "error_type": "bulkhead_rejected",
                },
            ],
        }
    )

    trace = result["rag_trace"]
    assert trace["retrieval_mode"] == "no_results"
    assert trace["retrieval_failure_reason"] == "all_sub_agents_failed"
    assert trace["sub_agent_failed_count"] == 2
    assert finalize_evidence_state(trace)["evidence_state"] == "NO_EVIDENCE"
