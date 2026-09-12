"""Tests for the Scope→Topic→Document funnel retrieval layer."""

from backend.indexing.milvus_client import COLLECTION_DEFAULT_BY_KIND
from backend.patient.scope import PatientScope
from backend.rag import funnel

PUBLIC_COLLECTION = COLLECTION_DEFAULT_BY_KIND["medical_qa"]
PATIENT_COLLECTION = COLLECTION_DEFAULT_BY_KIND["patient_record"]


# ---------------------------------------------------------------------------
# Scope stage
# ---------------------------------------------------------------------------


def test_stage_scope_public_selects_public_collection():
    scope = funnel.stage_scope("高血压的治疗", None)

    assert scope.domain == "public"
    assert scope.filter_expr == ""
    assert scope.collections == (PUBLIC_COLLECTION,)
    assert scope.patient_scope is None


def test_stage_scope_patient_selects_patient_collection_and_filter(monkeypatch):
    monkeypatch.setattr(funnel, "should_query_patient_context", lambda query: True)
    patient_scope = PatientScope("tenant-a", "patient-a", 7, "alice")

    scope = funnel.stage_scope("我的血压情况", patient_scope)

    assert scope.domain == "patient"
    assert scope.collections == (PATIENT_COLLECTION,)
    assert 'tenant_id == "tenant-a"' in scope.filter_expr
    assert 'patient_id == "patient-a"' in scope.filter_expr
    assert scope.patient_scope is patient_scope


def test_stage_scope_both_when_patient_scope_plus_general_medical_entity(monkeypatch):
    monkeypatch.setattr(funnel, "should_query_patient_context", lambda query: True)
    patient_scope = PatientScope("tenant-a", "patient-a", 7, "alice")

    # "高血压" is a general medical disease + the "我的" marker triggers patient scope.
    scope = funnel.stage_scope("高血压我的体检报告", patient_scope)

    assert scope.domain == "both"
    assert scope.collections == (PUBLIC_COLLECTION, PATIENT_COLLECTION)


def test_stage_scope_falls_back_to_public_without_scope(monkeypatch):
    monkeypatch.setattr(funnel, "should_query_patient_context", lambda query: True)

    scope = funnel.stage_scope("我的血压情况", None)

    assert scope.domain == "public"
    assert scope.collections == (PUBLIC_COLLECTION,)
    assert scope.filter_expr == ""


def test_stage_scope_degrades_to_public_on_error(monkeypatch):
    def boom(_query):
        raise RuntimeError("synthetic marker outage")

    monkeypatch.setattr(funnel, "should_query_patient_context", boom)

    scope = funnel.stage_scope("我的血压情况", PatientScope("tenant-a", "patient-a", 7, "alice"))

    assert scope.domain == "public"
    assert scope.degraded is True
    assert scope.collections == (PUBLIC_COLLECTION,)


# ---------------------------------------------------------------------------
# Topic stage
# ---------------------------------------------------------------------------


def test_stage_topic_produces_narrowing_filter():
    topic = funnel.stage_topic("高血压吃什么药")

    assert topic.topic_terms
    assert "高血压" in topic.filter_expr
    assert "drug_info" in topic.candidate_intents
    assert topic.document_terms


def test_stage_topic_degrades_to_default_on_error(monkeypatch):
    def boom(_query):
        raise RuntimeError("synthetic intent outage")

    monkeypatch.setattr(funnel, "analyze_medical_query", boom)

    topic = funnel.stage_topic("任何问题")

    assert topic.topic_terms == ()
    assert topic.filter_expr == ""
    assert topic.degraded is True


# ---------------------------------------------------------------------------
# Document stage + run_funnel
# ---------------------------------------------------------------------------

_PUBLIC_DOC = {
    "chunk_id": "pub-1",
    "text": "public medical evidence",
    "document_id": "doc-a",
    "score": 0.9,
    "filename": "guideline.pdf",
}
_PATIENT_DOC = {
    "chunk_id": "pat-1",
    "text": "patient private evidence",
    "document_id": "doc-b",
    "score": 0.8,
    "filename": "record.pdf",
}


