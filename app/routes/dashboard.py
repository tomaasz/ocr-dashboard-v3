"""
OCR Dashboard V2 - Dashboard Routes
HTML page views.
"""

import base64
import json
import os
import shlex
import socket
import subprocess
from urllib.parse import urlparse
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import config
from ..services import process as process_service
from ..services import profiles as profile_service
from ..services.remote_config import get_effective_remote_config
from ..utils.db import execute_query, execute_write

# Constants
MINUTES_THRESHOLD = 10
PROFILE_STATE_MAX_AGE_MIN = 10
PREVIEWS_LIMIT = 20
PROFILE_PARTS_MIN_LEN = 4

HAS_PSYCOPG2 = False
try:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import sql

    HAS_PSYCOPG2 = True
except ImportError:
    pass

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parents[1].parent / "templates")

SESSION_TEMPLATE = "(%s,%s::timestamptz)"


def _format_last_activity(ts: datetime | None) -> str | None:
    if not ts:
        return None
    # Format as two lines: date on top, time on bottom
    return f"{ts.strftime('%Y-%m-%d')} {ts.strftime('%H:%M:%S')}"


def _minutes_since(ts: datetime | None) -> int | None:
    if not ts:
        return None
    now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now(None)  # noqa: DTZ005
    return int(max(0, (now - ts).total_seconds()) // 60)


def _is_recent(ts: datetime | None, max_age_minutes: int) -> bool:
    if not ts:
        return False
    now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now(None)  # noqa: DTZ005
    age_sec = (now - ts).total_seconds()
    return age_sec <= (max_age_minutes * 60)


def _table_identifier(table_str: str):
    if not HAS_PSYCOPG2:
        return None
    if "." in table_str:
        schema, table = table_str.split(".", 1)
        return sql.SQL(".").join([sql.Identifier(schema), sql.Identifier(table)])
    return sql.Identifier(table_str)


def _load_proxies_map() -> dict[str, dict[str, str]]:
    """Load per-profile proxy config if present."""
    path = config.PROXIES_CONFIG_FILE
    env_server = os.environ.get("OCR_PROXY_SERVER", "").strip()
    env_user = os.environ.get("OCR_PROXY_USERNAME", "").strip()
    env_password = os.environ.get("OCR_PROXY_PASSWORD", "").strip()
    env_proxy: dict[str, str] | None = None
    if env_server:
        env_proxy = {"server": env_server}
        if env_user:
            env_proxy["username"] = env_user
        if env_password:
            env_proxy["password"] = env_password

    proxies: dict[str, dict[str, str]] = {}
    if not path.exists():
        if env_proxy:
            return {"default": env_proxy}
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        if env_proxy:
            return {"default": env_proxy}
        return {}
    proxies = data.get("proxies") if isinstance(data, dict) else None
    if isinstance(proxies, dict):
        if env_proxy:
            proxies["default"] = env_proxy
        return proxies
    if env_proxy:
        return {"default": env_proxy}
    return {}


def _load_profile_aliases() -> dict[str, str]:
    """Load profile name aliases for UI display."""
    path = config.PROFILE_ALIASES_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if k and v}
    return {}


def _proxy_display(server: str | None) -> tuple[str | None, str | None]:
    """Return safe display string and hostname from proxy server config."""
    if not server:
        return None, None
    raw = str(server).strip()
    if not raw:
        return None, None

    host = None
    port = None
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname
        port = parsed.port
    else:
        cleaned = raw
        if "@" in cleaned:
            cleaned = cleaned.split("@", 1)[1]
        cleaned = cleaned.lstrip("/")
        if "/" in cleaned:
            cleaned = cleaned.split("/", 1)[0]
        if ":" in cleaned:
            host, port_str = cleaned.rsplit(":", 1)
            port = port_str if port_str.isdigit() else None
        else:
            host = cleaned

    host = host.strip() if host else None
    display = None
    if host:
        display = f"{host}:{port}" if port else host
    return display, host


def _update_processed_stats(cur, table_id, session_rows, stats):
    """Fetch processed counts and last activity."""
    try:
        today_start = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        if session_rows:
            # Two separate queries:
            # 1. processed_today - ALL scans from today (no session_start filter)
            # 2. processed_total - scans since session_start

            # Query 1: Today's scans (no session filter)
            query_today = sql.SQL(
                """
                SELECT t.browser_profile,
                       COUNT(*) FILTER (
                           WHERE COALESCE(t.end_ts, t.created_at) >= %s
                             AND COALESCE(t.end_ts, t.created_at) < %s
                       ) AS processed_today
                FROM {} AS t
                WHERE t.browser_profile IS NOT NULL AND t.browser_profile <> ''
                GROUP BY t.browser_profile
                """
            ).format(table_id)
            cur.execute(query_today, (today_start, today_end))

            for profile, today in cur.fetchall():
                entry = stats.setdefault(profile, {})
                entry["processed_today"] = int(today or 0)
                # Backward compatibility now maps to session totals (since Full Reset)
                # so table columns show active-session data.
                entry["processed"] = entry.get("processed_total", 0)

            # Query 2: Total scans since session_start
            query_total = sql.SQL(
                """
                WITH sess(profile_name, session_start) AS (VALUES %s)
                SELECT t.browser_profile,
                       COUNT(*) AS processed_total,
                       MAX(COALESCE(t.end_ts, t.created_at)) AS last_activity
                FROM {} AS t
                JOIN sess s ON s.profile_name = t.browser_profile
                WHERE t.browser_profile IS NOT NULL AND t.browser_profile <> ''
                  AND (
                      s.session_start IS NULL
                      OR COALESCE(t.end_ts, t.created_at) >= s.session_start
                  )
                GROUP BY t.browser_profile
                """
            ).format(table_id)
            psycopg2.extras.execute_values(
                cur, query_total.as_string(cur), session_rows, template=SESSION_TEMPLATE
            )

            for profile, total, last_activity in cur.fetchall():
                entry = stats.setdefault(profile, {})
                entry["processed_total"] = int(total or 0)
                entry["processed"] = int(total or 0)  # Backward compatibility
                entry["last_activity"] = last_activity
        else:
            cur.execute(
                sql.SQL(
                    """
                    SELECT browser_profile,
                           COUNT(*) AS processed_total,
                           COUNT(*) FILTER (
                               WHERE COALESCE(end_ts, created_at) >= %s
                                 AND COALESCE(end_ts, created_at) < %s
                           ) AS processed_today,
                           MAX(COALESCE(end_ts, created_at)) AS last_activity
                    FROM {}
                    WHERE browser_profile IS NOT NULL AND browser_profile <> ''
                    GROUP BY browser_profile
                    """
                ).format(table_id),
                (today_start, today_end),
            )

            for profile, total, today, last_activity in cur.fetchall():
                entry = stats.setdefault(profile, {})
                entry["processed_total"] = int(total or 0)
                entry["processed_today"] = int(today or 0)
                entry["processed"] = int(total or 0)  # Backward compatibility
                entry["last_activity"] = last_activity
    except Exception as e:
        print(f"DEBUG: _update_processed_stats failed: {e}", flush=True)
        import traceback

        traceback.print_exc()
        pass


