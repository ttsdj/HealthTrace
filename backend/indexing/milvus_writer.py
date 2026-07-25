"""文档向量化并写入 Milvus - 密集向量 + Milvus 服务端 BM25"""
import os

from backend.indexing.embedding import EmbeddingService, embedding_service as _default_embedding_service
from backend.indexing.milvus_client import MilvusStore, get_milvus_store


class MilvusWriter:
    """文档向量化并写入 Milvus 服务 - 支持混合检索"""

    def __init__(self, embedding_service: EmbeddingService = None, milvus_manager: MilvusStore = None):
        self.embedding_service = embedding_service or _default_embedding_service
        self.milvus_manager = milvus_manager or get_milvus_store()

    @staticmethod
    def _insert_payload(doc: dict, dense_embedding: list[float]) -> dict:
        payload = {
            "dense_embedding": dense_embedding,
            "text": doc["text"],
            "filename": doc["filename"],
            "file_type": doc["file_type"],
            "file_path": doc.get("file_path", ""),
            "page_number": doc.get("page_number", 0),
            "chunk_idx": doc.get("chunk_idx", 0),
            "chunk_id": doc.get("chunk_id", ""),
            "parent_chunk_id": doc.get("parent_chunk_id", ""),
            "root_chunk_id": doc.get("root_chunk_id", ""),
            "chunk_level": doc.get("chunk_level", 0),
            "chunk_kind": doc.get("chunk_kind", ""),
            "structure_type": doc.get("structure_type", ""),
            "section_path": doc.get("section_path", ""),
            "source_dataset": doc.get("source_dataset", ""),
            "qa_question": doc.get("qa_question", ""),
            "document_id": doc.get("document_id", ""),
            "document_domain": doc.get("document_domain", "public_medical"),
            "tenant_id": doc.get("tenant_id", ""),
            "patient_id": doc.get("patient_id", ""),
            "owner_user_id": int(doc.get("owner_user_id", 0) or 0),
        }
        # These fields are dynamic in existing collections, so Phase 7 does not
        # require a destructive Milvus schema rebuild.
        for key in (
            "content_fingerprint",
            "index_update_id",
            "document_version",
            "content_sha256",
            "placement_fingerprint",
            "embedding_normalization_version",
            "archived_version_id",
            "retention_until_epoch",
        ):
            if doc.get(key) not in (None, ""):
                payload[key] = doc[key]
        return payload

    def prepare_documents(
        self,
        documents: list[dict],
        *,
        precomputed_embeddings: list[list[float] | None] | None = None,
        batch_size: int | None = None,
        progress_callback=None,
    ) -> list[dict]:
        """Build insert payloads without exposing a partially prepared version."""
        if not documents:
            return []

        if batch_size is None:
            batch_size = int(os.getenv("MILVUS_INSERT_BATCH_SIZE", "100"))
        batch_size = max(1, int(batch_size))
        total = len(documents)
        embeddings = precomputed_embeddings or [None] * total
        if len(embeddings) != total:
            raise ValueError("precomputed_embeddings must align with documents")

        prepared: list[dict] = []
        for i in range(0, total, batch_size):
            batch = documents[i : i + batch_size]
            batch_embeddings = list(embeddings[i : i + batch_size])
            missing_positions = [
                position for position, value in enumerate(batch_embeddings) if value is None
            ]
            if missing_positions:
                new_embeddings = self.embedding_service.get_embeddings(
                    [batch[position]["text"] for position in missing_positions]
                )
                if len(new_embeddings) != len(missing_positions):
                    raise ValueError("Embedding backend returned an unexpected vector count")
                for position, value in zip(missing_positions, new_embeddings):
                    batch_embeddings[position] = value

            prepared.extend(
                self._insert_payload(doc, dense_embedding)
                for doc, dense_embedding in zip(batch, batch_embeddings)
            )
            if progress_callback:
                progress_callback(min(i + batch_size, total), total)
        return prepared

    def insert_prepared(
        self,
        rows: list[dict],
        *,
        batch_size: int | None = None,
        progress_callback=None,
    ) -> list[int]:
        if not rows:
            return []
        dense_dim = int(os.getenv("DENSE_EMBEDDING_DIM", "1024"))
        batch_size = max(
            1,
            int(batch_size or os.getenv("MILVUS_INSERT_BATCH_SIZE", "100")),
        )
        total = len(rows)
        inserted_ids: list[int] = []
        with self.milvus_manager.session() as client:
            MilvusStore.ensure_collection(client, self.milvus_manager.collection_name, dense_dim)
            for i in range(0, total, batch_size):
                result = client.insert(
                    self.milvus_manager.collection_name,
                    rows[i : i + batch_size],
                )
                if isinstance(result, dict):
                    inserted_ids.extend(int(value) for value in result.get("ids", []))
                if progress_callback:
                    progress_callback(min(i + batch_size, total), total)
            client.flush(self.milvus_manager.collection_name)
        return inserted_ids

    def write_documents(self, documents: list[dict], batch_size: int | None = None, progress_callback=None):
        if not documents:
            return
        rows = self.prepare_documents(
            documents,
            batch_size=batch_size,
            progress_callback=progress_callback,
        )
        self.insert_prepared(rows, batch_size=batch_size)
