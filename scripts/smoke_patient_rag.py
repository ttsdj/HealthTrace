from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from backend.env import load_env


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _remove_empty_private_directories(path: Path, private_root: Path) -> None:
    current = path.parent
    root = private_root.resolve()
    while current.exists() and current.resolve() != root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a synthetic end-to-end patient RAG isolation smoke test"
    )
    parser.add_argument(
        "--embedding-backend",
        choices=("local", "hash"),
        default="local",
        help="Use the production local embedding model or the deterministic smoke backend",
    )
    args = parser.parse_args()

    load_env()
    if args.embedding_backend == "hash":
        os.environ["EMBEDDING_BACKEND"] = "hash"
    else:
        os.environ.pop("EMBEDDING_BACKEND", None)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.db.models import (
        DocumentRecord,
        PatientFact,
        PatientFactCandidate,
        PatientProfile,
        PatientTimelineEvent,
        Tenant,
        User,
    )
    from backend.indexing.milvus_client import get_milvus_store
    from backend.indexing.parent_chunk_store import ParentChunkStore
    from backend.infra.database import SessionLocal
    from backend.patient.context import build_verified_patient_context

    suffix = uuid4().hex[:12]
    usernames = [f"ht-smoke-a-{suffix}", f"ht-smoke-b-{suffix}"]
    password = f"Synthetic-{suffix}-Only"
    marker = f"HTSMOKE-{suffix.upper()}"
    filename = f"synthetic-patient-record-{suffix}.html"
    document_id = ""
    storage_path: Path | None = None
    tenant_ids: list[str] = []
    patient_ids: list[str] = []
    started_at = time.perf_counter()
    result: dict = {
        "status": "failed",
        "embedding_backend": args.embedding_backend,
        "marker": marker,
    }

    try:
        with TestClient(create_app()) as client:
            auth = []
            for username in usernames:
                response = client.post(
                    "/auth/register",
                    json={"username": username, "password": password},
                )
                response.raise_for_status()
                auth.append(response.json())
                tenant_ids.append(response.json()["tenant_id"])
                patient_ids.append(response.json()["patient_id"])

            html = (
                "<html><body><h1>Synthetic patient record</h1>"
                f"<p>Isolation marker: {marker}. This synthetic record states that the "
                "patient reported a temporary dry cough on 2026-07-22. It contains no real "
                "personally identifiable or clinical information.</p>"
                "<p>2026-07-22</p><p>血压：128/82 mmHg</p></body></html>"
            )
            upload = client.post(
                "/patient/documents/upload",
                headers=_authorization(auth[0]["access_token"]),
                files={"file": (filename, html.encode("utf-8"), "text/html")},
            )
            upload.raise_for_status()
            upload_body = upload.json()
            document_id = upload_body["document_id"]
            storage_path = (
                Path(__file__).resolve().parents[1]
                / "data"
                / "documents"
                / "private"
                / auth[0]["tenant_id"]
                / auth[0]["patient_id"]
                / document_id
                / filename
            )

            own_search = client.post(
                "/patient/records/search",
                headers=_authorization(auth[0]["access_token"]),
                json={"query": marker, "top_k": 5},
            )
            own_search.raise_for_status()
            own_body = own_search.json()
            own_ids = {item["document_id"] for item in own_body["evidence"]}
            if document_id not in own_ids:
                raise RuntimeError(
                    "owner could not retrieve the indexed synthetic record: "
                    + json.dumps(own_body, ensure_ascii=False)
                )

            patient_context, patient_context_meta = build_verified_patient_context(
                usernames[0],
                f"请结合我的病历解释 {marker}",
            )
            if marker not in patient_context:
                raise RuntimeError(
                    "indexed patient evidence was not injected into the chat context: "
                    + json.dumps(patient_context_meta, ensure_ascii=False)
                )

            cross_search = client.post(
                "/patient/records/search",
                headers=_authorization(auth[1]["access_token"]),
                json={"query": marker, "top_k": 5},
            )
            cross_search.raise_for_status()
            cross_body = cross_search.json()
            if any(item["document_id"] == document_id for item in cross_body["evidence"]):
                raise RuntimeError("cross-patient retrieval isolation failed")

            extraction = client.post(
                f"/patient/documents/{document_id}/fact-candidates/extract",
                headers=_authorization(auth[0]["access_token"]),
                json={"use_llm": False, "consent_external_processing": False},
            )
            extraction.raise_for_status()
            extraction_body = extraction.json()
            if extraction_body["candidate_count"] < 1:
                raise RuntimeError("local extraction did not create a fact candidate")
            candidate_id = extraction_body["candidates"][0]["candidate_id"]

            cross_candidates = client.get(
                "/patient/fact-candidates",
                headers=_authorization(auth[1]["access_token"]),
            )
            cross_candidates.raise_for_status()
            if cross_candidates.json()["candidates"]:
                raise RuntimeError("cross-patient fact candidate isolation failed")

            confirmation = client.post(
                f"/patient/fact-candidates/{candidate_id}/confirm",
                headers=_authorization(auth[0]["access_token"]),
                json={},
            )
            confirmation.raise_for_status()
            confirmed_fact_id = confirmation.json()["fact_id"]

            fact_count = len(
                client.get(
                    "/patient/facts",
                    headers=_authorization(auth[0]["access_token"]),
                ).json()["facts"]
            )
            timeline_count = len(
                client.get(
                    "/patient/timeline",
                    headers=_authorization(auth[0]["access_token"]),
                ).json()["events"]
            )
            if fact_count < 1 or timeline_count < 1:
                raise RuntimeError("confirmed candidate did not create fact and timeline")

            deletion = client.delete(
                f"/patient/documents/{document_id}",
                headers=_authorization(auth[0]["access_token"]),
            )
            deletion.raise_for_status()

            result = {
                "status": "passed",
                "embedding_backend": args.embedding_backend,
                "upload_status": upload_body["status"],
                "parent_chunks": upload_body["parent_chunks"],
                "leaf_chunks": upload_body["leaf_chunks"],
                "owner_retrieval_mode": own_body["mode"],
                "owner_hits": len(own_body["evidence"]),
                "chat_context_record_hits": patient_context_meta.get(
                    "patient_record_hits", 0
                ),
                "cross_patient_hits": len(cross_body["evidence"]),
                "candidate_method": extraction_body["extraction_method"],
                "candidate_count": extraction_body["candidate_count"],
                "confirmed_fact_id": confirmed_fact_id,
                "fact_count": fact_count,
                "timeline_count": timeline_count,
                "deleted": deletion.json(),
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
            }
    finally:
        # Clean only artifacts namespaced by this script's generated IDs.
        cleanup_documents: list[tuple[str, str, str, str]] = []
        with SessionLocal() as db:
            records = (
                db.query(DocumentRecord)
                .filter(DocumentRecord.filename == filename)
                .all()
            )
            cleanup_documents = [
                (
                    record.id,
                    record.tenant_id or "",
                    record.patient_id or "",
                    record.storage_uri,
                )
                for record in records
                if record.owner_user_id
                and db.query(User)
                .filter(User.id == record.owner_user_id, User.username.in_(usernames))
                .first()
            ]
        if document_id and not any(item[0] == document_id for item in cleanup_documents):
            cleanup_documents.append(
                (
                    document_id,
                    tenant_ids[0] if tenant_ids else "",
                    patient_ids[0] if patient_ids else "",
                    str(storage_path or ""),
                )
            )

        for cleanup_id, cleanup_tenant, cleanup_patient, cleanup_path in cleanup_documents:
            try:
                get_milvus_store("patient_record").delete(
                    f'document_id == "{cleanup_id}"'
                )
            except Exception:
                pass
            if cleanup_tenant and cleanup_patient:
                ParentChunkStore().delete_by_document_id(
                    cleanup_id,
                    cleanup_tenant,
                    cleanup_patient,
                )
            if cleanup_path:
                Path(cleanup_path).unlink(missing_ok=True)

        with SessionLocal() as db:
            cleanup_ids = [item[0] for item in cleanup_documents]
            if cleanup_ids:
                db.query(PatientFactCandidate).filter(
                    PatientFactCandidate.document_id.in_(cleanup_ids)
                ).delete(synchronize_session=False)
                db.query(DocumentRecord).filter(DocumentRecord.id.in_(cleanup_ids)).delete(
                    synchronize_session=False
                )
            if tenant_ids and patient_ids:
                db.query(PatientTimelineEvent).filter(
                    PatientTimelineEvent.tenant_id.in_(tenant_ids),
                    PatientTimelineEvent.patient_id.in_(patient_ids),
                ).delete(synchronize_session=False)
                db.query(PatientFact).filter(
                    PatientFact.tenant_id.in_(tenant_ids),
                    PatientFact.patient_id.in_(patient_ids),
                ).delete(synchronize_session=False)
            for username in usernames:
                user = db.query(User).filter(User.username == username).first()
                if user is None:
                    continue
                db.query(PatientProfile).filter(PatientProfile.user_id == user.id).delete(
                    synchronize_session=False
                )
                user.tenant_id = None
                db.flush()
                db.delete(user)
            db.flush()
            if tenant_ids:
                db.query(Tenant).filter(Tenant.id.in_(tenant_ids)).delete(
                    synchronize_session=False
                )
            db.commit()

        if storage_path is not None:
            try:
                storage_path.unlink(missing_ok=True)
                _remove_empty_private_directories(
                    storage_path,
                    Path(__file__).resolve().parents[1] / "data" / "documents" / "private",
                )
            except OSError:
                pass

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
