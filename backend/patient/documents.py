from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import HTTPException, UploadFile

from backend.patient.scope import PatientScope

PATIENT_DOCUMENT_DOMAIN = "patient_private"
PUBLIC_DOCUMENT_DOMAIN = "public_medical"


def patient_domains_enabled() -> bool:
    return os.getenv("HEALTHTRACE_PATIENT_DOMAIN_ENABLED", "true").lower() == "true"


def require_patient_domains_enabled() -> None:
    if not patient_domains_enabled():
        raise HTTPException(status_code=503, detail="Patient document domain is disabled")


def safe_upload_filename(filename: str) -> str:
    name = Path(filename or "").name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Filename is required")
    return name


# Mirror the types backend.indexing.document_loader can actually parse; a
# self-service patient upload must not become arbitrary-object storage.
_PATIENT_UPLOAD_ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".html",
        ".htm",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
    }
)


def patient_upload_max_bytes() -> int:
    try:
        max_mb = max(1, int(os.getenv("HEALTHTRACE_PATIENT_UPLOAD_MAX_MB", "50")))
    except ValueError:
        max_mb = 50
    return max_mb * 1024 * 1024


def validate_patient_upload(filename: str) -> str:
    name = safe_upload_filename(filename)
    if Path(name).suffix.lower() not in _PATIENT_UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported patient document type")
    return name


def patient_document_path(
    root: Path,
    scope: PatientScope,
    document_id: str,
    filename: str,
) -> Path:
    safe_name = safe_upload_filename(filename)
    return root / "private" / scope.tenant_id / scope.patient_id / document_id / safe_name


async def save_patient_upload(file: UploadFile, path: Path, max_bytes: int | None = None) -> str:
    """Stream the upload to disk under a hard byte cap.

    The partial file is removed when the cap is exceeded so failed uploads do
    not leave storage behind.
    """
    limit = patient_upload_max_bytes() if max_bytes is None else max_bytes
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    try:
        with path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail="Patient document exceeds the upload size limit",
                    )
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return digest.hexdigest()


def scope_document_chunks(
    docs: list[dict],
    *,
    document_id: str,
    scope: PatientScope,
    storage_uri: str,
) -> list[dict]:
    """Namespace chunk IDs and attach the authorization scope to every chunk."""
    id_map: dict[str, str] = {}
    for doc in docs:
        old_id = str(doc.get("chunk_id") or "")
        if old_id and old_id not in id_map:
            suffix = hashlib.sha256(old_id.encode("utf-8")).hexdigest()[:32]
            id_map[old_id] = f"{document_id}:{suffix}"

    scoped: list[dict] = []
    for doc in docs:
        item = dict(doc)
        for key in ("chunk_id", "parent_chunk_id", "root_chunk_id"):
            old_value = str(item.get(key) or "")
            item[key] = id_map.get(old_value, "") if old_value else ""
        item.update(
            {
                "document_id": document_id,
                "document_domain": PATIENT_DOCUMENT_DOMAIN,
                "tenant_id": scope.tenant_id,
                "patient_id": scope.patient_id,
                "owner_user_id": scope.user_id,
                "file_path": storage_uri,
            }
        )
        scoped.append(item)
    return scoped


def is_within_private_root(path: Path, private_root: Path) -> bool:
    try:
        path.resolve().relative_to(private_root.resolve())
        return True
    except ValueError:
        return False
