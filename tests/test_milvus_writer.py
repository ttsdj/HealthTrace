from contextlib import contextmanager

from backend.indexing.milvus_client import MilvusStore
from backend.indexing.milvus_writer import MilvusWriter


class _FakeEmbeddingService:
    def get_embeddings(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _FakeClient:
    def __init__(self):
        self.insert_batches = []
        self.flush_calls = []

    def insert(self, collection_name, data):
        self.insert_batches.append((collection_name, data))

    def flush(self, collection_name):
        self.flush_calls.append(collection_name)


class _FakeStore:
    collection_name = "patient-test"

    def __init__(self):
        self.client = _FakeClient()

    @contextmanager
    def session(self):
        yield self.client


def test_writer_flushes_once_after_all_insert_batches(monkeypatch):
    monkeypatch.setenv("DENSE_EMBEDDING_DIM", "2")
    monkeypatch.setattr(MilvusStore, "ensure_collection", lambda *args: None)
    store = _FakeStore()
    writer = MilvusWriter(_FakeEmbeddingService(), store)
    documents = [
        {
            "text": f"document {index}",
            "filename": "synthetic.html",
            "file_type": "HTML",
            "chunk_id": f"chunk-{index}",
            "chunk_level": 3,
        }
        for index in range(3)
    ]

    writer.write_documents(documents, batch_size=2)

    assert [len(item[1]) for item in store.client.insert_batches] == [2, 1]
    assert store.client.flush_calls == ["patient-test"]
