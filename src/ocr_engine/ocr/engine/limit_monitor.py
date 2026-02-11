#!/usr/bin/env python3
"""
Limit Monitor (limit_monitor.py)

Separate process that monitors PRO limits across all profiles.
- Checks all profile pause files periodically
- Opens browser to verify ACTUAL reset times
- Updates pause files with accurate times
- Reports which profiles are available

Usage:
    python limit_monitor.py --profiles-dir ~/.pw_profiles --interval 300
"""

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from ocr_engine.ocr.engine.db_locking import DbLockingManager
from ocr_engine.ocr.engine.pro_limit_handler import (
    PRO_LIMIT_TEXT_RE,
    ProLimitHandler,
)
from ocr_engine.utils.path_security import (
    safe_path_join,
    sanitize_profile_name,
    validate_cache_dir,
    validate_profiles_dir,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LimitMon] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class LimitMonitor:
    """Monitors PRO limits across all profiles."""

    def __init__(
        self,
        profiles_dir: Path,
        cache_dir: Path,
        check_interval: int = 300,  # 5 minutes
        deep_check_interval: int = 3600,  # 1 hour - browser check
    ):
        # Security: Validate paths using centralized utilities
        self.profiles_dir = validate_profiles_dir(str(profiles_dir))
        self.cache_dir = validate_cache_dir(str(cache_dir))

        self.check_interval = check_interval
        self.deep_check_interval = deep_check_interval

        self.status_file = self.cache_dir / "limit_monitor_status.json"
        self.running = True
        self.last_deep_check: dict[str, float] = {}

        # Initialize DB
        self.db = DbLockingManager(pg_dsn=os.environ.get("OCR_PG_DSN"))
        self.db.init_lock_table()  # Ensure basic tables exist
        self.db.init_error_traces_table()  # Ensure state tables exist (usually created by engine, but safe to init)

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Received shutdown signal.")
        self.running = False

    def _get_profiles(self) -> list[str]:
        """Get list of available profile names."""
        profiles = []
        if not self.profiles_dir.exists():
            return profiles

        for d in self.profiles_dir.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                # Extra safety: Ensure profile name is safe
                safe_name = sanitize_profile_name(d.name)
                if safe_name == d.name:
                    profiles.append(d.name)
        return sorted(profiles)

    def _get_pause_until(self, profile_name: str) -> datetime | None:
        """Get pause end time for profile from DB."""
        try:
            state = self.db.get_profile_state(profile_name)
            if state and state.get("is_paused") and state.get("paused_until"):
                return state["paused_until"]
        except Exception:
            pass
        return None

    def _set_pause_until(self, profile_name: str, until: datetime, reason: str = "limit_monitor"):
        """Set pause time for profile in DB."""
        try:
            self.db.set_profile_state(
                profile_name=profile_name,
                is_paused=True,
                paused_until=until,
                meta={"reason": reason, "set_by": "limit_monitor"},
            )
            logger.info(f"📝 [{profile_name}] Set pause until {until.strftime('%H:%M')}")
        except Exception as e:
            logger.error(f"Could not set pause for {profile_name}: {e}")

    def _clear_pause(self, profile_name: str):
        """Clear pause for profile in DB."""
        try:
            self.db.set_profile_state(
                profile_name=profile_name,
                is_paused=False,
                paused_until=None,
                meta={"action": "clear_pause", "set_by": "limit_monitor"},
            )
            logger.info(f"✅ [{profile_name}] Cleared pause")
        except Exception:
            pass

    def _get_profile_status(self, profile_name: str) -> dict:
        """Get status for single profile."""
        pause_until = self._get_pause_until(profile_name)
        now = datetime.now()

        if pause_until:
            if pause_until > now:
                remaining_min = int((pause_until - now).total_seconds() / 60)
                return {
                    "status": "PAUSED",
                    "until": pause_until.isoformat(),
                    "remaining_min": remaining_min,
                }
            # Pause expired
            return {"status": "AVAILABLE", "until": None, "remaining_min": 0}

        return {"status": "AVAILABLE", "until": None, "remaining_min": 0}

    def _check_all_profiles(self) -> dict[str, dict]:
        """Check status of all profiles."""
        profiles = self._get_profiles()
        statuses = {}

        for profile in profiles:
            statuses[profile] = self._get_profile_status(profile)

        return statuses

    def _deep_check_profile(self, profile_name: str) -> datetime | None:
        """
        Open browser for profile and check ACTUAL limit status.
        Returns reset time if limit active, None if no limit.
        """
        # Sanitize profile_name
        safe_profile = sanitize_profile_name(profile_name)
        if safe_profile != profile_name:
            logger.error(f"Invalid profile name: {profile_name}")
            return None

        # Safe join
        try:
            profile_path = safe_path_join(self.profiles_dir, safe_profile)
        except ValueError as e:
            logger.error(f"Security error resolving profile path: {e}")
            return None

        # deepcode ignore PT: profile_path validated with os.path.commonpath above - no traversal possible
        if not profile_path.exists():
            return None

        logger.info(f"🔍 [{profile_name}] Deep check - opening browser...")

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )

                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://gemini.google.com/app?hl=pl", wait_until="networkidle", timeout=30000)
                time.sleep(3)

                # Check for limit banner
                body_text = page.locator("body").inner_text(timeout=5000)

                if re.search(PRO_LIMIT_TEXT_RE, body_text):
                    # Extract reset time
                    # Use ProLimitHandler with our DB instance
                    handler = ProLimitHandler(profile_name, db_manager=self.db, pro_only=True)
                    reset_time = handler.extract_reset_datetime_from_text(body_text)

                    logger.warning(
                        f"⚠️ [{profile_name}] PRO LIMIT detected. Reset: {reset_time.strftime('%H:%M') if reset_time else 'unknown'}"
                    )
                    context.close()
                    return reset_time
                logger.info(f"✅ [{profile_name}] No limit detected")
                context.close()
                return None

        except Exception as e:
            logger.error(f"❌ [{profile_name}] Deep check failed: {e}")
            return None

    def _write_status(self, profiles_status: dict[str, dict]):
        """Write overall status to file."""
        try:
            available = [p for p, s in profiles_status.items() if s["status"] == "AVAILABLE"]
            paused = [p for p, s in profiles_status.items() if s["status"] == "PAUSED"]

            data = {
                "updated_at": datetime.now().isoformat(),
                "available_count": len(available),
                "paused_count": len(paused),
                "available_profiles": available,
                "paused_profiles": paused,
                "profiles": profiles_status,
            }
            self.status_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def run(self):
        """Main monitoring loop."""
        logger.info("🚀 Starting Limit Monitor")
        logger.info(f"   Profiles dir: {self.profiles_dir}")
        logger.info(f"   Check interval: {self.check_interval}s")
        logger.info(f"   Deep check interval: {self.deep_check_interval}s")

        profiles = self._get_profiles()
        logger.info(f"   Found {len(profiles)} profiles: {profiles}")

        while self.running:
            try:
                now = time.time()

                # Quick check - just verify pause files
                statuses = self._check_all_profiles()

                # Clear expired pauses
                for profile, status in statuses.items():
                    pause_until = self._get_pause_until(profile)
                    if pause_until and pause_until < datetime.now():
                        self._clear_pause(profile)
                        statuses[profile] = {
                            "status": "AVAILABLE",
                            "until": None,
                            "remaining_min": 0,
                        }

                # Log summary
                available = [p for p, s in statuses.items() if s["status"] == "AVAILABLE"]
                paused = [p for p, s in statuses.items() if s["status"] == "PAUSED"]

                if paused:
                    for p in paused:
                        s = statuses[p]
                        logger.info(f"⏸️ {p}: PAUSED for {s['remaining_min']}min")

                logger.info(f"📊 Status: {len(available)} available, {len(paused)} paused")

                # Deep check for profiles needing verification
                for profile in self._get_profiles():
                    last_deep = self.last_deep_check.get(profile, 0)
                    if now - last_deep > self.deep_check_interval:
                        reset_time = self._deep_check_profile(profile)
                        self.last_deep_check[profile] = now

                        if reset_time:
                            self._set_pause_until(profile, reset_time + timedelta(seconds=180))
                            statuses[profile] = {
                                "status": "PAUSED",
                                "until": reset_time.isoformat(),
                                "remaining_min": int(
                                    (reset_time - datetime.now()).total_seconds() / 60
                                ),
                            }
                        else:
                            self._clear_pause(profile)
                            statuses[profile] = {
                                "status": "AVAILABLE",
                                "until": None,
                                "remaining_min": 0,
                            }

                self._write_status(statuses)

                # Wait for next check
                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(10)

        logger.info("👋 Limit Monitor stopped")


def main():
    parser = argparse.ArgumentParser(description="Limit Monitor")
    parser.add_argument(
        "--profiles-dir",
        default=os.path.expanduser("~/.pw_profiles"),
        help="Directory containing browser profiles",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.path.expanduser("~/.cache/ocr-dashboard-v3"),
        help="Cache directory for pause files",
    )
    parser.add_argument(
        "--interval", type=int, default=300, help="Check interval in seconds (default: 300)"
    )
    parser.add_argument(
        "--deep-interval",
        type=int,
        default=3600,
        help="Deep browser check interval in seconds (default: 3600)",
    )

    args = parser.parse_args()

    # Security: Validate paths (handled in class init but good to fail fast here too, or just pass raw)
    # We pass raw paths to LimitMonitor which now uses validate_* functions

    try:
        monitor = LimitMonitor(
            profiles_dir=Path(os.path.expanduser(args.profiles_dir)),
            cache_dir=Path(os.path.expanduser(args.cache_dir)),
            check_interval=args.interval,
            deep_check_interval=args.deep_interval,
        )
        monitor.run()
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        return


if __name__ == "__main__":
    main()
