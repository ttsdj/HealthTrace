from backend.indexing.embedding import EmbeddingService


def test_single_embedding_uses_the_batch_contract(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("DENSE_EMBEDDING_DIM", "32")
    service = EmbeddingService()

    single = service.get_embedding("synthetic medical query")
    batch = service.get_embeddings(["synthetic medical query"])

    assert single == batch[0]
    assert len(single) == 32