def _update_token_stats(cur, session_rows, stats):
    """Fetch token usage statistics."""
    try:
        table_id = _table_identifier("public.ocr_token_usage")
        if session_rows:
            query = sql.SQL(
                """
                WITH sess(profile_name, session_start) AS (VALUES %s)
                SELECT t.browser_profile,
                       COALESCE(SUM(t.tok_total), 0) AS tok_total,
                       COALESCE(SUM(t.tok_total) FILTER (WHERE t.created_at >= CURRENT_DATE), 0) AS tok_today,
                       MAX(t.created_at) AS last_activity
                FROM {} AS t
                JOIN sess s ON s.profile_name = t.browser_profile
                WHERE t.browser_profile IS NOT NULL AND t.browser_profile <> ''
                  AND (s.session_start IS NULL OR t.created_at >= s.session_start)
                GROUP BY t.browser_profile
                """
            ).format(table_id)
            psycopg2.extras.execute_values(
                cur, query.as_string(cur), session_rows, template=SESSION_TEMPLATE
            )
        else:
            cur.execute(
                sql.SQL(
                    """
                    SELECT browser_profile,
                           COALESCE(SUM(tok_total), 0) AS tok_total,
                           COALESCE(SUM(tok_total) FILTER (WHERE created_at >= CURRENT_DATE), 0) AS tok_today,
                           MAX(created_at) AS last_activity
                    FROM {}
                    WHERE browser_profile IS NOT NULL AND browser_profile <> ''
                    GROUP BY browser_profile
                    """
                ).format(table_id)
            )

        for profile, tok_total, tok_today, last_activity in cur.fetchall():
            entry = stats.setdefault(profile, {})
            entry["tokens_total"] = int(tok_total or 0)
            entry["tokens_today"] = int(tok_today or 0)
            entry["tokens"] = int(tok_total or 0)  # Backward compatibility
            if last_activity and (
                not entry.get("last_activity") or last_activity > entry.get("last_activity")
            ):
                entry["last_activity"] = last_activity
    except Exception:
        pass


def _update_error_stats(cur, session_rows, stats):
    """Fetch error count statistics."""
    try:
        table_id = _table_identifier("public.error_traces")
        if session_rows:
            query = sql.SQL(
                """
                WITH sess(profile_name, session_start) AS (VALUES %s)
                SELECT t.browser_profile,
                       COUNT(*) AS errors_total,
                       COUNT(*) FILTER (WHERE t.created_at >= CURRENT_DATE) AS errors_today,
                       MAX(t.created_at) AS last_activity
                FROM {} AS t
                JOIN sess s ON s.profile_name = t.browser_profile
                WHERE t.browser_profile IS NOT NULL AND t.browser_profile <> ''
                  AND (s.session_start IS NULL OR t.created_at >= s.session_start)
                GROUP BY t.browser_profile
                """
            ).format(table_id)
            psycopg2.extras.execute_values(
                cur, query.as_string(cur), session_rows, template=SESSION_TEMPLATE
            )
        else:
            cur.execute(
                sql.SQL(
                    """
                    SELECT browser_profile,
                           COUNT(*) AS errors_total,
                           COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) AS errors_today,
                           MAX(created_at) AS last_activity
                    FROM {}
                    WHERE browser_profile IS NOT NULL AND browser_profile <> ''
                    GROUP BY browser_profile
                    """
                ).format(table_id)
            )

        for profile, errors_total, errors_today, last_activity in cur.fetchall():
            entry = stats.setdefault(profile, {})
            entry["errors_total"] = int(errors_total or 0)
            entry["errors_today"] = int(errors_today or 0)
            entry["errors"] = int(errors_total or 0)  # Backward compatibility
            if last_activity and (
                not entry.get("last_activity") or last_activity > entry.get("last_activity")
            ):
                entry["last_activity"] = last_activity
    except Exception:
        pass


def _update_system_activity(cur, session_rows, stats):
    """Fetch last activity from system log."""
    try:
        table_id = _table_identifier("public.system_activity_log")
        if session_rows:
            query = sql.SQL(
                """
                WITH sess(profile_name, session_start) AS (VALUES %s)
                SELECT t.profile_name, MAX(t.event_timestamp) AS last_activity
                FROM {} AS t
                JOIN sess s ON s.profile_name = t.profile_name
                WHERE t.profile_name IS NOT NULL AND t.profile_name <> ''
                  AND (s.session_start IS NULL OR t.event_timestamp >= s.session_start)
                GROUP BY t.profile_name
                """
            ).format(table_id)
            psycopg2.extras.execute_values(
                cur, query.as_string(cur), session_rows, template=SESSION_TEMPLATE
            )
        else:
            cur.execute(
                sql.SQL(
                    """
                    SELECT profile_name, MAX(event_timestamp) AS last_activity
                    FROM {}
                    WHERE profile_name IS NOT NULL AND profile_name <> ''
                    GROUP BY profile_name
                    """
                ).format(table_id)
            )

        for profile, last_activity in cur.fetchall():
            entry = stats.setdefault(profile, {})
            if last_activity and (
                not entry.get("last_activity") or last_activity > entry.get("last_activity")
            ):
                entry["last_activity"] = last_activity
    except Exception:
        pass


def _update_runtime_state(cur, session_rows, stats):
    """Fetch runtime state (paused, current action)."""
    try:
        table_id = _table_identifier("public.profile_runtime_state")
        if session_rows:
            query = sql.SQL(
                """
                WITH sess(profile_name, session_start) AS (VALUES %s)
                SELECT t.profile_name, t.is_paused, t.pause_until, t.pause_reason,
                       t.last_updated, t.current_action
                FROM {} AS t
                JOIN sess s ON s.profile_name = t.profile_name
                WHERE s.session_start IS NULL OR t.last_updated >= s.session_start
                """
            ).format(table_id)
            psycopg2.extras.execute_values(
                cur, query.as_string(cur), session_rows, template=SESSION_TEMPLATE
            )
        else:
            cur.execute(
                sql.SQL(
                    """
                    SELECT profile_name, is_paused, pause_until, pause_reason, last_updated, current_action
                    FROM {}
                    """
                ).format(table_id)
            )

        for row in cur.fetchall():
            (
                profile,
                is_paused,
                pause_until,
                pause_reason,
                last_updated,
                current_action,
            ) = row
            entry = stats.setdefault(profile, {})
            entry["state"] = {
                "is_paused": bool(is_paused),
                "pause_until": pause_until,
                "pause_reason": pause_reason,
                "last_updated": last_updated,
                "current_action": current_action,
            }
    except Exception:
        pass


def _update_limit_checks(cur, stats):
    """Fetch latest limit check info (reset time, status)."""
    try:
        cur.execute(
            """
            SELECT profile_name, is_limited, reset_time, status, checked_at,
                   pause_until, error_message, error_stage
            FROM v_latest_limit_checks
            """
        )
    except Exception:
        try:
            cur.execute(
                """
                SELECT DISTINCT ON (profile_name)
                    profile_name, is_limited, reset_time, status, checked_at,
                    pause_until, error_message, error_stage
                FROM limit_checks
                ORDER BY profile_name, checked_at DESC
                """
            )
        except Exception:
            return

    for row in cur.fetchall():
        (
            profile,
            is_limited,
            reset_time,
            status,
            checked_at,
            pause_until,
            error_message,
            error_stage,
        ) = row
        entry = stats.setdefault(profile, {})
        entry["limit"] = {
            "is_limited": bool(is_limited) if is_limited is not None else None,
            "reset_time": reset_time,
            "status": status,
            "checked_at": checked_at,
            "pause_until": pause_until,
            "error_message": error_message,
            "error_stage": error_stage,
        }


