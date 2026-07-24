import os
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.api.resources import (
    UPLOAD_DIR,
    delete_document_transactionally,
    ensure_upload_dir,
    is_supported_document,
    loader,
    milvus_manager,
    milvus_writer,
    parent_chunk_store,
    save_upload_file,
)
from backend.db.models import BackgroundJob, User
from backend.infra.auth import get_db, require_admin
from backend.jobs import DELETE_STEPS, delete_job_manager, upload_job_manager
from backend.jobs.queue import JobReporter, enqueue_job, job_snapshot
from backend.schemas import (
    DocumentDeleteJobResponse,
    DocumentDeleteResponse,
    DocumentDeleteStartResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUploadJobResponse,
    DocumentUploadResponse,
    DocumentUploadStartResponse,
)

router = APIRouter(tags=["documents"])


def _split_progressive_vector_batches(leaf_docs: list[dict]) -> tuple[list[dict], list[dict]]:
    first_pages = max(0, int(os.getenv("PROGRESSIVE_INDEX_FIRST_PAGES", "20")))
    first_chunks = max(0, int(os.getenv("PROGRESSIVE_INDEX_FIRST_CHUNKS", "200")))
    if not leaf_docs or (first_pages <= 0 and first_chunks <= 0):
        return leaf_docs, []

    first_batch: list[dict] = []
    first_ids: set[str] = set()
    for doc in leaf_docs:
        try:
            page_number = int(doc.get("page_number", 0) or 0)
        except (TypeError, ValueError):
            page_number = 0
        by_page = first_pages > 0 and page_number < first_pages
        by_count = first_chunks > 0 and len(first_batch) < first_chunks
        if by_page or by_count:
            first_batch.append(doc)
            first_ids.add(str(doc.get("chunk_id") or id(doc)))

    if len(first_batch) >= len(leaf_docs):
        return leaf_docs, []

    remaining = [
        doc
        for doc in leaf_docs
        if str(doc.get("chunk_id") or id(doc)) not in first_ids
    ]
    return first_batch, remaining


def _write_vectors_progressively(
    job_id: str,
    leaf_docs: list[dict],
    reporter=upload_job_manager,
) -> None:
    total_leaf = len(leaf_docs)
    first_batch, remaining = _split_progressive_vector_batches(leaf_docs)
    batch_size = max(1, int(os.getenv("MILVUS_INSERT_BATCH_SIZE", "100")))
    processed_offset = 0

    reporter.update_step(
        job_id,
        "vector_store",
        0,
        "running",
        f"Embedding first searchable batch: 0 / {total_leaf}",
        total_chunks=total_leaf,
        processed_chunks=0,
    )

    def _make_progress(label: str, offset: int):
        def _on_progress(processed: int, _batch_total: int) -> None:
            overall = min(offset + processed, total_leaf)
            percent = round(overall * 100 / total_leaf) if total_leaf else 100
            reporter.update_step(
                job_id,
                "vector_store",
                percent,
                "running",
                f"{label}: {overall} / {total_leaf}",
                total_chunks=total_leaf,
                processed_chunks=overall,
            )

        return _on_progress

    if first_batch:
        milvus_writer.write_documents(
            first_batch,
            batch_size=batch_size,
            progress_callback=_make_progress("Embedding first searchable batch", processed_offset),
        )
        processed_offset += len(first_batch)
        first_percent = round(processed_offset * 100 / total_leaf) if total_leaf else 100
        if remaining:
            reporter.update_step(
                job_id,
                "vector_store",
                first_percent,
                "running",
                (
                    f"First {processed_offset} chunks are searchable. "
                    f"Continuing background indexing for {len(remaining)} chunks."
                ),
                total_chunks=total_leaf,
                processed_chunks=processed_offset,
            )

    if remaining:
        milvus_writer.write_documents(
            remaining,
            batch_size=batch_size,
            progress_callback=_make_progress("Embedding remaining chunks", processed_offset),
        )


