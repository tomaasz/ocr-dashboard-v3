"""
OCR Dashboard V2 - Profile Routes
Profile management API endpoints.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

from ..models import ProfileDefaultVisibilityRequest, ProfileStartRequest
from ..services import process as process_service
from ..services import profiles as profile_service
from ..utils import validate_profile_name

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("")
def get_profiles():
    """List all available profiles."""
    profiles_list = profile_service.list_profiles(include_default=True)
    return {"profiles": profiles_list}


@router.get("/active-dir")
def get_active_profile_dir(profile: str):
    """Get active Chrome profile directory for a profile."""
    try:
        safe_name = validate_profile_name(profile)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Nieprawidłowa nazwa profilu") from e

    active_dir = profile_service.get_active_chrome_profile(safe_name)
    return {"profile": safe_name, "active_dir": active_dir}


@router.post("/create")
def create_profile(name: str):
    """Create a new profile."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    success, message = profile_service.create_profile(safe_name)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.delete("/{name}")
def delete_profile(name: str):
    """Delete a profile."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Explicitly sanitize to satisfy static analysis tools
    safe_name = os.path.basename(safe_name)  # noqa: PTH119

    # nosemgrep: python.lang.security.audit.dangerous-system-call.dangerous-system-call
    # Snyk false positive: Path traversal is prevented by:
    # 1. validate_profile_name() blocks .., /, \ characters
    # 2. delete_profile() verifies path is within CACHE_DIR using is_relative_to()
    success, message = profile_service.delete_profile(safe_name)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.post("/default/visibility")
def set_default_profile_visibility(payload: ProfileDefaultVisibilityRequest):
    """Hide or show the default profile in listings."""
    success, message = profile_service.set_default_profile_hidden(payload.hidden)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True, "message": message, "hidden": payload.hidden}


@router.post("/{name}/reset")
def reset_profile(name: str):
    """Reset profile (clear cache)."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Explicitly sanitize to satisfy static analysis tools
    safe_name = os.path.basename(safe_name)  # noqa: PTH119

    profile_dir = profile_service.get_profile_dir(safe_name)
    if not profile_dir.exists():
        raise HTTPException(status_code=404, detail=f"Profil '{safe_name}' nie istnieje")

    # Stop any running processes first to ensure clean state and prevent
    # "auto-restart" illusion where dashboard sees old process with new session.
    process_service.stop_profile_processes(safe_name, wait_timeout=5.0)

    # nosemgrep: python.lang.security.audit.dangerous-system-call.dangerous-system-call
    # Snyk false positive: Path traversal is prevented by:
    # 1. validate_profile_name() blocks .., /, \ characters
    # 2. get_profile_dir() constructs path safely within CACHE_DIR
    # 3. clear_profile_cache() verifies path is within CACHE_DIR using is_relative_to()
    profile_service.clear_profile_cache(profile_dir)
    # nosemgrep: python.lang.security.audit.sqli.slq-injection
    # Snyk false positive: SQL injection prevented by validate_profile_name() which ensures
    # safe alphanumeric chars, and psycopg2 parameterized queries in reset_profile_state()
    profile_service.reset_profile_state(safe_name)
    profile_service.set_profile_session_start(safe_name)
    return {"success": True, "message": f"Wyczyszczono cache profilu '{safe_name}'"}