def _update_critical_events(cur, session_rows, stats):
    """Fetch unresolved critical events."""
    try:
        table_id = _table_identifier("public.critical_events")
        if session_rows:
            query = sql.SQL(
                """
                WITH sess(profile_name, session_start) AS (VALUES %s)
                SELECT t.profile_name, t.event_type, t.message, t.requires_action, t.created_at
                FROM {} AS t
                JOIN sess s ON s.profile_name = t.profile_name
                WHERE t.resolved_at IS NULL
                  AND (s.session_start IS NULL OR t.created_at >= s.session_start)
                ORDER BY t.created_at DESC
                """
            ).format(table_id)
            psycopg2.extras.execute_values(
                cur, query.as_string(cur), session_rows, template=SESSION_TEMPLATE
            )
        else:
            cur.execute(
                sql.SQL(
                    """
                    SELECT profile_name, event_type, message, requires_action, created_at
                    FROM {}
                    WHERE resolved_at IS NULL
                    ORDER BY created_at DESC
                    """
                ).format(table_id)
            )

        for (
            profile,
            event_type,
            message,
            requires_action,
            created_at,
        ) in cur.fetchall():
            entry = stats.setdefault(profile, {})
            if "critical" not in entry:
                entry["critical"] = {
                    "event_type": event_type,
                    "message": message,
                    "requires_action": bool(requires_action),
                    "created_at": created_at,
                }
    except Exception:
        pass


def _fetch_profile_db_stats(
    pg_dsn: str,
    pg_table: str,
    session_start_map: dict[str, datetime | None] | None = None,
) -> dict[str, dict[str, Any]]:
    if not HAS_PSYCOPG2 or not pg_dsn:
        return {}

    stats: dict[str, dict[str, Any]] = {}
    session_rows: list[tuple[str, datetime | None]] = []
    if session_start_map:
        session_rows = [(name, session_start_map.get(name)) for name in session_start_map]

    conn = None
    try:
        # Debug: Log start of DB fetch
        # print("DEBUG: fetching DB stats...", flush=True)
        conn = psycopg2.connect(pg_dsn, connect_timeout=3)
        with conn.cursor() as cur:
            table_id = _table_identifier(pg_table)

            _update_processed_stats(cur, table_id, session_rows, stats)
            _update_token_stats(cur, session_rows, stats)
            _update_error_stats(cur, session_rows, stats)
            _update_system_activity(cur, session_rows, stats)
            _update_runtime_state(cur, session_rows, stats)
            _update_critical_events(cur, session_rows, stats)
            _update_limit_checks(cur, stats)
    except Exception:
        return {}
    finally:
        if conn:
            with suppress(Exception):
                conn.close()

    return stats


def _critical_status_label(event_type: str | None) -> str:
    mapping = {
        "login_required": "SESJA WYGASŁA",
        "verification_required": "WERYFIKACJA",
        "sms_verification_required": "SMS WERYFIKACJA",
        "oauth_app_verification": "OAUTH WERYFIKACJA",
        "browser_unsupported": "BROWSER",
        "captcha_detected": "CAPTCHA",
        "account_redirect": "PRZEKIEROWANIE",
        "ui_change_detected": "UI ZMIANA",
        "pro_limit_reached": "LIMIT",
        "location_prompt": "LOKALIZACJA",
    }
    key = (event_type or "").strip().lower()
    return mapping.get(key, "PROBLEM")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    """Legacy dashboard (redirect to V2)."""
    return dashboard_v2(request)


@router.get("/dashboard2", response_class=HTMLResponse)
def dashboard2(request: Request):
    """Dashboard 2 view."""
    # This might also need context if it uses the same base, but for now focusing on main v2
    return templates.TemplateResponse("dashboard2.html", {"request": request})