def _process_upload_job(
    job_id: str,
    file_path: str,
    filename: str,
    reporter=upload_job_manager,
    *,
    raise_on_error: bool = False,
) -> dict:
    failed_step = "cleanup"
    try:
        reporter.complete_step(job_id, "upload", "File saved on server")

        failed_step = "cleanup"
        reporter.update_step(job_id, "cleanup", 10, "running", "Cleaning old document version")
        delete_document_transactionally(filename)
        reporter.complete_step(job_id, "cleanup", "Old version cleaned")

        failed_step = "parse"
        reporter.update_step(
            job_id,
            "parse",
            8,
            "running",
            "Parsing and chunking document",
        )
        new_docs = loader.load_document(file_path, filename)
        if not new_docs:
            raise ValueError("Document processing failed: no content extracted")

        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise ValueError("Document processing failed: no searchable leaf chunks generated")
        reporter.complete_step(
            job_id,
            "parse",
            f"Parse completed: {len(parent_docs)} parent chunks, {len(leaf_docs)} leaf chunks",
        )

        failed_step = "parent_store"
        reporter.update_step(job_id, "parent_store", 20, "running", "Writing parent chunks")
        parent_chunk_store.upsert_documents(parent_docs)
        reporter.complete_step(job_id, "parent_store", f"Parent chunks stored: {len(parent_docs)}")

        failed_step = "vector_store"
        total_leaf = len(leaf_docs)
        _write_vectors_progressively(job_id, leaf_docs, reporter)
        reporter.complete_step(job_id, "vector_store", f"Vector indexing completed: {total_leaf} leaf chunks")
        reporter.complete_job(job_id, f"Uploaded and indexed {filename}")
        return {
            "filename": filename,
            "parent_chunks": len(parent_docs),
            "leaf_chunks": total_leaf,
        }
    except Exception as e:
        reporter.fail_job(job_id, failed_step, str(e))
        if raise_on_error:
            raise
        return {"filename": filename, "error": str(e)}


def _process_delete_job(
    job_id: str,
    filename: str,
    reporter=delete_job_manager,
    *,
    raise_on_error: bool = False,
) -> dict:
    try:
        chunks_deleted = delete_document_transactionally(filename, reporter, job_id)
        reporter.complete_job(job_id, f"Deleted {filename}, vector rows: {chunks_deleted}")
        return {"filename": filename, "chunks_deleted": chunks_deleted}
    except Exception as e:
        job = reporter.get_job(job_id)
        current_step = job.get("current_step", "prepare") if job else "prepare"
        reporter.fail_job(job_id, current_step, str(e))
        if raise_on_error:
            raise
        return {"filename": filename, "error": str(e)}


def process_queued_upload(job_id: str, payload: dict) -> dict:
    return _process_upload_job(
        job_id,
        str(payload["file_path"]),
        str(payload["filename"]),
        JobReporter(job_id),
        raise_on_error=True,
    )


