"""
Gemini OCR Engine - Modular Version

Clean orchestrator using extracted modules:
- browser_controller.py - Playwright operations
- db_locking.py - PostgreSQL locking
- pro_limit_handler.py - Rate limit handling
- image_processor.py - OpenCV preprocessing
- prompts.py - Prompt management
"""

import json
import logging
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import BrowserContext, Page

from ocr_engine.utils.activity_logger import ActivityLogger
from ocr_engine.utils.path_security import sanitize_profile_name, validate_profiles_dir

from .browser_controller import _PRO_MODEL_RE, GeminiBrowserController, SessionExpiredError
from .db_locking import DbLockingManager
from .image_processor import clear_temp_images, preprocess_image_smart
from .pro_limit_handler import PRO_LIMIT_TEXT_RE, ProLimitHandler
from .prompts import PromptManager
from .proxy_config import load_proxy_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


class FileStatus(Enum):
    """Status returned by _find_and_lock_next_file."""

    FOUND = "found"  # File available to process
    ALL_BUSY = "all_busy"  # Files exist but all locked by others
    ALL_DONE = "all_done"  # All files have been processed


@dataclass
class JobMeta:
    file_name: str
    source_path: str


@dataclass
class PageWorker:
    wid: int
    page: Page
    busy: bool = False
    image_path: Path | None = None
    prompt_text: str | None = None
    started_ts: float = 0.0
    done_count: int = 0
    card_id: str | None = None
    model_label: str | None = None
    last_capture_ts: float = 0.0
    context: BrowserContext | None = None  # Isolated context for this worker
    last_generating_log_ts: float = 0.0


class BrowserCrashedError(Exception):
    """Raised when the browser instance has crashed or closed unexpectedly."""

    pass


