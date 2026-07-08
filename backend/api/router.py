from fastapi import APIRouter

from backend.api.routes import (
    auth,
    care_navigation,
    chat,
    collections,
    documents,
    evaluation,
    health,
    sessions,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(sessions.router)
router.include_router(chat.router)
router.include_router(care_navigation.router)
router.include_router(documents.router)
router.include_router(collections.router)
router.include_router(evaluation.router)
router.include_router(health.router)
