"""Isolated Milvus retrieval baselines for RAGCare-QA evaluation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable

from pymilvus import (
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)

from backend.indexing.embedding import EmbeddingService, embedding_service

DEFAULT_COLLECTION = "med_ragcare_eval_v1"
BASELINE_NAMES = ("bm25", "dense", "hybrid", "hybrid_rerank")


@dataclass(frozen=True)
class EvalMilvusSettings:
    uri: str
    collection_name: str
    timeout: float

    @classmethod
    def from_env(cls, collection_name: str | None = None) -> "EvalMilvusSettings":
        host = os.getenv("MILVUS_HOST", "127.0.0.1")
        port = os.getenv("MILVUS_PORT", "19530")
        return cls(
            uri=f"http://{host}:{port}",
            collection_name=collection_name
            or os.getenv("RAGCARE_EVAL_COLLECTION", DEFAULT_COLLECTION),
            timeout=float(os.getenv("MILVUS_TIMEOUT", "30")),
        )


def _entity(hit: dict) -> dict:
    entity = hit.get("entity")
    return entity if isinstance(entity, dict) else hit


def _format_hits(results: list) -> list[dict]:
    formatted: list[dict] = []
    for hits in results:
        for hit in hits:
            entity = _entity(hit)
            formatted.append(
                {
                    "id": hit.get("id"),
                    "text": entity.get("text", ""),
                    "chunk_id": entity.get("chunk_id", ""),
                    "document_id": entity.get("document_id", ""),
                    "reference": entity.get("reference", ""),
                    "source_page": entity.get("source_page", ""),
                    "chunk_idx": entity.get("chunk_idx", 0),
                    "chunk_level": entity.get("chunk_level", 3),
                    "score": float(hit.get("distance", 0.0)),
                }
            )
    return formatted


class RagcareEvalStore:
    OUTPUT_FIELDS = [
        "text",
        "chunk_id",
        "document_id",
        "reference",
        "source_page",
        "chunk_idx",
        "chunk_level",
    ]

    def __init__(
        self,
        settings: EvalMilvusSettings | None = None,
        embedder: EmbeddingService | None = None,
    ):
        self.settings = settings or EvalMilvusSettings.from_env()
        self.embedder = embedder or embedding_service

    def _client(self) -> MilvusClient:
        return MilvusClient(uri=self.settings.uri, timeout=self.settings.timeout)

    def ensure_collection(self, dense_dim: int, *, rebuild: bool = False) -> None:
        client = self._client()
        try:
            exists = client.has_collection(self.settings.collection_name)
            if exists and rebuild:
                client.drop_collection(self.settings.collection_name)
                exists = False
            if exists:
                return

            schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
            schema.add_field("dense_embedding", DataType.FLOAT_VECTOR, dim=dense_dim)
            schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_field(
                "text",
                DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params={"type": "standard"},
                enable_match=True,
            )
            schema.add_field("chunk_id", DataType.VARCHAR, max_length=512)
            schema.add_field("document_id", DataType.VARCHAR, max_length=128)
            schema.add_field("reference", DataType.VARCHAR, max_length=4096)
            schema.add_field("source_page", DataType.VARCHAR, max_length=128)
            schema.add_field("chunk_idx", DataType.INT64)
            schema.add_field("chunk_level", DataType.INT64)
            schema.add_function(
                Function(
                    name="ragcare_text_bm25",
                    function_type=FunctionType.BM25,
                    input_field_names=["text"],
                    output_field_names=["sparse_embedding"],
                )
            )

            indexes = client.prepare_index_params()
            indexes.add_index(
                field_name="dense_embedding",
                index_type="HNSW",
                metric_type="IP",
                params={"M": 16, "efConstruction": 256},
            )
            indexes.add_index(
                field_name="sparse_embedding",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
                params={"drop_ratio_build": 0.2},
            )
            client.create_collection(
                collection_name=self.settings.collection_name,
                schema=schema,
                index_params=indexes,
            )
        finally:
            client.close()

    def row_count(self) -> int:
        client = self._client()
        try:
            if not client.has_collection(self.settings.collection_name):
                return 0
            stats = client.get_collection_stats(self.settings.collection_name)
            return int(stats.get("row_count") or 0)
        finally:
            client.close()

    def index_chunks(
        self,
        chunks: Iterable[dict],
        *,
        dense_dim: int,
        batch_size: int = 16,
        rebuild: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        chunks = list(chunks)
        self.ensure_collection(dense_dim, rebuild=rebuild)
        if self.row_count() > 0 and not rebuild:
            return self.row_count()

        client = self._client()
        inserted = 0
        try:
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                texts = [str(chunk.get("text") or "") for chunk in batch]
                vectors = self.embedder.get_embeddings(texts)
                payload = []
                for chunk, vector in zip(batch, vectors):
                    payload.append(
                        {
                            "dense_embedding": vector,
                            "text": str(chunk.get("text") or "")[:65535],
                            "chunk_id": str(chunk.get("chunk_id") or "")[:512],
                            "document_id": str(chunk.get("document_id") or "")[:128],
                            "reference": str(chunk.get("reference") or "")[:4096],
                            "source_page": str(chunk.get("source_page") or "")[:128],
                            "chunk_idx": int(chunk.get("chunk_idx") or 0),
                            "chunk_level": int(chunk.get("chunk_level") or 3),
                        }
                    )
                client.insert(self.settings.collection_name, payload)
                inserted += len(payload)
                if progress_callback:
                    progress_callback(inserted, len(chunks))
            client.flush(self.settings.collection_name)
            return inserted
        finally:
            client.close()

    def embed_query(self, query: str) -> list[float]:
        return self.embedder.get_embeddings([query])[0]

    def _dense_search(self, vector: list[float], limit: int) -> list[dict]:
        client = self._client()
        try:
            return _format_hits(
                client.search(
                    collection_name=self.settings.collection_name,
                    data=[vector],
                    anns_field="dense_embedding",
                    search_params={"metric_type": "IP", "params": {"ef": 64}},
                    limit=limit,
                    output_fields=self.OUTPUT_FIELDS,
                )
            )
        finally:
            client.close()

    def _bm25_search(self, query: str, limit: int) -> list[dict]:
        client = self._client()
        try:
            return _format_hits(
                client.search(
                    collection_name=self.settings.collection_name,
                    data=[query],
                    anns_field="sparse_embedding",
                    search_params={
                        "metric_type": "BM25",
                        "params": {"drop_ratio_search": 0.2},
                    },
                    limit=limit,
                    output_fields=self.OUTPUT_FIELDS,
                )
            )
        finally:
            client.close()

    def _hybrid_search(
        self,
        query: str,
        vector: list[float],
        *,
        limit: int,
        candidate_k: int,
        rrf_k: int,
    ) -> list[dict]:
        dense_request = AnnSearchRequest(
            data=[vector],
            anns_field="dense_embedding",
            param={"metric_type": "IP", "params": {"ef": 64}},
            limit=candidate_k,
        )
        sparse_request = AnnSearchRequest(
            data=[query],
            anns_field="sparse_embedding",
            param={"metric_type": "BM25", "params": {"drop_ratio_search": 0.2}},
            limit=candidate_k,
        )
        client = self._client()
        try:
            return _format_hits(
                client.hybrid_search(
                    collection_name=self.settings.collection_name,
                    reqs=[dense_request, sparse_request],
                    ranker=RRFRanker(k=rrf_k),
                    limit=limit,
                    output_fields=self.OUTPUT_FIELDS,
                )
            )
        finally:
            client.close()

    def retrieve(
        self,
        query: str,
        *,
        mode: str,
        top_k: int = 5,
        candidate_k: int = 20,
        dense_embedding: list[float] | None = None,
        reranker: "LocalBgeReranker | None" = None,
        rrf_k: int = 60,
    ) -> dict:
        if mode not in BASELINE_NAMES:
            raise ValueError(f"Unsupported baseline mode: {mode}")

        meta = {
            "mode": mode,
            "top_k": top_k,
            "candidate_k": candidate_k,
            "dense_used": False,
            "sparse_used": False,
            "reranker_used": False,
        }
        if mode == "bm25":
            docs = self._bm25_search(query, top_k)
            meta["sparse_used"] = True
        elif mode == "dense":
            vector = dense_embedding or self.embed_query(query)
            docs = self._dense_search(vector, top_k)
            meta["dense_used"] = True
        else:
            vector = dense_embedding or self.embed_query(query)
            limit = candidate_k if mode == "hybrid_rerank" else top_k
            docs = self._hybrid_search(
                query,
                vector,
                limit=limit,
                candidate_k=candidate_k,
                rrf_k=rrf_k,
            )
            meta["dense_used"] = True
            meta["sparse_used"] = True
            if mode == "hybrid_rerank":
                if reranker is None:
                    raise RuntimeError("hybrid_rerank requires a configured reranker")
                docs = reranker.rerank(query, docs, top_k=top_k)
                meta["reranker_used"] = True
        return {"docs": docs[:top_k], "meta": meta}


class LocalBgeReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        device: str = "cpu",
        cache_folder: str | None = None,
    ):
        self.model_name = model_name
        self.device = device
        self.cache_folder = cache_folder or os.getenv("HF_HOME")
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name,
                device=self.device,
                cache_folder=self.cache_folder,
            )
        return self._model

    def rerank(self, query: str, docs: list[dict], *, top_k: int) -> list[dict]:
        if not docs:
            return []
        model = self._load()
        pairs = [(query, str(doc.get("text") or "")) for doc in docs]
        scores = model.predict(pairs, show_progress_bar=False)
        reranked: list[dict] = []
        for doc, score in zip(docs, scores):
            item = dict(doc)
            item["retrieval_score"] = item.get("score")
            item["rerank_score"] = float(score)
            reranked.append(item)
        reranked.sort(key=lambda item: item["rerank_score"], reverse=True)
        return reranked[:top_k]
