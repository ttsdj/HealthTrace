from __future__ import annotations

from datetime import datetime, timezone

from backend.indexing.embedding import embedding_service
from backend.indexing.milvus_client import MilvusStore, get_milvus_store
from backend.indexing.milvus_writer import MilvusWriter
from backend.db.models import User
from backend.infra.database import SessionLocal
from backend.patient.scope import PatientScope, ensure_user_scope


SEMANTIC_MARKERS = (
    "我有",
    "我患有",
    "我正在",
    "我长期",
    "我今年",
    "我平时",
    "我的病史",
    "家族史",
    "过敏",
    "病史",
    "用药",
    "长期服用",
    "正在吃",
    "正在用",
    "怀孕",
    "备孕",
    "哺乳",
    "不能吃",
    "不适合",
    "偏好",
    "希望回答",
    "回答简短",
)


def is_semantic_memory_candidate(text: str) -> bool:
    """Conservatively identify user statements worth retaining long term."""

    return any(marker in (text or "") for marker in SEMANTIC_MARKERS)


def _escape_filter_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


class MemoryService:
    def __init__(self) -> None:
        self.episodic_store = get_milvus_store("episodic_memory")
        self.semantic_store = get_milvus_store("semantic_memory")
        self.episodic_writer = MilvusWriter(
            embedding_service=embedding_service,
            milvus_manager=self.episodic_store,
        )
        self.semantic_writer = MilvusWriter(
            embedding_service=embedding_service,
            milvus_manager=self.semantic_store,
        )

    @staticmethod
    def _memory_doc(
        text: str,
        user_id: str,
        session_id: str,
        memory_type: str,
        scope: PatientScope | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        chunk_id = f"{memory_type}:{user_id}:{session_id}:{now}"
        return {
            "filename": f"{memory_type}:{user_id}:{session_id}",
            "file_path": "",
            "file_type": memory_type,
            "page_number": 0,
            "text": text[:1800],
            "chunk_id": chunk_id,
            "parent_chunk_id": "",
            "root_chunk_id": chunk_id,
            "chunk_level": 3,
            "chunk_idx": 0,
            "user_id": str(user_id),
            "session_id": str(session_id),
            "memory_type": memory_type,
            "created_at": now,
            "verification_status": "candidate_unconfirmed",
            "document_domain": "patient_memory",
            "tenant_id": scope.tenant_id if scope else "",
            "patient_id": scope.patient_id if scope else "",
            "owner_user_id": scope.user_id if scope else 0,
        }

    @staticmethod
    def _resolve_scope(user_id: str) -> PatientScope | None:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if user is None:
                return None
            scope = ensure_user_scope(db, user)
            db.commit()
            return scope
        except Exception:
            db.rollback()
            return None
        finally:
            db.close()

    def store_turn(self, user_id: str, session_id: str, user_text: str, ai_response: str) -> None:
        scope = self._resolve_scope(user_id)
        if scope is None:
            return
        episode = (
            f"用户 {user_id} 在会话 {session_id} 中提问：{user_text}\n"
            f"系统回答摘要：{ai_response[:800]}"
        )
        try:
            self.episodic_writer.write_documents([
                self._memory_doc(episode, user_id, session_id, "episodic_memory", scope)
            ])
        except Exception as exc:
            print(f"Episodic memory write error: {exc}")

        if is_semantic_memory_candidate(user_text):
            semantic = f"用户 {user_id} 的长期医疗相关事实或偏好：{user_text}"
            try:
                self.semantic_writer.write_documents([
                    self._memory_doc(semantic, user_id, session_id, "semantic_memory", scope)
                ])
            except Exception as exc:
                print(f"Semantic memory write error: {exc}")

    def retrieve(
        self,
        query: str,
        user_id: str,
        session_id: str | None = None,
        top_k: int = 3,
    ) -> dict[str, list[dict]]:
        del session_id  # Reserved for an optional same-session boost/reranker.
        scope = self._resolve_scope(user_id)
        if scope is None:
            return {"episodic_memory": [], "semantic_memory": []}
        dense = embedding_service.get_embeddings([query])[0]
        user_filter = (
            f'user_id == "{_escape_filter_value(user_id)}" and '
            f'tenant_id == "{_escape_filter_value(scope.tenant_id)}" and '
            f'patient_id == "{_escape_filter_value(scope.patient_id)}"'
        )
        return {
            "episodic_memory": self._hybrid(
                self.episodic_store, dense, query, top_k, user_filter
            ),
            "semantic_memory": self._hybrid(
                self.semantic_store, dense, query, top_k, user_filter
            ),
        }

    @staticmethod
    def _hybrid(
        store: MilvusStore,
        dense: list[float],
        query: str,
        top_k: int,
        filter_expr: str,
    ) -> list[dict]:
        try:
            store.init_collection()
            return store.hybrid_retrieve(
                dense_embedding=dense,
                query=query,
                top_k=top_k,
                filter_expr=f"chunk_level == 3 and {filter_expr}",
            )
        except Exception:
            return []

    @staticmethod
    def format_for_prompt(memories: dict[str, list[dict]]) -> str:
        lines: list[str] = []
        labels = {
            "semantic_memory": "语义记忆",
            "episodic_memory": "情景记忆",
        }
        for key, docs in memories.items():
            if not docs:
                continue
            lines.append(f"【{labels.get(key, key)}】")
            for idx, doc in enumerate(docs, 1):
                lines.append(
                    f"{idx}. [未核验记忆，仅作上下文线索] {doc.get('text', '')[:500]}"
                )
        return "\n".join(lines)


memory_service = MemoryService()
