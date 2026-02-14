#!/usr/bin/env python3
"""
One-shot precheck for Gemini Pro limits before launching OCR workers.

Scans browser profiles, opens Gemini in headless mode, detects the Pro limit
banner, and writes pause files used by OCR runners.
"""

import argparse
import json
import os
import platform
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocr_engine.ocr.engine.db_locking import DbLockingManager
from ocr_engine.ocr.engine.pro_limit_handler import (
    PRO_LIMIT_TEXT_RE,
    ProLimitHandler,
)
from ocr_engine.utils.path_security import validate_cache_dir, validate_profiles_dir

# Database support (optional)
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

import socket
import uuid

# Database logging configuration
DB_DSN = os.environ.get("OCR_PG_DSN")
DB_LOG_ENABLED = bool(DB_DSN)

_MODEL_BUTTON_RE = re.compile(r"(Szybki|Fast|Flash|Pro|1\.5\s*Pro|2\.0\s*Pro|Thinking|Myślący)", re.IGNORECASE)
_PRO_MODEL_RE = re.compile(r"(\bPro\b|1\.5\s*Pro|2\.0\s*Pro)", re.IGNORECASE)
_FAST_MODEL_RE = re.compile(r"(Szybki|Fast|Flash|1\.5\s*Flash|2\.0\s*Flash)", re.IGNORECASE)


