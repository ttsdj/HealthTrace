from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.api.resources import DATA_DIR, loader, parent_chunk_store
from backend.db.models import DocumentRecord
from backend.indexing import MilvusWriter, embedding_service, get_milvus_store
from backend.infra.auth import get_db
from backend.jobs.queue import JobReporter, enqueue_job
from backend.infra.database import SessionLocal
from backend.patient.documents import (
    is_within_private_root,
    patient_document_path,
    require_patient_domains_enabled,
    safe_upload_filename,
    save_patient_upload,
    scope_document_chunks,
)
from backend.patient.retrieval import retrieve_patient_records
from backend.patient.scope import (
    PatientScope,
    get_current_patient_scope,
    get_current_patient_write_scope,
)
from backend.schemas.patient import (
    PatientDocumentDeleteResponse,
    PatientDocumentInfo,
    PatientDocumentListResponse,
    PatientDocumentUploadResponse,
    PatientDocumentUploadStartResponse,
    PatientEvidenceItem,
    PatientScopeResponse,
    PatientSearchRequest,
    PatientSearchResponse,
)

router = APIRouter(prefix="/patient", tags=["patient-records"])
PRIVATE_ROOT = DATA_DIR / "documents" / "private"


def _scoped_record_query(db: Session, scope: PatientScope):
    return db.query(DocumentRecord).filter(
        DocumentRecord.document_domain == "patient_private",
        DocumentRecord.tenant_id == scope.tenant_id,
        DocumentRecord.patient_id == scope.patient_id,
    )


def process_queued_patient_document(job_id: str, payload: dict) -> dict:
    reporter = JobReporter(job_id)
    document_id = str(payload["document_id"])
    db = SessionLocal()
    record = None
    try:
        record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
        if record is None:
            raise ValueError("Patient document record no longer exists")
        scope = PatientScope(
            record.tenant_id or "",
            record.patient_id or "",
            int(record.owner_user_id or 0),
            "background-worker",
            "manage",
        )
        path = Path(record.storage_uri)
        reporter.update_step(job_id, "parse", 5, "running", "Parsing patient document")
        record.status = "parsing"
        record.updated_at = datetime.utcnow()
        db.commit()

        loaded = loader.load_document(str(path), record.filename)
        scoped_docs = scope_document_chunks(
            loaded,
            document_id=document_id,
            scope=scope,
            storage_uri=str(path),
        )
        parent_docs = [
            item for item in scoped_docs if int(item.get("chunk_level", 0)) in (1, 2)
        ]
        leaf_docs = [
            item for item in scoped_docs if int(item.get("chunk_level", 0)) == 3
        ]
        if not leaf_docs:
            raise ValueError("No searchable patient record chunks were generated")
        reporter.complete_step(job_id, "parse", f"Generated {len(leaf_docs)} leaf chunks")

        reporter.update_step(job_id, "parent_store", 20, "running", "Writing parent chunks")
        parent_chunk_store.upsert_documents(parent_docs)
        reporter.complete_step(job_id, "parent_store", f"Stored {len(parent_docs)} parent chunks")

        reporter.update_step(job_id, "vector_store", 5, "running", "Embedding patient chunks")
        patient_store = get_milvus_store("patient_record")
        MilvusWriter(embedding_service, patient_store).write_documents(leaf_docs)
        reporter.complete_step(job_id, "vector_store", f"Indexed {len(leaf_docs)} chunks")

        record.status = "indexed"
        record.metadata_json = {
            "parent_chunks": len(parent_docs),
            "leaf_chunks": len(leaf_docs),
            "collection": patient_store.collection_name,
            "background_job_id": job_id,
        }
        record.updated_at = datetime.utcnow()
        db.commit()
        reporter.complete_job(job_id, "Patient document is searchable")
        return {
            "document_id": document_id,
            "parent_chunks": len(parent_docs),
            "leaf_chunks": len(leaf_docs),
        }
    except Exception as exc:
        db.rollback()
        if record is None:
            record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
        if record is not None:
            record.status = "failed"
            record.error_message = str(exc)[:2000]
            record.updated_at = datetime.utcnow()
            db.commit()
        reporter.fail_job(job_id, "vector_store", str(exc))
        raise
    finally:
        db.close()