def _patch_retrievals(monkeypatch, *, fail_public=False):
    """Patch the funnel's retrieval dependencies so no Milvus/DB is touched."""
    monkeypatch.setattr(
        funnel,
        "_candidate_document_ids",
        lambda store, topic, max_documents=None, **kwargs: ["doc-a"],
    )

    def _public(query, top_k, **kwargs):
        if fail_public:
            raise RuntimeError("synthetic public retrieval outage")
        return {"docs": [dict(_PUBLIC_DOC)], "meta": {"retrieval_mode": "hybrid", "leaf_retrieve_level": 3}}

    def _patient(query, scope, top_k, **kwargs):
        return {"docs": [dict(_PATIENT_DOC)], "mode": "hybrid", "attempts": [{"mode": "hybrid", "status": "ok"}]}

    monkeypatch.setattr(funnel, "retrieve_documents", _public)
    monkeypatch.setattr(funnel, "retrieve_patient_records", _patient)


def test_run_funnel_returns_docs_and_funnel_trace(monkeypatch):
    _patch_retrievals(monkeypatch)
    patient_scope = PatientScope("tenant-a", "patient-a", 7, "alice")

    docs, funnel_trace = funnel.run_funnel("高血压我的体检报告", patient_scope=patient_scope)

    texts = {doc["text"] for doc in docs}
    assert "public medical evidence" in texts
    assert "patient private evidence" in texts
    assert funnel_trace["scope"]["domain"] == "both"
    assert funnel_trace["topic"] != {}
    assert funnel_trace["document"] != {}
    assert funnel_trace["final_domain"] == "both"


def test_run_funnel_public_question_uses_public_scope(monkeypatch):
    monkeypatch.setattr(
        funnel,
        "_candidate_document_ids",
        lambda store, topic, max_documents=None, **kwargs: [],
    )
    monkeypatch.setattr(
        funnel,
        "retrieve_documents",
        lambda query, top_k, **kwargs: {"docs": [dict(_PUBLIC_DOC)], "meta": {}},
    )

    docs, funnel_trace = funnel.run_funnel("高血压的治疗")

    assert [doc["chunk_id"] for doc in docs] == ["pub-1"]
    assert funnel_trace["scope"]["domain"] == "public"
    # No patient signal, no degradation, so the public fallback flag is False.
    assert funnel_trace["final_public_fallback"] is False


def test_failure_in_scope_stage_degrades_to_public_fallback_without_raising(monkeypatch):
    _patch_retrievals(monkeypatch)
    monkeypatch.setattr(funnel, "stage_scope", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("scope boom")))

    docs, funnel_trace = funnel.run_funnel("高血压我的体检报告", patient_scope=PatientScope("tenant-a", "patient-a", 7, "alice"))

    assert isinstance(docs, list)
    # The doc-stage public path still ran (retrievals patched), so a doc is returned.
    assert any(doc["text"] == "public medical evidence" for doc in docs)
    assert funnel_trace["degraded"] is True
    assert "scope" in funnel_trace["degraded_stages"]
    assert funnel_trace["final_domain"] == "public"
    assert funnel_trace["final_public_fallback"] is True


def test_document_stage_failure_never_raises(monkeypatch):
    _patch_retrievals(monkeypatch, fail_public=True)
    # Prevent any candidate narrowing from touching the store.
    monkeypatch.setattr(
        funnel,
        "_candidate_document_ids",
        lambda store, topic, max_documents=None, **kwargs: [],
    )

    docs, funnel_trace = funnel.run_funnel("高血压的治疗")

    assert docs == []
    assert funnel_trace["document"].get("degraded", False) is True
    assert funnel_trace["degraded"] is True