class GeminiEngine:
    """
    OCR Engine using Gemini Web via Playwright.

    Modular design using extracted components for better testability and maintenance.
    """

    def __init__(
        self,
        job_dir: str,
        prompt_id: str = "generic_json",
        profile_dir: str | None = None,
        headed: bool = False,
        pwdebug: bool = False,
        locale: str = "pl-PL",
        enable_video: bool = True,
    ):
        self.job_dir = Path(job_dir).resolve()
        self.prompt_id = prompt_id
        self.headed = headed
        self.pwdebug = pwdebug
        self.locale = locale
        self.enable_video = enable_video

        # Profile configuration
        profile_suffix = os.environ.get("OCR_PROFILE_SUFFIX", "").strip()

        # Security: Use centralized sanitization
        if profile_suffix:
            original_suffix = profile_suffix
            profile_suffix = sanitize_profile_name(profile_suffix)

            if original_suffix != profile_suffix:
                logger.warning(
                    f"⚠️ [Security] Sanitized OCR_PROFILE_SUFFIX: '{original_suffix}' -> '{profile_suffix}'"
                )

        self.active_profile_name = profile_suffix if profile_suffix else "default"
        dir_name = f"gemini-profile-{profile_suffix}" if profile_suffix else "gemini-profile"

        if not profile_dir:
            # Check for explicit env override (e.g. for shared WSL/Windows profiles)
            env_profile_dir = os.environ.get("OCR_PROFILE_DIR")
            if env_profile_dir:
                try:
                    self.profile_dir = validate_profiles_dir(env_profile_dir)
                except ValueError as e:
                    logger.warning(f"⚠️ [Security] Invalid OCR_PROFILE_DIR: {e}. Using default.")
                    self.profile_dir = validate_profiles_dir() / dir_name
            else:
                self.profile_dir = validate_profiles_dir() / dir_name
        else:
            try:
                self.profile_dir = Path(profile_dir).resolve()
                # Basic check if provided explicitly
                if not self.profile_dir.is_absolute():
                    logger.warning(
                        f"⚠️ [Security] Relative profile_dir provided: {profile_dir}. Resolving to Absolute."
                    )
                    self.profile_dir = self.profile_dir.resolve()
            except Exception as e:
                logger.error(f"❌ [Security] Invalid profile_dir: {e}")
                raise

        logger.info(f"Using browser profile: {self.profile_dir} (ID: {self.active_profile_name})")

        # Directories
        self.ocr_dir = self.job_dir / "ocr"
        self.artifacts_dir = self.ocr_dir / "artifacts"
        self.status_file = self.ocr_dir / "status.json"
        self.job_file = self.job_dir / "job.json"
        self.temp_img_dir = self.ocr_dir / "temp_images"
        self.progress_file = self.ocr_dir / "progress.json"

        # Create directories
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.temp_img_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir = self.artifacts_dir / "traces"
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.live_dir = self.artifacts_dir / "live"
        if self.live_dir.exists():
            for f in self.live_dir.iterdir():
                try:
                    f.unlink()
                except Exception:
                    pass

        # Worker pool config - defaults can be overridden via OCR_DEFAULT_* or OCR_*.
        # windows = number of Chrome windows, tabs_per_window = tabs in each window
        # Total workers = windows × tabs_per_window
        try:
            self.workers_count = max(
                1,
                int(
                    os.environ.get("OCR_WINDOWS")
                    or os.environ.get("OCR_WORKERS")
                    or os.environ.get("OCR_DEFAULT_WORKERS")
                    or "2"
                ),
            )
        except Exception:
            self.workers_count = 2
        try:
            self.tabs_per_window = max(
                1,
                int(os.environ.get("OCR_TABS_PER_WINDOW") or "2"),
            )
        except Exception:
            self.tabs_per_window = 2
        try:
            self.scans_per_worker = max(
                1,
                int(
                    os.environ.get("OCR_SCANS_PER_WORKER")
                    or os.environ.get("OCR_DEFAULT_SCANS_PER_WORKER")
                    or "2"
                ),
            )
        except Exception:
            self.scans_per_worker = 2

        # Resolve source directory using standardized mounts
        _source_raw = os.environ.get("OCR_SOURCE_DIR", "")
        if _source_raw:
            try:
                from ocr_engine.utils.source_resolver import resolve_source_dir

                self.source_dir = resolve_source_dir(_source_raw)
            except Exception:
                # Fallback: treat as raw path
                self.source_dir = Path(_source_raw).resolve()
        else:
            self.source_dir = Path.cwd()

        # Feature flags
        self.pg_enabled = os.environ.get("OCR_PG_ENABLED", "0").strip() == "1"
        self.pg_table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
        self.batch_id = os.environ.get("OCR_BATCH_ID") or time.strftime("batch_%Y%m%d_%H%M%S")
        self.continue_mode = os.environ.get("OCR_CONTINUE", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        self.continuous_mode = os.environ.get("OCR_CONTINUOUS", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self.browser_id = os.environ.get("OCR_BROWSER_ID") or f"pw_{os.getpid()}_{int(time.time())}"
        self.pro_only = os.environ.get("OCR_PRO_ONLY", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        self.execution_mode = os.environ.get("OCR_EXECUTION_MODE", "local").strip() or "local"
        # Auto-advance is controlled by OCR_AUTO_ADVANCE (defaults to off if unset).
        self.auto_advance = os.environ.get("OCR_AUTO_ADVANCE", "0").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self.use_db_counts = os.environ.get("OCR_USE_DB_COUNTS", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        try:
            self.counts_max_age_sec = int(os.environ.get("OCR_COUNTS_MAX_AGE_SEC", "3600").strip())
        except Exception:
            self.counts_max_age_sec = 3600

        # Timeout config - increased to 400s to handle complex documents
        try:
            self.collect_timeout_sec = int(os.environ.get("OCR_COLLECT_TIMEOUT_SEC", "400").strip())
        except Exception:
            self.collect_timeout_sec = 400
        self.collect_timeout_sec = max(30, self.collect_timeout_sec)
        self.collect_timeout_ms = self.collect_timeout_sec * 1000

        try:
            self.startup_retries = int(os.environ.get("OCR_STARTUP_RETRIES", "3").strip())
        except Exception:
            self.startup_retries = 3
        self.startup_retries = max(1, self.startup_retries)
        try:
            self.startup_retry_base_sec = int(
                os.environ.get("OCR_STARTUP_RETRY_BASE_SEC", "5").strip()
            )
        except Exception:
            self.startup_retry_base_sec = 5
        self.startup_retry_base_sec = max(1, self.startup_retry_base_sec)

        # Initialize modules
        self.db = DbLockingManager(
            pg_table=self.pg_table,
            profile_name=self.active_profile_name,
            enabled=self.pg_enabled,
        )
        self._synced_source_paths: set[str] = set()

        if self.pg_enabled:
            self.db.init_lock_table()
            self.db.init_token_usage_table()
            self.db.init_error_traces_table()
            self.db.init_artifacts_table()
            self.db.init_critical_events_table()
            retention_hours = int(
                os.environ.get("OCR_ARTIFACT_RETENTION_HOURS", "24").strip() or "24"
            )
            self.db.cleanup_old_artifacts(retention_hours)

        self.limit_handler = ProLimitHandler(
            profile_name=self.active_profile_name,
            db_manager=self.db if self.pg_enabled else None,
            pro_only=self.pro_only,
            release_locks_callback=self.db.release_all_my_locks,
        )

        prompts_file = Path(__file__).parents[1] / "prompts" / "gemini_prompts.json"
        self.prompt_manager = PromptManager(prompts_file)

        # Remote browser selection (per-profile)
        remote_enabled = os.environ.get("OCR_REMOTE_BROWSER_ENABLED", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "y",
        )
        excluded = {
            p.strip()
            for p in os.environ.get("OCR_REMOTE_BROWSER_EXCLUDE_PROFILES", "").split(",")
            if p.strip()
        }
        remote_for_profile = remote_enabled and self.active_profile_name not in excluded

        # Proxy Configuration
        self.proxy_config = None
        if not remote_for_profile:
            proxies_file = Path("config/proxies.json").resolve()
            if not proxies_file.exists():
                proxies_file = self.job_dir.parent / "config" / "proxies.json"

            self.proxy_config = load_proxy_config(self.active_profile_name, proxies_file)
            if self.proxy_config:
                safe_proxy = self.proxy_config.copy()
                if "password" in safe_proxy:
                    safe_proxy["password"] = "***"
                logger.info(
                    f"🌐 [Proxy] Using proxy for profile '{self.active_profile_name}': {safe_proxy}"
                )

        video_dir = Path(self.artifacts_dir / "video") if enable_video else None
        if video_dir:
            video_dir.mkdir(parents=True, exist_ok=True)

        self.browser = GeminiBrowserController(
            profile_dir=self.profile_dir,
            headed=headed,
            locale=locale,
            enable_video=enable_video,
            video_dir=video_dir,
            proxy_config=self.proxy_config,
            db_manager=self.db if self.pg_enabled else None,
        )

        # Remote browser (offload Chrome to another host)
        if remote_for_profile:
            self.browser.remote_enabled = True
            self.browser.remote_host = os.environ.get("OCR_REMOTE_BROWSER_HOST")
            self.browser.remote_user = os.environ.get("OCR_REMOTE_BROWSER_USER")
            self.browser.remote_profile_root = os.environ.get("OCR_REMOTE_BROWSER_PROFILE_ROOT")
            self.browser.remote_python = os.environ.get("OCR_REMOTE_BROWSER_PYTHON", "python3")
            self.browser.remote_port_base = int(
                os.environ.get("OCR_REMOTE_BROWSER_PORT_BASE", "9222")
            )
            self.browser.remote_port_span = int(
                os.environ.get("OCR_REMOTE_BROWSER_PORT_SPAN", "100")
            )
            self.browser.remote_local_port_base = int(
                os.environ.get("OCR_REMOTE_BROWSER_LOCAL_PORT_BASE", "9222")
            )
            self.browser.remote_ssh_opts = os.environ.get(
                "OCR_REMOTE_BROWSER_SSH_OPTS", "-o StrictHostKeyChecking=no"
            )
            self.browser.remote_tunnel_enabled = os.environ.get(
                "OCR_REMOTE_BROWSER_TUNNEL", "1"
            ) not in ("0", "false", "no", "n")
            if os.environ.get("OCR_REMOTE_BROWSER_CHROME_BIN"):
                self.browser.remote_chrome_bin = os.environ.get("OCR_REMOTE_BROWSER_CHROME_BIN")

        # Runtime state
        self.workers: list[PageWorker] = []
        self.current_stage = "init"
        self._processed_local: set[str] = set()
        self._inflight_local: set[str] = set()
        # Increased thread pool for preprocessing + preload ahead
        estimated_workers = self.workers_count * self.tabs_per_window
        self._bg_pool = ThreadPoolExecutor(max_workers=max(4, estimated_workers * 2))
        # Preload queue: {filename: Future} - preprocess next files ahead
        self._preload_queue: dict = {}
        # Queue cursor + DB done cache for faster continuation scans
        self._scan_cursor = 0
        self._scan_cursor_source_dir: str | None = None
        self._db_done_cache: set[str] = set()
        self._db_done_cache_ts = 0.0
        self._db_done_cache_source_dir: str | None = None
        try:
            self.done_cache_ttl_sec = int(os.environ.get("OCR_DONE_CACHE_TTL_SEC", "30"))
        except Exception:
            self.done_cache_ttl_sec = 30
        self.done_cache_ttl_sec = max(1, self.done_cache_ttl_sec)

        # Initialize
        # DB Init moved to __init__ start to ensure tables exist before handler use
        self._load_local_progress()

        if os.environ.get("OCR_CLEAN_TEMP_IMAGES", "1").strip() == "1":
            clear_temp_images(self.temp_img_dir)

        self.last_live_preview_ts = 0
        self.last_limit_check_ts = 0  # For periodic limit verification
        self._session_retry_count = 0
        self._limit_retry_count = 0
        self.limit_check_interval_sec = int(os.environ.get("OCR_LIMIT_CHECK_INTERVAL", "1800"))
        self.auth_ensure_enabled = os.environ.get(
            "OCR_AUTH_ENSURE_ENABLED", "1"
        ).strip().lower() not in ("0", "false", "no")
        try:
            self.auth_ensure_interval_sec = int(
                os.environ.get("OCR_AUTH_ENSURE_INTERVAL_SEC", "900").strip()
            )
        except Exception:
            self.auth_ensure_interval_sec = 900
        self.auth_ensure_interval_sec = max(60, self.auth_ensure_interval_sec)
        self.last_auth_ensure_ts = 0.0
        self.close_idle_tabs = os.environ.get("OCR_CLOSE_IDLE_TABS", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        try:
            self.max_tabs_per_context = int(os.environ.get("OCR_MAX_TABS_PER_CONTEXT", "0").strip())
        except Exception:
            self.max_tabs_per_context = 0
        self.max_tabs_per_context = max(0, self.max_tabs_per_context)

        # Model verification configuration
        try:
            self.model_check_interval_sec = int(os.environ.get("OCR_MODEL_CHECK_INTERVAL", "300"))
        except Exception:
            self.model_check_interval_sec = 300  # Default: 5 minutes
        self.model_check_interval_sec = max(0, self.model_check_interval_sec)
        self.last_model_check_ts = 0

        # Activity logging for dashboard visibility
        self.activity_logger = ActivityLogger()
        self._run_start_time: float = 0.0
        self._activity_started = False
        self._activity_stopped = False
        self._exit_code = 0
        self._exit_error_message: str | None = None

        # Artifact cleanup configuration
        try:
            self.artifact_cleanup_interval_sec = int(
                os.environ.get("OCR_ARTIFACT_CLEANUP_INTERVAL", "600").strip()
            )
        except Exception:
            self.artifact_cleanup_interval_sec = 600  # Default: 10 minutes
        self.artifact_cleanup_interval_sec = max(60, self.artifact_cleanup_interval_sec)
        self.last_artifact_cleanup_ts = 0

    # ---- Main Run Loop ----

    def run(self) -> int:
        self._exit_code = 0
        self._exit_error_message = None
        try:
            if self.pwdebug:
                os.environ["PWDEBUG"] = os.environ.get("PWDEBUG", "1")

            # CRITICAL: Clear outdated pause state on startup
            if self.pg_enabled and self.limit_handler.db:
                # We assume starting fresh means we want to re-verify limits
                # unless a very recent pause exists (handled in limit_handler getter)
                # For now, just logging start
                logger.info(f"🚀 [Startup] Engine starting for profile: {self.active_profile_name}")

            if not self.continue_mode:
                logger.info("[Continue] OCR_CONTINUE=0 -> reset local progress.")
                self._processed_local = set()
                self._inflight_local = set()
                self._save_local_progress()

            startup_ok = False
            for attempt in range(1, self.startup_retries + 1):
                try:
                    self.browser.start()
                    logger.info("✅ [Startup] Browser launched successfully.")
                    self._session_retry_count = 0
                    self._init_pages()
                    logger.info(
                        f"✅ [Startup] All {len(self.workers)} worker tabs created and ready."
                    )
                    startup_ok = True
                    self._run_start_time = time.time()
                    # Log successful start to activity log
                    self.activity_logger.log_start(
                        component="profile_worker",
                        profile_name=self.active_profile_name,
                        reason=f"Started OCR engine - workers={self.workers_count}, continuous={self.continuous_mode}",
                    )
                    self._activity_started = True
                    if self.pg_enabled:
                        self.db.set_profile_state(
                            profile_name=self.active_profile_name,
                            active_worker_pid=os.getpid(),
                            current_action="running",
                        )
                    break
                except SessionExpiredError:
                    if self.browser.context and self.browser.context.pages:
                        if self._capture_session_screenshot(
                            self.browser.context.pages[0], "startup"
                        ):
                            raise
                    wait_s = self._next_backoff_seconds(
                        self._session_retry_count, base=self.startup_retry_base_sec, cap=60
                    )
                    logger.warning(
                        f"⚠️ [Session] Screenshot missing, retrying browser start in {wait_s}s (no proof)."
                    )
                    try:
                        self.browser.close()
                    except Exception:
                        pass
                    time.sleep(wait_s)
                except Exception as e:
                    wait_s = self._next_backoff_seconds(
                        attempt, base=self.startup_retry_base_sec, cap=60
                    )
                    logger.warning(
                        f"⚠️ [Startup] Attempt {attempt}/{self.startup_retries} failed: {e}"
                    )
                    try:
                        self.browser.close()
                    except Exception:
                        pass
                    if attempt < self.startup_retries:
                        logger.info(f"🔁 [Startup] Retrying browser start in {wait_s}s...")
                        time.sleep(wait_s)
            if not startup_ok:
                # Log startup failure before raising exception
                self.activity_logger.log_stop(
                    component="profile_worker",
                    profile_name=self.active_profile_name,
                    exit_code=1,
                    error_message=f"Startup failed after {self.startup_retries} retries",
                    reason="Browser startup failed",
                )
                self._activity_stopped = True
                self._exit_error_message = f"Startup failed after {self.startup_retries} retries"
                self._exit_code = 2
                raise RuntimeError("Startup failed after retries")

            # CRITICAL: Verify limit status from ACTUAL page before starting
            if self.pro_only and self._verify_limit_on_start():
                logger.info("🔄 [Startup] Limit active - will wait for reset.")
                self.limit_handler.maybe_wait_for_pause()

            while True:
                self._ensure_source_dir_indexed()
                if self._should_skip_dir_via_db_stats():
                    next_dir = self._get_next_source_dir()
                    if next_dir:
                        logger.info(f"➡️ [AutoAdvance] Switching source to: {next_dir}")
                        self.source_dir = next_dir.resolve()
                        continue
                    self._exit_code = 0
                    return self._exit_code

                all_files = self._get_images_from_source_dir()
                if not all_files:
                    if self.auto_advance:
                        next_dir = self._get_next_source_dir()
                        if next_dir:
                            logger.info(f"➡️ [AutoAdvance] Empty folder, switching to: {next_dir}")
                            self.source_dir = next_dir.resolve()
                            continue
                    logger.error(f"No images in source directory: {self.source_dir}")
                    self._exit_code = 1
                    return self._exit_code

                for w in self.workers:
                    w.done_count = 0
                    w.busy = False
                    w.image_path = None
                    w.prompt_text = None

                total_workers = max(1, len(self.workers))
                total_limit = total_workers * self.scans_per_worker
                if self.continuous_mode:
                    total_limit = 999999  # Effectively unlimited

                logger.info(
                    f"🚀 START: {self.source_dir} | Limit={total_limit if not self.continuous_mode else 'CONTINUOUS'} "
                    f"(workers={total_workers} "
                    f"[windows={self.workers_count} tabs={self.tabs_per_window}] x scans={self.scans_per_worker}) | "
                    f"continue={self.continue_mode} | continuous={self.continuous_mode} | pg={self.pg_enabled} | profile={self.active_profile_name}"
                )

                if not self.pg_enabled:
                    logger.info(
                        f"🧾 [LocalProgress] processed={len(self._processed_local)}, inflight={len(self._inflight_local)}"
                    )

                processed_in_this_run = 0
                all_done = False
                no_file_retries = 0  # Counter for ALL_BUSY retries

                while processed_in_this_run < total_limit and not all_done:
                    # PRO-ONLY: check global pause
                    if self.pro_only and self.limit_handler.maybe_wait_for_pause():
                        continue

                    # Periodic model verification (check drift)
                    if self.pro_only and self.model_check_interval_sec > 0:
                        now_ts = time.time()
                        if now_ts - self.last_model_check_ts > self.model_check_interval_sec:
                            self.last_model_check_ts = now_ts
                            for w in self.workers:
                                if not w.busy:
                                    # Only verify idle workers to avoid disrupting active jobs
                                    self._verify_worker_model(w)

                    # 1) Collect completed
                    for w in self.workers:
                        if w.busy:
                            if self._worker_try_collect(w):
                                processed_in_this_run += 1
                                no_file_retries = 0  # Reset retry counter on success

                    # 2) CRITICAL: Check ALL tabs for Pro limit (not just active worker tabs)
                    if self.pro_only and self._check_all_tabs_for_limit():
                        logger.critical(
                            "🛑 [Limit] PRO LIMIT detected in multi-tab scan. Shutting down ALL workers."
                        )
                        # Stop all workers immediately
                        for w in self.workers:
                            if w.busy:
                                w.busy = False
                                logger.info(f"[W{w.wid}] ⏸️ Stopped due to global limit detection.")
                        break  # Exit main loop

                    # 3) Assign new work
                    free_workers = [
                        w
                        for w in self.workers
                        if not w.busy
                        and (self.continuous_mode or w.done_count < self.scans_per_worker)
                    ]
                    if not free_workers:
                        if not self.continuous_mode and all(
                            w.done_count >= self.scans_per_worker for w in self.workers
                        ):
                            break
                        time.sleep(0.4)
                        continue

                    # Refresh done files from DB frequently to stay in sync with other profiles
                    if self.pg_enabled:
                        self._load_local_progress()

                    files_assigned = False
                    for w in free_workers:
                        if self._check_pro_limit(w.page):
                            continue

                        status, next_file = self._find_and_lock_next_file(all_files)

                        if status == FileStatus.ALL_DONE:
                            logger.info("✅ [Queue] All files have been processed!")
                            all_done = True
                            break
                        if status == FileStatus.ALL_BUSY:
                            if self.auto_advance:
                                logger.info(
                                    "⏩ [Queue] Current folder is fully busy (locked by others). Auto-advancing."
                                )
                                all_done = True
                                break

                            no_file_retries += 1
                            if no_file_retries >= 30:  # ~15 seconds of waiting
                                logger.warning(
                                    "⏳ [Queue] All files locked by others for too long. Waiting..."
                                )
                                no_file_retries = 0
                            break  # Wait for other profiles to finish
                        if status == FileStatus.FOUND and next_file:
                            files_assigned = True
                            prompt_text = self._setup_prompt(next_file)
                            self._worker_start(w, next_file, prompt_text)
                            time.sleep(0.15)

                    # If all files busy, wait a bit longer before retry
                    if not files_assigned and not all_done:
                        time.sleep(0.5)

                    # Preload next files for faster processing
                    self._preload_next_files(all_files, count=total_workers)

                    # Clean up idle tabs periodically (every ~10 iterations)
                    if processed_in_this_run % 10 == 0:
                        self._close_idle_tabs()

                        # Log heartbeat with detailed worker status
                        active_count = sum(1 for w in self.workers if w.busy)
                        busy_workers = [
                            f"W{w.wid}:{w.image_path.name if w.image_path else '?'}"
                            for w in self.workers
                            if w.busy
                        ]
                        busy_info = f" [{', '.join(busy_workers)}]" if busy_workers else ""
                        logger.info(
                            f"💓 [Heartbeat] Active: {active_count}/{total_workers}{busy_info} | Done: {len(self._processed_local)}"
                        )

                        # Periodic limit verification (every hour)
                        if self.pro_only:
                            self._periodic_limit_check()

                        # Periodic artifact cleanup
                        if self.pg_enabled:
                            self._periodic_artifact_cleanup()

                    self._auth_ensure("run_loop")

                    time.sleep(0.35)

                    # Live Preview Updates (every ~5s)
                    self._update_live_previews()

                # Finish remaining
                while any(w.busy for w in self.workers):
                    for w in self.workers:
                        if w.busy:
                            self._worker_try_collect(w)
                    self._update_live_previews()
                    time.sleep(0.5)

                # Write appropriate final status
                if all_done:
                    self._write_status("DONE", "finished")
                else:
                    logger.warning(
                        f"🛑 [RunLimit] Per-run limit reached: processed={processed_in_this_run}/{total_limit} "
                        f"(workers={total_workers} x scans={self.scans_per_worker}). "
                        "Set OCR_CONTINUOUS=1 or increase OCR_SCANS_PER_WORKER to continue without stopping."
                    )
                    # Partial completion - hit per-run limit but files remain
                    self._write_status("PARTIAL", "run_limit_reached")

                if all_done and self.auto_advance:
                    next_dir = self._get_next_source_dir()
                    if next_dir:
                        logger.info(f"➡️ [AutoAdvance] Switching source to: {next_dir}")
                        self.db.release_all_my_locks()
                        if not self.pg_enabled:
                            self._processed_local = set()
                            self._inflight_local = set()
                            self._save_local_progress()
                        self.source_dir = next_dir.resolve()
                        continue
                self._exit_code = 0
                return self._exit_code

        except BrowserCrashedError as e:
            logger.warning(f"🔄 [Restart] Browser crash detected: {e}. Requesting restart...")
            self.db.release_all_my_locks()
            self._close()
            # Return magic code 100 to signal run.py to restart the process/engine
            self._exit_code = 100
            return 100

        except Exception as e:
            logger.error(f"FAIL: {e}\n{traceback.format_exc()}")
            self.db.release_all_my_locks()
            self._write_status("FAIL", self.current_stage, error={"message": str(e)})
            self._exit_error_message = str(e)
            self._exit_code = 2
            return self._exit_code
        finally:
            if self.pg_enabled:
                try:
                    self.db.set_profile_state(
                        profile_name=self.active_profile_name,
                        active_worker_pid=None,
                        current_action="stopped",
                    )
                except Exception:
                    pass
            if self._activity_started and not self._activity_stopped:
                reason = "Run completed" if self._exit_code == 0 else "Run stopped"
                try:
                    self.activity_logger.log_stop(
                        component="profile_worker",
                        profile_name=self.active_profile_name,
                        exit_code=self._exit_code,
                        error_message=self._exit_error_message,
                        reason=reason,
                    )
                except Exception:
                    pass
                self._activity_stopped = True
            self._close()

    def _ensure_source_dir_indexed(self) -> None:
        """Ensure folder_file_entries is populated when starting a new folder.

        Uses cached index from background folder_indexer if available and recent.
        Only does full scan if cache is stale (> 30 min) or missing.
        """
        if not self.pg_enabled:
            return
        source_key = str(self.source_dir)
        if source_key in self._synced_source_paths:
            return

        try:
            # Check if we have recent cached index from background indexer
            stats = self.db.get_source_path_stats(source_key)
            if stats and stats.get("last_updated"):
                from datetime import datetime, timezone

                last_updated = stats["last_updated"]
                if hasattr(last_updated, "tzinfo") and last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                age_minutes = (now - last_updated).total_seconds() / 60

                if age_minutes < 30:
                    # Use cached index - no need to rescan
                    self._synced_source_paths.add(source_key)
                    logger.info(
                        f"📂 [Counts] Using cached index ({stats.get('files_on_disk', '?')} files, "
                        f"{age_minutes:.0f}m old) for {source_key}"
                    )
                    return
                else:
                    logger.info(f"📂 [Counts] Cache stale ({age_minutes:.0f}m), re-indexing...")

            # No cache or stale - do full scan
            count = self.db.sync_folder_entries(source_key)
            self._synced_source_paths.add(source_key)
            logger.info(f"📂 [Counts] Indexed {count} files for {source_key}")
        except Exception as e:
            logger.warning(f"[Counts] Failed to index {source_key}: {e}")

    def _update_live_previews(self) -> None:
        """Capture screenshots of busy workers for live dashboard."""
        now = time.time()
        if now - self.last_live_preview_ts < 5.0:
            return

        self.last_live_preview_ts = now
        # Changed: Use the centralized ui_health directory required by the dashboard
        live_dir = Path("artifacts/screenshots/ui_health")
        live_dir.mkdir(parents=True, exist_ok=True)

        for w in self.workers:
            if not w.page:
                continue
            now = time.time()
            should_capture = w.busy
            if not should_capture:
                should_capture = (
                    now - w.last_capture_ts
                ) >= 30.0  # refresh periodically even if idle
            if not should_capture:
                continue

            # Only snapshot if actually busy processing or due to periodic refresh
            try:
                # Namespace by profile to avoid collisions
                safe_profile = sanitize_profile_name(self.active_profile_name)
                # Format: ui_health_TIMESTAMP_PROFILE.png
                filename = f"ui_health_{int(now)}_{safe_profile}.png"
                path = live_dir / filename

                # Use PNG as required by dashboard
                w.page.screenshot(path=path, type="png")
                w.last_capture_ts = now
            except Exception:
                pass

    def _auth_ensure(self, context: str = "", force: bool = False) -> None:
        if not self.auth_ensure_enabled:
            return

        # CRITICAL: Skip auth_ensure during startup to avoid interfering with freshly initialized workers
        # Workers are already validated by wait_for_ui_ready in _init_pages
        if context == "startup":
            logger.info(
                "[AuthEnsure] Skipping session check during startup (workers already validated)"
            )
            return

        now = time.time()
        if not force and (now - self.last_auth_ensure_ts) < self.auth_ensure_interval_sec:
            return
        self.last_auth_ensure_ts = now

        page = next((w.page for w in self.workers if w.page), None)
        if not page and self.browser.context and self.browser.context.pages:
            page = self.browser.context.pages[0]
        if not page:
            return

        try:
            ok = self.browser.ensure_session(page, context=context or "loop")
            if not ok:
                logger.warning(f"⚠️ [AuthEnsure] UI/session not healthy ({context}).")
        except SessionExpiredError:
            self._capture_session_screenshot(page, f"auth_ensure_{context}", attempts=3)
            raise

    # ---- Worker Methods ----

    def _init_pages(self) -> None:
        if not self.browser.context:
            raise Exception("Browser context not initialized")

        self.workers = []

        tabs_per_window = max(1, self.tabs_per_window)

        if self.browser.use_isolated_contexts:
            # Create workers with isolated contexts (if enabled)
            context_offsets: dict[BrowserContext, int] = {}
            for window_id in range(1, self.workers_count + 1):
                # Create isolated context for this worker window
                context = self.browser.create_worker_context(worker_id=window_id)

                # Ensure enough tabs for this window, accounting for pooled contexts
                used = context_offsets.get(context, 0)
                required = used + tabs_per_window
                while len(context.pages) < required:
                    try:
                        context.new_page()
                    except Exception:
                        break

                pages = list(context.pages)
                window_pages = pages[used:required]
                context_offsets[context] = used + len(window_pages)
                for tab_idx, page in enumerate(window_pages, start=1):
                    page.set_default_timeout(30_000)
                    worker_id = (window_id - 1) * tabs_per_window + tab_idx
                    worker = PageWorker(wid=worker_id, page=page, context=context)
                    self.workers.append(worker)
                    logger.info(
                        f"[Init] Created worker {worker_id} (window={window_id} tab={tab_idx}) "
                        f"with isolated context ({len(context.pages)} tabs)"
                    )
        else:
            # Shared context: allocate tabs across all windows
            context = self.browser.context
            required_pages = self.workers_count * tabs_per_window

            while len(context.pages) < required_pages:
                try:
                    context.new_page()
                except Exception:
                    break

            pages = list(context.pages)
            for idx in range(min(required_pages, len(pages))):
                page = pages[idx]
                page.set_default_timeout(30_000)
                window_id = (idx // tabs_per_window) + 1
                tab_idx = (idx % tabs_per_window) + 1
                worker_id = idx + 1
                worker = PageWorker(wid=worker_id, page=page, context=context)
                self.workers.append(worker)
                logger.info(
                    f"[Init] Created worker {worker_id} (window={window_id} tab={tab_idx}) "
                    f"with shared context ({len(context.pages)} tabs)"
                )

        # Initialize all workers in parallel for faster startup
        def _init_single_worker(w: PageWorker) -> PageWorker:
            """Initialize a single worker (goto + wait_for_ui_ready)."""
            try:
                w.page.goto("https://gemini.google.com/app?hl=pl", wait_until="domcontentloaded")
            except Exception as e:
                reason = "Page.goto timeout" if "Timeout" in str(e) else "Page.goto failed"
                logger.error(f"❌ [Startup] W{w.wid} {reason}: {e}")
                raise

            while True:
                try:
                    self.browser.wait_for_ui_ready(w.page)
                    self._session_retry_count = 0

                    # Model enforcement via UI is DISABLED.
                    # @Pro prefix is prepended to every prompt (see _worker_start_inner),
                    # which is faster and doesn't get blocked by disabled Pro button (rate limits).
                    if self.pro_only:
                        logger.info(
                            f"[W{w.wid}] 🧠 Pro model will be set via @Pro prompt prefix (skip UI enforce)"
                        )

                    return w  # Success!

                except SessionExpiredError:
                    if self._capture_session_screenshot(w.page, "wait_for_ui_ready"):
                        raise
                    wait_s = self._next_backoff_seconds(self._session_retry_count)
                    logger.warning(
                        f"⚠️ [Session] W{w.wid} retrying UI ready in {wait_s}s (no proof)."
                    )
                    time.sleep(wait_s)
                    try:
                        w.page.reload(wait_until="domcontentloaded")
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"❌ [Startup] W{w.wid} wait_for_ui_ready failed: {e}")
                    raise

        # All workers load at the same time, reducing total init time from ~20s to ~5s
        logger.info(f"[Init] Initializing {len(self.workers)} workers in parallel...")
        start_time = time.time()

        # Updated: Initialize sequentially to avoid greenlet/thread switching errors with Playwright Sync API
        # Parallel init caused "Cannot switch to a different thread" errors because
        # Playwright objects (Page, Context) are not thread-safe.
        for w in self.workers:
            try:
                _init_single_worker(w)
                elapsed = time.time() - start_time
                logger.info(f"✅ [Init] W{w.wid} ready after {elapsed:.1f}s")
            except Exception as e:
                logger.error(f"❌ [Init] W{w.wid} failed: {e}")
                # Save error screenshots for all workers
                for ww in self.workers:
                    self._save_startup_error_screenshot(ww.page, ww.wid, "Init failed")
                raise

        total_time = time.time() - start_time
        logger.info(f"✅ [Init] All {len(self.workers)} workers ready in {total_time:.1f}s")

        self._auth_ensure("startup", force=True)

    def _worker_start(self, w: PageWorker, image_path: Path, prompt_text: str) -> None:
        """Start processing a file on worker. Handles errors gracefully."""
        try:
            self._worker_start_inner(w, image_path, prompt_text)
        except BrowserCrashedError:
            # Re-raise critical browser errors to trigger restart
            raise
        except FileNotFoundError as e:
            logger.error(f"❌ [W{w.wid}] File not found during start: {image_path.name} - {e}")
            self._unlock_file(image_path.name)
            # Don't mark as busy - worker remains available
        except Exception as e:
            error_str = str(e)
            if (
                "Target page, context or browser has been closed" in error_str
                or "Session closed" in error_str
            ):
                logger.error(f"❌ [W{w.wid}] CRITICAL BROWSER CRASH: {e}")
                self._unlock_file(image_path.name)
                raise BrowserCrashedError(error_str) from e

            logger.error(f"❌ [W{w.wid}] Start failed for {image_path.name}: {e}")
            self._unlock_file(image_path.name)
            # Worker remains available for next file

    def _worker_start_inner(self, w: PageWorker, image_path: Path, prompt_text: str) -> None:
        """Inner implementation of worker start."""
        p = w.page
        self.current_stage = f"worker_{w.wid}_start"

        logger.info(
            f"🧵 [W{w.wid}] START Processing: {image_path.name}\n"
            f"    📂 Source: {self.source_dir}\n"
            f"    🔢 Count: {w.done_count + 1}/{self.scans_per_worker}"
        )
        self._save_artifact(f"w{w.wid}_{image_path.name}_prompt.txt", prompt_text)

        logger.info(f"🧠 [W{w.wid}] Getting preloaded image + new chat...")

        # Start new chat while image might still be preprocessing
        t0 = time.time()
        self.browser.new_chat(p)

        w.card_id = self.browser.get_card_id(p)

        # Prepend @Pro to prompt if needed (faster switching than UI menu)
        if self.pro_only:
            if not prompt_text.startswith("@Pro"):
                prompt_text = f"@Pro {prompt_text}"
                logger.info(f"🧠 [W{w.wid}] Prepended @Pro to prompt (skip UI switch)")

        w.prompt_text = prompt_text
        # if self.pro_only:
        #     logger.info(f"🧠 [W{w.wid}] Verifying/Switching to Pro model...")
        #     w.model_label = self._ensure_pro_or_pause(p, f"W{w.wid} ensure_pro")
        #     logger.info(f"🧠 [W{w.wid}] Model set to: {w.model_label}")
        # else:
        w.model_label = self.browser.detect_model_label(p) or "unknown"

        effective = self.browser.detect_model_label(p) or w.model_label or "unknown"
        logger.info(f"🧠 [Model] EFFECTIVE: {effective}")

        # PRO-ONLY check: only verify limit banner (model label is irrelevant before send,
        # because @Pro prefix in the prompt will switch it at send time)
        if self.pro_only:
            has_banner = self._has_limit_banner(p)

            if has_banner:
                logger.critical(f"🛑 [W{w.wid}] PRO LIMIT BANNER detected! Pausing.")
                self._unlock_file(image_path.name)
                self._trigger_pause_from_page(
                    p, f"W{w.wid} limit_banner_before_send", force_if_missing=True
                )
                return

        # Get preprocessed image from preload queue or process now
        optimized_path = self._get_preloaded_image(image_path)

        # Check if preprocessed file exists (might have been deleted)
        if not optimized_path.exists():
            raise FileNotFoundError(f"Preprocessed image missing: {optimized_path}")

        # DEBUG: Save copy of preprocessed image AND original for inspection (opt-in)
        if os.environ.get("OCR_DEBUG_PREPROC", "0").strip() in ("1", "true", "yes", "y"):
            try:
                debug_dir = self.artifacts_dir / "debug_preproc"
                debug_dir.mkdir(parents=True, exist_ok=True)
                import shutil

                shutil.copy2(optimized_path, debug_dir / f"PRE_{optimized_path.name}")
                shutil.copy2(image_path, debug_dir / f"ORG_{image_path.name}")
                logger.info(f"💾 [Debug] Saved comparison to: {debug_dir}")
            except Exception:
                pass

        logger.info(
            f"🖼️ [W{w.wid}] Preprocess ready: {optimized_path.name} ({time.time() - t0:.2f}s)"
        )

        self.current_stage = f"worker_{w.wid}_upload_send"
        logger.info(f"📎 [W{w.wid}] Upload: {optimized_path.name}")
        prompt_filled = False
        try:
            self.browser.upload_image(p, optimized_path)
        except Exception as exc:
            logger.error(f"❌ [W{w.wid}] Upload failed: {exc}. Retrying with prompt-first flow...")
            self.browser.fill_prompt(p, prompt_text)
            prompt_filled = True
            self.browser.upload_image(p, optimized_path)

        # CLEANUP: Remove temp image to save space (redundant if debug copy exists)
        try:
            # Never delete the original input. Only delete temp preprocessed files.
            if (
                optimized_path.exists()
                and optimized_path.is_file()
                and optimized_path != image_path
                and optimized_path.resolve().is_relative_to(self.temp_img_dir.resolve())
            ):
                optimized_path.unlink()
        except Exception:
            pass

        if self.pro_only and self._has_limit_banner(p):
            self._unlock_file(image_path.name)
            self._trigger_pause_from_page(p, f"W{w.wid} before_send")
            return

        if not prompt_filled:
            self.browser.fill_prompt(p, prompt_text)
        logger.info(f"📤 [W{w.wid}] Sending prompt... ({image_path.name})")
        try:
            self.browser.click_send(p)
            logger.info(f"✅ [W{w.wid}] Prompt sent.")
        except Exception as exc:
            logger.error(f"❌ [W{w.wid}] Prompt not sent (click failed): {exc}")
            if "Target page, context or browser has been closed" in str(exc):
                raise BrowserCrashedError(f"Click failed - browser closed: {exc}") from exc
            raise

        w.busy = True
        w.image_path = image_path
        w.started_ts = time.time()

        logger.info(f"🚀 [W{w.wid}] SENT: {image_path.name} | model={w.model_label}")

    def _worker_try_collect(self, w: PageWorker) -> bool:
        if not w.busy or not w.image_path:
            return False

        p = w.page
        file_name = w.image_path.name

        # Check if still generating
        stop_btn = p.locator(
            "button[aria-label*='Zatrzymaj' i], button[aria-label*='Stop' i]"
        ).first
        try:
            if stop_btn.count() > 0 and stop_btn.is_visible():
                now = time.time()
                if now - w.last_generating_log_ts > 15.0:  # Log every 15s to show activity
                    logger.info(f"🔄 [W{w.wid}] Generating response... ({file_name})")
                    w.last_generating_log_ts = now
                return False
        except Exception:
            pass

        self.current_stage = f"worker_{w.wid}_collect"
        logger.info(f"📥 [W{w.wid}] Collecting final response for {file_name}...")
        try:
            raw, status = self.browser.wait_for_response_or_limit(
                p,
                timeout_ms=self.collect_timeout_ms,
                has_limit_banner_fn=lambda pg: self._has_limit_banner(pg),
                on_tick=self._update_live_previews,
                tick_ms=5_000,
            )

            if status == "limit_pro":
                logger.warning(f"⚠️ [W{w.wid}] Pro limit after send: {file_name}")
                # Log PRO limit event for dashboard visibility
                self.activity_logger.log_event(
                    profile_name=self.active_profile_name,
                    event_type="profile_worker_limit",
                    reason=f"PRO limit detected after send - file: {file_name}",
                )
                self._unlock_file(file_name)
                w.busy = False
                w.image_path = None
                w.prompt_text = None
                self._trigger_pause_from_page(p, f"W{w.wid} collect")
                return False

            if status != "response":
                elapsed = time.time() - w.started_ts
                raise RuntimeError(
                    f"Collect failed: status={status} | file={file_name} | elapsed={elapsed:.1f}s"
                )

            end_ts = time.time()
            duration = end_ts - w.started_ts
            logger.info(f"✅ [W{w.wid}] Response extracted/copied (length: {len(raw)} chars).")

            final_model = self.browser.detect_model_label(p) or w.model_label or "unknown"
            w.model_label = final_model

            self._save_artifact(f"w{w.wid}_{file_name}_raw_response.txt", raw)
            parsed_json = self._extract_json_block(raw)
            if parsed_json:
                self._save_artifact(
                    f"w{w.wid}_{file_name}_result.json",
                    json.dumps(parsed_json, indent=2, ensure_ascii=False),
                )

            page_no = self._guess_page_no(w.image_path)

            prompt_len = len(w.prompt_text or "")
            resp_len = len(raw or "")
            tok_in = _estimate_tokens(w.prompt_text)
            tok_out = _estimate_tokens(raw)
            tok_total = tok_in + tok_out

            logger.info(
                f"✅ [W{w.wid}] DONE: {file_name} | time={duration:.2f}s | model={final_model} "
                f"| tok_in={tok_in} tok_out={tok_out} tok_total={tok_total} "
                f"chars_in={prompt_len} chars_out={resp_len}"
            )

            # Get card_id after response
            w.card_id = self.browser.get_card_id(p) or w.card_id
            if not w.card_id:
                for _ in range(10):
                    p.wait_for_timeout(300)
                    w.card_id = self.browser.get_card_id(p) or w.card_id
                    if w.card_id:
                        break

            if self.pro_only and not ProLimitHandler.is_pro_label(final_model):
                logger.critical(
                    f"🛑 [W{w.wid}] NON-PRO RESULT - skip DB save: model='{final_model}' file={file_name}"
                )
                # Log non-PRO model event for dashboard visibility
                self.activity_logger.log_event(
                    profile_name=self.active_profile_name,
                    event_type="profile_worker_limit",
                    reason=f"Non-PRO model used: {final_model} - result discarded",
                )
                self._unlock_file(file_name)
                if self.pg_enabled:
                    self.db.release_lock(file_name)
                self._trigger_pause_from_page(
                    p, f"W{w.wid} non_pro_result model={final_model}", force_if_missing=True
                )
                w.busy = False
                w.image_path = None
                w.prompt_text = None
                return True

            # Save to DB
            self.db.save_result(
                created_at=datetime.now(),
                batch_id=self.batch_id,
                file_name=file_name,
                source_path=str(w.image_path.parent),
                page_no=page_no,
                raw_text=raw,
                card_id=w.card_id,
                browser_id=self.browser_id,
                ocr_duration_sec=duration,
                start_ts=datetime.fromtimestamp(w.started_ts) if w.started_ts else None,
                end_ts=datetime.fromtimestamp(end_ts),
                browser_profile=self.active_profile_name,
                model_label=final_model,
                execution_mode=self.execution_mode,
            )
            self.db.save_token_usage(
                created_at=datetime.now(),
                batch_id=self.batch_id,
                file_name=file_name,
                source_path=str(w.image_path.parent),
                page_no=page_no,
                browser_profile=self.active_profile_name,
                browser_id=self.browser_id,
                model_label=final_model,
                tok_in=tok_in,
                tok_out=tok_out,
                tok_total=tok_total,
                chars_in=prompt_len,
                chars_out=resp_len,
                ocr_duration_sec=duration,
            )

            self._processed_local.add(file_name)
            self._inflight_local.discard(file_name)
            self._save_local_progress()

            if self.pg_enabled:
                self.db.release_lock(file_name)

            w.done_count += 1
            w.busy = False
            w.image_path = None
            w.prompt_text = None
            return True

        except Exception as e:
            error_str = str(e)
            if (
                "Target page, context or browser has been closed" in error_str
                or "Session closed" in error_str
            ):
                logger.error(f"❌ [W{w.wid}] CRITICAL BROWSER CRASH during collect: {e}")
                raise BrowserCrashedError(error_str) from e

            elapsed = time.time() - w.started_ts if w.started_ts else 0
            logger.error(
                f"❌ [W{w.wid}] Collect failed for {file_name}: {e} | "
                f"wait_time={elapsed:.1f}s | model={w.model_label}"
            )

            # Save error trace
            try:
                trace_name = f"trace_{self.active_profile_name}_{datetime.now().strftime('%H%M%S')}_{file_name}.zip"
                trace_path = self.traces_dir / trace_name

                # Get trace bytes (this stops tracing, returns bytes, and restarts tracing)
                trace_bytes = self.browser.get_trace_bytes()

                if trace_bytes:
                    # Save to disk (for backward compatibility and easy access)
                    trace_path.parent.mkdir(parents=True, exist_ok=True)
                    trace_path.write_bytes(trace_bytes)
                    logger.info(f"📊 [Trace] Saved error trace -> {trace_path.name}")

                    # Log to database
                    if self.pg_enabled:
                        try:
                            # 1. Save content blob
                            self.db.save_artifact(
                                batch_id=self.batch_id,
                                file_name=file_name,
                                profile_name=self.active_profile_name,
                                artifact_type="trace_zip",
                                content=trace_bytes,
                                meta={"error": str(e)[:500], "type": "collection_error"},
                            )

                            # 2. Save metadata for analytics
                            self.db.save_error_trace(
                                created_at=datetime.now(),
                                batch_id=self.batch_id,
                                file_name=file_name,
                                source_path=str(w.image_path.parent),
                                page_no=self._guess_page_no(w.image_path),
                                browser_profile=self.active_profile_name,
                                browser_id=self.browser_id,
                                worker_id=w.wid,
                                error_type="collection_error",
                                error_message=str(e)[:500],
                                trace_file_path=str(trace_path.relative_to(self.job_dir)),
                                trace_file_size_bytes=len(trace_bytes),
                                model_label=w.model_label,
                                execution_mode=self.execution_mode,
                                ocr_duration_sec=time.time() - w.started_ts
                                if w.started_ts
                                else None,
                            )
                        except Exception as db_err:
                            logger.warning(f"[Trace] Failed to log trace to DB: {db_err}")
            except Exception as trace_err:
                logger.warning(f"[Trace] Failed to save trace: {trace_err}")

            # Save error screenshot (DB + Disk)
            try:
                file_stem = Path(file_name).stem
                ss_name = f"error_{datetime.now().strftime('%H%M%S')}_{file_stem}.png"

                # DB Storage
                if self.pg_enabled:
                    ss_data = self.browser.get_screenshot_bytes(p)
                    self.db.save_artifact(
                        batch_id=self.batch_id,
                        file_name=file_name,
                        profile_name=self.active_profile_name,
                        artifact_type="screenshot_png",
                        content=ss_data,
                        meta={"reason": "collection_error", "error": str(e)},
                    )

                # Disk Storage
                ss_path = self.artifacts_dir / "screenshots" / ss_name
                ss_path.parent.mkdir(parents=True, exist_ok=True)
                self.browser.save_screenshot(p, ss_path)

                # Log video path if available
                try:
                    video = p.video
                    if video:
                        video_path = video.path()
                        logger.info(f"[Video] Recording: {video_path}")
                except Exception:
                    pass
            except Exception:
                pass

            self._unlock_file(file_name)
            w.busy = False
            w.image_path = None
            w.prompt_text = None
            return True

    # ---- File Management ----

    def _preload_next_files(self, all_files: list[Path], count: int = 2) -> None:
        """Preprocess next files in background for faster worker start."""
        sorted_files = sorted(all_files, key=lambda x: x.name)

        # Find candidates not yet processed/inflight/preloaded
        candidates = []
        for f in sorted_files:
            if f.name in self._processed_local:
                continue
            if f.name in self._inflight_local:
                continue
            if f.name in self._preload_queue:
                continue
            candidates.append(f)
            if len(candidates) >= count:
                break

        # Submit preprocessing for candidates
        for f in candidates:
            logger.debug(f"🔄 [Preload] Starting preprocessing: {f.name}")
            self._preload_queue[f.name] = self._bg_pool.submit(
                preprocess_image_smart, f, self.temp_img_dir
            )

    def _get_preloaded_image(self, image_path: Path) -> Path:
        """Get preprocessed image from preload queue or process now."""
        if image_path.name in self._preload_queue:
            future = self._preload_queue.pop(image_path.name)
            try:
                return future.result(timeout=30)
            except Exception as e:
                logger.warning(f"[Preload] Failed for {image_path.name}: {e}")
        # Fallback: process synchronously
        return preprocess_image_smart(image_path, self.temp_img_dir)

    def _find_and_lock_next_file(self, all_files: list[Path]) -> tuple[FileStatus, Path | None]:
        """Find next file to process, return (status, file) tuple.

        Returns:
            (FOUND, path) - File available to process
            (ALL_BUSY, None) - Files exist but all locked by other profiles
            (ALL_DONE, None) - All files have been processed
        """
        sorted_files = all_files
        if not sorted_files:
            return (FileStatus.ALL_DONE, None)

        source_key = str(self.source_dir)
        if self._scan_cursor_source_dir != source_key:
            self._scan_cursor_source_dir = source_key
            self._scan_cursor = 0

        if self.pg_enabled:
            self.db.clean_old_locks()
            done_files = self._db_done_cache
        else:
            done_files = self._processed_local

        total = len(sorted_files)
        start_idx = self._scan_cursor % total
        has_unprocessed = False

        for offset in range(total):
            idx = (start_idx + offset) % total
            p = sorted_files[idx]

            if self.continue_mode and p.name in done_files:
                continue

            has_unprocessed = True

            if not self.pg_enabled:
                if p.name in self._inflight_local:
                    continue
                self._inflight_local.add(p.name)
                self._scan_cursor = (idx + 1) % total
                logger.info(f"📌 [Queue] Selected (local): {p.name}")
                return (FileStatus.FOUND, p)

            if self.db.try_acquire_lock(p.name):
                if self.continue_mode and self.db.is_file_done(source_key, p.name):
                    self.db.release_lock(p.name)
                    self._db_done_cache.add(p.name)
                    self._processed_local.add(p.name)
                    continue
                self._scan_cursor = (idx + 1) % total
                logger.info(f"🔒 [Queue] Selected (DB lock): {p.name}")
                return (FileStatus.FOUND, p)

        # Distinguish: all done vs all locked by others
        if has_unprocessed:
            return (FileStatus.ALL_BUSY, None)
        return (FileStatus.ALL_DONE, None)

    def _unlock_file(self, file_name: str) -> None:
        self._inflight_local.discard(file_name)
        if self.pg_enabled:
            self.db.release_lock(file_name)

    def _get_images_from_source_dir(self) -> list[Path]:
        if not self.source_dir.exists():
            return []
        source_key = str(self.source_dir)
        if self._scan_cursor_source_dir != source_key:
            self._scan_cursor_source_dir = source_key
            self._scan_cursor = 0
        if self.pg_enabled:
            queue = self.db.get_scan_queue(source_key)
            if queue is not None:
                # DB scan queue may contain non-image files if the folder index is broad.
                # DbLockingManager filters, but keep a second guard here.
                allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
                filtered: list[Path] = []
                for p in queue:
                    try:
                        if not p.exists() or not p.is_file():
                            continue
                        if p.suffix.lower() not in allowed:
                            continue
                        if p.stat().st_size <= 0:
                            continue
                        filtered.append(p)
                    except Exception:
                        continue
                return sorted(filtered, key=lambda x: x.name)
        files: list[Path] = []
        for ext in ["*.jpg", "*.jpeg", "*.png", "*.webp"]:
            files.extend(self.source_dir.glob(ext))
        return sorted(list(set(files)), key=lambda x: x.name)

    def _should_skip_dir_via_db_stats(self) -> bool:
        if not (self.pg_enabled and self.auto_advance and self.use_db_counts):
            return False
        stats = self.db.get_source_path_stats(str(self.source_dir))
        if not stats:
            return False
        files_on_disk = stats.get("files_on_disk") or 0
        remaining = stats.get("remaining_to_ocr")
        if files_on_disk <= 0 or remaining is None:
            return False
        last_updated = stats.get("last_updated")
        if not last_updated:
            logger.info(
                f"ℹ️ [Counts] Missing last_updated for {self.source_dir}, skipping DB fast-path."
            )
            return False
        now = datetime.now(last_updated.tzinfo) if last_updated.tzinfo else datetime.now()
        age_sec = (now - last_updated).total_seconds()
        if age_sec > self.counts_max_age_sec:
            logger.info(
                f"ℹ️ [Counts] Stale counts for {self.source_dir} "
                f"(age={int(age_sec)}s, max={self.counts_max_age_sec}s)."
            )
            return False
        if remaining <= 0:
            logger.info(
                f"➡️ [AutoAdvance] DB view indicates completed "
                f"(remaining={remaining}, files={files_on_disk})."
            )
            return True
        return False

    def _get_next_source_dir(self) -> Path | None:
        """Find next source folder to process.

        Priority:
        1. Check DB view v_source_path_scan_queue for folders with pending files
        2. Fallback to sequential scan (alphabetical order) in parent directory
        """
        # 1. Try DB-based selection (prioritizes folders with actual remaining files)
        if self.pg_enabled:
            try:
                next_source = self.db.get_next_source_from_queue(str(self.source_dir))
                if next_source:
                    next_path = Path(next_source)
                    if next_path.exists():
                        logger.info(
                            f"➡️ [AutoAdvance] DB queue found folder with pending files: {next_path}"
                        )
                        return next_path
                    else:
                        logger.warning(f"[AutoAdvance] DB queue path not accessible: {next_source}")
            except Exception as e:
                logger.debug(f"[AutoAdvance] DB queue check failed: {e}")

        # 2. Fallback to sequential scan (alphabetical order in parent directory)
        try:
            parent = self.source_dir.parent
            if not parent.exists():
                return None
            candidates = sorted([p for p in parent.iterdir() if p.is_dir()], key=lambda p: p.name)
            for p in candidates:
                if p.name > self.source_dir.name:
                    return p
        except Exception as e:
            logger.warning(f"[AutoAdvance] Cannot determine next folder: {e}")
        return None

    # ---- Pro Limit Helpers ----

    def _verify_worker_model(self, w: PageWorker) -> None:
        """
        Verify worker is still using Pro model, switch if needed.
        Called periodically to prevent model drift.
        """
        # Disable periodic check as we use prompt-based switching now
        return

        # if not self.pro_only:
        #     return

        # try:
        #     current_model = self.browser.detect_model_label(w.page)
        #     if current_model and not re.search(_PRO_MODEL_RE, current_model):
        #         logger.warning(
        #             f"[W{w.wid}] ⚠️ Model drifted to {current_model}, switching back to Pro..."
        #         )
        #         self.browser.ensure_pro_model(
        #             w.page, has_limit_banner_fn=lambda pg: self._has_limit_banner(pg)
        #         )
        # except Exception as e:
        #     logger.debug(f"[W{w.wid}] Model verification failed: {e}")

    def _has_limit_banner(self, page: Page) -> bool:
        try:
            body = page.locator("body").first
            if body.count() > 0:
                txt = body.inner_text(timeout=2000)
                if txt and re.search(PRO_LIMIT_TEXT_RE, txt):
                    return True
        except Exception:
            pass
        return False

    def _find_limit_banner_page(self, page: Page) -> Page | None:
        """Return a page that actually shows the Pro limit banner, if any."""
        try:
            if self._has_limit_banner(page):
                return page
        except Exception:
            pass

        try:
            self.browser.new_chat(page)
            if self.pro_only:
                self.browser.ensure_pro_model(
                    page, has_limit_banner_fn=lambda pg: self._has_limit_banner(pg)
                )
            if self._has_limit_banner(page):
                return page
        except Exception:
            pass

        try:
            for other in self.browser.context.pages:
                if other == page:
                    continue
                if self._has_limit_banner(other):
                    return other
        except Exception:
            pass

        return None

    def _check_all_tabs_for_limit(self) -> bool:
        """
        Check ALL browser tabs for Pro limit message.
        Critical: Limit popup can appear in a different tab than the one worker is using.

        Returns True if limit detected on ANY tab.
        """
        try:
            all_pages = self.browser.context.pages
            logger.debug(f"[Limit Check] Scanning {len(all_pages)} tabs for Pro limit...")

            for idx, page in enumerate(all_pages):
                try:
                    if self._has_limit_banner(page):
                        logger.critical(f"⚠️ [Limit] PRO LIMIT detected on tab #{idx + 1}!")
                        # Trigger pause immediately
                        self._trigger_pause_from_page(page, f"tab_{idx + 1}_global_scan")
                        return True
                except Exception as e:
                    logger.warning(f"[Limit Check] Tab #{idx + 1} scan failed: {e}")
                    continue

            return False
        except Exception as e:
            logger.warning(f"[Limit Check] Global scan failed: {e}")
            return False

    def _close_idle_tabs(self) -> None:
        """
        Close tabs that are not being used by any worker.
        Optionally enforce max tabs per context while keeping worker tabs.
        """
        if not self.close_idle_tabs and self.max_tabs_per_context <= 0:
            return

        try:
            contexts: list[BrowserContext] = []
            if self.browser.use_isolated_contexts:
                contexts.extend(self.browser.worker_contexts.values())
                if self.browser.context:
                    contexts.append(self.browser.context)
            elif self.browser.context:
                contexts.append(self.browser.context)

            worker_pages = {w.page for w in self.workers if w.page}
            for context in contexts:
                try:
                    pages = list(context.pages)
                except Exception:
                    continue
                if not pages:
                    continue

                keep_pages = set()
                if self.browser.use_isolated_contexts:
                    keep_pages.update({p for p in worker_pages if p.context == context})
                else:
                    keep_pages.update(worker_pages)

                # Consistently keep first worker tabs in ALL contexts (shared or isolated)
                # This protects the active worker pages from cleanup
                if len(pages) > 0:
                    keep_pages.add(pages[0])
                if len(pages) > 1:
                    keep_pages.add(pages[1])

                if self.close_idle_tabs:
                    for page in list(pages):
                        if page not in keep_pages:
                            try:
                                logger.debug(f"[Cleanup] Closing idle tab: {page.url[:50]}")
                                page.close()
                            except Exception:
                                pass

                if self.max_tabs_per_context > 0:
                    try:
                        pages = list(context.pages)
                    except Exception:
                        continue
                    effective_limit = max(self.max_tabs_per_context, len(keep_pages))
                    if len(pages) > effective_limit:
                        candidates = [p for p in pages if p not in keep_pages]
                        for page in candidates:
                            if len(pages) <= effective_limit:
                                break
                            try:
                                logger.debug(f"[Cleanup] Closing extra tab: {page.url[:50]}")
                                page.close()
                                pages.remove(page)
                            except Exception:
                                continue
                        if len(pages) > effective_limit:
                            logger.debug(
                                f"[Cleanup] Tab limit not met (kept {len(keep_pages)} worker tabs)"
                            )
        except Exception as e:
            logger.debug(f"[Cleanup] Tab cleanup failed: {e}")

    def _check_pro_limit(self, page: Page) -> bool:
        if self.pro_only and self.limit_handler.maybe_wait_for_pause():
            return True
        if self._has_limit_banner(page):
            self._trigger_pause_from_page(page, "check_pro_limit")
            return True
        return False

    def _ensure_pro_or_pause(self, page: Page, context: str) -> str:
        """Force Pro model selection; pause if limit banner or no-Pro persists."""
        last_label = self.browser.detect_model_label(page) or "unknown"
        for attempt in range(1, self.browser.model_switch_retries + 1):
            label = self.browser.ensure_pro_model(
                page, has_limit_banner_fn=lambda pg: self._has_limit_banner(pg)
            )
            last_label = label or last_label

            if self._has_limit_banner(page):
                self._trigger_pause_from_page(page, f"{context} attempt={attempt}")
                return last_label

            if ProLimitHandler.is_pro_label(last_label):
                return last_label

            logger.warning(f"🧠 [Model] Still not Pro after attempt {attempt}: {last_label}")
            try:
                page.reload(wait_until="domcontentloaded")
                self.browser.wait_for_ui_ready(page)
            except Exception:
                pass

        self._trigger_pause_from_page(
            page, f"{context} no_pro_after_retries", force_if_missing=True
        )
        return last_label

    def _trigger_pause_from_page(
        self, page: Page, context: str = "", force_if_missing: bool = False
    ) -> None:
        try:
            target_page = self._find_limit_banner_page(page) if self.pro_only else page
            if not target_page:
                if force_if_missing:
                    try:
                        if self.pro_only:
                            self.browser.ensure_pro_model(
                                page, has_limit_banner_fn=lambda pg: self._has_limit_banner(pg)
                            )
                    except Exception:
                        pass
                    if self._capture_limit_screenshot(
                        page, f"{context} forced_capture", attempts=6
                    ):
                        target_page = self._find_limit_banner_page(page) if self.pro_only else page
                else:
                    if self._capture_limit_screenshot(page, f"{context} banner_search"):
                        target_page = self._find_limit_banner_page(page) if self.pro_only else page
                    if not target_page:
                        logger.warning("⚠️ [Limit] Banner not found, skip pause (no proof).")
                        return
                if not target_page:
                    logger.warning(
                        "⚠️ [Limit] Banner not found, forcing pause due to non-Pro detection."
                    )
                    if not self._save_pause_screenshot(page, context):
                        logger.warning("⚠️ [Limit] Pause screenshot failed.")
                    self.limit_handler.pause_until(
                        datetime.now() + timedelta(minutes=15), "non_pro_no_banner"
                    )
                    return
            body = target_page.locator("body").first
            txt = body.inner_text(timeout=2500) if body.count() > 0 else ""
            if not self._capture_limit_screenshot(target_page, context):
                logger.warning("⚠️ [Limit] Screenshot missing, skip pause (no proof).")
                return
            self.limit_handler.trigger_pause_from_text(txt, context)
        except Exception as e:
            logger.warning(f"[PRO-ONLY] Could not trigger pause: {e}")
            self.limit_handler.pause_until(datetime.now() + timedelta(minutes=30), "fallback")

    def _save_session_screenshot(self, page: Page, context: str = "") -> bool:
        """Save a screenshot when session is expired for live preview."""
        try:
            live_dir = self.artifacts_dir / "live"
            live_dir.mkdir(parents=True, exist_ok=True)
            safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
            path = live_dir / f"{safe_profile}_session.jpg"
            page.screenshot(path=path, type="jpeg", quality=70, full_page=True)
            self._stamp_image(path)

            # DB Storage
            if self.pg_enabled:
                try:
                    data = path.read_bytes()
                    self.db.save_artifact(
                        batch_id=self.batch_id,
                        file_name=path.name,
                        profile_name=self.active_profile_name,
                        artifact_type="screenshot_jpg",
                        content=data,
                        meta={"type": "session_expired", "context": context},
                    )
                except Exception:
                    pass

            if context:
                logger.info(f"📸 [Session] Saved session screenshot ({context}) -> {path.name}")
            return True
        except Exception:
            live_dir = self.artifacts_dir / "live"
            safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
            path = live_dir / f"{safe_profile}_session.jpg"
            return path.exists()

    def _save_limit_screenshot(self, page: Page, context: str = "") -> bool:
        """Save a screenshot when Pro limit is detected for live preview."""
        try:
            if not self._has_limit_banner(page):
                return False
            live_dir = self.artifacts_dir / "live"
            live_dir.mkdir(parents=True, exist_ok=True)
            safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
            path = live_dir / f"{safe_profile}_limit.jpg"
            page.screenshot(path=path, type="jpeg", quality=60, full_page=True)
            self._stamp_image(path)

            # DB Storage
            if self.pg_enabled:
                try:
                    data = path.read_bytes()
                    self.db.save_artifact(
                        batch_id=self.batch_id,
                        file_name=path.name,
                        profile_name=self.active_profile_name,
                        artifact_type="screenshot_jpg",
                        content=data,
                        meta={"type": "limit_detected", "context": context},
                    )
                except Exception:
                    pass

            if context:
                logger.info(f"📸 [Limit] Saved limit screenshot ({context}) -> {path.name}")
            return True
        except Exception:
            live_dir = self.artifacts_dir / "live"
            safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
            path = live_dir / f"{safe_profile}_limit.jpg"
            return path.exists()

    def _save_pause_screenshot(self, page: Page, context: str = "") -> bool:
        """Save a screenshot when pause is forced (no banner) for live preview."""
        try:
            live_dir = self.artifacts_dir / "live"
            live_dir.mkdir(parents=True, exist_ok=True)
            safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
            path = live_dir / f"{safe_profile}_pause.jpg"
            page.screenshot(path=path, type="jpeg", quality=60, full_page=True)
            self._stamp_image(path)

            # DB Storage
            if self.pg_enabled:
                try:
                    data = path.read_bytes()
                    self.db.save_artifact(
                        batch_id=self.batch_id,
                        file_name=path.name,
                        profile_name=self.active_profile_name,
                        artifact_type="screenshot_jpg",
                        content=data,
                        meta={"type": "pause_forced", "context": context},
                    )
                except Exception:
                    pass

            if context:
                logger.info(f"📸 [Pause] Saved pause screenshot ({context}) -> {path.name}")
            return True
        except Exception:
            try:
                # Fallback check if it exists
                live_dir = self.artifacts_dir / "live"
                safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
                path = live_dir / f"{safe_profile}_pause.jpg"
                return path.exists()
            except:
                return False

    def _save_startup_error_screenshot(self, page: Page, wid: int, reason: str) -> None:
        """Save a diagnostic screenshot to live preview when startup fails."""
        live_dir = self.artifacts_dir / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.active_profile_name)
        path = live_dir / f"{safe_profile}_w{wid}.jpg"
        message = f"STARTUP ERROR: {reason}"
        try:
            page.screenshot(path=path, type="jpeg", quality=70, full_page=True)
            self._stamp_image(path)
        except Exception:
            try:
                img = Image.new("RGB", (1280, 720), (10, 10, 10))
                draw = ImageDraw.Draw(img)
                font = ImageFont.load_default()
                draw.text((24, 24), message, fill=(220, 80, 80), font=font)
                draw.text(
                    (24, 48),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    fill=(200, 200, 200),
                    font=font,
                )
                img.save(path, "JPEG", quality=80)
            except Exception:
                pass

        # Save to DB (optional)
        if self.pg_enabled and path.exists():
            try:
                data = path.read_bytes()
                self.db.save_artifact(
                    batch_id=self.batch_id,
                    file_name=path.name,
                    profile_name=self.active_profile_name,
                    artifact_type="screenshot_jpg",
                    content=data,
                    meta={"type": "startup_error", "reason": reason},
                )
            except Exception:
                pass

    def _next_backoff_seconds(self, current_count: int, base: int = 10, cap: int = 300) -> int:
        """Calculate backoff seconds based on retry count."""
        count = max(1, current_count + 1)
        return min(cap, base * (2 ** (count - 1)))

    def _stamp_image(self, path: Path) -> None:
        """Add timestamp watermark directly onto the JPEG file."""
        try:
            img = Image.open(path).convert("RGB")
            draw = ImageDraw.Draw(img)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            font = ImageFont.load_default()
            if hasattr(draw, "textbbox"):
                bbox = draw.textbbox((0, 0), ts, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            else:
                text_w, text_h = draw.textsize(ts, font=font)
            pad = 4
            margin = 6
            x = max(0, img.width - text_w - pad * 2 - margin)
            y = max(0, img.height - text_h - pad * 2 - margin)
            draw.rectangle(
                [x, y, x + text_w + pad * 2, y + text_h + pad * 2],
                fill=(0, 0, 0),
            )
            draw.text((x + pad, y + pad), ts, fill=(255, 255, 255), font=font)
            img.save(path, "JPEG", quality=75)
        except Exception:
            pass

    def _capture_limit_screenshot(self, page: Page, context: str = "", attempts: int = 4) -> bool:
        """Retry capture with backoff when limit screenshot is required."""
        for attempt in range(1, max(1, attempts) + 1):
            target_page = self._find_limit_banner_page(page) or page
            attempt_ctx = f"{context} attempt={attempt}" if context else f"attempt={attempt}"
            if self._save_limit_screenshot(target_page, attempt_ctx):
                self._limit_retry_count = 0
                return True
            self._limit_retry_count += 1
            wait_s = self._next_backoff_seconds(self._limit_retry_count)
            logger.warning(
                f"⚠️ [Limit] Retry capture in {wait_s}s (attempt {self._limit_retry_count})."
            )
            time.sleep(wait_s)
            try:
                target_page.reload(wait_until="domcontentloaded")
                self.browser.wait_for_ui_ready(target_page)
            except Exception:
                pass
        return False

    def _capture_session_screenshot(self, page: Page, context: str = "", attempts: int = 2) -> bool:
        """Retry capture with backoff when session screenshot is required."""
        for _ in range(max(1, attempts)):
            if self._save_session_screenshot(page, context):
                self._session_retry_count = 0
                return True
            self._session_retry_count += 1
            wait_s = self._next_backoff_seconds(self._session_retry_count)
            logger.warning(
                f"⚠️ [Session] Retry capture in {wait_s}s (attempt {self._session_retry_count})."
            )
            time.sleep(wait_s)
        return False

    def _verify_limit_on_start(self) -> bool:
        """
        Verify limit status from page on startup.
        Returns True if limit is active and we should wait.

        CRITICAL: Reads ACTUAL page state, not trusted stored timestamps.
        """
        logger.info("🔍 [Limit Check] Verifying limit status on startup...")
        self.last_limit_check_ts = time.time()

        # Check first worker tab
        if not self.workers:
            return False

        page = self.workers[0].page
        try:
            if self.pro_only:
                # Ensure Pro is selected so banner contains real reset time.
                self.browser.ensure_pro_model(
                    page, has_limit_banner_fn=lambda pg: self._has_limit_banner(pg)
                )
            # Get current page text
            body = page.locator("body").first
            if body.count() == 0:
                return False

            body_text = body.inner_text(timeout=5000)

            if re.search(PRO_LIMIT_TEXT_RE, body_text):
                if not self._capture_limit_screenshot(page, "startup_verified", attempts=6):
                    logger.warning("⚠️ [Limit Check] Screenshot missing, skip pause (no proof).")
                    return False
                # Limit detected - extract REAL reset time from page
                reset_time = self.limit_handler.extract_reset_datetime_from_text(body_text)

                if reset_time:
                    logger.warning(
                        f"⚠️ [Limit Check] PRO LIMIT ACTIVE. Reset at {reset_time.strftime('%H:%M')} (from page)"
                    )
                    # Update pause file with real time + buffer
                    self.limit_handler.set_pause_until(
                        reset_time + timedelta(seconds=self.limit_handler.pause_buffer_sec),
                        "startup_verified",
                    )
                    return True
                logger.warning("⚠️ [Limit Check] PRO LIMIT ACTIVE but could not parse reset time")
                return True
            # No limit - clear any stale pause state
            logger.info("✅ [Limit Check] No limit detected. Clearing stale pause data.")
            try:
                if self.limit_handler.db:
                    self.limit_handler.set_profile_state(
                        self.active_profile_name,
                        is_paused=False,
                        pause_until=None,
                        meta={"action": "clear_stale_limit", "pid": os.getpid()},
                    )
            except Exception:
                pass
            return False

        except Exception as e:
            logger.warning(f"[Limit Check] Startup verification failed: {e}")
            return False

    def _periodic_limit_check(self) -> bool:
        """
        Periodic check (every hour) to verify limit status is still accurate.
        Returns True if limit detected.
        """
        now = time.time()
        # Check every N seconds
        if now - self.last_limit_check_ts < self.limit_check_interval_sec:
            return False

        mins = max(1, int(self.limit_check_interval_sec / 60))
        logger.info(f"🔄 [Limit Check] Periodic verification (every {mins}m)...")
        self.last_limit_check_ts = now

        # Use first idle worker or any worker
        check_page = None
        for w in self.workers:
            if not w.busy:
                check_page = w.page
                break
        if not check_page and self.workers:
            check_page = self.workers[0].page

        if not check_page:
            return False

        try:
            if self.pro_only:
                # Ensure Pro is selected so banner contains real reset time.
                self.browser.ensure_pro_model(
                    check_page, has_limit_banner_fn=lambda pg: self._has_limit_banner(pg)
                )
            body = check_page.locator("body").first
            if body.count() == 0:
                return False

            body_text = body.inner_text(timeout=5000)

            if re.search(PRO_LIMIT_TEXT_RE, body_text):
                if not self._capture_limit_screenshot(check_page, "periodic_verified", attempts=6):
                    logger.warning("⚠️ [Limit Check] Screenshot missing, skip pause (no proof).")
                    return False
                reset_time = self.limit_handler.extract_reset_datetime_from_text(body_text)
                stored_until = self.limit_handler.get_pause_until()

                if reset_time:
                    # Update with fresh reset time if different
                    if not stored_until or abs((reset_time - stored_until).total_seconds()) > 300:
                        logger.info(
                            f"📝 [Limit Check] Updating reset time to: {reset_time.strftime('%H:%M')} (was: {stored_until.strftime('%H:%M') if stored_until else 'none'})"
                        )
                        self.limit_handler.set_pause_until(
                            reset_time + timedelta(seconds=self.limit_handler.pause_buffer_sec),
                            "periodic_verified",
                        )

                return True
            # Limit cleared - update if we had pause active
            stored_until = self.limit_handler.get_pause_until()
            if stored_until and stored_until > datetime.now():
                logger.info("✅ [Limit Check] Limit cleared early! Removing pause.")
                try:
                    if self.limit_handler.db:
                        self.limit_handler.set_profile_state(
                            self.active_profile_name,
                            is_paused=False,
                            pause_until=None,
                            meta={"action": "clear_early_limit", "pid": os.getpid()},
                        )
                except Exception:
                    pass
            return False

        except Exception as e:
            logger.warning(f"[Limit Check] Periodic check failed: {e}")
            return False

    def _periodic_artifact_cleanup(self) -> None:
        """
        Periodic cleanup of old debug artifacts (every 10 minutes by default).
        Helps prevent database bloat from accumulated screenshots and traces.
        """
        now = time.time()
        # Check if enough time has passed since last cleanup
        if now - self.last_artifact_cleanup_ts < self.artifact_cleanup_interval_sec:
            return

        mins = max(1, int(self.artifact_cleanup_interval_sec / 60))
        logger.info(f"🧹 [Artifact Cleanup] Starting periodic cleanup (every {mins}m)...")
        self.last_artifact_cleanup_ts = now

        try:
            retention_hours = int(
                os.environ.get("OCR_ARTIFACT_RETENTION_HOURS", "24").strip() or "24"
            )
            deleted_count = self.db.cleanup_old_artifacts(retention_hours)

            if deleted_count > 0:
                logger.info(
                    f"✅ [Artifact Cleanup] Completed - removed {deleted_count} artifacts older than {retention_hours}h"
                )
            else:
                logger.debug(
                    f"[Artifact Cleanup] No artifacts to clean (retention: {retention_hours}h)"
                )
        except Exception as e:
            logger.warning(f"⚠️ [Artifact Cleanup] Failed: {e}")

    def _setup_prompt(self, image_path: Path) -> str:
        return self.prompt_manager.setup_and_render(
            prompt_id=self.prompt_id,
            file_name=image_path.name,
            source_path=str(image_path.parent),
        )

    # ---- Progress ----

    def _load_local_progress(self) -> None:
        # 1) Z bazy danych (priorytet)
        if self.pg_enabled:
            now_ts = time.time()
            source_key = str(self.source_dir)
            if self._db_done_cache_source_dir != source_key:
                self._db_done_cache_source_dir = source_key
                self._db_done_cache = set()
                self._db_done_cache_ts = 0.0

            if now_ts - self._db_done_cache_ts >= self.done_cache_ttl_sec:
                db_done = self.db.get_done_files(source_key)
                if db_done is not None:
                    self._db_done_cache = db_done
                    self._db_done_cache_ts = now_ts
                    logger.info(f"📊 [Sync] Loaded {len(db_done)} completed files from DB.")

            if self._db_done_cache:
                self._processed_local.update(self._db_done_cache)

        # 2) Z pliku lokalnego
        if not self.progress_file.exists():
            return
        try:
            data = json.loads(self.progress_file.read_text(encoding="utf-8"))
            processed = data.get("processed_files", [])
            if isinstance(processed, list):
                self._processed_local.update(set(str(x) for x in processed))
        except Exception as e:
            logger.warning(f"[Progress] Cannot load progress.json: {e}")

    def _save_local_progress(self) -> None:
        try:
            payload = {
                "source_dir": str(self.source_dir),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "processed_files": sorted(self._processed_local),
            }
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            self.progress_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Progress] Cannot save progress.json: {e}")

    # ---- Utilities ----

    def _extract_json_block(self, text: str):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        blob = m.group(1) if m else text
        try:
            return json.loads(re.search(r"(\{.*\})", blob, flags=re.DOTALL).group(1))
        except Exception:
            return None

    def _save_artifact(self, filename: str, content: str):
        # Save to disk
        (self.artifacts_dir / filename).write_text(content, encoding="utf-8")

        # Save to DB
        if self.pg_enabled:
            try:
                # Infer type
                atype = "text_plain"
                if filename.endswith(".html"):
                    atype = "html_dump"
                elif filename.endswith(".json"):
                    atype = "json_dump"
                elif filename.endswith(".log"):
                    atype = "text_log"

                self.db.save_artifact(
                    batch_id=self.batch_id,
                    file_name=filename,
                    profile_name=self.active_profile_name,
                    artifact_type=atype,
                    content=content.encode("utf-8"),
                    meta={},
                )
            except Exception as e:
                logger.warning(f"[Artifact] Failed to save {filename} to DB: {e}")

    def _write_status(self, technical_state: str, stage: str, error=None):
        payload = {
            "engine": "gemini_modular",
            "state": technical_state,
            "stage": stage,
            "batch": self.batch_id,
        }
        if error:
            payload["error"] = error
        with open(self.status_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def _guess_page_no(self, p: Path) -> int | None:
        try:
            return int(re.search(r"(\d+)(?!.*\d)", p.stem).group(1))
        except Exception:
            return None

    def _close(self):
        self.browser.close()
        self.db.close()
        try:
            self._bg_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


# Backwards compatibility alias
GeminiWebEngineV2 = GeminiEngine
