"""文档向量化并写入 Milvus - 密集向量 + Milvus 服务端 BM25"""
import os

from backend.indexing.embedding import EmbeddingService, embedding_service as _default_embedding_service
from backend.indexing.milvus_client import MilvusStore, get_milvus_store


class MilvusWriter:
    """文档向量化并写入 Milvus 服务 - 支持混合检索"""

    def __init__(self, embedding_service: EmbeddingService = None, milvus_manager: MilvusStore = None):
        self.embedding_service = embedding_service or _default_embedding_service
        self.milvus_manager = milvus_manager or get_milvus_store()

    def write_documents(self, documents: list[dict], batch_size: int | None = None, progress_callback=None):
        if not documents:
            return

        dense_dim = int(os.getenv("DENSE_EMBEDDING_DIM", "1024"))
        if batch_size is None:
            batch_size = int(os.getenv("MILVUS_INSERT_BATCH_SIZE", "100"))
        batch_size = max(1, int(batch_size))

        total = len(documents)
        with self.milvus_manager.session() as client:
            MilvusStore.ensure_collection(client, self.milvus_manager.collection_name, dense_dim)

            for i in range(0, total, batch_size):
                batch = documents[i : i + batch_size]
                texts = [doc["text"] for doc in batch]
                dense_embeddings = self.embedding_service.get_embeddings(texts)

                insert_data = [
                    {
                        "dense_embedding": dense_emb,
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
                    for doc, dense_emb in zip(batch, dense_embeddings)
                ]

                client.insert(self.milvus_manager.collection_name, insert_data)

                if progress_callback:
                    processed = min(i + batch_size, total)
                    progress_callback(processed, total)

            # The API reports a document as indexed only after all inserted rows are searchable.
            client.flush(self.milvus_manager.collection_name)
