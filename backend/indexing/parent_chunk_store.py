"""父级分块文档存储（用于 Auto-merging Retriever）"""
from datetime import datetime
from typing import List

from backend.infra.cache import cache
from backend.infra.database import SessionLocal
from backend.db.models import ParentChunk


class ParentChunkStore:
    """基于 PostgreSQL + Redis 的父级分块存储。"""

    @staticmethod
    def _to_dict(item: ParentChunk) -> dict:
        return {
            "text": item.text,
            "filename": item.filename,
            "file_type": item.file_type,
            "file_path": item.file_path,
            "page_number": item.page_number,
            "chunk_id": item.chunk_id,
            "parent_chunk_id": item.parent_chunk_id,
            "root_chunk_id": item.root_chunk_id,
            "chunk_level": item.chunk_level,
            "chunk_idx": item.chunk_idx,
            "document_id": item.document_id,
            "document_domain": item.document_domain,
            "tenant_id": item.tenant_id,
            "patient_id": item.patient_id,
            "owner_user_id": item.owner_user_id,
        }

    @staticmethod
    def _cache_key(chunk_id: str) -> str:
        return f"parent_chunk:{chunk_id}"

    def upsert_documents(self, docs: List[dict]) -> int:
        """写入/更新父级分块，返回写入条数。"""
        if not docs:
            return 0

        db = SessionLocal()
        upserted = 0
        try:
            for doc in docs:
                chunk_id = (doc.get("chunk_id") or "").strip()
                if not chunk_id:
                    continue

                record = db.query(ParentChunk).filter(ParentChunk.chunk_id == chunk_id).first()
                payload = {
                    "text": doc.get("text", ""),
                    "filename": doc.get("filename", ""),
                    "file_type": doc.get("file_type", ""),
                    "file_path": doc.get("file_path", ""),
                    "page_number": int(doc.get("page_number", 0) or 0),
                    "parent_chunk_id": doc.get("parent_chunk_id", ""),
                    "root_chunk_id": doc.get("root_chunk_id", ""),
                    "chunk_level": int(doc.get("chunk_level", 0) or 0),
                    "chunk_idx": int(doc.get("chunk_idx", 0) or 0),
                    "document_id": doc.get("document_id", ""),
                    "document_domain": doc.get("document_domain", "public_medical"),
                    "tenant_id": doc.get("tenant_id") or None,
                    "patient_id": doc.get("patient_id") or None,
                    "owner_user_id": doc.get("owner_user_id") or None,
                    "updated_at": datetime.utcnow(),
                }
                cache_payload = {
                    "chunk_id": chunk_id,
                    "text": payload["text"],
                    "filename": payload["filename"],
                    "file_type": payload["file_type"],
                    "file_path": payload["file_path"],
                    "page_number": payload["page_number"],
                    "parent_chunk_id": payload["parent_chunk_id"],
                    "root_chunk_id": payload["root_chunk_id"],
                    "chunk_level": payload["chunk_level"],
                    "chunk_idx": payload["chunk_idx"],
                    "document_id": payload["document_id"],
                    "document_domain": payload["document_domain"],
                    "tenant_id": payload["tenant_id"],
                    "patient_id": payload["patient_id"],
                    "owner_user_id": payload["owner_user_id"],
                }
                if record:
                    for key, value in payload.items():
                        setattr(record, key, value)
                else:
                    db.add(ParentChunk(chunk_id=chunk_id, **payload))

                cache.set_json(self._cache_key(chunk_id), cache_payload)
                upserted += 1

            db.commit()
        finally:
            db.close()

        return upserted

    def get_documents_by_ids(
        self,
        chunk_ids: List[str],
        tenant_id: str | None = None,
        patient_id: str | None = None,
    ) -> List[dict]:
        if not chunk_ids:
            return []

        ordered_results = {}
        missing_ids = []
        for chunk_id in chunk_ids:
            key = (chunk_id or "").strip()
            if not key:
                continue
            cached = cache.get_json(self._cache_key(key))
            cache_matches_scope = cached and (
                tenant_id is None
                or (
                    cached.get("tenant_id") == tenant_id
                    and cached.get("patient_id") == patient_id
                )
            )
            if cache_matches_scope:
                ordered_results[key] = cached
            else:
                missing_ids.append(key)

        if missing_ids:
            db = SessionLocal()
            try:
                query = db.query(ParentChunk).filter(ParentChunk.chunk_id.in_(missing_ids))
                if tenant_id is not None:
                    query = query.filter(
                        ParentChunk.tenant_id == tenant_id,
                        ParentChunk.patient_id == patient_id,
                    )
                rows = query.all()
                for row in rows:
                    payload = self._to_dict(row)
                    ordered_results[row.chunk_id] = payload
                    cache.set_json(self._cache_key(row.chunk_id), payload)
            finally:
                db.close()

        return [ordered_results[item] for item in chunk_ids if item in ordered_results]

    def delete_by_filename(self, filename: str) -> int:
        """按文件名删除父级分块，返回删除条数。"""
        if not filename:
            return 0

        db = SessionLocal()
        try:
            rows = db.query(ParentChunk).filter(ParentChunk.filename == filename).all()
            chunk_ids = [row.chunk_id for row in rows]
            deleted = len(chunk_ids)
            if deleted > 0:
                db.query(ParentChunk).filter(ParentChunk.filename == filename).delete(synchronize_session=False)
                db.commit()
                for chunk_id in chunk_ids:
                    cache.delete(self._cache_key(chunk_id))
            return deleted
        finally:
            db.close()

    def delete_by_document_id(
        self,
        document_id: str,
        tenant_id: str,
        patient_id: str,
    ) -> int:
        """Delete one patient document's parent chunks after scope verification."""
        db = SessionLocal()
        try:
            rows = (
                db.query(ParentChunk)
                .filter(
                    ParentChunk.document_id == document_id,
                    ParentChunk.tenant_id == tenant_id,
                    ParentChunk.patient_id == patient_id,
                )
                .all()
            )
            chunk_ids = [row.chunk_id for row in rows]
            if chunk_ids:
                (
                    db.query(ParentChunk)
                    .filter(
                        ParentChunk.document_id == document_id,
                        ParentChunk.tenant_id == tenant_id,
                        ParentChunk.patient_id == patient_id,
                    )
                    .delete(synchronize_session=False)
                )
                db.commit()
                for chunk_id in chunk_ids:
                    cache.delete(self._cache_key(chunk_id))
            return len(chunk_ids)
        finally:
            db.close()
