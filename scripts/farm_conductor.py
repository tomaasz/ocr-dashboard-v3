#!/usr/bin/env python3
"""
Farm Conductor — Self-healing OCR farm orchestrator.

Automatically starts, monitors, and restarts OCR profile workers.
Designed to run as a systemd service for automatic recovery after
server reboots and crashes.

Usage:
    python scripts/farm_conductor.py                # Run with default config
    python scripts/farm_conductor.py --once         # Single check cycle
    python scripts/farm_conductor.py --dry-run      # Show what would be done

Environment Variables:
    OCR_PG_DSN              - PostgreSQL connection string
    FARM_CONFIG_PATH        - Path to farm_profiles.json (default: config/farm_profiles.json)
    FARM_DASHBOARD_URL      - Dashboard API URL (default: http://localhost:9090)
    FARM_LOG_FILE           - Log file path (default: logs/farm_conductor.log)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Project root for relative paths
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_file: str | None = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("farm_conductor")
    logger.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(ch)

    # File handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        logger.addHandler(fh)

    return logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class FarmConfig:
    """Farm configuration loaded from JSON."""

    profiles: list[str] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    max_concurrent: int = 4
    startup_delay_sec: int = 30
    restart_backoff_base_sec: int = 10
    restart_max_backoff_sec: int = 300
    health_check_interval_sec: int = 30
    max_memory_percent: float = 85.0
    max_cpu_load_1m: float = 7.0


@dataclass
class ProfileState:
    """Tracks the runtime state of a managed profile."""

    name: str
    running: bool = False
    pids: list[int] = field(default_factory=list)
    last_start_time: float = 0.0
    last_crash_time: float = 0.0
    consecutive_failures: int = 0
    total_restarts: int = 0
    last_error: str | None = None
    backoff_until: float = 0.0


# ---------------------------------------------------------------------------
# System monitoring
# ---------------------------------------------------------------------------
def get_memory_percent() -> float:
    """Get current memory usage as a percentage."""
    try:
        with open("/proc/meminfo") as f:
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
                return round(100 * (total - available) / total, 1)
    except Exception:
        pass
    return 0.0


def get_cpu_load_1m() -> float:
    """Get 1-minute load average."""
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def get_running_profiles() -> dict[str, list[int]]:
    """Scan /proc for running run.py processes grouped by profile."""
    profiles: dict[str, list[int]] = {}
    proc_root = Path("/proc")

    if not proc_root.exists():
        return profiles

    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            # Skip zombies
            stat_path = entry / "stat"
            if stat_path.exists():
                try:
                    state = stat_path.read_text(encoding="utf-8", errors="ignore").split()[2]
                    if state == "Z":
                        continue
                except Exception:
                    pass

            cmdline = (entry / "cmdline").read_bytes()
            if b"run.py" not in cmdline:
                continue

            # Extract profile suffix from environment
            profile = None
            try:
                env_bytes = (entry / "environ").read_bytes()
                for item in env_bytes.split(b"\x00"):
                    if item.startswith(b"OCR_PROFILE_SUFFIX="):
                        profile = item.split(b"=", 1)[1].decode("utf-8", "ignore")
                        break
            except Exception:
                pass

            if profile:
                profiles.setdefault(profile, []).append(pid)
        except Exception:
            continue

    return profiles


# ---------------------------------------------------------------------------
# Dashboard API interaction
# ---------------------------------------------------------------------------
def api_start_profile(
    dashboard_url: str,
    profile_name: str,
    config: dict | None = None,
    timeout: int = 15,
) -> tuple[bool, str]:
    """Start a profile via dashboard API."""
    if not HAS_REQUESTS:
        return False, "requests library not available"

    url = f"{dashboard_url}/api/profile/{profile_name}/start"
    payload = config or {}

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json()
        success = data.get("success", False)
        message = data.get("message", "Unknown response")
        return success, message
    except requests.exceptions.ConnectionError:
        return False, "Dashboard not reachable"
    except requests.exceptions.Timeout:
        return False, "Dashboard request timeout"
    except Exception as e:
        return False, f"API error: {str(e)[:100]}"


def api_check_dashboard(dashboard_url: str, timeout: int = 5) -> bool:
    """Check if the dashboard API is responding."""
    if not HAS_REQUESTS:
        return False
    try:
        resp = requests.get(f"{dashboard_url}/api/profiles", timeout=timeout)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Farm Conductor
# ---------------------------------------------------------------------------
class FarmConductor:
    """Main farm orchestration engine."""

    def __init__(
        self,
        config: FarmConfig,
        dashboard_url: str = "http://localhost:9090",
        dry_run: bool = False,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.dashboard_url = dashboard_url
        self.dry_run = dry_run
        self.log = logger or logging.getLogger("farm_conductor")

        # Profile states
        self.states: dict[str, ProfileState] = {}
        for name in config.profiles:
            self.states[name] = ProfileState(name=name)

        self._shutdown = False

    def _signal_handler(self, signum, frame):
        self.log.info("🛑 Shutdown signal received (sig=%s). Stopping conductor...", signum)
        self._shutdown = True

    def _check_resources(self) -> tuple[bool, str]:
        """Check if system resources allow starting more profiles."""
        mem_pct = get_memory_percent()
        cpu_load = get_cpu_load_1m()

        if mem_pct > self.config.max_memory_percent:
            return False, f"Memory too high: {mem_pct}% > {self.config.max_memory_percent}%"
        if cpu_load > self.config.max_cpu_load_1m:
            return False, f"CPU load too high: {cpu_load} > {self.config.max_cpu_load_1m}"

        return True, f"OK (mem={mem_pct}%, load={cpu_load})"

    def _compute_backoff(self, failures: int) -> float:
        """Compute exponential backoff with cap."""
        delay = self.config.restart_backoff_base_sec * (2 ** min(failures, 6))
        return min(delay, self.config.restart_max_backoff_sec)

    def _build_profile_config(self) -> dict:
        """Build the config dict for profile start API call."""
        defaults = self.config.defaults.copy()
        config: dict = {}

        if defaults.get("auto_advance"):
            config["auto_advance"] = True
        if defaults.get("pg_enabled"):
            config["pg_enabled"] = True
        if defaults.get("continuous"):
            config["continuous_mode"] = True

        windows = defaults.get("windows")
        if windows:
            config["windows"] = windows

        tabs = defaults.get("tabs_per_window")
        if tabs:
            config["tabs_per_window"] = tabs

        return config

    def run_cycle(self) -> dict:
        """Execute one monitoring/management cycle. Returns status summary."""
        now = time.time()
        running = get_running_profiles()
        summary = {
            "time": datetime.now(UTC).isoformat(),
            "profiles": {},
            "actions": [],
        }

        # Update states from running processes
        for name, state in self.states.items():
            pids = running.get(name, [])
            state.pids = pids
            state.running = len(pids) > 0
            summary["profiles"][name] = {
                "running": state.running,
                "pids": pids,
                "failures": state.consecutive_failures,
                "restarts": state.total_restarts,
            }

        # Count running profiles
        running_count = sum(1 for s in self.states.values() if s.running)

        # Decide actions
        for name, state in self.states.items():
            if state.running:
                # Profile is healthy
                if state.consecutive_failures > 0:
                    self.log.info(
                        "✅ [%s] Recovered after %d failures. Resetting counter.",
                        name,
                        state.consecutive_failures,
                    )
                    state.consecutive_failures = 0
                continue

            # Profile is NOT running — should we (re)start it?

            # Check backoff
            if now < state.backoff_until:
                wait_remaining = int(state.backoff_until - now)
                self.log.debug("⏳ [%s] In backoff period (%ds remaining)", name, wait_remaining)
                summary["actions"].append(
                    {"profile": name, "action": "backoff", "wait_s": wait_remaining}
                )
                continue

            # Check max concurrent
            if running_count >= self.config.max_concurrent:
                self.log.info(
                    "⚠️ [%s] Skipping start: max_concurrent=%d reached (%d running)",
                    name,
                    self.config.max_concurrent,
                    running_count,
                )
                summary["actions"].append({"profile": name, "action": "skip_max_concurrent"})
                continue

            # Check resources
            res_ok, res_msg = self._check_resources()
            if not res_ok:
                self.log.warning("⚠️ [%s] Skipping start: %s", name, res_msg)
                summary["actions"].append(
                    {"profile": name, "action": "skip_resources", "reason": res_msg}
                )
                continue

            # Check staggered startup delay (don't start all at once)
            last_start_any = max(
                (s.last_start_time for s in self.states.values()),
                default=0,
            )
            if (
                now - last_start_any < self.config.startup_delay_sec
                and state.consecutive_failures == 0
            ):
                self.log.debug(
                    "⏳ [%s] Stagger delay: %ds since last start",
                    name,
                    int(now - last_start_any),
                )
                summary["actions"].append({"profile": name, "action": "stagger_wait"})
                continue

            # START the profile
            action_type = "restart" if state.total_restarts > 0 else "start"

            if self.dry_run:
                self.log.info("🔵 [DRY-RUN] Would %s profile: %s", action_type, name)
                summary["actions"].append({"profile": name, "action": f"dry_run_{action_type}"})
                continue

            profile_config = self._build_profile_config()
            self.log.info(
                "🚀 [%s] %s profile (failures=%d, restarts=%d)...",
                name,
                action_type.upper(),
                state.consecutive_failures,
                state.total_restarts,
            )

            success, message = api_start_profile(self.dashboard_url, name, profile_config)

            if success:
                state.last_start_time = now
                state.total_restarts += 1
                running_count += 1
                self.log.info("✅ [%s] Started: %s", name, message)
                summary["actions"].append(
                    {"profile": name, "action": action_type, "success": True, "message": message}
                )
            else:
                state.consecutive_failures += 1
                state.last_error = message
                state.last_crash_time = now
                backoff = self._compute_backoff(state.consecutive_failures)
                state.backoff_until = now + backoff
                self.log.error(
                    "❌ [%s] Failed to start: %s (backoff=%ds, failures=%d)",
                    name,
                    message,
                    int(backoff),
                    state.consecutive_failures,
                )
                summary["actions"].append(
                    {
                        "profile": name,
                        "action": action_type,
                        "success": False,
                        "message": message,
                        "backoff_sec": int(backoff),
                    }
                )

        return summary

    def run_loop(self):
        """Main monitor loop. Runs until shutdown signal."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        interval = max(5, self.config.health_check_interval_sec)
        self.log.info(
            "🏁 Farm Conductor started (profiles=%d, interval=%ds, max_concurrent=%d)",
            len(self.config.profiles),
            interval,
            self.config.max_concurrent,
        )
        self.log.info("   Profiles: %s", ", ".join(self.config.profiles))
        self.log.info("   Dashboard: %s", self.dashboard_url)

        # Wait for dashboard to be ready
        dashboard_wait_start = time.time()
        while not self._shutdown and time.time() - dashboard_wait_start < 120:
            if api_check_dashboard(self.dashboard_url):
                self.log.info("✅ Dashboard is ready.")
                break
            self.log.info("⏳ Waiting for dashboard to be ready...")
            time.sleep(5)
        else:
            if not self._shutdown:
                self.log.warning("⚠️ Dashboard not reachable after 120s, proceeding anyway...")

        cycle_count = 0
        while not self._shutdown:
            cycle_count += 1
            try:
                summary = self.run_cycle()
                actions = summary.get("actions", [])

                # Log summary
                running = sum(1 for p in summary.get("profiles", {}).values() if p.get("running"))
                total = len(summary.get("profiles", {}))
                self.log.info(
                    "💓 Cycle #%d: %d/%d profiles running, %d actions",
                    cycle_count,
                    running,
                    total,
                    len(actions),
                )

            except Exception as e:
                self.log.error("❌ Cycle error: %s", str(e), exc_info=True)

            # Sleep in small increments to respond to shutdown quickly
            wait_end = time.time() + interval
            while not self._shutdown and time.time() < wait_end:
                time.sleep(1)

        self.log.info("🛑 Farm Conductor stopped.")


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------
def load_config(config_path: str | Path) -> FarmConfig:
    """Load farm configuration from JSON file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Farm config not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return FarmConfig(
        profiles=data.get("profiles", []),
        defaults=data.get("defaults", {}),
        max_concurrent=data.get("max_concurrent", 4),
        startup_delay_sec=data.get("startup_delay_sec", 30),
        restart_backoff_base_sec=data.get("restart_backoff_base_sec", 10),
        restart_max_backoff_sec=data.get("restart_max_backoff_sec", 300),
        health_check_interval_sec=data.get("health_check_interval_sec", 30),
        max_memory_percent=data.get("max_memory_percent", 85.0),
        max_cpu_load_1m=data.get("max_cpu_load_1m", 7.0),
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Farm Conductor — self-healing OCR farm orchestrator"
    )
    parser.add_argument("--once", action="store_true", help="Run single cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without executing")
    parser.add_argument(
        "--config",
        default=os.environ.get(
            "FARM_CONFIG_PATH",
            str(PROJECT_ROOT / "config" / "farm_profiles.json"),
        ),
        help="Path to farm configuration JSON",
    )
    parser.add_argument(
        "--dashboard-url",
        default=os.environ.get("FARM_DASHBOARD_URL", "http://localhost:9090"),
        help="Dashboard API URL",
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get(
            "FARM_LOG_FILE",
            str(PROJECT_ROOT / "logs" / "farm_conductor.log"),
        ),
        help="Log file path",
    )
    args = parser.parse_args()

    logger = setup_logging(log_file=args.log_file)

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error("❌ %s", e)
        return 1
    except json.JSONDecodeError as e:
        logger.error("❌ Invalid JSON in config: %s", e)
        return 1

    if not config.profiles:
        logger.error("❌ No profiles configured in %s", args.config)
        return 1

    conductor = FarmConductor(
        config=config,
        dashboard_url=args.dashboard_url,
        dry_run=args.dry_run,
        logger=logger,
    )

    if args.once:
        summary = conductor.run_cycle()
        logger.info("Summary: %s", json.dumps(summary, indent=2, default=str))
        return 0

    conductor.run_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