def test_patient_document_candidates_stay_inside_the_patient_scope(monkeypatch):
    """The candidate-document query read the whole shared patient collection.

    ``_candidate_document_ids`` built its predicate from ``chunk_level`` and
    ``section_path`` only -- it never received the scope -- so the id list it
    returned came from every tenant. That list is copied into
    ``funnel_trace["document"]["document_ids"]`` (``stage_document``) and the
    trace is streamed verbatim by ``/chat/stream``, so an authenticated caller
    read back other patients' document identifiers.
    """
    queries: list[tuple[str, str]] = []

    class _Store:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def query_all(self, filter_expr, output_fields=None):
            queries.append((self.kind, filter_expr))
            return []

    monkeypatch.setattr(funnel, "get_milvus_store", lambda kind: _Store(kind))
    monkeypatch.setattr(funnel, "should_query_patient_context", lambda query: True)
    monkeypatch.setattr(
        funnel,
        "retrieve_documents",
        lambda query, top_k, **kwargs: {"docs": [], "meta": {}},
    )
    monkeypatch.setattr(
        funnel,
        "retrieve_patient_records",
        lambda query, scope, top_k, **kwargs: {"docs": [], "mode": "stub", "attempts": []},
    )

    topic = funnel.TopicQuery(("高血压",), (), "", ("高血压",), "test")
    scope = funnel.stage_scope(
        "高血压我的体检报告", PatientScope("tenant-a", "patient-a", 7, "alice")
    )
    assert scope.domain == "both"

    funnel.stage_document("高血压我的体检报告", scope, topic)

    patient_queries = [expr for kind, expr in queries if kind == funnel._PATIENT_RECORD_KIND]
    assert patient_queries, "the patient document query must reach the patient collection"
    for expr in patient_queries:
        assert 'document_domain == "patient_private"' in expr
        assert 'tenant_id == "tenant-a"' in expr
        assert 'patient_id == "patient-a"' in expr
    # Public medical knowledge is shared on purpose: no tenant term belongs there.
    public_queries = [expr for kind, expr in queries if kind == funnel._MEDICAL_QA_KIND]
    assert public_queries, "the public document query must still run"
    for expr in public_queries:
        assert "tenant_id" not in expr


# ---------------------------------------------------------------------------
# Backward-compatible retrieve_documents kwargs
# ---------------------------------------------------------------------------


class _RecordingStore:
    def __init__(self):
        self.last_filter = None

    def hybrid_retrieve(self, dense_embedding, query, top_k, filter_expr):
        self.last_filter = filter_expr
        return [{"chunk_id": "c-1", "text": "evidence", "score": 0.9}]


class _FakeEmbedding:
    def get_embeddings(self, texts):
        return [[0.0, 1.0]] * len(texts)


def test_retrieve_documents_kwargs_build_document_id_filter(monkeypatch):
    store = _RecordingStore()
    monkeypatch.setattr("backend.rag.utils._milvus_manager", store)
    monkeypatch.setattr("backend.rag.utils._embedding_service", _FakeEmbedding())

    result = __import__("backend.rag.utils", fromlist=["retrieve_documents"]).retrieve_documents(
        "query", 5, filter_expr="", candidate_document_ids=["doc-a", "doc-b"]
    )

    assert store.last_filter is not None
    assert "document_id in" in store.last_filter
    assert '"doc-a"' in store.last_filter
    assert '"doc-b"' in store.last_filter
    assert result["docs"][0]["chunk_id"] == "c-1"


def test_retrieve_documents_kwargs_default_unchanged(monkeypatch):
    store = _RecordingStore()
    monkeypatch.setattr("backend.rag.utils._milvus_manager", store)
    monkeypatch.setattr("backend.rag.utils._embedding_service", _FakeEmbedding())

    result = __import__("backend.rag.utils", fromlist=["retrieve_documents"]).retrieve_documents(
        "query", 5
    )

    assert store.last_filter == "chunk_level == 3"
    assert result["docs"][0]["chunk_id"] == "c-1"
