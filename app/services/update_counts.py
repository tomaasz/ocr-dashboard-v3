"""Run update_counts.sh on a safe cadence and when new source paths appear."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time

from ..config import (
    BASE_DIR,
    UPDATE_COUNTS_CONFIG_FILE,
    UPDATE_COUNTS_MIN_INTERVAL_SEC,
    UPDATE_COUNTS_NOTIFY_ENABLED,
    UPDATE_COUNTS_ON_NEW_PATHS,
    UPDATE_COUNTS_ON_START,
    UPDATE_COUNTS_POLL_SEC,
    UPDATE_COUNTS_SEEN_PATHS_FILE,
    UPDATE_COUNTS_TS_FILE,
)
from ..utils.db import execute_query

SCRIPT_PATH = BASE_DIR / "scripts" / "update_counts.sh"


def _read_last_run() -> float | None:
    """Read last run timestamp from cache file."""
    if not UPDATE_COUNTS_TS_FILE.exists():
        return None
    try:
        return float(UPDATE_COUNTS_TS_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_last_run(ts: float) -> None:
    """Persist last run timestamp to cache file."""
    try:
        UPDATE_COUNTS_TS_FILE.write_text(f"{ts:.0f}\n", encoding="utf-8")
    except Exception:
        pass


def _should_run(now: float) -> bool:
    """Check if update_counts should run based on cadence and config."""
    if not UPDATE_COUNTS_ON_START:
        return False
    last_run = _read_last_run()
    if last_run is None:
        return True
    return (now - last_run) >= UPDATE_COUNTS_MIN_INTERVAL_SEC


def run_update_counts_if_due() -> None:
    """Run update_counts.sh if configured and cadence allows."""
    now = time.time()
    if not _should_run(now):
        return
    if _run_script():
        _write_last_run(now)
        _refresh_seen_paths()


def _run_script() -> bool:
    """Run update_counts.sh and return True on success."""
    if not SCRIPT_PATH.exists():
        print(f"⚠️  update_counts.sh not found at {SCRIPT_PATH}")
        return False

    print("🔄 Running update_counts.sh...")
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print("✅ update_counts.sh completed.")
        return True
    print(
        "❌ update_counts.sh failed "
        f"(exit {result.returncode}). Stderr: {result.stderr.strip()}"
    )
    return False


def _load_seen_paths() -> set[str]:
    """Load previously seen source paths from cache."""
    if not UPDATE_COUNTS_SEEN_PATHS_FILE.exists():
        return set()
    try:
        content = UPDATE_COUNTS_SEEN_PATHS_FILE.read_text(encoding="utf-8")
    except Exception:
        return set()
    return {line.strip() for line in content.splitlines() if line.strip()}


def _save_seen_paths(paths: set[str]) -> None:
    """Persist seen source paths to cache."""
    try:
        UPDATE_COUNTS_SEEN_PATHS_FILE.write_text(
            "\n".join(sorted(paths)) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_update_counts_config() -> dict:
    """Load update counts settings from cache file."""
    if not UPDATE_COUNTS_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(UPDATE_COUNTS_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _get_effective_settings() -> tuple[bool, int]:
    """Return (on_new_paths, poll_sec) from config with env fallback."""
    data = _load_update_counts_config()
    on_new_paths = data.get("OCR_UPDATE_COUNTS_ON_NEW_PATHS", UPDATE_COUNTS_ON_NEW_PATHS)
    poll_sec = data.get("OCR_UPDATE_COUNTS_POLL_SEC", UPDATE_COUNTS_POLL_SEC)
    try:
        poll_value = int(poll_sec)
    except Exception:
        poll_value = UPDATE_COUNTS_POLL_SEC
    return _coerce_bool(on_new_paths), max(0, poll_value)


def _fetch_source_paths() -> set[str]:
    """Fetch distinct source paths from ocr_raw_texts."""
    rows = execute_query("SELECT DISTINCT source_path FROM ocr_raw_texts")
    paths: set[str] = set()
    for row in rows:
        if not row:
            continue
        value = str(row[0]).strip()
        if value:
            paths.add(value)
    return paths


def _has_new_source_paths() -> bool:
    """Check if new source paths appeared since last check."""
    current = _fetch_source_paths()
    if not current:
        return False
    seen = _load_seen_paths()
    new_paths = current - seen
    if not new_paths:
        return False
    _save_seen_paths(current)
    return True


def _refresh_seen_paths() -> None:
    """Persist current source paths to cache."""
    current = _fetch_source_paths()
    if current:
        _save_seen_paths(current)


def run_update_counts_if_new_paths() -> None:
    """Run update_counts.sh when new source paths appear in DB."""
    on_new_paths, _ = _get_effective_settings()
    if not on_new_paths:
        return
    if not _has_new_source_paths():
        return
    print("🆕 New source_path detected, running update_counts.sh...")
    if _run_script():
        _write_last_run(time.time())
        _refresh_seen_paths()


async def watch_new_source_paths() -> None:
    """Background watcher that triggers updates when new paths appear."""
    while True:
        _, poll_sec = _get_effective_settings()
        if poll_sec <= 0:
            await asyncio.sleep(30)
            continue
        await asyncio.to_thread(run_update_counts_if_new_paths)
        await asyncio.sleep(poll_sec)


def listen_new_source_paths() -> None:
    """Listen for DB NOTIFY events when new source_path appears."""
    if not UPDATE_COUNTS_NOTIFY_ENABLED:
        return

    try:
        import psycopg2
        import psycopg2.extensions
    except Exception as exc:
        print(f"⚠️  psycopg2 not available for LISTEN/NOTIFY: {exc!s}")
        return

    from ..config import PG_DSN

    if not PG_DSN:
        print("⚠️  OCR_PG_DSN not set; LISTEN/NOTIFY disabled.")
        return

    while True:
        try:
            conn = psycopg2.connect(PG_DSN)
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute("LISTEN ocr_new_source_path;")
            print("👂 Listening for ocr_new_source_path NOTIFY events...")

            while True:
                conn.poll()
                if not conn.notifies:
                    time.sleep(1)
                    continue
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    now = time.time()
                    if _should_run(now):
                        print(f"🆕 NOTIFY new source_path: {notify.payload}")
                        if _run_script():
                            _write_last_run(now)
                            _refresh_seen_paths()
        except Exception as exc:
            print(f"⚠️  LISTEN/NOTIFY error: {exc!s}. Reconnecting in 5s...")
            time.sleep(5)
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
