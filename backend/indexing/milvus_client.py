"""Milvus 访问层：无状态 Store + 短生命周期 gRPC 连接（避免长期持有失效 channel）。"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from pymilvus import AnnSearchRequest, DataType, MilvusClient, RRFRanker, Function, FunctionType

QUERY_MAX_LIMIT = 16384
T = TypeVar("T")


def escape_filter_value(value: object) -> str:
    """Escape a value before embedding it in a double-quoted Milvus filter literal.

    Milvus filter expressions are parsed, not parameterised: a raw value
    containing a quote terminates the literal and can append further clauses.
    ``filename == "x" or filename != ""`` for instance matches every row in the
    collection, so every caller that interpolates must escape first.
    """
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


COLLECTION_ENV_BY_KIND = {
    "medical_qa": "MILVUS_MEDICAL_QA_COLLECTION",
    "patient_record": "MILVUS_PATIENT_RECORD_COLLECTION",
    "episodic_memory": "MILVUS_EPISODIC_MEMORY_COLLECTION",
    "semantic_memory": "MILVUS_SEMANTIC_MEMORY_COLLECTION",
    "medical_qa_archive": "MILVUS_MEDICAL_QA_ARCHIVE_COLLECTION",
    "patient_record_archive": "MILVUS_PATIENT_RECORD_ARCHIVE_COLLECTION",
}

COLLECTION_DEFAULT_BY_KIND = {
    "medical_qa": "healthtrace_medical_text_v1",
    "patient_record": "healthtrace_patient_record_text_v1",
    "episodic_memory": "healthtrace_episodic_memory_v1",
    "semantic_memory": "healthtrace_semantic_memory_v1",
    "medical_qa_archive": "healthtrace_medical_text_archive_v1",
    "patient_record_archive": "healthtrace_patient_record_text_archive_v1",
}


@dataclass(frozen=True)
class MilvusSettings:
    host: str
    port: str
    collection_name: str
    uri: str
    timeout: float

    @classmethod
    def from_env(cls, collection_name: str | None = None) -> MilvusSettings:
        host = os.getenv("MILVUS_HOST", "localhost")
        port = os.getenv("MILVUS_PORT", "19530")
        collection = collection_name or os.getenv("MILVUS_COLLECTION", "med_medical_qa")
        timeout = float(os.getenv("MILVUS_TIMEOUT", "30"))
        return cls(
            host=host,
            port=port,
            collection_name=collection,
            uri=f"http://{host}:{port}",
            timeout=timeout,
        )


@contextmanager
def milvus_client_session(settings: MilvusSettings | None = None) -> Iterator[MilvusClient]:
    """一次 RPC 会话：创建连接，用完后关闭，不缓存 gRPC channel。"""
    cfg = settings or MilvusSettings.from_env()
    client = MilvusClient(uri=cfg.uri, timeout=cfg.timeout)
    try:
        yield client
    finally:
        client.close()


def _normalize_filter(filter_expr: str) -> str:
    return filter_expr.strip() if filter_expr.strip() else "id >= 0"


def _read_consistency_level() -> str:
    return os.getenv("MILVUS_READ_CONSISTENCY_LEVEL", "Strong").strip() or "Strong"


def _deduplicate_versioned_results(results: list[dict], top_k: int) -> list[dict]:
    """Prefer the newest version when an update overlap returns duplicate content."""
    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for result in results:
        document_key = str(result.get("document_id") or result.get("filename") or "")
        content_key = str(
            result.get("content_fingerprint")
            or result.get("chunk_id")
            or result.get("id")
        )
        key = (document_key, content_key)
        current = best.get(key)
        if current is None:
            best[key] = result
            order.append(key)
            continue
        current_version = int(current.get("document_version", 0) or 0)
        candidate_version = int(result.get("document_version", 0) or 0)
        if candidate_version > current_version:
            best[key] = result
    return [best[key] for key in order][:top_k]


VERSION_OUTPUT_FIELDS = [
    "content_fingerprint",
    "placement_fingerprint",
    "embedding_normalization_version",
    "document_version",
    "content_sha256",
]


class MilvusStore:
    """Milvus 集合读写；本身不持有连接，所有 IO 经 milvus_client_session。"""

    def __init__(self, settings: MilvusSettings | None = None):
        self._settings = settings or MilvusSettings.from_env()

    @classmethod
    def for_collection(cls, collection_name: str) -> "MilvusStore":
        return cls(MilvusSettings.from_env(collection_name=collection_name))

    @classmethod
    def for_kind(cls, kind: str) -> "MilvusStore":
        env_name = COLLECTION_ENV_BY_KIND.get(kind)
        collection = os.getenv(env_name, "") if env_name else ""
        if not collection:
            if kind == "medical_qa":
                collection = os.getenv(
                    "MILVUS_COLLECTION",
                    COLLECTION_DEFAULT_BY_KIND["medical_qa"],
                )
            else:
                collection = COLLECTION_DEFAULT_BY_KIND.get(kind, f"healthtrace_{kind}_v1")
        return cls.for_collection(collection)

    @property
    def collection_name(self) -> str:
        return self._settings.collection_name

    def _run(self, operation: Callable[[MilvusClient], T]) -> T:
        with milvus_client_session(self._settings) as client:
            return operation(client)

    @contextmanager
    def session(self) -> Iterator[MilvusClient]:
        """同一业务流（如整次上传）内复用一条连接，用毕即关。"""
        with milvus_client_session(self._settings) as client:
            yield client

    @staticmethod
    def ensure_collection(client: MilvusClient, collection_name: str, dense_dim: int) -> None:
        if client.has_collection(collection_name):
            return

        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("dense_embedding", DataType.FLOAT_VECTOR, dim=dense_dim)
        schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(
            "text",
            DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
            enable_match=True,
        )
        schema.add_field("filename", DataType.VARCHAR, max_length=255)
        schema.add_field("file_type", DataType.VARCHAR, max_length=50)
        schema.add_field("file_path", DataType.VARCHAR, max_length=1024)
        schema.add_field("page_number", DataType.INT64)
        schema.add_field("chunk_idx", DataType.INT64)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=512)
        schema.add_field("parent_chunk_id", DataType.VARCHAR, max_length=512)
        schema.add_field("root_chunk_id", DataType.VARCHAR, max_length=512)
        schema.add_field("chunk_level", DataType.INT64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_domain", DataType.VARCHAR, max_length=32)
        schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
        schema.add_field("patient_id", DataType.VARCHAR, max_length=64)
        schema.add_field("owner_user_id", DataType.INT64)

        bm25_function = Function(
            name="text_bm25_emb",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["sparse_embedding"],
        )
        schema.add_function(bm25_function)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="dense_embedding",
            index_type="HNSW",
            metric_type="IP",
            params={"M": 16, "efConstruction": 256},
        )
        index_params.add_index(
            field_name="sparse_embedding",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"drop_ratio_build": 0.2},
        )
        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )

    def init_collection(self, dense_dim: int | None = None) -> None:
        if dense_dim is None:
            dense_dim = int(os.getenv("DENSE_EMBEDDING_DIM", "1024"))

        def _init(client: MilvusClient) -> None:
            self.ensure_collection(client, self.collection_name, dense_dim)

        self._run(_init)

    def insert(self, data: list[dict]):
        return self._run(lambda client: client.insert(self.collection_name, data))

    def query(
        self,
        filter_expr: str = "",
        output_fields: list[str] | None = None,
        limit: int = 10000,
        offset: int = 0,
    ):
        expr = _normalize_filter(filter_expr)
        fields = output_fields or ["filename", "file_type"]

        def _query(client: MilvusClient):
            return client.query(
                collection_name=self.collection_name,
                filter=expr,
                output_fields=fields,
                limit=min(limit, QUERY_MAX_LIMIT),
                offset=offset,
                consistency_level=_read_consistency_level(),
            )

        return self._run(_query)

    def query_all(self, filter_expr: str = "", output_fields: list[str] | None = None) -> list:
        """分页拉取；单次 session 内完成，避免每页新建连接。"""
        fields = output_fields or ["filename", "file_type"]
        expr = _normalize_filter(filter_expr)

        def _query_all(client: MilvusClient) -> list:
            out: list = []
            offset = 0
            while True:
                batch = client.query(
                    collection_name=self.collection_name,
                    filter=expr,
                    output_fields=fields,
                    limit=QUERY_MAX_LIMIT,
                    offset=offset,
                    consistency_level=_read_consistency_level(),
                )
                if not batch:
                    break
                out.extend(batch)
                if len(batch) < QUERY_MAX_LIMIT:
                    break
                offset += len(batch)
            return out

        return self._run(_query_all)

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict]:
        ids = [item for item in chunk_ids if item]
        if not ids:
            return []
        quoted_ids = ", ".join(f'"{escape_filter_value(item)}"' for item in ids)
        return self.query(
            filter_expr=f"chunk_id in [{quoted_ids}]",
            output_fields=[
                "text",
                "filename",
                "file_type",
                "page_number",
                "chunk_id",
                "parent_chunk_id",
                "root_chunk_id",
                "chunk_level",
                "chunk_idx",
                "chunk_kind",
                "structure_type",
                "section_path",
                "source_dataset",
                "qa_question",
            ],
            limit=len(ids),
        )

    def hybrid_retrieve(
        self,
        dense_embedding: list[float],
        query: str,
        top_k: int = 5,
        rrf_k: int = 60,
        filter_expr: str = "",
    ) -> list[dict]:
        output_fields = [
            "text",
            "filename",
            "file_type",
            "page_number",
            "chunk_id",
            "parent_chunk_id",
            "root_chunk_id",
            "chunk_level",
            "chunk_idx",
            "document_id",
            "document_domain",
            "tenant_id",
            "patient_id",
            "owner_user_id",
            *VERSION_OUTPUT_FIELDS,
        ]
        dense_search = AnnSearchRequest(
            data=[dense_embedding],
            anns_field="dense_embedding",
            param={"metric_type": "IP", "params": {"ef": 64}},
            limit=top_k * 2,
            expr=filter_expr,
        )
        sparse_search = AnnSearchRequest(
            data=[query],
            anns_field="sparse_embedding",
            param={"metric_type": "BM25", "params": {"drop_ratio_search": 0.2}},
            limit=top_k * 2,
            expr=filter_expr,
        )
        reranker = RRFRanker(k=rrf_k)

        def _search(client: MilvusClient):
            return client.hybrid_search(
                collection_name=self.collection_name,
                reqs=[dense_search, sparse_search],
                ranker=reranker,
                limit=top_k * 3,
                output_fields=output_fields,
                consistency_level=_read_consistency_level(),
            )

        results = self._run(_search)
        formatted_results = []
        for hits in results:
            for hit in hits:
                formatted_results.append({
                    "id": hit.get("id"),
                    "text": hit.get("text", ""),
                    "filename": hit.get("filename", ""),
                    "file_type": hit.get("file_type", ""),
                    "page_number": hit.get("page_number", 0),
                    "chunk_id": hit.get("chunk_id", ""),
                    "parent_chunk_id": hit.get("parent_chunk_id", ""),
                    "root_chunk_id": hit.get("root_chunk_id", ""),
                    "chunk_level": hit.get("chunk_level", 0),
                    "chunk_idx": hit.get("chunk_idx", 0),
                    "document_id": hit.get("document_id", ""),
                    "document_domain": hit.get("document_domain", ""),
                    "tenant_id": hit.get("tenant_id", ""),
                    "patient_id": hit.get("patient_id", ""),
                    "owner_user_id": hit.get("owner_user_id", 0),
                    "content_fingerprint": hit.get("content_fingerprint", ""),
                    "placement_fingerprint": hit.get("placement_fingerprint", ""),
                    "embedding_normalization_version": hit.get(
                        "embedding_normalization_version", ""
                    ),
                    "document_version": hit.get("document_version", 0),
                    "content_sha256": hit.get("content_sha256", ""),
                    "chunk_kind": hit.get("chunk_kind", ""),
                    "structure_type": hit.get("structure_type", ""),
                    "section_path": hit.get("section_path", ""),
                    "source_dataset": hit.get("source_dataset", ""),
                    "qa_question": hit.get("qa_question", ""),
                    "score": hit.get("distance", 0.0),
                })
        return _deduplicate_versioned_results(formatted_results, top_k)

    def dense_retrieve(
        self,
        dense_embedding: list[float],
        top_k: int = 5,
        filter_expr: str = "",
    ) -> list[dict]:
        def _search(client: MilvusClient):
            return client.search(
                collection_name=self.collection_name,
                data=[dense_embedding],
                anns_field="dense_embedding",
                search_params={"metric_type": "IP", "params": {"ef": 64}},
                limit=top_k * 3,
                output_fields=[
                    "text",
                    "filename",
                    "file_type",
                    "page_number",
                    "chunk_id",
                    "parent_chunk_id",
                    "root_chunk_id",
                    "chunk_level",
                    "chunk_idx",
                    "document_id",
                    "document_domain",
                    "tenant_id",
                    "patient_id",
                    "owner_user_id",
                    "chunk_kind",
                    "structure_type",
                    "section_path",
                    "source_dataset",
                    "qa_question",
                    *VERSION_OUTPUT_FIELDS,
                ],
                filter=filter_expr,
                consistency_level=_read_consistency_level(),
            )

        results = self._run(_search)
        formatted_results = []
        for hits in results:
            for hit in hits:
                formatted_results.append({
                    "id": hit.get("id"),
                    "text": hit.get("entity", {}).get("text", ""),
                    "filename": hit.get("entity", {}).get("filename", ""),
                    "file_type": hit.get("entity", {}).get("file_type", ""),
                    "page_number": hit.get("entity", {}).get("page_number", 0),
                    "chunk_id": hit.get("entity", {}).get("chunk_id", ""),
                    "parent_chunk_id": hit.get("entity", {}).get("parent_chunk_id", ""),
                    "root_chunk_id": hit.get("entity", {}).get("root_chunk_id", ""),
                    "chunk_level": hit.get("entity", {}).get("chunk_level", 0),
                    "chunk_idx": hit.get("entity", {}).get("chunk_idx", 0),
                    "document_id": hit.get("entity", {}).get("document_id", ""),
                    "document_domain": hit.get("entity", {}).get("document_domain", ""),
                    "tenant_id": hit.get("entity", {}).get("tenant_id", ""),
                    "patient_id": hit.get("entity", {}).get("patient_id", ""),
                    "owner_user_id": hit.get("entity", {}).get("owner_user_id", 0),
                    "content_fingerprint": hit.get("entity", {}).get(
                        "content_fingerprint", ""
                    ),
                    "placement_fingerprint": hit.get("entity", {}).get(
                        "placement_fingerprint", ""
                    ),
                    "embedding_normalization_version": hit.get("entity", {}).get(
                        "embedding_normalization_version", ""
                    ),
                    "document_version": hit.get("entity", {}).get("document_version", 0),
                    "content_sha256": hit.get("entity", {}).get("content_sha256", ""),
                    "chunk_kind": hit.get("entity", {}).get("chunk_kind", ""),
                    "structure_type": hit.get("entity", {}).get("structure_type", ""),
                    "section_path": hit.get("entity", {}).get("section_path", ""),
                    "source_dataset": hit.get("entity", {}).get("source_dataset", ""),
                    "qa_question": hit.get("entity", {}).get("qa_question", ""),
                    "score": hit.get("distance", 0.0),
                })
        return _deduplicate_versioned_results(formatted_results, top_k)

    def sparse_retrieve(
        self,
        query: str,
        top_k: int = 5,
        filter_expr: str = "",
    ) -> list[dict]:
        def _search(client: MilvusClient):
            return client.search(
                collection_name=self.collection_name,
                data=[query],
                anns_field="sparse_embedding",
                search_params={"metric_type": "BM25", "params": {"drop_ratio_search": 0.2}},
                limit=top_k * 3,
                output_fields=[
                    "text",
                    "filename",
                    "file_type",
                    "page_number",
                    "chunk_id",
                    "parent_chunk_id",
                    "root_chunk_id",
                    "chunk_level",
                    "chunk_idx",
                    "document_id",
                    "document_domain",
                    "tenant_id",
                    "patient_id",
                    "owner_user_id",
                    "chunk_kind",
                    "structure_type",
                    "section_path",
                    "source_dataset",
                    "qa_question",
                    *VERSION_OUTPUT_FIELDS,
                ],
                filter=filter_expr,
                consistency_level=_read_consistency_level(),
            )

        results = self._run(_search)
        formatted_results = []
        for hits in results:
            for hit in hits:
                entity = hit.get("entity", {})
                formatted_results.append({
                    "id": hit.get("id"),
                    "text": entity.get("text", ""),
                    "filename": entity.get("filename", ""),
                    "file_type": entity.get("file_type", ""),
                    "page_number": entity.get("page_number", 0),
                    "chunk_id": entity.get("chunk_id", ""),
                    "parent_chunk_id": entity.get("parent_chunk_id", ""),
                    "root_chunk_id": entity.get("root_chunk_id", ""),
                    "chunk_level": entity.get("chunk_level", 0),
                    "chunk_idx": entity.get("chunk_idx", 0),
                    "document_id": entity.get("document_id", ""),
                    "document_domain": entity.get("document_domain", ""),
                    "tenant_id": entity.get("tenant_id", ""),
                    "patient_id": entity.get("patient_id", ""),
                    "owner_user_id": entity.get("owner_user_id", 0),
                    "content_fingerprint": entity.get("content_fingerprint", ""),
                    "placement_fingerprint": entity.get("placement_fingerprint", ""),
                    "embedding_normalization_version": entity.get(
                        "embedding_normalization_version", ""
                    ),
                    "document_version": entity.get("document_version", 0),
                    "content_sha256": entity.get("content_sha256", ""),
                    "chunk_kind": entity.get("chunk_kind", ""),
                    "structure_type": entity.get("structure_type", ""),
                    "section_path": entity.get("section_path", ""),
                    "source_dataset": entity.get("source_dataset", ""),
                    "qa_question": entity.get("qa_question", ""),
                    "score": hit.get("distance", 0.0),
                })
        return _deduplicate_versioned_results(formatted_results, top_k)

    def delete(self, filter_expr: str):
        def _delete(client: MilvusClient):
            result = client.delete(collection_name=self.collection_name, filter=filter_expr)
            if os.getenv("MILVUS_FLUSH_AFTER_DELETE", "true").lower() == "true":
                client.flush(self.collection_name)
            return result

        return self._run(_delete)

    def has_collection(self) -> bool:
        return self._run(lambda client: client.has_collection(self.collection_name))

    def drop_collection(self) -> None:
        def _drop(client: MilvusClient) -> None:
            if client.has_collection(self.collection_name):
                client.drop_collection(self.collection_name)

        self._run(_drop)


# 兼容旧名；全项目共用同一无状态 Store 实例即可（不缓存连接）
MilvusManager = MilvusStore

_stores: dict[str, MilvusStore] = {}


def get_milvus_store(kind: str = "medical_qa") -> MilvusStore:
    if kind not in _stores:
        _stores[kind] = MilvusStore.for_kind(kind)
    return _stores[kind]
