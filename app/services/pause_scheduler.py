"""
OCR Dashboard - Pause Scheduler Service
Periodically checks for profiles with expired pause_until and auto-starts them.
"""

import asyncio
import logging

from ..utils.db import pg_cursor

logger = logging.getLogger(__name__)

# Check every 30 seconds for expired pauses
_CHECK_INTERVAL_SEC = 30


def _get_expired_paused_profiles() -> list[str]:
    """Return profile names where is_paused=TRUE and pause_until <= NOW()."""
    with pg_cursor() as cur:
        if cur is None:
            return []
        try:
            cur.execute(
                """
                SELECT profile_name
                FROM public.profile_runtime_state
                WHERE is_paused = TRUE
                  AND pause_until IS NOT NULL
                  AND pause_until <= NOW()
                """
            )
            return [row[0] for row in cur.fetchall()]
        except Exception:
            logger.exception("Failed to query expired paused profiles")
            return []


def _clear_pause_state(profile_name: str) -> bool:
    """Clear the pause flag for a profile so it can be restarted."""
    with pg_cursor() as cur:
        if cur is None:
            return False
        try:
            cur.execute(
                """
                UPDATE public.profile_runtime_state
                SET is_paused = FALSE, pause_until = NULL, pause_reason = NULL,
                    last_updated = NOW()
                WHERE profile_name = %s AND is_paused = TRUE
                """,
                (profile_name,),
            )
            return bool(cur.rowcount)
        except Exception:
            logger.exception("Failed to clear pause state for %s", profile_name)
            return False


def resume_expired_profiles() -> list[str]:
    """Find profiles with expired pause_until, clear pause, and start them.

    Returns list of successfully resumed profile names.
    """
    from . import process as process_service

    expired = _get_expired_paused_profiles()
    if not expired:
        return []

    resumed: list[str] = []
    for profile_name in expired:
        if not _clear_pause_state(profile_name):
            continue

        ok, msg = process_service.start_profile_process(profile_name)
        if ok:
            logger.info("⏰ Auto-resumed paused profile '%s'", profile_name)
            resumed.append(profile_name)
        else:
            logger.warning(
                "⏰ Cleared pause for '%s' but start failed: %s", profile_name, msg
            )
            resumed.append(profile_name)

    return resumed


async def run_pause_scheduler() -> None:
    """Background loop that resumes profiles after their pause_until expires."""
    logger.info("⏰ Pause scheduler started (interval=%ds)", _CHECK_INTERVAL_SEC)
    while True:
        try:
            resumed = await asyncio.to_thread(resume_expired_profiles)
            if resumed:
                logger.info("⏰ Resumed %d profile(s): %s", len(resumed), ", ".join(resumed))
        except Exception:
            logger.exception("Pause scheduler error")
        await asyncio.sleep(_CHECK_INTERVAL_SEC)
