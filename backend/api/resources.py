import hashlib
import os
from datetime import datetime
from pathlib import Path

from backend.indexing import (
    DocumentLoader,
    MilvusWriter,
    ParentChunkStore,
    embedding_service,
    public_document_id,
)
from backend.db.models import DocumentRecord, DocumentVersion
from backend.infra.database import SessionLocal
from backend.indexing.milvus_client import escape_filter_value, get_milvus_store
from backend.indexing.ocr import IMAGE_EXTENSIONS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "documents"

loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
milvus_manager = get_milvus_store()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)


def delete_document_transactionally(filename: str, job_manager=None, job_id=None) -> int:
    """
    一致性且事务性地删除文档的所有关联数据（Milvus 2.5+ 新版由服务端自动维护 BM25 索引统计）。
    包含以下步骤：
    1. 初始化 Milvus 集合。
    2. 删除 Milvus 向量数据。
    3. 删除 PostgreSQL 中的 L1/L2 父级分块以及对应的 Redis 缓存。
    """
    if job_manager and job_id:
        job_manager.update_step(job_id, "prepare", 50, "running", "正在初始化 Milvus 集合")
    
    milvus_manager.init_collection()
    delete_expr = f'filename == "{escape_filter_value(filename)}"'
    
    if job_manager and job_id:
        job_manager.complete_step(job_id, "prepare", "准备完成")
        # 兼容已有前端删除步骤
        job_manager.update_step(job_id, "bm25", 100, "completed", "BM25 全文检索统计已自动同步（Milvus 服务端自动维护）")

    # 删除 Milvus 向量
    if job_manager and job_id:
        job_manager.update_step(job_id, "milvus", 20, "running", "正在物理删除 Milvus 中的向量分块")
    
    chunks_deleted = 0
    try:
        result = milvus_manager.delete(delete_expr)
        chunks_deleted = result.get("delete_count", 0) if isinstance(result, dict) else 0
    except Exception as e:
        raise RuntimeError(f"删除 Milvus 向量失败: {str(e)}") from e

    if job_manager and job_id:
        job_manager.complete_step(job_id, "milvus", f"向量数据清理完成，共删除 {chunks_deleted} 条记录")

    # 删除 Postgres 中的 ParentChunk 和 Redis 缓存
    if job_manager and job_id:
        job_manager.update_step(job_id, "parent_store", 20, "running", "正在清理 PostgreSQL 数据库和 Redis 中的父级分块")
    
    try:
        parent_chunk_store.delete_by_filename(filename)
    except Exception as e:
        raise RuntimeError(f"清理 PostgreSQL 父级分块及缓存失败: {str(e)}") from e

    if job_manager and job_id:
        job_manager.complete_step(job_id, "parent_store", "父级分块及 Redis 缓存已清空")

    db = SessionLocal()
    try:
        record = (
            db.query(DocumentRecord)
            .filter(DocumentRecord.id == public_document_id(filename))
            .first()
        )
        if record is not None:
            now = datetime.utcnow()
            record.status = "deleted"
            record.error_message = ""
            record.updated_at = now
            record.metadata_json = {
                **(record.metadata_json or {}),
                "deleted_at": now.isoformat() + "Z",
            }
            (
                db.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == record.id,
                    DocumentVersion.status == "active",
                )
                .update({"status": "deleted"}, synchronize_session=False)
            )
            db.commit()
    finally:
        db.close()

    return chunks_deleted


def is_supported_document(filename: str) -> bool:
    file_lower = filename.lower()
    return (
        file_lower.endswith(".pdf")
        or file_lower.endswith((".docx", ".doc"))
        or file_lower.endswith((".xlsx", ".xls"))
        or file_lower.endswith((".html", ".htm"))
        or file_lower.endswith(IMAGE_EXTENSIONS)
    )


async def save_upload_file(file, file_path: Path) -> str:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            f.write(chunk)
    return digest.hexdigest()


def ensure_upload_dir() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
