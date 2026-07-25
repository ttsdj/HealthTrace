from contextlib import contextmanager

from backend.indexing.milvus_client import (
    MilvusSettings,
    MilvusStore,
    _deduplicate_versioned_results,
    _read_consistency_level,
)


class _Client:
    def __init__(self):
        self.flush_calls = []

    def delete(self, collection_name, filter):
        return {"delete_count": 2, "collection_name": collection_name, "filter": filter}

    def flush(self, collection_name):
        self.flush_calls.append(collection_name)


def test_version_overlap_deduplication_prefers_newest_business_version():
    results = [
        {
            "id": 10,
            "document_id": "doc-1",
            "content_fingerprint": "same",
            "document_version": 1,
        },
        {
            "id": 20,
            "document_id": "doc-1",
            "content_fingerprint": "same",
            "document_version": 2,
        },
        {
            "id": 30,
            "document_id": "doc-1",
            "content_fingerprint": "other",
            "document_version": 2,
        },
    ]

    deduplicated = _deduplicate_versioned_results(results, top_k=5)

    assert [item["id"] for item in deduplicated] == [20, 30]


def test_delete_flushes_before_returning(monkeypatch):
    client = _Client()

    @contextmanager
    def _session(_settings):
        yield client

    monkeypatch.setenv("MILVUS_FLUSH_AFTER_DELETE", "true")
    monkeypatch.setattr(
        "backend.indexing.milvus_client.milvus_client_session",
        _session,
    )
    store = MilvusStore(
        MilvusSettings(
            host="127.0.0.1",
            port="19530",
            collection_name="active",
            uri="http://127.0.0.1:19530",
            timeout=1,
        )
    )

    result = store.delete("id in [1,2]")

    assert result["delete_count"] == 2
    assert client.flush_calls == ["active"]
    assert _read_consistency_level() == "Strong"
