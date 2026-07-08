from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.indexing.milvus_client import COLLECTION_ENV_BY_KIND, get_milvus_store
from backend.infra.auth import require_admin
from backend.db.models import User


class CollectionStatus(BaseModel):
    kind: str
    collection_name: str
    exists: bool
    row_count: int = 0
    error: str = ""


class CollectionStatusResponse(BaseModel):
    collections: list[CollectionStatus]


router = APIRouter(tags=["collections"])


@router.get("/collections/status", response_model=CollectionStatusResponse)
async def collection_status(_: User = Depends(require_admin)):
    statuses: list[CollectionStatus] = []
    for kind in COLLECTION_ENV_BY_KIND:
        store = get_milvus_store(kind)
        row_count = 0
        exists = False
        error = ""
        try:
            with store.session() as client:
                exists = client.has_collection(store.collection_name)
                if exists:
                    stats = client.get_collection_stats(store.collection_name)
                    row_count = int(stats.get("row_count", 0))
        except Exception as exc:
            error = str(exc)
        statuses.append(
            CollectionStatus(
                kind=kind,
                collection_name=store.collection_name,
                exists=exists,
                row_count=row_count,
                error=error,
            )
        )
    return CollectionStatusResponse(collections=statuses)
