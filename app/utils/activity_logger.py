#!/usr/bin/env python3
"""
Activity Logger Module

Comprehensive logging system for tracking all farm and web application lifecycle events.
Logs to PostgreSQL database with detailed metadata about who, when, why, and whether automatic.

Usage:
    from ocr_engine.utils.activity_logger import ActivityLogger

    logger = ActivityLogger()

    # Log start event
    event_id = logger.log_start(
        component="farm",
        profile_name="fdg24w_new",
        configuration={"workers": 4, "headed": True}
    )

    # Log stop event
    logger.log_stop(
        component="farm",
        event_id=event_id,
        exit_code=0
    )
"""

import os
import socket
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg2
    import psycopg2.extras

    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


class ActivityLogger:
    """Logger for system activity events."""

    # Default DSN matching the one in start_farm.sh
    DEFAULT_DSN = "postgresql://tomaasz:123Karinka!%40%23@127.0.0.1:5432/ocr"

    def __init__(self, pg_dsn: str | None = None):
        """
        Initialize activity logger.

        Args:
            pg_dsn: PostgreSQL connection string. If None, uses OCR_PG_DSN env var or default.
        """
        self.pg_dsn = pg_dsn or os.environ.get("OCR_PG_DSN") or self.DEFAULT_DSN
        self._start_times: dict[str, float] = {}  # Track start times for duration calculation

    def _get_connection(self):
        """Get database connection."""
        if not self.pg_dsn or not HAS_PSYCOPG2:
            return None
        try:
            return psycopg2.connect(self.pg_dsn)
        except Exception as e:
            print(f"⚠️ ActivityLogger: Failed to connect to database: {e}", file=sys.stderr)
            return None

    def _get_system_info(self) -> dict[str, Any]:
        """Collect system information."""
        info = {
            "hostname": socket.gethostname(),
            "ip_address": None,
            "trigger_user": os.environ.get("USER") or os.environ.get("USERNAME"),
            "process_id": os.getpid(),
            "parent_process_id": os.getppid(),
        }

        # Try to get IP address
        try:
            info["ip_address"] = socket.gethostbyname(info["hostname"])
        except Exception:
            pass

        return info

    def _get_process_info(self, pid: int) -> dict[str, Any]:
        """Get information about a process."""
        info = {}
        try:
            proc_path = Path(f"/proc/{pid}")
            if proc_path.exists():
                # Get command line
                cmdline_file = proc_path / "cmdline"
                if cmdline_file.exists():
                    cmdline = (
                        cmdline_file.read_bytes()
                        .replace(b"\x00", b" ")
                        .decode("utf-8", errors="ignore")
                        .strip()
                    )
                    info["cmdline"] = cmdline[:500]  # Limit length

                # Get environment variables
                environ_file = proc_path / "environ"
                if environ_file.exists():
                    env_bytes = environ_file.read_bytes()
                    env_vars = {}
                    for item in env_bytes.split(b"\x00"):
                        if b"=" in item:
                            key, val = item.split(b"=", 1)
                            key_str = key.decode("utf-8", errors="ignore")
                            # Only collect OCR_* variables
                            if key_str.startswith("OCR_"):
                                env_vars[key_str] = val.decode("utf-8", errors="ignore")
                    info["env_vars"] = env_vars
        except Exception:
            pass
        return info

    def log_event(
        self,
        event_type: str,
        component: str,
        triggered_by: str = "user",
        reason: str = "",
        is_automatic: bool = False,
        profile_name: str | None = None,
        configuration: dict[str, Any] | None = None,
        exit_code: int | None = None,
        exit_signal: str | None = None,
        duration_seconds: int | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Log a system activity event.

        Args:
            event_type: Type of event (farm_start, farm_stop, web_start, web_stop, etc.)
            component: Component name (farm, web_dashboard, limit_worker, profile_worker)
            triggered_by: What triggered the event (user, system, auto_restart, api, script)
            reason: Human-readable reason for the event
            is_automatic: Whether this was an automatic restart
            profile_name: Profile name (for worker events)
            configuration: Configuration dict (for start events)
            exit_code: Exit code (for stop events)
            exit_signal: Signal that caused exit (for stop events)
            duration_seconds: Duration in seconds (for stop events)
            error_message: Error message if any
            metadata: Additional metadata dict

        Returns:
            Event ID (UUID) if logged successfully, None otherwise
        """
        conn = self._get_connection()
        if not conn:
            # Fallback to file logging if DB not available
            self._log_to_file(event_type, component, profile_name, reason)
            return None

        try:
            system_info = self._get_system_info()
            event_id = str(uuid.uuid4())

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_activity_log (
                        event_type, component, profile_name,
                        triggered_by, trigger_user, reason, is_automatic,
                        process_id, parent_process_id,
                        hostname, ip_address,
                        configuration,
                        exit_code, exit_signal, duration_seconds,
                        error_message,
                        metadata,
                        event_timestamp
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s,
                        %s, %s, %s,
                        %s,
                        %s,
                        NOW()
                    ) RETURNING id
                """,
                    (
                        event_type,
                        component,
                        profile_name,
                        triggered_by,
                        system_info["trigger_user"],
                        reason,
                        is_automatic,
                        system_info["process_id"],
                        system_info["parent_process_id"],
                        system_info["hostname"],
                        system_info["ip_address"],
                        psycopg2.extras.Json(configuration or {}),
                        exit_code,
                        exit_signal,
                        duration_seconds,
                        error_message,
                        psycopg2.extras.Json(metadata or {}),
                    ),
                )
                db_id = cur.fetchone()[0]

            conn.commit()
            return event_id

        except Exception as e:
            print(f"⚠️ ActivityLogger: Failed to log event: {e}", file=sys.stderr)
            conn.rollback()
            return None
        finally:
            conn.close()

    def log_start(
        self,
        component: str,
        profile_name: str | None = None,
        configuration: dict[str, Any] | None = None,
        triggered_by: str = "user",
        reason: str = "",
        is_automatic: bool = False,
        **kwargs,
    ) -> str | None:
        """
        Log component start event.

        Args:
            component: Component name (farm, web_dashboard, limit_worker, profile_worker)
            profile_name: Profile name (for worker events)
            configuration: Configuration dict with env vars, parameters
            triggered_by: What triggered the start (user, system, auto_restart, api, script)
            reason: Human-readable reason
            is_automatic: Whether this was an automatic restart
            **kwargs: Additional metadata

        Returns:
            Event ID for tracking this session
        """
        event_type = f"{component}_start"

        # Collect configuration from environment if not provided
        if configuration is None:
            configuration = {}
            for key, value in os.environ.items():
                if key.startswith("OCR_"):
                    # Don't log sensitive data like DSN passwords
                    if "DSN" in key or "PASSWORD" in key or "SECRET" in key:
                        configuration[key] = "***"
                    else:
                        configuration[key] = value

        # Store start time for duration calculation
        cache_key = f"{component}:{profile_name or 'default'}"
        self._start_times[cache_key] = time.time()

        event_id = self.log_event(
            event_type=event_type,
            component=component,
            profile_name=profile_name,
            triggered_by=triggered_by,
            reason=reason or f"Starting {component}",
            is_automatic=is_automatic,
            configuration=configuration,
            metadata=kwargs,
        )

        return event_id

    def log_stop(
        self,
        component: str,
        profile_name: str | None = None,
        event_id: str | None = None,
        exit_code: int | None = None,
        exit_signal: str | None = None,
        error_message: str | None = None,
        triggered_by: str = "user",
        reason: str = "",
        **kwargs,
    ) -> bool:
        """
        Log component stop event.

        Args:
            component: Component name
            profile_name: Profile name (for worker events)
            event_id: Event ID from corresponding start event
            exit_code: Exit code (0 = success)
            exit_signal: Signal that caused exit (SIGTERM, SIGKILL, etc.)
            error_message: Error message if any
            triggered_by: What triggered the stop
            reason: Human-readable reason
            **kwargs: Additional metadata

        Returns:
            True if logged successfully
        """
        event_type = f"{component}_stop"

        # Calculate duration if we have start time
        cache_key = f"{component}:{profile_name or 'default'}"
        duration_seconds = None
        if cache_key in self._start_times:
            duration_seconds = int(time.time() - self._start_times[cache_key])
            del self._start_times[cache_key]

        event_id = self.log_event(
            event_type=event_type,
            component=component,
            profile_name=profile_name,
            triggered_by=triggered_by,
            reason=reason or f"Stopping {component}",
            exit_code=exit_code,
            exit_signal=exit_signal,
            duration_seconds=duration_seconds,
            error_message=error_message,
            metadata=kwargs,
        )

        return event_id is not None

    def log_restart(
        self,
        component: str,
        profile_name: str | None = None,
        reason: str = "Automatic restart",
        **kwargs,
    ) -> str | None:
        """
        Log automatic restart event.

        Args:
            component: Component name
            profile_name: Profile name (for worker events)
            reason: Reason for restart
            **kwargs: Additional metadata

        Returns:
            Event ID
        """
        event_type = f"{component}_restart"
        return self.log_event(
            event_type=event_type,
            component=component,
            profile_name=profile_name,
            triggered_by="system",
            reason=reason,
            is_automatic=True,
            metadata=kwargs,
        )

    def _log_to_file(self, event_type: str, component: str, profile_name: str | None, reason: str):
        """Fallback logging to file when database is not available."""
        try:
            log_dir = Path.home() / ".cache" / "ocr-dashboard-v3" / "activity_logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            log_file = log_dir / f"activity_{datetime.now().strftime('%Y%m%d')}.log"

            timestamp = datetime.now().isoformat()
            profile_str = f" profile={profile_name}" if profile_name else ""
            log_line = (
                f"{timestamp} {event_type} component={component}{profile_str} reason={reason}\n"
            )

            with open(log_file, "a") as f:
                f.write(log_line)
        except Exception:
            pass  # Silent fail for fallback logging


# Convenience function for quick logging
def log_activity(event_type: str, component: str, **kwargs) -> str | None:
    """
    Quick activity logging function.

    Usage:
        log_activity("farm_start", "farm", profile_name="fdg24w_new")
    """
    logger = ActivityLogger()
    return logger.log_event(event_type, component, **kwargs)