@router.get("/scope", response_model=PatientScopeResponse)
async def get_scope(scope: PatientScope = Depends(get_current_patient_scope)):
    return PatientScopeResponse(tenant_id=scope.tenant_id, patient_id=scope.patient_id)


@router.get("/documents", response_model=PatientDocumentListResponse)
async def list_patient_documents(
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    require_patient_domains_enabled()
    records = _scoped_record_query(db, scope).order_by(DocumentRecord.created_at.desc()).all()
    return PatientDocumentListResponse(
        documents=[
            PatientDocumentInfo(
                document_id=item.id,
                filename=item.filename,
                file_type=item.file_type,
                status=item.status,
                created_at=item.created_at,
                updated_at=item.updated_at,
                chunks_processed=int((item.metadata_json or {}).get("leaf_chunks", 0)),
                fact_candidate_count=int(
                    ((item.metadata_json or {}).get("fact_extraction") or {}).get(
                        "candidate_count", 0
                    )
                ),
                fact_extraction_status=(
                    ((item.metadata_json or {}).get("fact_extraction") or {}).get(
                        "status", "not_started"
                    )
                ),
                fact_extraction_method=(
                    ((item.metadata_json or {}).get("fact_extraction") or {}).get(
                        "method", ""
                    )
                ),
                fact_extraction_warning=(
                    ((item.metadata_json or {}).get("fact_extraction") or {}).get(
                        "warning", ""
                    )
                ),
            )
            for item in records
        ]
    )


@router.post("/documents/upload", response_model=PatientDocumentUploadResponse)
async def upload_patient_document(
    file: UploadFile = File(...),
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    require_patient_domains_enabled()
    filename = safe_upload_filename(file.filename or "")
    document_id = f"doc-{uuid4()}"
    path = patient_document_path(DATA_DIR / "documents", scope, document_id, filename)
    file_type = Path(filename).suffix.lstrip(".").upper()

    record = DocumentRecord(
        id=document_id,
        document_domain="patient_private",
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
        owner_user_id=scope.user_id,
        filename=filename,
        file_type=file_type,
        storage_uri=str(path),
        status="uploading",
    )
    db.add(record)
    db.commit()

    try:
        record.content_sha256 = await save_patient_upload(file, path)
        record.status = "parsing"
        record.updated_at = datetime.utcnow()
        db.commit()

        loaded = loader.load_document(str(path), filename)
        scoped_docs = scope_document_chunks(
            loaded,
            document_id=document_id,
            scope=scope,
            storage_uri=str(path),
        )
        parent_docs = [item for item in scoped_docs if int(item.get("chunk_level", 0)) in (1, 2)]
        leaf_docs = [item for item in scoped_docs if int(item.get("chunk_level", 0)) == 3]
        if not leaf_docs:
            raise ValueError("No searchable patient record chunks were generated")

        parent_chunk_store.upsert_documents(parent_docs)
        patient_store = get_milvus_store("patient_record")
        MilvusWriter(embedding_service, patient_store).write_documents(leaf_docs)

        record.status = "indexed"
        record.metadata_json = {
            "parent_chunks": len(parent_docs),
            "leaf_chunks": len(leaf_docs),
            "collection": patient_store.collection_name,
        }
        record.updated_at = datetime.utcnow()
        db.commit()
        return PatientDocumentUploadResponse(
            document_id=document_id,
            filename=filename,
            parent_chunks=len(parent_docs),
            leaf_chunks=len(leaf_docs),
            status=record.status,
        )
    except Exception as exc:
        record.status = "failed"
        record.error_message = str(exc)[:2000]
        record.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=f"Patient document processing failed: {exc}") from exc


@router.post(
    "/documents/upload/async",
    response_model=PatientDocumentUploadStartResponse,
)
async def upload_patient_document_async(
    file: UploadFile = File(...),
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    require_patient_domains_enabled()
    filename = safe_upload_filename(file.filename or "")
    document_id = f"doc-{uuid4()}"
    path = patient_document_path(DATA_DIR / "documents", scope, document_id, filename)
    record = DocumentRecord(
        id=document_id,
        document_domain="patient_private",
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
        owner_user_id=scope.user_id,
        filename=filename,
        file_type=Path(filename).suffix.lstrip(".").upper(),
        storage_uri=str(path),
        status="uploading",
    )
    db.add(record)
    db.flush()
    try:
        record.content_sha256 = await save_patient_upload(file, path)
        record.status = "queued"
        record.updated_at = datetime.utcnow()
        progress = {
            "job_id": "",
            "filename": filename,
            "status": "queued",
            "current_step": "parse",
            "message": "Patient document saved; waiting for durable worker",
            "total_chunks": 0,
            "processed_chunks": 0,
            "error": None,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "steps": [
                {"key": "parse", "label": "Parse and chunk", "percent": 0, "status": "pending", "message": ""},
                {"key": "parent_store", "label": "Store parent chunks", "percent": 0, "status": "pending", "message": ""},
                {"key": "vector_store", "label": "Embed and index", "percent": 0, "status": "pending", "message": ""},
            ],
        }
        job, _ = enqueue_job(
            db,
            job_type="patient_document_index",
            queue_name="default",
            payload={"document_id": document_id},
            progress=progress,
            idempotency_key=f"patient-document:{document_id}",
            tenant_id=scope.tenant_id,
            patient_id=scope.patient_id,
            created_by_user_id=scope.user_id,
            max_attempts=3,
        )
        progress["job_id"] = job.id
        job.progress_json = progress
        db.commit()
        return PatientDocumentUploadStartResponse(
            job_id=job.id,
            document_id=document_id,
            filename=filename,
            status="queued",
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Patient file save failed: {exc}") from exc


@router.post("/records/search", response_model=PatientSearchResponse)
async def search_patient_records(
    request: PatientSearchRequest,
    scope: PatientScope = Depends(get_current_patient_scope),
):
    require_patient_domains_enabled()
    result = retrieve_patient_records(request.query, scope, request.top_k)
    evidence = [PatientEvidenceItem(**item) for item in result["docs"]]
    return PatientSearchResponse(mode=result["mode"], evidence=evidence, attempts=result["attempts"])


@router.delete("/documents/{document_id}", response_model=PatientDocumentDeleteResponse)
async def delete_patient_document(
    document_id: str,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    require_patient_domains_enabled()
    record = _scoped_record_query(db, scope).filter(DocumentRecord.id == document_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Patient document not found")

    record.status = "deleting"
    db.commit()
    try:
        store = get_milvus_store("patient_record")
        result = store.delete(f'document_id == "{document_id}"')
        vectors_deleted = result.get("delete_count", 0) if isinstance(result, dict) else 0
        parents_deleted = parent_chunk_store.delete_by_document_id(
            document_id,
            scope.tenant_id,
            scope.patient_id,
        )

        raw_file_deleted = False
        path = Path(record.storage_uri)
        if path.exists() and is_within_private_root(path, PRIVATE_ROOT):
            path.unlink()
            raw_file_deleted = True

        db.delete(record)
        db.commit()
        return PatientDocumentDeleteResponse(
            document_id=document_id,
            vectors_deleted=int(vectors_deleted),
            parent_chunks_deleted=parents_deleted,
            raw_file_deleted=raw_file_deleted,
        )
    except Exception as exc:
        db.rollback()
        record = _scoped_record_query(db, scope).filter(DocumentRecord.id == document_id).first()
        if record is not None:
            record.status = "delete_failed"
            record.error_message = str(exc)[:2000]
            db.commit()
        raise HTTPException(status_code=500, detail="Patient document deletion failed") from exc
