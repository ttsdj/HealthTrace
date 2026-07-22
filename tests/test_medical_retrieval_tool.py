from backend.tools import medical_retrieval


def test_public_rag_is_exposed_as_typed_evidence_bundle(monkeypatch):
    monkeypatch.setattr(
        medical_retrieval,
        "run_rag_graph",
        lambda query: {
            "docs": [
                {
                    "text": "医学证据",
                    "filename": "guideline.pdf",
                    "page_number": 3,
                    "chunk_id": "chunk-1",
                    "score": 0.88,
                }
            ],
            "rag_trace": {"retrieval_mode": "hybrid", "retrieval_attempts": []},
        },
    )

    bundle, trace = medical_retrieval.retrieve_public_medical_evidence("query")

    assert bundle.status == "ok"
    assert bundle.source == "public_rag"
    assert bundle.retrieval_mode == "hybrid"
    assert bundle.evidence[0].patient_specific is False
    assert bundle.evidence[0].page == 3
    assert trace["retrieval_mode"] == "hybrid"


def test_public_rag_failure_returns_unavailable_not_fake_evidence(monkeypatch):
    def fail(_query):
        raise RuntimeError("milvus unavailable")

    monkeypatch.setattr(medical_retrieval, "run_rag_graph", fail)

    bundle, trace = medical_retrieval.retrieve_public_medical_evidence("query")

    assert bundle.status == "unavailable"
    assert bundle.evidence == []
    assert trace["retrieval_degraded"] is True
