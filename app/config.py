"""
OCR Dashboard V2 - Configuration Module
Centralized configuration from environment variables.
"""

import os
from pathlib import Path


def _get_int(env_names: list[str], fallback: int) -> int:
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            try:
                return max(1, int(value))
            except Exception:
                continue
    return fallback


# Paths
BASE_DIR = Path(__file__).parents[1]
CACHE_DIR = Path.home() / ".cache" / "ocr-dashboard-v3"
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Server
SERVER_PORT = int(os.environ.get("OCR_DASHBOARD_PORT", "9090"))

# PostgreSQL
PG_DSN: str | None = os.environ.get("OCR_PG_DSN")

# Worker Defaults
DEFAULT_WORKERS = _get_int(["OCR_DEFAULT_WORKERS", "OCR_WORKERS"], 2)
DEFAULT_SCANS_PER_WORKER = _get_int(["OCR_DEFAULT_SCANS_PER_WORKER", "OCR_SCANS_PER_WORKER"], 2)

# Remote Worker Configuration (SSH)
REMOTE_HOST = os.environ.get("OCR_REMOTE_HOST")
REMOTE_USER = os.environ.get("OCR_REMOTE_USER") or os.environ.get("USER")
REMOTE_REPO_DIR = os.environ.get("OCR_REMOTE_REPO_DIR") or str(BASE_DIR)
REMOTE_SOURCE_DIR = os.environ.get("OCR_REMOTE_SOURCE_DIR")
REMOTE_SSH_OPTS = os.environ.get("OCR_REMOTE_SSH_OPTS", "-o BatchMode=yes -o ConnectTimeout=8")
REMOTE_ENABLED = bool(REMOTE_HOST)

# Remote Browser Configuration
REMOTE_BROWSER_HOST = os.environ.get("OCR_REMOTE_BROWSER_HOST")
REMOTE_BROWSER_USER = os.environ.get("OCR_REMOTE_BROWSER_USER") or os.environ.get("USER")
REMOTE_BROWSER_PROFILE_ROOT = os.environ.get(
    "OCR_REMOTE_BROWSER_PROFILE_ROOT",
    str(CACHE_DIR),
)
REMOTE_BROWSER_PYTHON = os.environ.get(
    "OCR_REMOTE_BROWSER_PYTHON",
    str(BASE_DIR / "venv" / "bin" / "python"),
)
REMOTE_BROWSER_PORT_BASE = os.environ.get("OCR_REMOTE_BROWSER_PORT_BASE", "9222")
REMOTE_BROWSER_PORT_SPAN = os.environ.get("OCR_REMOTE_BROWSER_PORT_SPAN", "400")
REMOTE_BROWSER_SSH_OPTS = os.environ.get(
    "OCR_REMOTE_BROWSER_SSH_OPTS",
    "-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no",
)
REMOTE_BROWSER_LOCAL_PORT_BASE = os.environ.get("OCR_REMOTE_BROWSER_LOCAL_PORT_BASE", "18222")
REMOTE_BROWSER_TUNNEL = os.environ.get("OCR_REMOTE_BROWSER_TUNNEL", "0")
REMOTE_BROWSER_CHROME_BIN = os.environ.get("OCR_REMOTE_BROWSER_CHROME_BIN")

# Limit Check
LIMIT_WORKER_URL = os.environ.get("LIMIT_WORKER_URL")
LIMIT_WORKER_URL_REMOTE = os.environ.get("LIMIT_WORKER_URL_REMOTE") or LIMIT_WORKER_URL

# Update Counts
UPDATE_COUNTS_ON_START = os.environ.get("OCR_UPDATE_COUNTS_ON_START", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
try:
    UPDATE_COUNTS_MIN_INTERVAL_SEC = int(
        os.environ.get("OCR_UPDATE_COUNTS_MIN_INTERVAL_SEC", "900").strip()
    )
except Exception:
    UPDATE_COUNTS_MIN_INTERVAL_SEC = 900
UPDATE_COUNTS_NOTIFY_ENABLED = os.environ.get(
    "OCR_UPDATE_COUNTS_NOTIFY_ENABLED", "1"
).strip().lower() in ("1", "true", "yes")
try:
    UPDATE_COUNTS_POLL_SEC = int(
        os.environ.get("OCR_UPDATE_COUNTS_POLL_SEC", "60").strip()
    )
except Exception:
    UPDATE_COUNTS_POLL_SEC = 60
UPDATE_COUNTS_POLL_SEC = max(0, UPDATE_COUNTS_POLL_SEC)
UPDATE_COUNTS_ON_NEW_PATHS = (
    os.environ.get("OCR_UPDATE_COUNTS_ON_NEW_PATHS", "1").strip().lower()
    in ("1", "true", "yes")
)

# Misc Flags
FORCE_ALL_PROFILES_PRO = os.environ.get("OCR_FORCE_ALL_PROFILES_PRO", "0") == "1"

# Cache Files
AUTO_RESTART_CONFIG_FILE = CACHE_DIR / "auto_restart_farm.json"
X11_DISPLAY_CONFIG_FILE = CACHE_DIR / "x11_display.json"
REMOTE_HOSTS_CONFIG_FILE = CACHE_DIR / "remote_hosts.json"
PROFILE_ALIASES_FILE = CACHE_DIR / "profile_aliases.json"
PRECHECK_STATUS_FILE = CACHE_DIR / "limit_precheck_status.json"
PRECHECK_HISTORY_FILE = CACHE_DIR / "limit_precheck_history.json"
UPDATE_COUNTS_TS_FILE = CACHE_DIR / "update_counts_last_run.txt"
UPDATE_COUNTS_SEEN_PATHS_FILE = CACHE_DIR / "update_counts_seen_paths.txt"
UPDATE_COUNTS_CONFIG_FILE = CACHE_DIR / "update_counts_config.json"
PENDING_CLEANUP_FILE = CACHE_DIR / "pending_cleanup.json"

# Optional per-profile proxy configuration
PROXIES_CONFIG_FILE = Path(
    os.environ.get("OCR_PROXIES_FILE", str(BASE_DIR / "config" / "proxies.json"))
)
