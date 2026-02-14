#!/usr/bin/env python3
"""
Farm Health Monitoring Script

Periodically checks if the OCR farm is running and logs health status to PostgreSQL.
Can run once or continuously with configurable interval.

Usage:
    # Single check
    python monitor_farm_health.py --once

    # Continuous monitoring (every 60 seconds)
    python monitor_farm_health.py --interval 60

Environment Variables:
    OCR_PG_DSN - PostgreSQL connection string (required)
    WEB_DASHBOARD_URL - URL of web dashboard (default: http://localhost:9090)
    FARM_HEALTH_CHECK_INTERVAL - Default interval in seconds (default: 120)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras

    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("WARNING: requests not installed. Web API checks will be skipped.", file=sys.stderr)


def _validate_web_url(url: str) -> bool:
    """Validate URL is safe for internal monitoring API calls."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        blocked_hosts = [
            "169.254.169.254",
            "metadata.google.internal",
            "metadata",
            "metadata.goog",
        ]
        if parsed.hostname and parsed.hostname.lower() in blocked_hosts:
            return False

        if not parsed.hostname:
            return False

        host_lower = parsed.hostname.lower()
        if host_lower in ("localhost", "127.0.0.1", "::1"):
            return True

        if (
            host_lower.startswith("192.168.")
            or host_lower.startswith("10.")
            or host_lower.startswith("172.")
            or host_lower.startswith("100.")
        ):
            return True

        return False
    except Exception:
        return False


