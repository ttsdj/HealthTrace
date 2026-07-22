from backend.indexing import embedding as embedding_module
from backend.indexing import milvus_client as milvus_module
from backend.patient.retrieval import retrieve_patient_records
from backend.patient.scope import PatientScope


class _UnavailableEmbedding:
    def get_embedding(self, query):
        raise RuntimeError("synthetic dense backend outage")


class _SparseOnlyStore:
    def hybrid_retrieve(self, *args, **kwargs):
        raise AssertionError("hybrid retrieval must be skipped without a dense vector")

    def dense_retrieve(self, *args, **kwargs):
        raise AssertionError("dense retrieval must be skipped without a dense vector")

    def sparse_retrieve(self, query, top_k, filter_expr):
        assert 'tenant_id == "tenant-a"' in filter_expr
        assert 'patient_id == "patient-a"' in filter_expr
        return [
            {
                "text": "synthetic scoped evidence",
                "document_id": "doc-a",
                "score": 1.0,
            }
        ]


def test_patient_retrieval_falls_back_to_sparse_when_embedding_is_unavailable(monkeypatch):
    monkeypatch.setattr(embedding_module, "embedding_service", _UnavailableEmbedding())
    monkeypatch.setattr(
        milvus_module,
        "get_milvus_store",
        lambda kind: _SparseOnlyStore(),
    )
    scope = PatientScope("tenant-a", "patient-a", 7, "alice")

    result = retrieve_patient_records("synthetic query", scope, top_k=5)

    assert result["mode"] == "sparse"
    assert result["docs"][0]["document_id"] == "doc-a"
    assert result["attempts"][0]["mode"] == "embedding"
    assert result["attempts"][0]["status"] == "error"
    assert not any(item["mode"] in {"hybrid", "dense"} for item in result["attempts"])
