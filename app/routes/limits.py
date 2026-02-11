"""
OCR Dashboard V2 - Limits Routes
Pro limit check endpoints used by the Limits tab.
"""
# cspell:words precheck timespec

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from .. import config
from ..services import process as process_service
from ..services import profiles as profile_service
from .dashboard import _fetch_profile_db_stats

router = APIRouter(prefix="/api/limits", tags=["limits"])

_LAST_LIMIT_STATUS: dict[str, Any] = {"results": [], "run_at": None}

_SAFE_HOST_ID_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_SAFE_PROFILE_RE = re.compile(r"^[a-zA-Z0-9_\-\.\(\) ]+$")


def _coerce_profiles(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(p) for p in raw if str(p).strip()]
    return []


def _parse_parallel(value: object) -> int | None:
    """Parse parallel value from payload, raising HTTPException on bad input."""
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Nieprawidłowa wartość parallel") from None


def _sanitize_host_id(host_id: object) -> str:
    """Validate and return a sanitized host_id string."""
    safe = str(host_id).strip()
    if not _SAFE_HOST_ID_RE.match(safe):
        raise HTTPException(status_code=400, detail="Nieprawidłowy identyfikator hosta")
    return safe


def _sanitize_profiles(profiles: list[str]) -> list[str]:
    """Validate profile names and return sanitized copies."""
    sanitized: list[str] = []
    for p in profiles:
        if not _SAFE_PROFILE_RE.match(p):
            raise HTTPException(status_code=400, detail=f"Nieprawidłowa nazwa profilu: {p}")
        sanitized.append(str(p))
    return sanitized


def _is_limited(state: dict, critical: dict) -> bool:
    """Determine if a profile is currently limited."""
    pause_reason = (state.get("pause_reason") or "").lower()
    if state.get("is_paused") and "limit" in pause_reason:
        return True
    return (critical.get("event_type") or "").lower() == "pro_limit_reached"


def _build_limit_results(profiles: list[str]) -> list[dict[str, Any]]:
    pg_dsn = config.PG_DSN
    pg_table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
    session_start_map = {name: profile_service.get_profile_session_start(name) for name in profiles}
    stats = _fetch_profile_db_stats(pg_dsn, pg_table, session_start_map) if pg_dsn else {}
    results: list[dict[str, Any]] = []
    now = datetime.now(tz=UTC)

    for name in profiles:
        p_stats = stats.get(name, {})
        state = p_stats.get("state") or {}
        critical = p_stats.get("critical") or {}
        limited = _is_limited(state, critical)

        reset_time = "-"
        pro_remaining = "-"
        pause_until = state.get("pause_until")
        if pause_until and isinstance(pause_until, datetime) and pause_until > now:
            reset_time = pause_until.strftime("%H:%M")
            remaining_min = int((pause_until - now).total_seconds() / 60)
            pro_remaining = f"{remaining_min}m"

        results.append(
            {
                "profile": name,
                "limited": limited,
                "error": False,
                "pro_remaining": pro_remaining,
                "reset_time": reset_time,
            }
        )

    return results


def _store_last_status(results: list[dict[str, Any]]) -> dict[str, Any]:
    run_at = datetime.now(tz=UTC).isoformat(timespec="seconds")
    status_results = []
    last_any: dict[str, dict[str, str]] = {}
    for r in results:
        status = "OK"
        if r.get("limited"):
            reset = r.get("reset_time") or "-"
            status = f"LIMIT until {reset}" if reset != "-" else "LIMIT"
        status_results.append({"profile": r.get("profile", ""), "status": status})
        last_any[r.get("profile", "")] = {"status": status}

    payload = {"results": status_results, "run_at": run_at, "last_any": last_any}
    _LAST_LIMIT_STATUS.update(payload)
    return payload


def _run_remote_check(
    host_id: str, profiles: list[str], quick: bool, parallel_val: int | None
) -> dict[str, Any]:
    """Execute remote limit precheck with already-sanitized inputs."""
    try:
        ok, message, results = process_service.run_remote_limit_precheck(
            host_id, profiles=profiles, quick=quick, parallel=parallel_val
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=500, detail=message)
    return {"status": "completed", "results": {"results": results, "host_id": host_id}}


@router.post("/run", responses={400: {"description": "Validation error"}, 500: {"description": "Check failed"}})
def run_limit_check(payload: Annotated[dict, Body(default_factory=dict)]):
    """Run a quick limit status check based on current DB state."""
    profiles = _coerce_profiles(payload.get("profiles"))
    if not profiles:
        raise HTTPException(status_code=400, detail="Wybierz przynajmniej jeden profil")

    host_id = payload.get("host")
    quick = bool(payload.get("quick", False))
    parallel_val = _parse_parallel(payload.get("parallel"))

    if host_id and str(host_id) not in {"local", "remote"}:
        safe_host = _sanitize_host_id(host_id)
        safe_profiles = _sanitize_profiles(profiles)
        return _run_remote_check(safe_host, safe_profiles, quick, parallel_val)

    results = _build_limit_results(profiles)
    _store_last_status(results)
    return {"status": "completed", "results": {"results": results}}


@router.post("/precheck/start")
def start_limit_precheck(payload: Annotated[dict, Body(default_factory=dict)]):
    """Start limit precheck script that logs into limit_checks table."""
    profiles = _coerce_profiles(payload.get("profiles"))
    quick = bool(payload.get("quick", False))
    parallel_val = _parse_parallel(payload.get("parallel"))

    ok, message = process_service.start_limit_precheck(
        profiles=profiles or None, quick=quick, parallel=parallel_val
    )
    if not ok:
        return {"success": False, "message": message}
    return {"success": True, "message": message}


@router.post("/precheck/stop", responses={400: {"description": "Stop failed"}})
def stop_limit_precheck():
    """Stop running limit precheck process."""
    ok, message = process_service.stop_limit_precheck()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True, "message": message}


@router.get("/precheck/status")
def get_limit_precheck_status():
    """Return last precheck status snapshot (from cache)."""
    cache_dir = Path.home() / ".cache" / "ocr-dashboard-v3"
    status_path = cache_dir / "limit_precheck_status.json"
    if not status_path.exists():
        return {"running": False, "status": None}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return {"running": False, "status": None}
    return {"running": bool(data.get("in_progress")), "status": data}


@router.get("/status")
def get_limit_status(session_start: str | None = None):  # noqa: ARG001
    """Return the latest limit status snapshot."""
    if _LAST_LIMIT_STATUS.get("results"):
        return {
            "running": False,
            "status": {
                "results": _LAST_LIMIT_STATUS.get("results", []),
                "run_at": _LAST_LIMIT_STATUS.get("run_at"),
            },
            "last_any": _LAST_LIMIT_STATUS.get("last_any", {}),
        }

    return {
        "running": False,
        "status": {"results": [], "run_at": None},
        "last_any": {},
    }
