import os
import sys
from pathlib import Path

# 支持 `python backend/app.py` 与 `uvicorn backend.app:app` 两种启动方式
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.env import PROJECT_ROOT, load_env

load_env()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import router
from backend.infra.database import init_db
from backend.jobs.worker import start_background_job_worker, stop_background_job_worker
from backend.security.middleware import audit_request_middleware
from backend.observability.telemetry import configure_telemetry
from backend.tasks.scheduler import start_health_task_scheduler, stop_health_task_scheduler

FRONTEND_DIR = PROJECT_ROOT / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="HealthTrace Health Consultation API")

    @app.on_event("startup")
    async def _startup_init_db():
        try:
            init_db()
        except Exception as exc:
            if os.getenv("STRICT_STARTUP", "false").lower() == "true":
                raise
            print(f"Database startup initialization skipped: {exc}")
        start_health_task_scheduler()
        start_background_job_worker()

    @app.on_event("shutdown")
    async def _shutdown_task_scheduler():
        await stop_health_task_scheduler()
        await stop_background_job_worker()

    cors_origins = [
        item.strip()
        for item in os.getenv(
            "CORS_ORIGINS",
            "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:8000,http://localhost:8000",
        ).split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(audit_request_middleware)

    @app.middleware("http")
    async def _no_cache(request, call_next):
        response = await call_next(request)
        path = request.url.path or ""
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.include_router(router)
    configure_telemetry(app)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", 8000)))