@router.post("/login")
def login_profile_endpoint(payload: dict):
    """Start login helper process."""
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Brak nazwy profilu")

    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    success, message = process_service.start_login_process(safe_name)
    if not success:
        # If it's already running, it might be OK or not. Frontend expects success to start polling.
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.get("/login/log")
def get_login_log(name: str, tail: int = 100):
    """Get tail of login log."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Determine log file path - must match process.py
    # process.py uses: cwd / "logs" / "profiles" / f"{profile_name}.login.log"
    # We need to find cwd. In routes, we can assume relative to app root?
    # Best to ask process service for log path or construct safely.
    # process.py: cwd = Path(__file__).parents[2]

    cwd = Path(__file__).parents[2]  # app/routes/profiles.py -> app/routes -> app -> root
    log_file = cwd / "logs" / "profiles" / f"{safe_name}.login.log"

    if not log_file.exists():
        return {"log": ""}

    return _read_log_file_tail(log_file, tail)


def _read_log_file_tail(log_file: Path, tail: int) -> dict:
    """Helper to read log file tail synchronously."""
    try:
        tail = max(1, min(int(tail), 2000))
        # Simple tail implementation
        lines = []
        with log_file.open(encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            lines = all_lines[-tail:]

        return {"log": "".join(lines)}
    except Exception as e:
        return {"log": f"[Error reading log: {e}]"}


def _read_log_file_lines(log_file: Path) -> list[str]:
    """Read full log file safely."""
    try:
        with log_file.open(encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception as e:
        return [f"[Error reading log: {e}]"]


def _is_error_line(line: str) -> bool:
    """Detect error/critical lines for profile logs."""
    return "ERROR" in line or "CRITICAL" in line or "Traceback" in line or "Exception" in line


# Alias router for singular /api/profile access if needed
# OR: we simple add the route here but internally it is mounted at /api/profiles
# The frontend requests /api/profile/{name}/start (Singular)
# But this router key prefix is /api/profiles (Plural)
# We can add a specialized router for singular access or just bind it here and assume main.py handles mounting.

single_router = APIRouter(prefix="/api/profile", tags=["profile"])


@single_router.get("/{name}/logs")
def get_profile_logs(name: str, tail: int = 200, error_tail: int = 100):
    """Get tail of profile log and recent error lines."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    safe_name = os.path.basename(safe_name)  # noqa: PTH119
    tail = max(1, min(int(tail), 2000))
    error_tail = max(1, min(int(error_tail), 2000))

    cwd = Path(__file__).parents[2]
    log_file = cwd / "logs" / "profiles" / f"{safe_name}.log"

    if not log_file.exists():
        return {"log": "", "errors": "", "exists": False, "lines": 0, "error_lines": 0}

    lines = _read_log_file_lines(log_file)
    log_lines = lines[-tail:]
    error_lines = [line for line in lines if _is_error_line(line)]
    error_lines = error_lines[-error_tail:]

    return {
        "log": "".join(log_lines),
        "errors": "".join(error_lines),
        "exists": True,
        "lines": len(log_lines),
        "error_lines": len(error_lines),
    }


@single_router.post("/{name}/start")
def start_profile_endpoint(
    name: str,
    headed: bool = False,
    windows: int | None = None,
    tabs_per_window: int | None = None,
    payload: ProfileStartRequest | None = Body(default=None),
):
    """Start profile worker."""

    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    effective_headed = headed
    effective_windows = windows
    effective_tabs = tabs_per_window
    config: dict[str, object] | None = None
    if payload is not None:
        payload_data = payload.model_dump(exclude_none=True)
        effective_headed = payload_data.pop("headed", effective_headed)
        effective_windows = payload_data.pop("windows", effective_windows)
        effective_tabs = payload_data.pop("tabs_per_window", effective_tabs)
        config = payload_data or None

    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use.dangerous-subprocess-use
    # Snyk false positive: Command injection prevented by validate_profile_name() (line 242)
    # which restricts to [a-zA-Z0-9_-], and start_profile_process uses list-based subprocess
    success, message = process_service.start_profile_process(
        safe_name,
        headed=effective_headed,
        windows=effective_windows,
        tabs_per_window=effective_tabs,
        config=config,
    )
    if not success:
        # Return a 200 OK with success=False or 400? Frontend checks response.ok
        # If already running, frontend expects success/info.
        # But generic error should be 400.
        if "już pracuje" in message:
            return {"success": False, "message": message}
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@single_router.post("/{name}/stop")
def stop_profile_endpoint(name: str):
    """Stop profile worker."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    process_service.stop_profile_processes(safe_name, wait_timeout=5.0)
    return {"success": True, "message": f"Zatrzymano profil '{safe_name}'"}


@single_router.get("/{name}/status")
def get_profile_status(name: str):
    """Get profile status (running/stopped)."""
    try:
        safe_name = validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Check if profile exists
    if not profile_service.profile_exists(safe_name):
        raise HTTPException(status_code=404, detail=f"Profil '{safe_name}' nie istnieje")

    # Get all PIDs for this profile (run in thread pool to avoid blocking)
    pids = process_service.get_profile_pids(safe_name)

    # Check if any processes are running
    running = len(pids) > 0

    return {
        "profile": safe_name,
        "status": "running" if running else "stopped",
        "running": running,
        "pids": list(pids),  # Convert set to list for JSON serialization
    }