def _normalize_check_data(d: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(d or {})
    normalized.setdefault("limit_detected_method", "none")
    normalized.setdefault("model_initial", "unknown")
    normalized.setdefault("model_after_switch", "unknown")
    normalized.setdefault("model_final", normalized.get("model_initial") or "unknown")
    normalized.setdefault("model_switch_needed", False)
    normalized.setdefault("model_switch_success", False)
    normalized.setdefault("model_switch_attempts", 0)
    normalized.setdefault("session_valid", True)
    normalized.setdefault("login_detected", False)
    if normalized.get("login_provider") is None:
        normalized["login_provider"] = "google" if normalized.get("login_detected") else "none"
    if normalized.get("account_email") is None:
        normalized["account_email"] = ""
    normalized.setdefault("chat_opened", False)
    normalized.setdefault("chat_ready", False)
    normalized.setdefault("prompt_box_found", False)
    normalized.setdefault("prompt_sent", False)
    normalized.setdefault("prompt_response_received", False)
    normalized.setdefault("status", "OK")
    normalized.setdefault("error_message", "")
    normalized.setdefault("error_stage", "none")
    normalized.setdefault("check_duration_ms", 0)
    normalized.setdefault("browser_launch_ms", 0)
    normalized.setdefault("navigation_ms", 0)
    normalized.setdefault("page_load_ms", 0)
    normalized.setdefault("login_check_ms", 0)
    normalized.setdefault("model_detect_ms", 0)
    normalized.setdefault("model_switch_ms", 0)
    normalized.setdefault("prompt_send_ms", 0)
    normalized.setdefault("prompt_response_ms", 0)
    normalized.setdefault("limit_detect_ms", 0)
    normalized.setdefault("screenshot_ms", 0)
    normalized.setdefault("worker_type", "local")
    normalized.setdefault("browser_timeout_ms", 0)
    normalized.setdefault("browser_user_agent", "unknown")
    normalized.setdefault("browser_viewport_width", 0)
    normalized.setdefault("browser_viewport_height", 0)
    normalized.setdefault("screenshot_path", "")
    normalized.setdefault("screenshot_size_bytes", 0)
    normalized.setdefault("triggered_by", "")
    normalized.setdefault("pause_written", False)
    normalized.setdefault("pause_cleared", False)
    normalized.setdefault("pause_reason", "none")
    normalized.setdefault("page_title", "")
    normalized.setdefault("page_language", "")
    normalized.setdefault("limit_banner_text", "")
    normalized.setdefault("menu_text", "")
    normalized.setdefault("retry_count", 0)
    normalized.setdefault("total_attempts", 1)
    normalized.setdefault("raw_body_text_sample", "")
    if normalized.get("metadata") is None:
        normalized["metadata"] = {}
    if normalized.get("timings_breakdown") is None:
        normalized["timings_breakdown"] = {}
    return normalized


def _profile_name_from_dir(dir_name: str) -> str:
    if dir_name == "gemini-profile":
        return "default"
    prefix = "gemini-profile-"
    if dir_name.startswith(prefix):
        return dir_name[len(prefix) :]
    return dir_name


def _iter_profiles(base_dir: Path, only: set[str] | None):
    if not base_dir.exists():
        return []
    profiles = []
    for d in sorted(base_dir.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        if not d.name.startswith("gemini-profile"):
            continue
        name = _profile_name_from_dir(d.name)
        if only and name not in only:
            continue
        profiles.append((name, d))
    return profiles


def _get_db_manager(profile_name: str) -> DbLockingManager | None:
    if not DB_DSN:
        return None
    pg_table = os.environ.get("OCR_PG_TABLE", "public.ocr_raw_texts")
    db = DbLockingManager(pg_table=pg_table, profile_name=profile_name, enabled=True)
    db.init_artifacts_table()
    return db


def _write_pause(cache_dir: Path, profile_name: str, until: datetime, reason: str, run_id: str):
    _ = cache_dir
    db = _get_db_manager(profile_name)
    if not db:
        return
    handler = ProLimitHandler(profile_name, db_manager=db, pro_only=True)
    handler.set_pause_until(until, reason=reason, source="precheck", run_id=run_id, checked_at=datetime.now())


def _clear_pause(cache_dir: Path, profile_name: str):
    _ = cache_dir
    db = _get_db_manager(profile_name)
    if not db:
        return
    db.set_profile_state(profile_name, is_paused=False, pause_until=None, pause_reason=None)


def _log_check_to_db(check_data: dict[str, Any]) -> bool:
    """Log a limit check result to PostgreSQL database."""
    if not DB_LOG_ENABLED or not HAS_PSYCOPG2:
        return False

    try:
        conn = psycopg2.connect(DB_DSN)
    except Exception as e:
        print(f"⚠️ DB connection failed: {e}")
        return False

    d = _normalize_check_data(check_data)
    model_final = d.get("model_final") or d.get("model_detected")
    model_is_pro = bool(model_final and re.search(_PRO_MODEL_RE, model_final))
    worker_host = socket.gethostname()
    worker_ip = None
    try:
        worker_ip = socket.gethostbyname(worker_host) if worker_host else None
    except Exception:
        worker_ip = None

    try:
        import playwright
        pw_version = getattr(playwright, "__version__", "unknown")
    except Exception:
        pw_version = "unknown"

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO limit_checks (
                    run_id, check_id, profile_name, profile_path, profile_type,
                    is_limited, reset_time, limit_detected_method,
                    model_initial, model_after_switch, model_final, model_is_pro,
                    model_switch_needed, model_switch_success, model_switch_attempts,
                    session_valid, login_detected, login_provider, account_email,
                    chat_opened, chat_ready, prompt_box_found, prompt_sent, prompt_response_received,
                    status, error_message, error_stage,
                    check_duration_ms, browser_launch_ms, navigation_ms, page_load_ms,
                    login_check_ms, model_detect_ms, model_switch_ms,
                    prompt_send_ms, prompt_response_ms, limit_detect_ms, screenshot_ms,
                    worker_host, worker_ip, worker_type, worker_os, worker_python_version, playwright_version,
                    browser_headed, browser_timeout_ms, browser_user_agent, browser_viewport_width, browser_viewport_height,
                    screenshot_path, screenshot_saved, screenshot_size_bytes,
                    source_application, triggered_by,
                    pause_written, pause_until, pause_cleared, pause_reason,
                    page_title, page_language, limit_banner_text, menu_text,
                    retry_count, total_attempts,
                    metadata, timings_breakdown, raw_body_text_sample,
                    checked_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    NOW()
                )
            """, (
                d.get("run_id"),
                str(uuid.uuid4()),
                d.get("profile_name"),
                d.get("profile_path"),
                d.get("profile_type", "gemini"),
                d.get("is_limited", False),
                d.get("reset_time"),
                d.get("limit_detected_method")[:64] if d.get("limit_detected_method") else None,
                d.get("model_initial")[:64] if d.get("model_initial") else None,
                d.get("model_after_switch")[:64] if d.get("model_after_switch") else None,
                model_final[:64] if model_final else None,
                model_is_pro,
                d.get("model_switch_needed", False),
                d.get("model_switch_success"),
                d.get("model_switch_attempts", 0),
                d.get("session_valid"),
                d.get("login_detected"),
                d.get("login_provider"),
                d.get("account_email"),
                d.get("chat_opened"),
                d.get("chat_ready"),
                d.get("prompt_box_found"),
                d.get("prompt_sent"),
                d.get("prompt_response_received"),
                d.get("status"),
                d.get("error_message"),
                d.get("error_stage"),
                d.get("check_duration_ms"),
                d.get("browser_launch_ms"),
                d.get("navigation_ms"),
                d.get("page_load_ms"),
                d.get("login_check_ms"),
                d.get("model_detect_ms"),
                d.get("model_switch_ms"),
                d.get("prompt_send_ms"),
                d.get("prompt_response_ms"),
                d.get("limit_detect_ms"),
                d.get("screenshot_ms"),
                worker_host,
                worker_ip,
                d.get("worker_type", "local"),
                platform.platform(),
                platform.python_version(),
                pw_version,
                d.get("browser_headed", False),
                d.get("browser_timeout_ms"),
                d.get("browser_user_agent"),
                d.get("browser_viewport_width"),
                d.get("browser_viewport_height"),
                d.get("screenshot_path"),
                bool(d.get("screenshot_path")),
                d.get("screenshot_size_bytes"),
                d.get("source_application", "precheck_script"),
                d.get("triggered_by"),
                d.get("pause_written", False),
                d.get("pause_until"),
                d.get("pause_cleared", False),
                d.get("pause_reason"),
                d.get("page_title"),
                d.get("page_language"),
                d.get("limit_banner_text", "")[:2000] if d.get("limit_banner_text") else None,
                d.get("menu_text", "")[:2000] if d.get("menu_text") else None,
                d.get("retry_count", 0),
                d.get("total_attempts", 1),
                psycopg2.extras.Json(d.get("metadata") or {}),
                psycopg2.extras.Json(d.get("timings_breakdown") or {}),
                d.get("raw_body_text_sample", "")[:500] if d.get("raw_body_text_sample") else None,
            ))
        conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ DB insert failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def _write_limit_proof(page, profile_name: str, cache_dir: Path, run_id: str) -> tuple[str | None, int | None]:
    safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", profile_name)
    cache_path = cache_dir / f"limit_proof_{safe_profile}.jpg"
    cache_run_path = cache_dir / f"limit_proof_{safe_profile}_{run_id}.jpg"
    try:
        page.screenshot(path=cache_path, type="jpeg", quality=70, full_page=True)
        if cache_path.exists():
            shutil.copy2(cache_path, cache_run_path)
    except Exception:
        return None, None

    try:
        repo_root = Path(__file__).resolve().parents[1]
        jobs_dir = repo_root / "jobs"
        if jobs_dir.exists():
            batches = sorted([d for d in jobs_dir.iterdir() if d.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True)
            if batches:
                live_dir = batches[0] / "ocr" / "artifacts" / "live"
                live_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_path, live_dir / f"{safe_profile}_limit.jpg")
    except Exception:
        pass

    if cache_path.exists():
        try:
            return str(cache_path), cache_path.stat().st_size
        except Exception:
            return str(cache_path), None
    return None, None


def _handle_limit_detected(profile_name: str, cache_dir: Path, body_text: str, run_id: str, page):
    """Helper to process detected limit and return result tuple."""
    db = _get_db_manager(profile_name)
    handler = ProLimitHandler(profile_name, db_manager=db, pro_only=True) if db else None
    reset_time = handler.extract_reset_datetime_from_text(body_text or "") if handler else None
    if not reset_time:
        reset_time = datetime.now() + timedelta(minutes=60)
    screenshot_start = time.time()
    screenshot_path, screenshot_size = _write_limit_proof(page, profile_name, cache_dir, run_id)
    screenshot_ms = int((time.time() - screenshot_start) * 1000)
    if not screenshot_path:
        return False, None, "LIMIT (no proof)", None, None, None, screenshot_ms
    pause_until = reset_time + timedelta(seconds=180)
    _write_pause(cache_dir, profile_name, pause_until, "precheck", run_id)
    return True, reset_time, None, screenshot_path, pause_until, screenshot_size, screenshot_ms


def _find_model_button(page):
    candidates = [
        page.locator("button").filter(has_text=_MODEL_BUTTON_RE),
        page.locator("[role='button']").filter(has_text=_MODEL_BUTTON_RE),
        page.locator("button[aria-label*='model' i]"),
        page.locator("[role='button'][aria-label*='model' i]"),
        page.locator("button[aria-label*='modelu' i]"),
        page.locator("[role='button'][aria-label*='modelu' i]"),
        page.locator("[data-testid*='model' i]"),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _detect_model_label(page) -> str | None:
    try:
        loc = _find_model_button(page)
        if not loc:
            return None
        btn = loc.last
        if btn.count() > 0 and btn.is_visible(timeout=1500):
            t = btn.inner_text(timeout=1500).strip()
            if t:
                return t
            aria = btn.get_attribute("aria-label")
            if aria:
                return aria.strip()
    except Exception:
        pass
    return None


def _find_prompt_box(page, timeout_ms: int = 8000):
    selectors = [
        'div[contenteditable="true"]',
        'rich-textarea [contenteditable="true"]',
        "textarea[placeholder]",
        ".ql-editor",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout_ms):
                return loc
        except Exception:
            continue
    return None


def _ensure_pro_model(page) -> str | None:
    before = _detect_model_label(page) or "unknown"
    if re.search(_PRO_MODEL_RE, before):
        return before
    for _ in range(3):
        try:
            loc = _find_model_button(page)
            if not loc:
                page.wait_for_timeout(400)
                continue
            btn = loc.last
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                btn.click()
                page.wait_for_timeout(700)
                menu = page.locator("[role='menu'], [role='listbox']").first
                if menu.count() > 0 and menu.is_visible(timeout=1500):
                    pro_item = menu.locator(
                        "div[role='menuitem'], button[role='menuitem'], [role='menuitemradio'], [role='option'], button, div"
                    ).filter(has_text=_PRO_MODEL_RE).first
                else:
                    pro_item = page.locator(
                        "div[role='menuitem'], button[role='menuitem'], [role='menuitemradio'], [role='option']"
                    ).filter(has_text=_PRO_MODEL_RE).first
                if pro_item.count() > 0 and pro_item.is_visible(timeout=1500):
                    pro_item.click(force=True)
                    page.wait_for_timeout(1100)
                    after = _detect_model_label(page) or before
                    return after
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
        except Exception:
            pass
        page.wait_for_timeout(300)
    return _detect_model_label(page) or before


def _read_model_menu_text(page) -> str:
    try:
        loc = _find_model_button(page)
        if loc and loc.count() > 0 and loc.last.is_visible(timeout=1500):
            loc.last.click()
            page.wait_for_timeout(600)
            menu = page.locator("div[role='menu'], div[role='listbox']").first
            if menu.count() > 0:
                txt = menu.inner_text(timeout=1500)
            else:
                txt = page.locator("body").inner_text(timeout=1500)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return txt or ""
    except Exception:
        return ""
    return ""


def _check_profile(
    profile_name: str,
    profile_path: Path,
    cache_dir: Path,
    timeout_ms: int,
    run_id: str,
    quick_mode: bool,
):
    """Check if profile has Pro limit with proof (banner/menu + screenshot)."""
    check_start = time.time()
    tracking: dict[str, Any] = {
        "run_id": run_id,
        "profile_name": profile_name,
        "profile_path": str(profile_path),
        "profile_type": "gemini",
        "is_limited": False,
        "reset_time": None,
        "limit_detected_method": None,
        "model_initial": None,
        "model_after_switch": None,
        "model_final": None,
        "model_switch_needed": False,
        "model_switch_success": None,
        "model_switch_attempts": 0,
        "session_valid": None,
        "login_detected": None,
        "login_provider": None,
        "account_email": None,
        "chat_opened": False,
        "chat_ready": False,
        "prompt_box_found": False,
        "prompt_sent": False,
        "prompt_response_received": False,
        "status": None,
        "error_message": None,
        "error_stage": None,
        "check_duration_ms": 0,
        "browser_launch_ms": None,
        "navigation_ms": None,
        "page_load_ms": None,
        "login_check_ms": None,
        "model_detect_ms": None,
        "model_switch_ms": None,
        "prompt_send_ms": None,
        "prompt_response_ms": None,
        "limit_detect_ms": None,
        "screenshot_ms": None,
        "worker_type": "local",
        "browser_headed": False,
        "browser_timeout_ms": timeout_ms,
        "browser_user_agent": None,
        "browser_viewport_width": None,
        "browser_viewport_height": None,
        "screenshot_path": None,
        "screenshot_size_bytes": None,
        "source_application": "precheck_script",
        "triggered_by": None,
        "pause_written": False,
        "pause_until": None,
        "pause_cleared": False,
        "pause_reason": None,
        "page_title": None,
        "page_language": None,
        "limit_banner_text": None,
        "menu_text": None,
        "retry_count": 0,
        "total_attempts": 1,
        "metadata": {"quick_mode": quick_mode},
        "timings_breakdown": {},
        "raw_body_text_sample": None,
    }
    result = {
        "profile": profile_name,
        "limited": False,
        "reset_time": None,
        "status": None,
        "error": None,
        "duration_ms": 0,
    }

    body_text = ""
    proof_error = None
    context = None


    try:
        with sync_playwright() as p:
            browser_start = time.time()
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            tracking["browser_launch_ms"] = int((time.time() - browser_start) * 1000)
            tracking["timings_breakdown"]["browser_launch"] = tracking["browser_launch_ms"]
            page = context.pages[0] if context.pages else context.new_page()
            nav_start = time.time()
            page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=timeout_ms)
            tracking["navigation_ms"] = int((time.time() - nav_start) * 1000)
            tracking["timings_breakdown"]["navigation"] = tracking["navigation_ms"]
            load_start = time.time()
            time.sleep(1)
            tracking["page_load_ms"] = int((time.time() - load_start) * 1000)
            tracking["timings_breakdown"]["page_load"] = tracking["page_load_ms"]

            try:
                tracking["page_title"] = page.title()
            except Exception:
                pass
            try:
                tracking["page_language"] = page.evaluate("() => document.documentElement.lang || null")
            except Exception:
                pass
            try:
                tracking["browser_user_agent"] = page.evaluate("() => navigator.userAgent")
            except Exception:
                pass
            try:
                viewport = page.viewport_size
                if viewport:
                    tracking["browser_viewport_width"] = viewport.get("width")
                    tracking["browser_viewport_height"] = viewport.get("height")
                else:
                    dims = page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
                    tracking["browser_viewport_width"] = dims.get("w")
                    tracking["browser_viewport_height"] = dims.get("h")
            except Exception:
                pass

            tracking["chat_opened"] = "gemini.google.com" in page.url

            login_start = time.time()
            prompt_probe = _find_prompt_box(page, timeout_ms=8000)
            if prompt_probe:
                tracking["prompt_box_found"] = True
                tracking["chat_ready"] = True
                tracking["login_detected"] = False
                tracking["session_valid"] = True
            body_text = page.locator("body").inner_text(timeout=5000)
            tracking["raw_body_text_sample"] = body_text[:500] if body_text else None

            login_patterns = [
                r"Zaloguj się",
                r"Sign in",
                r"Log in",
                r"Create account",
                r"Załóż konto",
            ]
            if not tracking.get("session_valid"):
                tracking["login_detected"] = any(re.search(p, body_text, re.IGNORECASE) for p in login_patterns)
                tracking["session_valid"] = not tracking["login_detected"]
            tracking["login_check_ms"] = int((time.time() - login_start) * 1000)
            tracking["timings_breakdown"]["login_check"] = tracking["login_check_ms"]
            if tracking["login_detected"]:
                tracking["status"] = "SESSION_EXPIRED"
                tracking["error_message"] = "Login required"
                tracking["error_stage"] = "session_check"
                try:
                    safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", profile_name)
                    debug_subdir = cache_dir / "debug_screenshots"
                    debug_subdir.mkdir(parents=True, exist_ok=True)
                    debug_path = debug_subdir / f"session_expired_{safe_profile}_{run_id}.jpg"
                    page.screenshot(path=str(debug_path), type="jpeg", quality=70, full_page=True)
                    tracking["screenshot_path"] = str(debug_path)
                    tracking["screenshot_size_bytes"] = debug_path.stat().st_size
                except Exception:
                    pass
                tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                result["status"] = tracking["status"]
                result["error"] = tracking["status"]
                result["duration_ms"] = tracking["check_duration_ms"]
                return {"tracking": tracking, **result}

            email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", body_text)
            if email_match:
                tracking["account_email"] = email_match.group(0)

            # Check if limit banner already visible
            if re.search(PRO_LIMIT_TEXT_RE, body_text or ""):
                limit_start = time.time()
                ok, reset_time, err, screenshot_path, pause_until, screenshot_size, screenshot_ms = _handle_limit_detected(
                    profile_name, cache_dir, body_text, run_id, page
                )
                tracking["limit_detect_ms"] = int((time.time() - limit_start) * 1000)
                tracking["limit_detected_method"] = "banner_initial"
                tracking["limit_banner_text"] = body_text
                tracking["screenshot_path"] = screenshot_path
                tracking["screenshot_size_bytes"] = screenshot_size
                tracking["screenshot_ms"] = screenshot_ms
                tracking["pause_written"] = bool(ok)
                tracking["pause_until"] = pause_until
                tracking["pause_reason"] = "limit_banner"
                tracking["is_limited"] = bool(ok)
                tracking["reset_time"] = reset_time
                if ok:
                    result["limited"] = True
                    result["reset_time"] = reset_time
                    tracking["status"] = "LIMIT"
                else:
                    tracking["status"] = err or "LIMIT_NO_PROOF"
                    tracking["error_message"] = err
                tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                result["status"] = tracking["status"]
                result["duration_ms"] = tracking["check_duration_ms"]
                return {"tracking": tracking, **result}

            # Try to switch to Pro and detect limit from menu text
            model_detect_start = time.time()
            tracking["model_initial"] = _detect_model_label(page)
            tracking["model_detect_ms"] = int((time.time() - model_detect_start) * 1000)
            tracking["timings_breakdown"]["model_detect"] = tracking["model_detect_ms"]

            if not (tracking["model_initial"] and re.search(_PRO_MODEL_RE, tracking["model_initial"])):
                tracking["model_switch_needed"] = True
                switch_start = time.time()
                for attempt in range(3):
                    tracking["model_switch_attempts"] = attempt + 1
                    tracking["model_after_switch"] = _ensure_pro_model(page)
                    if tracking["model_after_switch"] and re.search(_PRO_MODEL_RE, tracking["model_after_switch"]):
                        tracking["model_switch_success"] = True
                        break
                tracking["model_switch_ms"] = int((time.time() - switch_start) * 1000)
                tracking["timings_breakdown"]["model_switch"] = tracking["model_switch_ms"]
            tracking["model_final"] = _detect_model_label(page) or tracking["model_after_switch"] or tracking["model_initial"]

            body_text = page.locator("body").inner_text(timeout=5000)
            if re.search(PRO_LIMIT_TEXT_RE, body_text or ""):
                limit_start = time.time()
                ok, reset_time, err, screenshot_path, pause_until, screenshot_size, screenshot_ms = _handle_limit_detected(
                    profile_name, cache_dir, body_text, run_id, page
                )
                tracking["limit_detect_ms"] = int((time.time() - limit_start) * 1000)
                tracking["limit_detected_method"] = "banner_after_switch"
                tracking["limit_banner_text"] = body_text
                tracking["screenshot_path"] = screenshot_path
                tracking["screenshot_size_bytes"] = screenshot_size
                tracking["screenshot_ms"] = screenshot_ms
                tracking["pause_written"] = bool(ok)
                tracking["pause_until"] = pause_until
                tracking["pause_reason"] = "limit_banner"
                tracking["is_limited"] = bool(ok)
                tracking["reset_time"] = reset_time
                if ok:
                    result["limited"] = True
                    result["reset_time"] = reset_time
                    tracking["status"] = "LIMIT"
                else:
                    tracking["status"] = err or "LIMIT_NO_PROOF"
                    tracking["error_message"] = err
                tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                result["status"] = tracking["status"]
                result["duration_ms"] = tracking["check_duration_ms"]
                return {"tracking": tracking, **result}
            menu_text = _read_model_menu_text(page)
            tracking["menu_text"] = menu_text or None
            if menu_text and re.search(PRO_LIMIT_TEXT_RE, menu_text):
                limit_start = time.time()
                ok, reset_time, err, screenshot_path, pause_until, screenshot_size, screenshot_ms = _handle_limit_detected(
                    profile_name, cache_dir, menu_text, run_id, page
                )
                tracking["limit_detect_ms"] = int((time.time() - limit_start) * 1000)
                tracking["limit_detected_method"] = "menu_text"
                tracking["limit_banner_text"] = menu_text
                tracking["screenshot_path"] = screenshot_path
                tracking["screenshot_size_bytes"] = screenshot_size
                tracking["screenshot_ms"] = screenshot_ms
                tracking["pause_written"] = bool(ok)
                tracking["pause_until"] = pause_until
                tracking["pause_reason"] = "limit_menu"
                tracking["is_limited"] = bool(ok)
                tracking["reset_time"] = reset_time
                if ok:
                    result["limited"] = True
                    result["reset_time"] = reset_time
                    tracking["status"] = "LIMIT"
                else:
                    tracking["status"] = err or "LIMIT_NO_PROOF"
                    tracking["error_message"] = err
                tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                result["status"] = tracking["status"]
                result["duration_ms"] = tracking["check_duration_ms"]
                return {"tracking": tracking, **result}

            if quick_mode:
                _clear_pause(cache_dir, profile_name)
                tracking["pause_cleared"] = True
                tracking["status"] = "OK"
                tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                result["status"] = tracking["status"]
                result["duration_ms"] = tracking["check_duration_ms"]
                return {"tracking": tracking, **result}

            # Send test prompt to trigger potential limit
            try:
                prompt_box = _find_prompt_box(page, timeout_ms=2000)

                if prompt_box:
                    tracking["prompt_box_found"] = True
                    tracking["chat_ready"] = True
                    # First try to switch to Pro before sending
                    _ensure_pro_model(page)
                    time.sleep(0.5)
                    prompt_send_start = time.time()
                    prompt_box.click()
                    time.sleep(0.3)
                    prompt_box.fill("1")
                    time.sleep(0.3)
                    page.keyboard.press("Enter")
                    tracking["prompt_sent"] = True
                    tracking["prompt_send_ms"] = int((time.time() - prompt_send_start) * 1000)
                    tracking["timings_breakdown"]["prompt_send"] = tracking["prompt_send_ms"]

                    # Multiple retries to detect banner with increasing waits
                    banner_detected = False
                    response_start = time.time()
                    for retry in range(5):
                        wait_time = 3 + retry * 2  # 3s, 5s, 7s, 9s, 11s
                        time.sleep(wait_time)
                        tracking["retry_count"] = retry

                        body_text = page.locator("body").inner_text(timeout=5000)

                        # Check for limit banner after sending prompt
                        if re.search(PRO_LIMIT_TEXT_RE, body_text or ""):
                            print(f"  [{profile_name}] Limit banner detected on retry {retry+1}")
                            limit_start = time.time()
                            ok, reset_time, err, screenshot_path, pause_until, screenshot_size, screenshot_ms = _handle_limit_detected(
                                profile_name, cache_dir, body_text, run_id, page
                            )
                            tracking["limit_detect_ms"] = int((time.time() - limit_start) * 1000)
                            tracking["limit_detected_method"] = "prompt_response"
                            tracking["limit_banner_text"] = body_text
                            tracking["screenshot_path"] = screenshot_path
                            tracking["screenshot_size_bytes"] = screenshot_size
                            tracking["screenshot_ms"] = screenshot_ms
                            tracking["pause_written"] = bool(ok)
                            tracking["pause_until"] = pause_until
                            tracking["pause_reason"] = "prompt_limit"
                            tracking["is_limited"] = bool(ok)
                            tracking["reset_time"] = reset_time
                            banner_detected = True
                            if ok:
                                result["limited"] = True
                                result["reset_time"] = reset_time
                                tracking["status"] = "LIMIT"
                            else:
                                tracking["status"] = err or "LIMIT_NO_PROOF"
                                tracking["error_message"] = err
                            tracking["prompt_response_ms"] = int((time.time() - response_start) * 1000)
                            tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                            result["status"] = tracking["status"]
                            result["duration_ms"] = tracking["check_duration_ms"]
                            return {"tracking": tracking, **result}

                        # Also check menu text for limit info
                        menu_text = _read_model_menu_text(page)
                        tracking["menu_text"] = menu_text or tracking["menu_text"]
                        if menu_text and re.search(PRO_LIMIT_TEXT_RE, menu_text):
                            print(f"  [{profile_name}] Limit banner in menu on retry {retry+1}")
                            limit_start = time.time()
                            ok, reset_time, err, screenshot_path, pause_until, screenshot_size, screenshot_ms = _handle_limit_detected(
                                profile_name, cache_dir, menu_text, run_id, page
                            )
                            tracking["limit_detect_ms"] = int((time.time() - limit_start) * 1000)
                            tracking["limit_detected_method"] = "menu_text"
                            tracking["limit_banner_text"] = menu_text
                            tracking["screenshot_path"] = screenshot_path
                            tracking["screenshot_size_bytes"] = screenshot_size
                            tracking["screenshot_ms"] = screenshot_ms
                            tracking["pause_written"] = bool(ok)
                            tracking["pause_until"] = pause_until
                            tracking["pause_reason"] = "limit_menu"
                            tracking["is_limited"] = bool(ok)
                            tracking["reset_time"] = reset_time
                            banner_detected = True
                            if ok:
                                result["limited"] = True
                                result["reset_time"] = reset_time
                                tracking["status"] = "LIMIT"
                            else:
                                tracking["status"] = err or "LIMIT_NO_PROOF"
                                tracking["error_message"] = err
                            tracking["prompt_response_ms"] = int((time.time() - response_start) * 1000)
                            tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                            result["status"] = tracking["status"]
                            result["duration_ms"] = tracking["check_duration_ms"]
                            return {"tracking": tracking, **result}

                        # Check if model was forced to Fast/Flash
                        label = _detect_model_label(page) or ""
                        is_fast = re.search(_FAST_MODEL_RE, label) and not re.search(_PRO_MODEL_RE, label)

                        if is_fast:
                            # Model is on Fast - try to switch to Pro again to trigger banner
                            print(f"  [{profile_name}] Retry {retry+1}: Model on Fast, trying Pro switch...")
                            _ensure_pro_model(page)
                            time.sleep(1)

                            # Check body again after Pro switch attempt
                            body_text = page.locator("body").inner_text(timeout=5000)
                            if re.search(PRO_LIMIT_TEXT_RE, body_text or ""):
                                print(f"  [{profile_name}] Limit banner after Pro switch on retry {retry+1}")
                                limit_start = time.time()
                                ok, reset_time, err, screenshot_path, pause_until, screenshot_size, screenshot_ms = _handle_limit_detected(
                                    profile_name, cache_dir, body_text, run_id, page
                                )
                                tracking["limit_detect_ms"] = int((time.time() - limit_start) * 1000)
                                tracking["limit_detected_method"] = "prompt_response"
                                tracking["limit_banner_text"] = body_text
                                tracking["screenshot_path"] = screenshot_path
                                tracking["screenshot_size_bytes"] = screenshot_size
                                tracking["screenshot_ms"] = screenshot_ms
                                tracking["pause_written"] = bool(ok)
                                tracking["pause_until"] = pause_until
                                tracking["pause_reason"] = "prompt_limit"
                                tracking["is_limited"] = bool(ok)
                                tracking["reset_time"] = reset_time
                                banner_detected = True
                                if ok:
                                    result["limited"] = True
                                    result["reset_time"] = reset_time
                                    tracking["status"] = "LIMIT"
                                else:
                                    tracking["status"] = err or "LIMIT_NO_PROOF"
                                    tracking["error_message"] = err
                                tracking["prompt_response_ms"] = int((time.time() - response_start) * 1000)
                                tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
                                result["status"] = tracking["status"]
                                result["duration_ms"] = tracking["check_duration_ms"]
                                return {"tracking": tracking, **result}
                        else:
                            # Still on Pro - no limit, we can stop checking
                            print(f"  [{profile_name}] Retry {retry+1}: Still on Pro model - OK")
                            tracking["prompt_response_received"] = True
                            tracking["prompt_response_ms"] = int((time.time() - response_start) * 1000)
                            break

                    tracking["total_attempts"] = tracking["retry_count"] + 1

                    # After all retries, check final state
                    label = _detect_model_label(page) or ""
                    tracking["model_final"] = label or tracking["model_final"]
                    if re.search(_FAST_MODEL_RE, label) and not re.search(_PRO_MODEL_RE, label):
                        # Still on Fast after all retries - no banner appeared
                        proof_error = "FAST_NO_BANNER"
                        # Take a screenshot for debugging
                        try:
                            safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", profile_name)
                            debug_subdir = cache_dir / "debug_screenshots"
                            debug_subdir.mkdir(parents=True, exist_ok=True)
                            debug_path = debug_subdir / f"fast_no_banner_{safe_profile}_{run_id}.jpg"
                            page.screenshot(path=str(debug_path), type="jpeg", quality=70, full_page=True)
                            print(f"  [{profile_name}] Fast but no banner - screenshot saved: {debug_path}")
                        except Exception:
                            pass
                else:
                    print(f"  [{profile_name}] Could not find prompt input")
                    proof_error = "NO_PROMPT"
                    tracking["prompt_box_found"] = False
                    tracking["chat_ready"] = False
                    tracking["error_message"] = "Prompt box not found"
                    tracking["error_stage"] = "prompt_detection"
                    try:
                        safe_profile = re.sub(r"[^a-zA-Z0-9_.-]+", "_", profile_name)
                        debug_subdir = cache_dir / "debug_screenshots"
                        debug_subdir.mkdir(parents=True, exist_ok=True)
                        debug_path = debug_subdir / f"no_prompt_{safe_profile}_{run_id}.jpg"
                        page.screenshot(path=str(debug_path), type="jpeg", quality=70, full_page=True)
                        tracking["screenshot_path"] = str(debug_path)
                        tracking["screenshot_size_bytes"] = debug_path.stat().st_size
                    except Exception:
                        pass
            except Exception as e:
                print(f"  [{profile_name}] Test prompt failed: {e}")
                tracking["error_message"] = str(e)
                tracking["error_stage"] = "prompt_send"

    except Exception as e:
        print(f"  [{profile_name}] Browser error: {e}")
        tracking["error_message"] = str(e)
        tracking["error_stage"] = "browser"
        tracking["status"] = "BROWSER_ERROR"
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass

    if re.search(PRO_LIMIT_TEXT_RE, body_text or ""):
        # No page handle here; treat as no proof.
        tracking["status"] = "LIMIT_NO_PROOF"
        tracking["error_message"] = "LIMIT_NO_PROOF"
        tracking["limit_banner_text"] = body_text or None
        tracking["limit_detected_method"] = tracking["limit_detected_method"] or "banner_fallback"

    _clear_pause(cache_dir, profile_name)
    tracking["pause_cleared"] = True
    if proof_error:
        tracking["status"] = proof_error
        tracking["error_message"] = proof_error

    tracking["check_duration_ms"] = int((time.time() - check_start) * 1000)
    result["duration_ms"] = tracking["check_duration_ms"]
    if tracking["is_limited"]:
        tracking["status"] = "LIMIT"
    elif tracking["error_message"]:
        tracking["status"] = tracking["status"] or proof_error or "ERROR"
    else:
        tracking["status"] = "OK"
    result["status"] = tracking["status"]
    result["error"] = tracking["error_message"]
    result["limited"] = tracking["is_limited"]
    result["reset_time"] = tracking["reset_time"]
    return {"tracking": tracking, **result}


def _summarize_results(results: list[tuple[str, str, int]]) -> dict:
    summary = {"ok": 0, "limit": 0, "error": 0, "skipped": 0, "total": len(results)}
    for _, status, _ in results:
        label = str(status or "").upper()
        if "SKIPPED" in label:
            summary["skipped"] += 1
        elif "LIMIT" in label:
            summary["limit"] += 1
        elif "OK" in label:
            summary["ok"] += 1
        else:
            summary["error"] += 1
    return summary


def _write_status(
    cache_dir: Path,
    run_id: str,
    results: list[tuple[str, str, int]],
    *,
    in_progress: bool = False,
    total: int | None = None,
    started_at: str | None = None,
    current_profile: str | None = None,
    quick_mode: bool | None = None,
):
    total = total if total is not None else len(results)
    summary = _summarize_results(results)
    summary["total"] = total
    payload = {
        "run_id": run_id,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "in_progress": in_progress,
        "total": total,
        "completed": len(results),
        "pending": max(total - len(results), 0),
        "summary": summary,
        "current_profile": current_profile,
        "results": [{"profile": p, "status": s, "duration_ms": d} for p, s, d in results],
        "source": "local",
        "worker_host": socket.gethostname(),
    }
    if quick_mode is not None:
        payload["quick_mode"] = bool(quick_mode)
    path = cache_dir / "limit_precheck_status.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_history(cache_dir: Path, run_id: str, results: list[tuple[str, str, int]]):
    path = cache_dir / "limit_precheck_history.json"
    entry = {
        "run_id": run_id,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "results": [{"profile": p, "status": s, "duration_ms": d} for p, s, d in results],
        "source": "local",
        "worker_host": socket.gethostname(),
    }
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = []
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    data.append(entry)
    if len(data) > 200:
        data = data[-200:]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profiles-dir",
        default=str(Path.home() / ".cache/ocr-dashboard-v3"),
        help="Base directory containing gemini-profile-* folders.",
    )
    parser.add_argument(
        "--profiles",
        default="",
        help="Comma-separated profile names to check (optional).",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30000,
        help="Navigation timeout in milliseconds.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Repeat check every N seconds (0 = run once).",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=int(os.environ.get("OCR_PRECHECK_PARALLEL", "2")),
        help="Number of profiles to check in parallel.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: only check Pro/menu/banners without sending prompt.",
    )
    args = parser.parse_args()

    base_dir = validate_profiles_dir(args.profiles_dir)
    cache_dir = validate_cache_dir(str(base_dir))
    only = {p.strip() for p in args.profiles.split(",") if p.strip()} or None

    while True:
        # Clear cached status/history to avoid stale results between runs
        try:
            status_path = cache_dir / "limit_precheck_status.json"
            history_path = cache_dir / "limit_precheck_history.json"
            if status_path.exists():
                status_path.unlink()
            if history_path.exists():
                history_path.unlink()
        except Exception:
            pass

        # Clear cached artifacts to avoid stale screenshots between runs
        try:
            for path in cache_dir.glob("limit_proof_*.jpg"):
                try:
                    path.unlink()
                except Exception:
                    pass
            debug_dir = cache_dir / "debug_screenshots"
            if debug_dir.exists():
                for path in debug_dir.glob("*.jpg"):
                    try:
                        path.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

        # Remove stale live limit screenshots from the latest batch (if any)
        try:
            repo_root = Path(__file__).resolve().parents[1]
            jobs_dir = repo_root / "jobs"
            if jobs_dir.exists():
                batches = [d for d in jobs_dir.iterdir() if d.is_dir()]
                if batches:
                    latest = max(batches, key=lambda x: x.stat().st_mtime)
                    live_dir = latest / "ocr" / "artifacts" / "live"
                    if live_dir.exists():
                        for path in live_dir.glob("*_limit.jpg"):
                            try:
                                path.unlink()
                            except Exception:
                                pass
        except Exception:
            pass

        profiles = _iter_profiles(base_dir, only)
        if not profiles:
            print(f"No profiles found in {base_dir}")
            return 0

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_started_at = datetime.now().isoformat(timespec="seconds")
        results = []
        skipped_only = []

        from concurrent.futures import ThreadPoolExecutor, as_completed

        result_map = {}
        profile_paths = {name: path for name, path in profiles}
        max_workers = max(1, int(args.parallel))
        check_start_times = {}
        check_durations = {}

        total_profiles = len(profiles)

        def _progress_results() -> list[tuple[str, str, int]]:
            return [(name, status, check_durations.get(name, 0)) for name, status in result_map.items()]

        _write_status(
            cache_dir,
            run_id,
            [],
            in_progress=True,
            total=total_profiles,
            started_at=run_started_at,
            quick_mode=args.quick,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_check_profile, name, path, cache_dir, args.timeout_ms, run_id, args.quick): name
                for name, path in profiles
            }
            # Track start times
            for future, name in future_map.items():
                check_start_times[name] = time.time()

            for future in as_completed(future_map):
                name = future_map[future]
                check_duration_ms = int((time.time() - check_start_times.get(name, time.time())) * 1000)
                profile_path = str(profile_paths.get(name, ""))

                try:
                    result = future.result()
                    limited = result.get("limited", False)
                    reset_time = result.get("reset_time")
                    status = result.get("status") or "OK"
                    err = result.get("error")
                    tracking = result.get("tracking") or {}
                    tracking.setdefault("run_id", run_id)
                    tracking.setdefault("profile_name", name)
                    tracking.setdefault("profile_path", profile_path)
                    tracking.setdefault("check_duration_ms", result.get("duration_ms", check_duration_ms))

                    if limited:
                        result_map[name] = f"LIMIT until {reset_time.strftime('%H:%M') if reset_time else '?'}"
                    elif err:
                        result_map[name] = str(err)
                    else:
                        result_map[name] = "OK"

                    check_durations[name] = int(tracking.get("check_duration_ms") or check_duration_ms)
                    _log_check_to_db(_normalize_check_data(tracking))
                except Exception as e:
                    result_map[name] = f"ERROR {e}"
                    check_durations[name] = check_duration_ms
                    _log_check_to_db(_normalize_check_data({
                        "run_id": run_id,
                        "profile_name": name,
                        "profile_path": profile_path,
                        "is_limited": False,
                        "status": "ERROR",
                        "error_message": str(e),
                        "error_stage": "thread_result",
                        "check_duration_ms": check_duration_ms,
                        "source_application": "precheck_script",
                    }))
                _write_status(
                    cache_dir,
                    run_id,
                    _progress_results(),
                    in_progress=True,
                    total=total_profiles,
                    started_at=run_started_at,
                    current_profile=name,
                    quick_mode=args.quick,
                )

        for name, _ in profiles:
            results.append((name, result_map.get(name, "ERROR no result"), check_durations.get(name, 0)))

        _write_status(
            cache_dir,
            run_id,
            results,
            in_progress=False,
            total=total_profiles,
            started_at=run_started_at,
            quick_mode=args.quick,
        )
        _append_history(cache_dir, run_id, results)
        print(json.dumps({"run_id": run_id, "profiles": results}, ensure_ascii=False, indent=2))

        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
