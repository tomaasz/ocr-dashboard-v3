"""
Pro limit handling module for OCR engine.

Manages Gemini Pro model rate limiting: detection, pause until reset,
and cross-process coordination via pause files.
"""

import logging
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# Month name mappings for date parsing
_PL_MONTHS = {
    "sty": 1,
    "lut": 2,
    "mar": 3,
    "kwi": 4,
    "maj": 5,
    "cze": 6,
    "lip": 7,
    "sie": 8,
    "wrz": 9,
    "paź": 10,
    "paz": 10,
    "lis": 11,
    "gru": 12,
}

_EN_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Regex for detecting Pro limit banners
PRO_LIMIT_TEXT_RE = re.compile(
    r"(Osiągnięto limit modelu Pro|Reached Pro model limit|Limit zostanie zresetowany|Limit resetuje się|Limit resets)",
    re.IGNORECASE,
)

PRO_MODEL_RE = re.compile(r"(\bPro\b|1\.5\s*Pro|2\.0\s*Pro)", re.IGNORECASE)


class ProLimitHandler:
    """Handles Pro model rate limiting detection and pause coordination."""

    def __init__(
        self,
        profile_name: str,
        db_manager: Any,  # DbLockingManager
        pro_only: bool = True,
        release_locks_callback: Callable[[], None] | None = None,
        page_reload_callback: Callable[[], None] | None = None,
    ):
        self.profile_name = profile_name
        self.db = db_manager
        self.pro_only = pro_only
        self._release_locks = release_locks_callback or (lambda: None)
        self._reload_page = page_reload_callback or (lambda: None)
        try:
            self.pause_buffer_sec = int(os.environ.get("OCR_PRO_PAUSE_BUFFER_SEC", "180").strip())
        except Exception:
            self.pause_buffer_sec = 180
        try:
            self.fallback_pause_min = int(
                os.environ.get("OCR_PRO_FALLBACK_PAUSE_MIN", "60").strip()
            )
        except Exception:
            self.fallback_pause_min = 60
        self.pause_buffer_sec = max(0, self.pause_buffer_sec)
        self.fallback_pause_min = max(5, self.fallback_pause_min)

    def get_pause_until(self) -> datetime | None:
        """Get the pause end time if active."""
        if not self.db:
            return None
        state = self.db.get_profile_state(self.profile_name)
        if state and state.get("is_paused") and state.get("pause_until"):
            return state["pause_until"]
        return None

    def set_pause_until(
        self,
        until: datetime,
        reason: str = "pro_limit",
        source: str | None = None,
        run_id: str | None = None,
        checked_at: datetime | None = None,
    ) -> None:
        """Set pause until specific time."""
        if not self.db:
            return

        try:
            meta = {
                "set_at": datetime.now().isoformat(),
                "pid": os.getpid(),
                "source": source,
                "run_id": run_id,
            }
            if checked_at:
                meta["checked_at"] = checked_at.isoformat()

            self.db.set_profile_state(
                self.profile_name, is_paused=True, pause_until=until, pause_reason=reason, meta=meta
            )
        except Exception as e:
            logger.warning(f"[PRO-ONLY] Cannot save pause state: {e}")

    def extract_reset_datetime_from_text(self, text: str) -> datetime | None:
        """
        Extract reset time from banner text.
        Supports Polish and English formats like '9 sty, 12:21' or '9 Jan, 12:21'.
        """
        try:
            # PL: "9 sty, 12:21"
            m = re.search(r"(\d{1,2})\s+([A-Za-ząćęłńóśźżŁŚŻŹĆĘÓŃ]+)\s*,\s*(\d{1,2}:\d{2})", text)
            if m:
                day = int(m.group(1))
                mon_raw = m.group(2).strip().lower().replace(".", "")
                mon = _PL_MONTHS.get(mon_raw[:3]) or _PL_MONTHS.get(mon_raw)
                if mon:
                    hh, mm = m.group(3).split(":")
                    now = datetime.now()
                    dt = datetime(now.year, mon, day, int(hh), int(mm), 0)
                    if dt < now - timedelta(minutes=5):
                        if (now - dt).days > 180:
                            dt = datetime(now.year + 1, mon, day, int(hh), int(mm), 0)
                        else:
                            dt = dt + timedelta(days=1)
                    return dt

            # EN: "9 Jan, 12:21"
            m2 = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s*,\s*(\d{1,2}:\d{2})", text)
            if m2:
                day = int(m2.group(1))
                mon_name = m2.group(2).strip().lower()[:3]
                mon = _EN_MONTHS.get(mon_name)
                if mon:
                    hh, mm = m2.group(3).split(":")
                    now = datetime.now()
                    dt = datetime(now.year, mon, day, int(hh), int(mm), 0)
                    if dt < now - timedelta(minutes=5):
                        if (now - dt).days > 180:
                            dt = datetime(now.year + 1, mon, day, int(hh), int(mm), 0)
                        else:
                            dt = dt + timedelta(days=1)
                    return dt
        except Exception:
            return None
        return None

    def pause_until(self, until: datetime, reason: str = "pro_limit") -> None:
        """Execute pause until specified time. Checks periodically for updates."""
        until_buf = until + timedelta(seconds=self.pause_buffer_sec)
        self.set_pause_until(until_buf, reason=reason)

        reset_time_str = until_buf.strftime("%H:%M")
        wait_seconds = max(60, int((until_buf - datetime.now()).total_seconds()))
        logger.warning(
            f"🛑 [PRO-ONLY] PAUSE until {reset_time_str} ({int(wait_seconds / 60)} min). Releasing locks..."
        )

        self._release_locks()

        last_logged_min = None
        check_interval = 30  # Check every 30 seconds for responsiveness

        while True:
            now = datetime.now()

            # Re-read pause file in case it was updated externally
            current_until = self.get_pause_until()
            if current_until and current_until < now:
                logger.info("✅ [PRO-ONLY] Pause ended (reset time passed). Resuming...")
                break
            if not current_until:
                logger.info("✅ [PRO-ONLY] Pause file cleared. Resuming...")
                break

            # Update target if changed
            if current_until != until_buf:
                until_buf = current_until
                logger.info(f"📝 [PRO-ONLY] Reset time updated to: {until_buf.strftime('%H:%M')}")

            remaining_sec = (until_buf - now).total_seconds()
            if remaining_sec <= 0:
                logger.info("✅ [PRO-ONLY] Wait complete. Resuming...")
                break

            remaining_min = int(remaining_sec / 60)
            if remaining_min != last_logged_min and (remaining_min % 5 == 0 or remaining_min <= 2):
                logger.info(f"⏳ [PRO-ONLY] Waiting... {remaining_min} min remaining.")
                last_logged_min = remaining_min

                # Reload page periodically to refresh session
                if remaining_min % 10 == 0 and remaining_min > 0:
                    try:
                        self._reload_page()
                    except Exception:
                        pass

            time.sleep(check_interval)

        # Final reload before resuming
        try:
            self._reload_page()
        except Exception:
            pass

        # Clear pause state after resume
        try:
            if self.db:
                self.db.set_profile_state(
                    self.profile_name,
                    is_paused=False,
                    pause_until=None,
                    pause_reason=None,
                    meta={"action": "resume", "pid": os.getpid()},
                )
        except Exception as e:
            logger.warning(f"[PRO-ONLY] Failed to clear pause state: {e}")

    def maybe_wait_for_pause(self) -> bool:
        """Wait if global pause is active. Returns True if waited."""
        until = self.get_pause_until()
        if not until:
            return False
        now = datetime.now(until.tzinfo) if until.tzinfo else datetime.now()
        if until <= now:
            return False
        self.pause_until(until, reason="global_pause")
        return True

    @staticmethod
    def is_pro_label(label: str) -> bool:
        """Check if model label indicates Pro model."""
        return bool(re.search(PRO_MODEL_RE, label or ""))

    def has_pro_limit_banner(self, page_text: str) -> bool:
        """Check if text contains Pro limit banner."""
        return bool(re.search(PRO_LIMIT_TEXT_RE, page_text or ""))

    def trigger_pause_from_text(self, text: str, context: str = "") -> None:
        """Parse reset time from text and trigger pause."""
        until = self.extract_reset_datetime_from_text(text or "")
        if not until:
            until = datetime.now() + timedelta(minutes=self.fallback_pause_min)

        ctx = f" ({context})" if context else ""
        logger.warning(
            f"⚠️ [PRO-ONLY] PRO LIMIT detected{ctx}. Reset ~ {until.strftime('%Y-%m-%d %H:%M')}."
        )

        # Log to database
        if self.db and hasattr(self.db, "log_critical_event"):
            try:
                self.db.log_critical_event(
                    profile_name=self.profile_name,
                    event_type="pro_limit_reached",
                    message=f"Reached Pro limit{ctx}. Reset at {until.strftime('%H:%M')}",
                    requires_action=False,  # Automated pause handles it
                    meta={"reset_time": until.isoformat(), "context": context},
                )
            except Exception:
                pass

        self.pause_until(until, reason="pro_limit")