def process_queued_delete(job_id: str, payload: dict) -> dict:
    return _process_delete_job(
        job_id,
        str(payload["filename"]),
        JobReporter(job_id),
        raise_on_error=True,
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(_: User = Depends(require_admin)):
    try:
        milvus_manager.init_collection()
        results = milvus_manager.query(
            output_fields=["filename", "file_type"],
            limit=10000,
        )

        file_stats = {}
        for item in results:
            filename = item.get("filename", "")
            file_type = item.get("file_type", "")
            if filename not in file_stats:
                file_stats[filename] = {
                    "filename": filename,
                    "file_type": file_type,
                    "chunk_count": 0,
                }
            file_stats[filename]["chunk_count"] += 1

        documents = [DocumentInfo(**stats) for stats in file_stats.values()]
        return DocumentListResponse(documents=documents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


@router.post("/documents/upload/async", response_model=DocumentUploadStartResponse)
async def upload_document_async(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if not is_supported_document(filename):
        raise HTTPException(
            status_code=400,
            detail="Only PDF, Word, Excel, HTML, and common image files are supported",
        )

    ensure_upload_dir()
    job = upload_job_manager.create_job(filename)
    file_path = UPLOAD_DIR / filename

    try:
        upload_job_manager.update_step(job["job_id"], "upload", 1, "running", "Saving file")
        await save_upload_file(file, file_path)
        upload_job_manager.complete_step(job["job_id"], "upload", "File uploaded, waiting for background processing")
    except Exception as e:
        upload_job_manager.fail_job(job["job_id"], "upload", f"File save failed: {e}")
        raise HTTPException(status_code=500, detail=f"File save failed: {e}")

    durable_job, _ = enqueue_job(
        db,
        job_id=job["job_id"],
        job_type="document_upload",
        queue_name="default",
        payload={"file_path": str(file_path), "filename": filename},
        progress=job,
        idempotency_key=f"upload:{filename}:{uuid4().hex}",
        created_by_user_id=current_user.id,
        max_attempts=3,
    )
    db.commit()
    return DocumentUploadStartResponse(
        job_id=durable_job.id,
        filename=filename,
        message="File uploaded. Background parsing, chunking, and vector indexing started.",
    )


@router.get("/documents/upload/jobs/{job_id}", response_model=DocumentUploadJobResponse)
async def get_upload_job(
    job_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    job = job_snapshot(row) if row else upload_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found or expired")
    return DocumentUploadJobResponse(**job)


@router.get("/documents/upload/jobs", response_model=list[DocumentUploadJobResponse])
async def list_upload_jobs(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(BackgroundJob)
        .filter(BackgroundJob.job_type == "document_upload")
        .order_by(BackgroundJob.created_at.desc())
        .limit(100)
        .all()
    )
    jobs = [job_snapshot(item) for item in rows]
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return [DocumentUploadJobResponse(**job) for job in jobs]


@router.delete("/documents/delete/async/{filename}", response_model=DocumentDeleteStartResponse)
async def delete_document_async(
    filename: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    job = delete_job_manager.create_job(
        filename,
        steps=DELETE_STEPS,
        current_step="prepare",
        message="Waiting for delete",
        completion_step="parent_store",
    )
    delete_job_manager.update_step(job["job_id"], "prepare", 1, "running", "Delete job submitted")
    durable_job, _ = enqueue_job(
        db,
        job_id=job["job_id"],
        job_type="document_delete",
        queue_name="default",
        payload={"filename": filename},
        progress=job,
        idempotency_key=f"delete:{filename}:{uuid4().hex}",
        created_by_user_id=current_user.id,
        max_attempts=3,
    )
    db.commit()
    return DocumentDeleteStartResponse(
        job_id=durable_job.id,
        filename=filename,
        message=f"Deleting {filename}",
    )


@router.get("/documents/delete/jobs/{job_id}", response_model=DocumentDeleteJobResponse)
async def get_delete_job(
    job_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    job = job_snapshot(row) if row else delete_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Delete job not found or expired")
    return DocumentDeleteJobResponse(**job)


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...), _: User = Depends(require_admin)):
    try:
        filename = file.filename or ""
        if not filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        if not is_supported_document(filename):
            raise HTTPException(
                status_code=400,
                detail="Only PDF, Word, Excel, HTML, and common image files are supported",
            )

        ensure_upload_dir()
        delete_document_transactionally(filename)

        file_path = UPLOAD_DIR / filename
        content = await file.read()
        file_path.write_bytes(content)

        try:
            new_docs = loader.load_document(str(file_path), filename)
        except Exception as doc_err:
            raise HTTPException(status_code=500, detail=f"Document processing failed: {doc_err}")

        if not new_docs:
            raise HTTPException(status_code=500, detail="Document processing failed: no content extracted")

        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise HTTPException(status_code=500, detail="Document processing failed: no searchable leaf chunks generated")

        parent_chunk_store.upsert_documents(parent_docs)
        milvus_writer.write_documents(leaf_docs)

        return DocumentUploadResponse(
            filename=filename,
            chunks_processed=len(leaf_docs),
            message=(
                f"Uploaded and indexed {filename}: {len(leaf_docs)} leaf chunks, "
                f"{len(parent_docs)} parent chunks"
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document upload failed: {str(e)}")


@router.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
async def delete_document(filename: str, _: User = Depends(require_admin)):
    try:
        chunks_deleted = delete_document_transactionally(filename)

        return DocumentDeleteResponse(
            filename=filename,
            chunks_deleted=chunks_deleted,
            message=f"Deleted vector data for {filename}; local file is retained",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document delete failed: {str(e)}")