@router.get("/v2", response_class=HTMLResponse)
def dashboard_v2(request: Request):
    """Dashboard V2 - main view."""
    profiles = profile_service.list_profiles(include_default=True)
    pg_dsn = config.PG_DSN or os.environ.get("OCR_PG_DSN")
    pg_table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
    proxies_map = _load_proxies_map()
    session_start_map = {
        p_name: profile_service.get_profile_session_start(p_name) for p_name in profiles
    }
    db_stats = _fetch_profile_db_stats(pg_dsn, pg_table, session_start_map) if pg_dsn else {}

    profiles_data = []
    for p_name in profiles:
        progress = profile_service.get_profile_worker_progress(p_name)
        last_error = profile_service.get_profile_last_error(p_name)
        prompt_blocked = bool(
            last_error
            and (
                "Prompt not sent" in last_error
                or "click failed" in last_error
                or "send failed" in last_error
            )
        )
        p_stats = db_stats.get(p_name, {})
        processed = p_stats.get("processed", 0)
        tokens_k = int(p_stats.get("tokens", 0) // 1000)
        errors = p_stats.get("errors", 0)
        last_activity_ts = p_stats.get("last_activity")
        last_activity = _format_last_activity(last_activity_ts)
        status = "idle"
        status_label = "IDLE"

        state = p_stats.get("state") or {}
        critical = p_stats.get("critical") or {}
        limit_info = p_stats.get("limit") or {}
        proxy_entry = proxies_map.get(p_name) or proxies_map.get("default")
        proxy_server = None
        if isinstance(proxy_entry, dict):
            proxy_server = proxy_entry.get("server")
        elif isinstance(proxy_entry, str):
            proxy_server = proxy_entry
        proxy_display, proxy_host = _proxy_display(proxy_server)
        limit_reset_ts = limit_info.get("reset_time") or limit_info.get("pause_until")
        limit_reset = _format_last_activity(limit_reset_ts) if limit_reset_ts else None
        limit_checked = limit_info.get("checked_at")
        limit_checked_at = _format_last_activity(limit_checked) if limit_checked else None
        critical_message = critical.get("message")
        critical_requires_action = bool(critical.get("requires_action")) if critical else False
        critical_event_type = critical.get("event_type")
        if state.get("is_paused"):
            pause_until = state.get("pause_until")
            pause_reason = (state.get("pause_reason") or "").lower()
            is_limit = "limit" in pause_reason
            status = "limit" if is_limit else "paused"
            if pause_until:
                status_label = (
                    f"{'LIMIT' if is_limit else 'PAUZA'} do {pause_until.strftime('%H:%M')}"
                )
            else:
                status_label = "LIMIT" if is_limit else "PAUZA"
        elif critical:
            status = "error"
            status_label = _critical_status_label(critical.get("event_type"))
        else:
            mins = _minutes_since(last_activity_ts)
            if mins is not None and mins > MINUTES_THRESHOLD:
                status_label = "BEZCZYNNY?"
        profiles_data.append(
            {
                "name": p_name,
                "status": status,  # Default state
                "status_label": status_label,
                "critical_message": critical_message,
                "critical_requires_action": critical_requires_action,
                "critical_event_type": critical_event_type,
                "processed": processed,
                "tokens_k": tokens_k,
                "errors": errors,
                "last_activity": last_activity,
                "headed": False,
                "last_error": last_error,
                "prompt_blocked": prompt_blocked,
                "current_action": state.get("current_action"),
                "proxy": proxy_display,
                "proxy_host": proxy_host,
                "proxy_server": proxy_server,
                "limit_reset": limit_reset,
                "limit_status": limit_info.get("status"),
                "limit_checked_at": limit_checked_at,
                **progress,
            }
        )

    return templates.TemplateResponse(
        "dashboard_v2.html",
        {
            "request": request,
            "profiles": profiles_data,
            "limit_worker_url": config.LIMIT_WORKER_URL or "",
            "remote_hosts": get_effective_remote_config(),
        },
    )


def _get_profile_dashboard_data(
    p_name: str,
    db_stats: dict[str, Any],
    proxies_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Build dashboard data for a single profile."""
    try:
        # Check if profile has running processes
        pids = process_service.get_profile_pids(p_name)
        running_pids = [pid for pid in pids if process_service.pid_is_running(pid)]
        remote_info = process_service.is_profile_running_remote(p_name)

        # Determine status based on running processes
        status = "idle"
        status_label = "IDLE"

        if running_pids:
            status = "active"
            status_label = "ACTIVE"
        elif remote_info:
            status = "active"
            host_id = remote_info.get("host_id", "Remote")
            status_label = f"REMOTE ({host_id})"
        elif process_service.is_start_recent(p_name):
            status = "starting"
            status_label = "STARTING..."

        progress = profile_service.get_profile_worker_progress(p_name)
        last_error = profile_service.get_profile_last_error(p_name)
        prompt_blocked = bool(
            last_error
            and (
                "Prompt not sent" in last_error
                or "click failed" in last_error
                or "send failed" in last_error
            )
        )
        headed = any(process_service.pid_is_headed(pid) for pid in running_pids)
        p_stats = db_stats.get(p_name, {})

        # Safe getters with defaults
        processed_today = p_stats.get("processed_today", 0)
        processed_total = p_stats.get("processed_total", 0)
        tokens_today_k = int(p_stats.get("tokens_today", 0) // 1000)
        tokens_total_k = int(p_stats.get("tokens_total", 0) // 1000)
        errors_today = p_stats.get("errors_today", 0)
        errors_total_val = p_stats.get("errors_total", 0)

        last_activity_ts = p_stats.get("last_activity")
        last_activity = _format_last_activity(last_activity_ts)
        state = p_stats.get("state") or {}
        critical = p_stats.get("critical") or {}
        limit_info = p_stats.get("limit") or {}
        proxy_entry = proxies_map.get(p_name) or proxies_map.get("default")
        proxy_server = None
        if isinstance(proxy_entry, dict):
            proxy_server = proxy_entry.get("server")
        elif isinstance(proxy_entry, str):
            proxy_server = proxy_entry
        proxy_display, proxy_host = _proxy_display(proxy_server)
        limit_reset_ts = limit_info.get("reset_time") or limit_info.get("pause_until")
        limit_reset = (
            _format_last_activity(limit_reset_ts) if limit_reset_ts else None
        )
        limit_checked = limit_info.get("checked_at")
        limit_checked_at = (
            _format_last_activity(limit_checked) if limit_checked else None
        )

        # Extract critical event information
        critical_message = critical.get("message") if critical else None
        critical_requires_action = (
            bool(critical.get("requires_action")) if critical else False
        )
        critical_event_type = critical.get("event_type") if critical else None

        if state.get("is_paused"):
            pause_until = state.get("pause_until")
            pause_reason = (state.get("pause_reason") or "").lower()
            is_limit = "limit" in pause_reason
            status = "limit" if is_limit else "paused"
            if pause_until:
                status_label = f"{'LIMIT' if is_limit else 'PAUZA'} do {pause_until.strftime('%H:%M')}"
            else:
                status_label = "LIMIT" if is_limit else "PAUZA"
        elif critical and (
            critical_requires_action or status not in {"active", "starting"}
        ):
            status = "error"
            # Assuming _critical_status_label is available in scope
            status_label = _critical_status_label(critical.get("event_type"))
        else:
            mins = _minutes_since(last_activity_ts)
            if mins is not None and mins > MINUTES_THRESHOLD and status == "active":
                status_label = "BEZCZYNNY?"

        return {
            "name": p_name,
            "status": status,
            "status_label": status_label,
            "critical_message": critical_message,
            "critical_requires_action": critical_requires_action,
            "critical_event_type": critical_event_type,
            "processed": processed_total,
            "processed_today": processed_today,
            "processed_total": processed_total,
            "tokens_k": tokens_total_k,
            "tokens_today_k": tokens_today_k,
            "tokens_total_k": tokens_total_k,
            "errors": errors_total_val,
            "errors_today": errors_today,
            "errors_total": errors_total_val,
            "last_activity": last_activity,
            "headed": headed,
            "last_error": last_error,
            "prompt_blocked": prompt_blocked,
            "current_action": state.get("current_action"),
            "proxy": proxy_display,
            "proxy_host": proxy_host,
            "proxy_server": proxy_server,
            "limit_reset": limit_reset,
            "limit_status": limit_info.get("status"),
            "limit_checked_at": limit_checked_at,
            **progress,
        }
    except Exception as e:
        print(f"Error processing profile {p_name} in dashboard stats: {e}")
        return {
            "name": p_name,
            "status": "error",
            "status_label": "ERROR",
            "critical_message": str(e),
            "processed": 0,
            "tokens_k": 0,
            "errors": 0,
            "processed_today": 0,
            "processed_total": 0,
            "tokens_today_k": 0,
            "tokens_total_k": 0,
            "errors_today": 0,
            "errors_total": 0,
            "proxy": None,
            "limit_reset": None,
            "limit_status": None,
            "current_action": None,
        }


@router.get("/api/stats/v2")
def get_stats_v2():
    """Get dashboard statistics and profiles data."""
    # print("DEBUG: Start get_stats_v2", flush=True)

    profiles = profile_service.list_profiles(include_default=True)
    # print(f"DEBUG: Profiles list: {len(profiles)}", flush=True)

    pg_dsn = config.PG_DSN or os.environ.get("OCR_PG_DSN")
    pg_table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
    proxies_map = _load_proxies_map()

    # print("DEBUG: Getting session starts", flush=True)
    session_start_map = {
        p_name: profile_service.get_profile_session_start(p_name)
        for p_name in profiles
    }

    # print("DEBUG: Fetching DB stats", flush=True)
    db_stats = (
        _fetch_profile_db_stats(pg_dsn, pg_table, session_start_map) if pg_dsn else {}
    )
    # print("DEBUG: DB stats fetched", flush=True)

    profiles_data = []
    active_count = 0

    for p_name in profiles:
        data = _get_profile_dashboard_data(p_name, db_stats, proxies_map)
        if data["status"] == "active":
            active_count += 1
        profiles_data.append(data)

    # Statistics - enhanced with real active worker count
    # Use actual database values, not fallback calculations
    today_scans = sum(int(p.get("processed_today") or 0) for p in profiles_data)
    since_reset_scans = sum(int(p.get("processed_total") or 0) for p in profiles_data)
    errors_total = sum(int(p.get("errors_total") or 0) for p in profiles_data)

    stats = {
        "today_scans": today_scans,  # Actual count from database for today
        "since_reset_scans": since_reset_scans,  # Total scans since last Full Reset
        "total_processed": since_reset_scans,  # Backward compatibility
        "active_workers": active_count,
        "errors": errors_total,
    }

    return {
        "stats": stats,
        "profiles": profiles_data,
    }


@router.get("/api/live-preview")
def get_live_preview():
    """Get live preview screenshots from active workers."""
    screenshots_dir = Path("artifacts/screenshots/ui_health")

    if not screenshots_dir.exists():
        return {"previews": []}

    session_cutoffs: dict[str, float | None] = {}

    def _get_session_cutoff_ts(profile_name: str) -> float | None:
        if profile_name in session_cutoffs:
            return session_cutoffs[profile_name]
        session_start = profile_service.get_profile_session_start(profile_name)
        if session_start is None:
            session_cutoffs[profile_name] = None
            return None
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=UTC)
        cutoff_ts = session_start.timestamp()
        session_cutoffs[profile_name] = cutoff_ts
        return cutoff_ts

    # Get all screenshot files, sorted by modification time (newest first)
    screenshot_files = sorted(
        screenshots_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True
    )

    # Group screenshots by profile name
    # Filename format: ui_health_TIMESTAMP_PROFILE_NAME.png or similar
    previews = []
    seen_profiles = set()

    for screenshot_file in screenshot_files:
        # Extract profile name from filename
        # Expected format: ui_health_20250128_141530_profile_name.png
        filename = screenshot_file.stem  # Remove .png extension
        parts = filename.split("_")

        # Try to extract profile name (usually after timestamp)
        profile_name = "unknown"
        if len(parts) >= PROFILE_PARTS_MIN_LEN:
            # Join remaining parts as profile name
            profile_name = "_".join(parts[3:])

        mtime = screenshot_file.stat().st_mtime
        cutoff_ts = _get_session_cutoff_ts(profile_name)
        if cutoff_ts is not None and mtime < cutoff_ts:
            continue

        # Only include the most recent screenshot per profile
        if profile_name in seen_profiles:
            continue
        seen_profiles.add(profile_name)

        updated_at = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        updated_at_ts = int(mtime)

        # Build preview data
        previews.append(
            {
                "profile": profile_name,
                "status": "active",  # If screenshot exists, worker is/was active
                "image_url": f"/artifacts/screenshots/ui_health/{screenshot_file.name}",
                "updated_at": updated_at,
                "updated_at_ts": updated_at_ts,
            }
        )

        # Limit to 20 most recent previews
        if len(previews) >= PREVIEWS_LIMIT:
            break

    return {"previews": previews}


def _parse_activity_log_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None

    first_space = line.find(" ")
    if first_space == -1:
        return None
    ts_raw = line[:first_space]
    rest = line[first_space + 1 :]

    second_space = rest.find(" ")
    if second_space == -1:
        return None
    event_type = rest[:second_space]
    rest = rest[second_space + 1 :]

    reason = ""
    reason_idx = rest.find(" reason=")
    if reason_idx >= 0:
        kv_part = rest[:reason_idx]
        reason = rest[reason_idx + len(" reason=") :].strip()
    else:
        kv_part = rest

    component = ""
    profile_name = ""
    for token in kv_part.split():
        if token.startswith("component="):
            component = token.split("=", 1)[1]
        elif token.startswith("profile="):
            profile_name = token.split("=", 1)[1]

    time_str = ts_raw
    with suppress(Exception):
        time_str = datetime.fromisoformat(ts_raw).strftime("%Y-%m-%d %H:%M:%S")

    event_type_lc = event_type.lower()
    if "error" in event_type_lc or "fail" in event_type_lc:
        log_level = "ERROR"
    elif "stop" in event_type_lc or "limit" in event_type_lc:
        log_level = "WARNING"
    else:
        log_level = "INFO"

    message_parts: list[str] = []
    if component:
        message_parts.append(f"[{component}]")
    if profile_name:
        message_parts.append(f"profile={profile_name}")
    if event_type:
        message_parts.append(f"event={event_type}")
    if reason:
        message_parts.append(f"reason={reason}")
    message = " | ".join(message_parts).strip()

    return {
        "id": None,
        "time": time_str,
        "level": log_level,
        "message": message,
        "profile": profile_name,
        "event_type": event_type,
    }


def _load_file_logs(profile: str | None, level: str | None, limit: int) -> list[dict]:
    activity_dir = config.CACHE_DIR / "activity_logs"
    if not activity_dir.exists():
        return []

    files = sorted(
        activity_dir.glob("activity_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    logs: list[dict] = []
    profile_filter = profile if profile and profile != "all" else None
    level_filter = level.lower() if level and level != "all" else None

    for log_file in files:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        for line in reversed(lines):
            entry = _parse_activity_log_line(line)
            if not entry:
                continue
            if profile_filter and entry.get("profile") != profile_filter:
                continue
            if level_filter and entry.get("level", "").lower() != level_filter:
                continue
            logs.append(entry)
            if len(logs) >= limit:
                return logs

    return logs


def _build_logs_query(
    profile: str | None, level: str | None, hours: int | None, limit: int
) -> tuple[str, list[Any]]:
    query = """
        SELECT
            id, event_type, component, profile_name, triggered_by, reason,
            is_automatic, event_timestamp, error_message
        FROM system_activity_log
        WHERE 1=1
    """
    params: list[Any] = []

    if profile and profile != "all":
        query += " AND profile_name = %s"
        params.append(profile)

    if level and level != "all":
        if level == "error":
            query += " AND (error_message IS NOT NULL OR event_type LIKE '%error%')"
        elif level == "warning":
            query += " AND (event_type LIKE '%stop%' OR event_type LIKE '%limit%')"
        elif level == "info":
            query += " AND event_type LIKE '%start%'"

    if hours:
        query += " AND event_timestamp >= NOW() - INTERVAL '%s hours'"
        params.append(hours)

    query += " ORDER BY event_timestamp DESC LIMIT %s"
    params.append(limit)
    return query, params


def _get_local_system_stats() -> dict[str, Any]:
    """Get system resource stats for localhost using /proc."""
    stats: dict[str, Any] = {
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "memory_used_gb": 0.0,
        "memory_total_gb": 0.0,
        "available": True,
    }

    try:
        # Read memory info from /proc/meminfo
        mem_total = 0
        mem_available = 0
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) * 1024  # Convert KB to bytes
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) * 1024
                if mem_total and mem_available:
                    break

        if mem_total > 0:
            mem_used = mem_total - mem_available
            stats["memory_total_gb"] = round(mem_total / (1024**3), 2)
            stats["memory_used_gb"] = round(mem_used / (1024**3), 2)
            stats["memory_percent"] = round((mem_used / mem_total) * 100, 1)

        # Read CPU stats - calculate average load
        with open("/proc/loadavg") as f:
            load_avg_1min = float(f.read().split()[0])
        # Approximate CPU percent based on load average (rough estimation)
        # Normalized by number of CPU cores
        cpu_count = os.cpu_count() or 1
        stats["cpu_percent"] = round(min(100, (load_avg_1min / cpu_count) * 100), 1)

    except Exception:
        stats["available"] = False

    return stats


def _run_ssh_command(
    host: str, user: str, ssh_opts: str, command: str, timeout: int = 5
) -> subprocess.CompletedProcess:
    ssh_cmd = ["ssh"]
    if ssh_opts:
        ssh_cmd.extend(shlex.split(ssh_opts))
    ssh_cmd.extend(
        [
            "-o",
            "ConnectTimeout=3",
            "-o",
            "StrictHostKeyChecking=no",
            f"{user}@{host}",
            command,
        ]
    )
    return subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _powershell_encoded_command(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return f"powershell -NoProfile -EncodedCommand {encoded}"


def _parse_ps_output(output: str, limit: int = 5) -> list[dict[str, Any]]:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    if not lines:
        return []
    results: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        try:
            cpu_percent = float(parts[2].replace(",", "."))
        except ValueError:
            cpu_percent = 0.0
        try:
            mem_percent = float(parts[3].replace(",", "."))
        except ValueError:
            mem_percent = 0.0
        results.append(
            {
                "pid": pid,
                "name": name,
                "cpu_percent": round(cpu_percent, 1),
                "memory_percent": round(mem_percent, 1),
            }
        )
        if len(results) >= limit:
            break
    return results


def _get_local_top_processes(limit: int = 5) -> list[dict[str, Any]]:
    try:
        cmd = "LC_ALL=C ps -eo pid,comm,pcpu,pmem --sort=-pcpu | head -n {}".format(limit + 1)
        result = subprocess.run(
            ["sh", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return []
        return _parse_ps_output(result.stdout, limit=limit)
    except Exception:
        return []


def _browser_process_patterns() -> tuple[str, ...]:
    return (
        "chrome",
        "chromium",
        "chrome-headless",
        "chrome-headless-shell",
        "google-chrome",
        "msedge",
        "firefox",
        "WebKitWebProcess",
        "WebKitNetworkProcess",
    )


def _count_process_names(output: str) -> int:
    patterns = _browser_process_patterns()
    count = 0
    for line in output.splitlines():
        name = line.strip()
        if not name:
            continue
        for pattern in patterns:
            if name == pattern:
                count += 1
                break
    return count


def _get_local_chrome_process_count() -> int:
    try:
        cmd = "LC_ALL=C ps -eo comm"
        result = subprocess.run(
            ["sh", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return 0
        return _count_process_names(result.stdout)
    except Exception:
        return 0


def _get_remote_browser_process_count(host: str, user: str, ssh_opts: str) -> int | None:
    if not host or not user:
        return None
    try:
        cmd = "LC_ALL=C ps -eo comm"
        result = _run_ssh_command(host, user, ssh_opts, f"sh -lc {shlex.quote(cmd)}")
        if result.returncode == 0 and result.stdout.strip():
            return _count_process_names(result.stdout)
    except Exception:
        pass

    try:
        ps_script = """
$names = @("chrome","msedge","firefox")
$count = 0
foreach ($n in $names) {
  $procs = Get-Process -Name $n -ErrorAction SilentlyContinue
  if ($procs) { $count += $procs.Count }
}
Write-Output $count
"""
        ps_cmd = _powershell_encoded_command(ps_script.strip())
        result = _run_ssh_command(host, user, ssh_opts, ps_cmd, timeout=8)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    return None


def _kill_local_browser_processes() -> dict[str, int]:
    before = _get_local_chrome_process_count()
    try:
        patterns = _browser_process_patterns()
        kill_cmd = " ".join([f"pkill -x {shlex.quote(p)} 2>/dev/null || true;" for p in patterns])
        subprocess.run(
            ["sh", "-lc", kill_cmd], capture_output=True, text=True, timeout=8, check=False
        )
    except Exception:
        pass
    after = _get_local_chrome_process_count()
    return {"before": before, "after": after, "killed_estimate": max(0, before - after)}


def _kill_remote_browser_processes(host: str, user: str, ssh_opts: str) -> dict[str, int | None]:
    before = _get_remote_browser_process_count(host, user, ssh_opts)
    try:
        patterns = _browser_process_patterns()
        kill_cmd = " ".join([f"pkill -x {shlex.quote(p)} 2>/dev/null || true;" for p in patterns])
        result = _run_ssh_command(
            host, user, ssh_opts, f"sh -lc {shlex.quote(kill_cmd)}", timeout=12
        )
        if result.returncode == 0:
            after = _get_remote_browser_process_count(host, user, ssh_opts)
            killed = None if before is None or after is None else max(0, before - after)
            return {"before": before, "after": after, "killed_estimate": killed}
    except Exception:
        pass

    try:
        ps_script = """
$names = @("chrome","msedge","firefox")
foreach ($n in $names) {
  Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force
}
"""
        ps_cmd = _powershell_encoded_command(ps_script.strip())
        result = _run_ssh_command(host, user, ssh_opts, ps_cmd, timeout=12)
        if result.returncode == 0:
            after = _get_remote_browser_process_count(host, user, ssh_opts)
            killed = None if before is None or after is None else max(0, before - after)
            return {"before": before, "after": after, "killed_estimate": killed}
    except Exception:
        pass

    return {"before": before, "after": before, "killed_estimate": 0}


def _get_remote_top_processes(
    host: str, user: str, ssh_opts: str, limit: int = 5
) -> list[dict[str, Any]]:
    if not host or not user:
        return []
    try:
        cmd = "LC_ALL=C ps -eo pid,comm,pcpu,pmem --sort=-pcpu | head -n {}".format(limit + 1)
        result = _run_ssh_command(host, user, ssh_opts, f"sh -lc {shlex.quote(cmd)}")
        if result.returncode == 0 and result.stdout.strip():
            parsed = _parse_ps_output(result.stdout, limit=limit)
            if parsed:
                return parsed
    except Exception:
        pass

    try:
        ps_script = f"""
$os = Get-CimInstance Win32_OperatingSystem
$uptime = (Get-Date) - $os.LastBootUpTime
$uptimeSeconds = [math]::Max(1, $uptime.TotalSeconds)
$memTotal = [float]$os.TotalVisibleMemorySize * 1024
$procs = Get-Process | Sort-Object CPU -Descending | Select-Object -First {int(limit)} Id,ProcessName,CPU,WorkingSet
foreach ($p in $procs) {{
  $cpuPct = 0
  if ($p.CPU -ne $null) {{ $cpuPct = [math]::Round(100 * ($p.CPU / $uptimeSeconds), 1) }}
  $memPct = 0
  if ($memTotal -gt 0) {{ $memPct = [math]::Round(100 * ($p.WorkingSet / $memTotal), 1) }}
  Write-Output "$($p.Id)|$($p.ProcessName)|$cpuPct|$memPct"
}}
"""
        ps_cmd = _powershell_encoded_command(ps_script.strip())
        result = _run_ssh_command(host, user, ssh_opts, ps_cmd, timeout=8)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        results: list[dict[str, Any]] = []
        for line in lines:
            parts = line.split("|")
            if len(parts) != 4:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            name = parts[1] or "process"
            try:
                cpu_percent = float(parts[2].replace(",", "."))
            except ValueError:
                cpu_percent = 0.0
            try:
                mem_percent = float(parts[3].replace(",", "."))
            except ValueError:
                mem_percent = 0.0
            results.append(
                {
                    "pid": pid,
                    "name": name,
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_percent": round(mem_percent, 1),
                }
            )
        return results
    except Exception:
        return []


def _get_remote_system_stats(host: str, user: str, ssh_opts: str) -> dict[str, Any]:
    """Get system resource stats from remote host via SSH."""
    stats: dict[str, Any] = {
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "memory_used_gb": 0.0,
        "memory_total_gb": 0.0,
        "available": False,
        "error": None,
    }

    if not host or not user:
        stats["error"] = "Missing host or user"
        return stats

    try:
        result = _run_ssh_command(
            host,
            user,
            ssh_opts,
            "cat /proc/meminfo /proc/loadavg 2>/dev/null || echo 'ERROR'",
        )

        if result.returncode == 0 and "ERROR" not in result.stdout:
            lines = result.stdout.strip().split("\n")

            # Parse meminfo
            mem_total = 0
            mem_available = 0
            for line in lines:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) * 1024

            # Parse loadavg (last line)
            if lines and not lines[-1].startswith("Mem"):
                load_avg_1min = float(lines[-1].split()[0])
                # Rough CPU % estimate
                stats["cpu_percent"] = round(min(100, load_avg_1min * 20), 1)

            if mem_total > 0:
                mem_used = mem_total - mem_available
                stats["memory_total_gb"] = round(mem_total / (1024**3), 2)
                stats["memory_used_gb"] = round(mem_used / (1024**3), 2)
                stats["memory_percent"] = round((mem_used / mem_total) * 100, 1)
                stats["available"] = True
            return stats
        stats["error"] = _format_ssh_error(result, fallback="Linux stats unavailable")
    except Exception:
        pass

    try:
        ps_script = """
$os = Get-CimInstance Win32_OperatingSystem
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$memTotal = [float]$os.TotalVisibleMemorySize * 1024
$memFree = [float]$os.FreePhysicalMemory * 1024
Write-Output "CPU=$cpu"
Write-Output "MEM_TOTAL=$memTotal"
Write-Output "MEM_FREE=$memFree"
"""
        ps_cmd = _powershell_encoded_command(ps_script.strip())
        result = _run_ssh_command(host, user, ssh_opts, ps_cmd, timeout=8)
        if result.returncode != 0 or not result.stdout.strip():
            stats["error"] = _format_ssh_error(result, fallback="Windows stats unavailable")
            return stats
        values: dict[str, float] = {}
        for line in result.stdout.strip().splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            try:
                values[key.strip()] = float(value.strip().replace(",", "."))
            except ValueError:
                continue
        mem_total = values.get("MEM_TOTAL", 0.0)
        mem_free = values.get("MEM_FREE", 0.0)
        if mem_total > 0:
            mem_used = mem_total - mem_free
            stats["memory_total_gb"] = round(mem_total / (1024**3), 2)
            stats["memory_used_gb"] = round(mem_used / (1024**3), 2)
            stats["memory_percent"] = round((mem_used / mem_total) * 100, 1)
        stats["cpu_percent"] = round(min(100.0, values.get("CPU", 0.0)), 1)
        stats["available"] = mem_total > 0
    except Exception:
        stats["error"] = stats["error"] or "Failed to parse remote stats"
        return stats

    return stats


def _format_ssh_error(
    result: subprocess.CompletedProcess, fallback: str = "SSH command failed"
) -> str:
    if result.returncode == 255:
        return "SSH failed or unreachable"
    stderr = (result.stderr or "").strip()
    if stderr:
        return stderr.splitlines()[-1][:200]
    return fallback


@router.get("/api/host-load")
def get_host_load():
    """Get load information for each remote host."""

    # Get all profiles and their processes
    profiles = profile_service.list_profiles(include_default=True)
    remote_config = get_effective_remote_config()
    hosts_list = remote_config.get("OCR_REMOTE_HOSTS_LIST", [])
    profile_aliases = _load_profile_aliases()

    # Initialize host load tracking
    host_loads: dict[str, dict] = {}

    # Add localhost first
    localhost_name = socket.gethostname()
    localhost_stats = _get_local_system_stats()
    host_loads["localhost"] = {
        "id": "localhost",
        "name": f"Localhost ({localhost_name})",
        "host": "127.0.0.1",
        "active_profiles": [],
        "worker_count": 0,
        "max_recommended": 10,  # Higher for localhost
        "load_percentage": 0,
        "status": "available",
        "cpu_percent": localhost_stats.get("cpu_percent", 0.0),
        "memory_percent": localhost_stats.get("memory_percent", 0.0),
        "memory_used_gb": localhost_stats.get("memory_used_gb", 0.0),
        "memory_total_gb": localhost_stats.get("memory_total_gb", 0.0),
        "system_available": localhost_stats.get("available", True),
        "chrome_process_count": _get_local_chrome_process_count(),
    }

    # Add all configured remote hosts
    for host in hosts_list if isinstance(hosts_list, list) else []:
        host_id = str(host.get("id", ""))
        host_name = host.get("name", f"Host {host_id}")
        host_addr = host.get("host", "")
        host_user = host.get("user", "")
        host_ssh_opts = str(host.get("ssh") or host.get("sshOpts") or "").strip()
        host_port = host.get("port")
        if not host_ssh_opts and host_port:
            host_ssh_opts = f"-p {host_port}"

        # Try to get system stats from remote host
        remote_stats = _get_remote_system_stats(host_addr, host_user, host_ssh_opts)
        top_processes = _get_remote_top_processes(host_addr, host_user, host_ssh_opts)
        chrome_count = _get_remote_browser_process_count(host_addr, host_user, host_ssh_opts)

        host_loads[host_id] = {
            "id": host_id,
            "name": host_name,
            "host": host_addr,
            "active_profiles": [],
            "worker_count": 0,
            "max_recommended": 5,  # Default recommendation
            "load_percentage": 0,
            "status": "available",
            "cpu_percent": remote_stats.get("cpu_percent", 0.0),
            "memory_percent": remote_stats.get("memory_percent", 0.0),
            "memory_used_gb": remote_stats.get("memory_used_gb", 0.0),
            "memory_total_gb": remote_stats.get("memory_total_gb", 0.0),
            "system_available": remote_stats.get("available", False),
            "system_error": remote_stats.get("error"),
            "top_processes": top_processes,
            "chrome_process_count": chrome_count,
        }

    # Count profiles per host
    counted_pids: set[int] = set()
    counted_profiles: set[str] = set()
    for p_name in profiles:
        # First check if profile is running remotely
        remote_info = process_service.is_profile_running_remote(p_name)
        if remote_info:
            rid = str(remote_info.get("host_id", ""))
            if rid and rid in host_loads:
                host_loads[rid]["active_profiles"].append(profile_aliases.get(p_name, p_name))
                host_loads[rid]["worker_count"] += 1
            # If running remotely, skip local pid check for this profile
            counted_profiles.add(p_name)
            continue

        # Check if profile has running processes locally
        pids = process_service.get_profile_pids(p_name)
        running_pids = [pid for pid in pids if process_service.pid_is_running(pid)]

        if not running_pids:
            continue
        counted_pids.update(running_pids)

        # Get remote host ID from process environment
        remote_host_id = None
        is_local = True  # Assume local by default
        for pid in running_pids:
            # Try to read OCR_REMOTE_HOST env from process
            try:
                env_bytes = (Path("/proc") / str(pid) / "environ").read_bytes()
                for item in env_bytes.split(b"\0"):
                    if item.startswith(b"OCR_REMOTE_RUN_ENABLED="):
                        is_remote = item.split(b"=", 1)[1].decode("utf-8", "ignore").strip()
                        if is_remote in {"1", "true", "yes", "on"}:
                            is_local = False
                        break

                # Try to determine which host by reading env vars
                # Since we don't store remote_host_id in env, we need to match by host address
                if not is_local:
                    for item in env_bytes.split(b"\0"):
                        if item.startswith(b"OCR_REMOTE_HOST="):
                            host_addr = item.split(b"=", 1)[1].decode("utf-8", "ignore").strip()
                            # Find matching host
                            for host in hosts_list if isinstance(hosts_list, list) else []:
                                if host.get("host") == host_addr:
                                    remote_host_id = str(host.get("id", ""))
                                    break
                            break
            except Exception:
                continue

            if remote_host_id or is_local:
                break

        # Assign profile to appropriate host
        if is_local and "localhost" in host_loads:
            host_loads["localhost"]["active_profiles"].append(profile_aliases.get(p_name, p_name))
            host_loads["localhost"]["worker_count"] += 1
            counted_profiles.add(p_name)
        elif remote_host_id and remote_host_id in host_loads:
            host_loads[remote_host_id]["active_profiles"].append(
                profile_aliases.get(p_name, p_name)
            )
            host_loads[remote_host_id]["worker_count"] += 1
            counted_profiles.add(p_name)

    # Count any remaining run.py processes as local workers (if profile env not readable)
    for pid, profile in process_service.iter_runpy_processes():
        if pid in counted_pids or not process_service.pid_is_running(pid):
            continue
        # If the profile was already assigned (e.g. remote state marker), don't double count it.
        if profile and profile in counted_profiles:
            continue
        if "localhost" in host_loads:
            host_loads["localhost"]["worker_count"] += 1
            if profile:
                host_loads["localhost"]["active_profiles"].append(
                    profile_aliases.get(profile, profile)
                )
                counted_profiles.add(profile)

    # Fallback: use runtime state to detect locally running profiles (if pids were missed)
    if "localhost" in host_loads:
        try:
            rows = execute_query(
                """
                SELECT profile_name, active_worker_pid, current_action, last_updated
                FROM public.profile_runtime_state
                """
            )
            for profile_name, active_pid, current_action, last_updated in rows:
                if not profile_name or profile_name in counted_profiles:
                    continue
                if not active_pid:
                    continue
                if not process_service.pid_is_running(int(active_pid)):
                    continue
                if current_action and str(current_action).lower() in {"stopped", "idle"}:
                    continue
                if not _is_recent(last_updated, PROFILE_STATE_MAX_AGE_MIN):
                    continue
                host_loads["localhost"]["worker_count"] += 1
                host_loads["localhost"]["active_profiles"].append(
                    profile_aliases.get(profile_name, profile_name)
                )
                counted_profiles.add(profile_name)
        except Exception:
            pass

    # Calculate load percentages and status for all hosts
    for load_info in host_loads.values():
        worker_count = load_info["worker_count"]
        max_rec = load_info["max_recommended"]

        load_info["load_percentage"] = min(100, int((worker_count / max_rec) * 100))

        # Status based on worker count
        if worker_count == 0:
            load_info["status"] = "idle"
        elif worker_count < max_rec * 0.7:
            load_info["status"] = "available"
        elif worker_count < max_rec:
            load_info["status"] = "busy"
        else:
            load_info["status"] = "overloaded"

        # Override status if system resources unavailable
        if not load_info.get("system_available"):
            load_info["status"] = "unavailable"

    if "localhost" in host_loads:
        host_loads["localhost"]["top_processes"] = _get_local_top_processes()

    # Return as list sorted: localhost first, then by load
    hosts_values = list(host_loads.values())
    localhost_host = [h for h in hosts_values if h["id"] == "localhost"]
    other_hosts = sorted(
        [h for h in hosts_values if h["id"] != "localhost"],
        key=lambda x: x["worker_count"],
        reverse=True,
    )

    return {"hosts": localhost_host + other_hosts}


@router.post("/api/host-browsers/kill", responses={400: {"description": "Validation error"}, 404: {"description": "Host not found"}})
def kill_host_browsers(payload: Annotated[dict[str, Any], Body(...)]) -> dict[str, Any]:
    host_id = str(payload.get("host_id", "")).strip()
    if not host_id:
        raise HTTPException(status_code=400, detail="host_id is required")

    remote_config = get_effective_remote_config()
    hosts_list = remote_config.get("OCR_REMOTE_HOSTS_LIST", [])

    if host_id == "localhost":
        stats = _kill_local_browser_processes()
        return {"host_id": host_id, "status": "ok", **stats}

    host_entry = None
    for host in hosts_list if isinstance(hosts_list, list) else []:
        if str(host.get("id", "")) == host_id:
            host_entry = host
            break

    if not host_entry:
        raise HTTPException(status_code=404, detail="Host not found")

    host_addr = host_entry.get("host", "")
    host_user = host_entry.get("user", "")
    host_ssh_opts = str(host_entry.get("ssh") or host_entry.get("sshOpts") or "").strip()
    host_port = host_entry.get("port")
    if not host_ssh_opts and host_port:
        host_ssh_opts = f"-p {host_port}"

    if not host_addr or not host_user:
        raise HTTPException(status_code=400, detail="Missing host connection details")

    stats = _kill_remote_browser_processes(host_addr, host_user, host_ssh_opts)
    return {"host_id": host_id, "status": "ok", **stats}


@router.get("/api/logs")
def get_logs(
    profile: str | None = None,
    level: str | None = None,
    limit: int = 100,
    hours: int | None = None,
):
    """Get activity logs from database (or fallback file)."""
    logs = []

    if not HAS_PSYCOPG2:
        return {
            "logs": _load_file_logs(profile, level, limit),
            "error": "psycopg2 not available",
        }

    # Get database DSN from config or environment
    pg_dsn = config.PG_DSN or os.environ.get("OCR_PG_DSN")

    # Fallback to hardcoded only if essential (user specified), but ideally remove it.
    # For now, to solve security issue, we rely on config or env.
    if not pg_dsn:
        return {
            "logs": _load_file_logs(profile, level, limit),
            "error": "Database credentials not configured",
        }

    conn = None
    try:
        conn = psycopg2.connect(pg_dsn)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query, params = _build_logs_query(profile, level, hours, limit)
            cur.execute(query, params)
            rows = cur.fetchall()

            for row in rows:
                # Determine log level based on event type
                event_type = row.get("event_type", "")
                if row.get("error_message") or "error" in event_type.lower():
                    log_level = "ERROR"
                elif "stop" in event_type or "limit" in event_type:
                    log_level = "WARNING"
                else:
                    log_level = "INFO"

                # Format timestamp
                ts = row.get("event_timestamp")
                time_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "-"

                # Build message with more detail
                profile_name = row.get("profile_name") or ""
                reason = row.get("reason") or ""
                component = row.get("component") or ""
                triggered_by = row.get("triggered_by") or ""
                is_automatic = row.get("is_automatic")
                error_message = row.get("error_message") or ""

                message_parts: list[str] = []
                if component:
                    message_parts.append(f"[{component}]")
                if profile_name:
                    message_parts.append(f"profile={profile_name}")
                if event_type:
                    message_parts.append(f"event={event_type}")
                if reason:
                    message_parts.append(f"reason={reason}")
                if triggered_by:
                    message_parts.append(f"by={triggered_by}")
                if is_automatic is not None:
                    message_parts.append("auto=yes" if is_automatic else "auto=no")
                if error_message:
                    message_parts.append(f"error={error_message}")

                message = " | ".join(message_parts) if message_parts else event_type

                logs.append(
                    {
                        "id": row.get("id"),
                        "time": time_str,
                        "level": log_level,
                        "message": message,
                        "profile": profile_name,
                        "event_type": event_type,
                    }
                )

    except Exception as e:
        print(f"Error fetching logs: {e}")
        return {"logs": _load_file_logs(profile, level, limit), "error": str(e)}
    finally:
        if conn:
            with suppress(Exception):
                conn.close()

    return {"logs": logs}


@router.post("/api/logs/clear")
def clear_logs():
    """Clear activity logs from database and fallback files."""
    removed = {"db_rows": 0, "files": 0}

    # Clear DB logs if available
    if HAS_PSYCOPG2:
        pg_dsn = config.PG_DSN or os.environ.get("OCR_PG_DSN")
        if pg_dsn:
            try:
                conn = psycopg2.connect(pg_dsn)
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM system_activity_log")
                    removed["db_rows"] = cur.rowcount or 0
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"Error clearing logs (db): {e}")

    # Clear fallback log files
    activity_dir = config.CACHE_DIR / "activity_logs"
    if activity_dir.exists():
        for log_file in activity_dir.glob("activity_*.log"):
            try:
                log_file.unlink()
                removed["files"] += 1
            except Exception:
                pass

    return {"success": True, "removed": removed}


@router.get("/api/logs/file")
def get_file_log(name: str, tail: int = 400):
    """Read a whitelisted log file from logs/ directory."""
    allowed = {
        "monitor_farm_health": Path("logs/monitor_farm_health.service.log"),
        "precheck_limits": Path("logs/precheck_limits.service.log"),
        "precheck_nohup": Path("logs/precheck_limits.log"),
        "farm_health_nohup": Path("logs/monitor_farm_health.log"),
    }
    if name not in allowed:
        raise HTTPException(status_code=400, detail="Nieprawidłowa nazwa logu")

    path = allowed[name]
    try:
        if not path.exists():
            return {"log": "", "path": str(path)}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = max(10, min(int(tail), 2000))
        return {"log": "\n".join(lines[-tail:]), "path": str(path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/farm/stop")
def stop_farm():
    """Stop all running profiles."""
    profiles = profile_service.list_profiles(include_default=True)
    stopped: list[str] = []
    for name in profiles:
        process_service.stop_profile_processes(name)
        stopped.append(name)
    return {"success": True, "stopped": stopped}


@router.post("/api/farm/start")
def start_farm(headed: bool = False):
    """Start all profiles (best-effort)."""
    profiles = profile_service.list_profiles(include_default=True)
    started: list[str] = []
    failed: dict[str, str] = {}
    for name in profiles:
        ok, message = process_service.start_profile_process(name, headed=headed)
        if ok:
            started.append(name)
        else:
            failed[name] = message
    return {"success": True, "started": started, "failed": failed}


@router.post("/api/critical-events/resolve")
def resolve_critical_event(payload: dict):
    """Mark a critical event as resolved."""
    event_id = payload.get("id") or payload.get("event_id")
    try:
        event_id_int = int(event_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Nieprawidłowe ID zdarzenia") from e

    affected = execute_write(
        "UPDATE public.critical_events SET resolved_at = NOW() "
        "WHERE id = %s AND resolved_at IS NULL",
        (event_id_int,),
    )
    return {"success": True, "resolved": affected}