class FarmHealthMonitor:
    """Monitor OCR farm health and log to database."""

    def __init__(self, pg_dsn: str, web_url: str = "http://localhost:9090"):
        self.pg_dsn = pg_dsn
        if not _validate_web_url(web_url):
            raise ValueError(f"Invalid or unsafe web URL: {web_url}")
        self.web_url = web_url.rstrip("/")

    def _get_connection(self):
        try:
            return psycopg2.connect(self.pg_dsn)
        except Exception as e:
            print(f"ERROR: Failed to connect to database: {e}", file=sys.stderr)
            return None

    def _check_farm_processes(self) -> tuple[int, list[str]]:
        processes = []
        profiles = []

        proc_root = Path("/proc")
        if not proc_root.exists():
            return 0, []

        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue

            try:
                cmdline_file = entry / "cmdline"
                if not cmdline_file.exists():
                    continue

                cmdline = cmdline_file.read_bytes()
                if b"run.py" not in cmdline:
                    continue

                processes.append(int(entry.name))

                try:
                    environ_file = entry / "environ"
                    if environ_file.exists():
                        env_bytes = environ_file.read_bytes()
                        for item in env_bytes.split(b"\x00"):
                            if item.startswith(b"OCR_PROFILE_SUFFIX="):
                                profile = item.split(b"=", 1)[1].decode("utf-8", "ignore")
                                if profile:
                                    profiles.append(profile)
                                break
                except Exception:
                    pass
            except Exception:
                continue

        return len(processes), profiles

    def _check_web_api(self, process_count: int = 0) -> tuple[bool, int | None, str | None]:
        if not HAS_REQUESTS:
            return False, None, "requests library not available"

        try:
            start_time = time.time()
            response = requests.get(f"{self.web_url}/api/profiles", timeout=5)
            response_time_ms = int((time.time() - start_time) * 1000)

            if 200 <= response.status_code < 300:
                try:
                    data = response.json()

                    if "profiles" not in data:
                        return False, response_time_ms, "Invalid API schema: missing 'profiles' key"

                    if process_count > 0 and len(data["profiles"]) == 0:
                        return (
                            False,
                            response_time_ms,
                            "CRITICAL: API Blindness - processes running but no profiles in API response",
                        )

                    return True, response_time_ms, None
                except (json.JSONDecodeError, ValueError) as e:
                    return False, response_time_ms, f"Invalid JSON response: {str(e)[:100]}"
            return False, response_time_ms, f"HTTP {response.status_code}"
        except requests.exceptions.Timeout:
            return False, None, "Request timeout (>5s)"
        except requests.exceptions.ConnectionError as e:
            return False, None, f"Connection error: {str(e)[:100]}"
        except Exception as e:
            return False, None, f"Error: {str(e)[:100]}"

    def _get_system_load(self) -> dict:
        metrics: dict[str, float | int] = {}

        try:
            with open("/proc/stat", encoding="utf-8") as f:
                cpu_line = f.readline()
                cpu_values = [int(x) for x in cpu_line.split()[1:]]
                cpu_total = sum(cpu_values)
                cpu_idle = cpu_values[3]
                if cpu_total > 0:
                    metrics["cpu_percent"] = round(100 * (1 - cpu_idle / cpu_total), 2)
        except Exception:
            pass

        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = int(parts[1].strip().split()[0])
                        meminfo[key] = value

                total = meminfo.get("MemTotal", 0)
                available = meminfo.get("MemAvailable", 0)
                if total > 0:
                    used = total - available
                    metrics["memory_percent"] = round(100 * used / total, 2)
                    metrics["memory_used_mb"] = round(used / 1024, 0)
                    metrics["memory_total_mb"] = round(total / 1024, 0)
        except Exception:
            pass

        try:
            st = os.statvfs("/")
            total = st.f_frsize * st.f_blocks
            free = st.f_frsize * st.f_bfree
            used = total - free
            if total > 0:
                metrics["disk_percent"] = round(100 * used / total, 2)
        except Exception:
            pass

        return metrics

    def _insert_health_record(
        self,
        is_healthy: bool,
        process_count: int,
        active_profiles: list[str],
        web_api_ok: bool,
        web_api_time: int | None,
        web_api_error: str | None,
        system_load: dict,
        error_details: str | None,
    ) -> bool:
        conn = self._get_connection()
        if not conn:
            return False

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO farm_health_checks (
                        is_healthy,
                        farm_processes_count,
                        active_profiles,
                        web_api_responsive,
                        web_api_response_time_ms,
                        web_api_error,
                        system_load,
                        error_details,
                        metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        is_healthy,
                        process_count,
                        psycopg2.extras.Json(active_profiles),
                        web_api_ok,
                        web_api_time,
                        web_api_error,
                        psycopg2.extras.Json(system_load),
                        error_details,
                        psycopg2.extras.Json({"web_url": self.web_url}),
                    ),
                )
            conn.commit()
            return True
        except Exception as e:
            print(f"ERROR: Failed to insert health record: {e}", file=sys.stderr)
            conn.rollback()
            return False
        finally:
            conn.close()

    def run_once(self) -> tuple[bool, str]:
        process_count, profiles = self._check_farm_processes()
        web_ok, web_time, web_error = self._check_web_api(process_count)
        system_load = self._get_system_load()

        is_healthy = process_count > 0 and web_ok
        error_details = None
        if process_count == 0:
            error_details = "No farm processes detected"
        elif not web_ok:
            error_details = web_error or "Web API not responsive"

        ok = self._insert_health_record(
            is_healthy=is_healthy,
            process_count=process_count,
            active_profiles=profiles,
            web_api_ok=web_ok,
            web_api_time=web_time,
            web_api_error=web_error,
            system_load=system_load,
            error_details=error_details,
        )
        status = "OK" if is_healthy else "FAIL"
        reason = error_details or "Healthy"
        api_time = f"{web_time}ms" if web_time is not None else "-"
        summary = (
            f"status={status} "
            f"processes={process_count} "
            f"profiles={len(profiles)} "
            f"api_ok={str(web_ok).lower()} "
            f"api_time={api_time} "
            f"reason={reason}"
        )
        return ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR farm health monitor")
    parser.add_argument("--once", action="store_true", help="Run single check and exit")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("FARM_HEALTH_CHECK_INTERVAL", "120")),
        help="Check interval in seconds",
    )
    parser.add_argument(
        "--web-url",
        default=os.environ.get("WEB_DASHBOARD_URL", "http://localhost:9090"),
        help="Dashboard base URL for API checks",
    )
    args = parser.parse_args()

    pg_dsn = os.environ.get("OCR_PG_DSN")
    if not pg_dsn:
        print("ERROR: OCR_PG_DSN environment variable not set", file=sys.stderr)
        return 1

    monitor = FarmHealthMonitor(pg_dsn=pg_dsn, web_url=args.web_url)

    if args.once:
        ok, summary = monitor.run_once()
        print(summary, flush=True)
        return 0 if ok else 1

    interval = max(10, int(args.interval))
    print(f"Starting farm health monitor (interval: {interval}s)", flush=True)

    try:
        while True:
            _, summary = monitor.run_once()
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{timestamp} {summary}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
