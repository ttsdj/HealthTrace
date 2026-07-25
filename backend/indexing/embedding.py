"""文本向量化服务 - 只支持密集向量（由 Milvus 2.5+ 原生支持中文分词与 BM25 全文检索）"""
import os
import hashlib
import threading

from backend.env import load_env
from backend.indexing.text_normalization import canonicalize_embedding_text

load_env()

from langchain_huggingface import HuggingFaceEmbeddings


def _hash_embedding(text: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    clean = (text or "").strip()
    if not clean:
        return vector

    tokens = [clean[i : i + 2] for i in range(max(len(clean) - 1, 1))]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def _create_dense_embedder() -> HuggingFaceEmbeddings:
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    device = os.getenv("EMBEDDING_DEVICE", "cpu")
    return HuggingFaceEmbeddings(
        model_name=model_name,
        cache_folder=os.getenv("HF_HOME"),
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},
    )


class EmbeddingService:
    """文本向量化服务 - 密集向量本地模型"""

    def __init__(self, state_path=None):
        self._embedder = None
        self._embedder_lock = threading.Lock()

    def _get_embedder(self) -> HuggingFaceEmbeddings:
        if self._embedder is None:
            with self._embedder_lock:
                if self._embedder is None:
                    self._embedder = _create_dense_embedder()
        return self._embedder

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        canonical_texts = [canonicalize_embedding_text(text) for text in texts]
        if os.getenv("EMBEDDING_BACKEND", "").strip().lower() == "hash":
            dim = int(os.getenv("DENSE_EMBEDDING_DIM", "1024"))
            return [_hash_embedding(text, dim) for text in canonical_texts]
        try:
            return self._get_embedder().embed_documents(canonical_texts)
        except Exception as e:
            raise Exception(f"本地密集嵌入模型调用失败: {str(e)}") from e

    def get_embedding(self, text: str) -> list[float]:
        """Embed one query while keeping batch embedding as the single implementation path."""
        embeddings = self.get_embeddings([text])
        if not embeddings:
            raise ValueError("Embedding backend returned no vector for the query")
        return embeddings[0]


# 全进程唯一实例
embedding_service = EmbeddingService()
