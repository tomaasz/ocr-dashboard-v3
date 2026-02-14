"""
OCR Dashboard V2 - FastAPI Application Entry Point
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, PENDING_CLEANUP_FILE
from .routes import (
    dashboard_router,
    limits_router,
    profiles_router,
    profiles_single_router,
    settings_router,
)
from .services.cleanup import DEFAULT_CLEANUP_TARGETS, cleanup_folders
from .services.pause_scheduler import run_pause_scheduler
from .services.profiles import reset_all_profiles
from .services.update_counts import (
    listen_new_source_paths,
    run_update_counts_if_due,
    watch_new_source_paths,
)

# Track server start time for session filtering
SERVER_START_TIME = time.time()

# Track background tasks to prevent garbage collection
_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    """Track background task and auto-remove when done."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Manage application startup/shutdown lifecycle."""
    print(f"🚀 OCR Dashboard V2 started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _schedule_pending_cleanup()
    _track_task(asyncio.create_task(asyncio.to_thread(run_update_counts_if_due)))
    _track_task(asyncio.create_task(asyncio.to_thread(listen_new_source_paths)))
    _track_task(asyncio.create_task(watch_new_source_paths()))
    _track_task(asyncio.create_task(run_pause_scheduler()))
    yield
    print(f"👋 OCR Dashboard V2 stopping at {time.strftime('%Y-%m-%d %H:%M:%S')}")


# Create FastAPI app
app = FastAPI(
    title="OCR Dashboard V2",
    description="Dashboard for OCR Farm Management",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount artifacts directory for screenshots
artifacts_dir = Path(__file__).parent.parent / "artifacts"
if artifacts_dir.exists():
    app.mount("/artifacts", StaticFiles(directory=artifacts_dir), name="artifacts")

# Include routers
app.include_router(dashboard_router)
app.include_router(limits_router)
app.include_router(profiles_router)
app.include_router(profiles_single_router)  # Singular /api/profile endpoints
app.include_router(settings_router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "server_start_time": SERVER_START_TIME,
        "uptime_sec": int(time.time() - SERVER_START_TIME),
    }


def _schedule_pending_cleanup() -> None:
    """Run deferred cleanup without blocking startup."""
    if not PENDING_CLEANUP_FILE.exists():
        return

    try:
        payload = json.loads(PENDING_CLEANUP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return

    try:
        PENDING_CLEANUP_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    targets = payload.get("targets") or DEFAULT_CLEANUP_TARGETS
    force = bool(payload.get("force", False))
    reset_profiles = bool(payload.get("reset_profiles", False))

    _track_task(asyncio.create_task(asyncio.to_thread(cleanup_folders, BASE_DIR, targets, force)))
    if reset_profiles:
        _track_task(asyncio.create_task(asyncio.to_thread(reset_all_profiles)))
