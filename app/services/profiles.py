"""
OCR Dashboard V2 - Profile Service
Profile management business logic.
"""

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..config import CACHE_DIR
from ..utils.db import pg_cursor

LOGS_DIR = Path(__file__).parents[2] / "logs" / "profiles"
MAX_LOG_BYTES = 5 * 1024 * 1024
_INIT_WORKER_RE = re.compile(r"\[Init\]\s+Created worker (\d+)")
_PROMPT_SENT_RE = re.compile(r"\[W(\d+)\].*Prompt sent\.")
_PROFILE_LOG_CACHE: dict[str, dict[str, object]] = {}
_SESSION_MARKER = ".session_start"
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _parse_log_timestamp_utc(line: str) -> datetime | None:
    """Parse log timestamps like: '2026-01-31 23:15:22,734 - INFO - ...' as UTC."""
    match = _LOG_TS_RE.match(line or "")
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return dt


def _read_log_tail(log_file: Path, max_bytes: int = MAX_LOG_BYTES) -> list[str]:
    """Read tail of a log file and return lines."""
    try:
        size = log_file.stat().st_size
        with log_file.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        return data.decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def get_profile_worker_progress(profile_name: str) -> dict[str, int]:
    """Return counts of initialized workers and sent prompts for a profile."""
    log_file = LOGS_DIR / f"{profile_name}.log"
    if not log_file.exists():
        return {"workers_initialized": 0, "workers_prompt_sent": 0}

    try:
        mtime = log_file.stat().st_mtime
    except OSError:
        return {"workers_initialized": 0, "workers_prompt_sent": 0}

    cached = _PROFILE_LOG_CACHE.get(profile_name)
    if cached and cached.get("mtime") == mtime:
        return cached.get("data", {"workers_initialized": 0, "workers_prompt_sent": 0})

    lines = _read_log_tail(log_file)
    if not lines:
        data = {"workers_initialized": 0, "workers_prompt_sent": 0}
        _PROFILE_LOG_CACHE[profile_name] = {"mtime": mtime, "data": data}
        return data

    session_start = get_profile_session_start(profile_name)
    if session_start is not None and session_start.tzinfo is None:
        session_start = session_start.replace(tzinfo=UTC)

    start_idx = 0
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if "[Startup]" in line and "Engine starting for profile" in line:
            if session_start is not None:
                dt = _parse_log_timestamp_utc(line)
                # If we cannot parse the timestamp, treat it as pre-session.
                if dt is None or dt < session_start:
                    continue
            start_idx = i
            break

    init_workers: set[str] = set()
    prompt_workers: set[str] = set()
    for line in lines[start_idx:]:
        init_match = _INIT_WORKER_RE.search(line)
        if init_match:
            init_workers.add(init_match.group(1))
        prompt_match = _PROMPT_SENT_RE.search(line)
        if prompt_match:
            prompt_workers.add(prompt_match.group(1))

    data = {
        "workers_initialized": len(init_workers),
        "workers_prompt_sent": len(prompt_workers),
    }
    _PROFILE_LOG_CACHE[profile_name] = {"mtime": mtime, "data": data}
    return data


def get_profile_last_error(profile_name: str) -> str | None:
    """Return the timestamp of the most recent error/critical line from the profile log."""
    log_file = LOGS_DIR / f"{profile_name}.log"
    if not log_file.exists():
        return None

    lines = _read_log_tail(log_file)
    if not lines:
        return None

    session_start = get_profile_session_start(profile_name)
    if session_start is not None and session_start.tzinfo is None:
        session_start = session_start.replace(tzinfo=UTC)

    for line in reversed(lines):
        # Check if line contains error markers
        if not (" - ERROR - " in line or " - CRITICAL - " in line or "❌" in line):
            continue

        dt = _parse_log_timestamp_utc(line)
        if dt is None:
            # If we can't parse the timestamp, don't surface pre-session noise.
            continue
        if session_start is not None and dt < session_start:
            continue
        return f"{dt.strftime('%Y-%m-%d')} {dt.strftime('%H:%M:%S')}"

    return None


def list_profiles(include_default: bool = False) -> list[str]:
    """List available profiles from cache directory."""
    default_dir = CACHE_DIR / "gemini-profile"
    default_hidden_marker = CACHE_DIR / ".hide_default_profile"
    profiles: list[str] = []

    try:
        if include_default and default_dir.is_dir() and not default_hidden_marker.exists():
            profiles.append("default")

        if CACHE_DIR.exists():
            for d in CACHE_DIR.iterdir():
                if d.is_dir() and d.name.startswith("gemini-profile-"):
                    suffix = d.name.replace("gemini-profile-", "")
                    if suffix == "default" and default_hidden_marker.exists():
                        continue
                    profiles.append(suffix)
    except Exception:
        return []

    return sorted({p for p in profiles if p})


def list_all_profiles() -> list[str]:
    """List all profiles, including default even if hidden."""
    profiles: list[str] = []
    default_dir = CACHE_DIR / "gemini-profile"

    try:
        if default_dir.is_dir():
            profiles.append("default")

        if CACHE_DIR.exists():
            for d in CACHE_DIR.iterdir():
                if d.is_dir() and d.name.startswith("gemini-profile-"):
                    suffix = d.name.replace("gemini-profile-", "")
                    profiles.append(suffix)
    except Exception:
        return []

    return sorted({p for p in profiles if p})


def set_default_profile_hidden(hidden: bool) -> tuple[bool, str]:
    """Hide or show the default profile by toggling a marker file."""
    default_hidden_marker = CACHE_DIR / ".hide_default_profile"
    try:
        if hidden:
            default_hidden_marker.write_text("1", encoding="utf-8")
            return True, "Ukryto profil domyślny"
        if default_hidden_marker.exists():
            default_hidden_marker.unlink()
        return True, "Przywrócono profil domyślny"
    except Exception as e:
        return False, f"Błąd ustawiania widoczności profilu domyślnego: {e}"


def get_profile_dir(profile_name: str) -> Path:
    """Get the directory path for a profile."""
    profile_name = os.path.basename(profile_name)  # noqa: PTH119
    if profile_name == "default":
        return CACHE_DIR / "gemini-profile"
    return CACHE_DIR / f"gemini-profile-{profile_name}"


def profile_exists(profile_name: str) -> bool:
    """Check if profile directory exists."""
    return get_profile_dir(profile_name).is_dir()


def create_profile(name: str) -> tuple[bool, str]:
    """Create a new profile directory."""
    profile_dir = get_profile_dir(name)

    if profile_dir.exists():
        return False, f"Profil '{name}' już istnieje"

    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        return True, f"Utworzono profil '{name}'"
    except Exception as e:
        return False, f"Błąd tworzenia profilu: {e}"


def delete_profile(name: str) -> tuple[bool, str]:
    """Delete a profile directory."""
    if name == "default":
        return False, "Nie można usunąć domyślnego profilu"

    profile_dir = get_profile_dir(name)

    if not profile_dir.exists():
        return False, f"Profil '{name}' nie istnieje"

    try:
        # Security check: Ensure we are deleting a directory inside CACHE_DIR
        resolved_profile_dir = profile_dir.resolve()
        resolved_cache_dir = CACHE_DIR.resolve()

        if (
            not resolved_profile_dir.is_relative_to(resolved_cache_dir)
            or resolved_profile_dir == resolved_cache_dir
        ):
            return (
                False,
                f"Błąd bezpieczeństwa: Próba usunięcia katalogu spoza cache: {profile_dir}",
            )

        shutil.rmtree(resolved_profile_dir)
        return True, f"Usunięto profil '{name}'"
    except Exception as e:
        return False, f"Błąd usuwania profilu: {e}"


def get_active_chrome_profile(profile_name: str) -> str | None:
    """Get the active Chrome profile directory for a profile."""
    try:
        profile_dir = get_profile_dir(profile_name).resolve()
        # Security check
        if not profile_dir.is_relative_to(CACHE_DIR.resolve()):
            return None
    except (ValueError, OSError):
        return None

    active_file = profile_dir / ".active_chrome_profile"

    if active_file.exists():
        try:
            return active_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Fallback: find most recently used profile
    if profile_dir.exists():
        try:
            candidates = []
            for d in profile_dir.iterdir():
                if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile ")):
                    candidates.append(d)

            if candidates:

                def cookie_mtime(p: Path) -> float:
                    cookies = p / "Cookies"
                    try:
                        return cookies.stat().st_mtime
                    except Exception:
                        try:
                            return p.stat().st_mtime
                        except Exception:
                            return 0.0

                latest = max(candidates, key=cookie_mtime)
                return latest.name
        except Exception:
            pass

    return None


def clear_profile_cache(profile_dir: Path) -> None:
    """Clear browser cache from profile directory."""
    # Security check
    try:
        resolved_profile_dir = profile_dir.resolve()
        if not resolved_profile_dir.is_relative_to(CACHE_DIR.resolve()):
            return
    except (ValueError, OSError):
        return

    cache_patterns = [
        "Default/Cache",
        "Default/Code Cache",
        "Default/GPUCache",
        "Profile */Cache",
        "Profile */Code Cache",
        "Profile */GPUCache",
        "GrShaderCache",
        "ShaderCache",
    ]

    for pattern in cache_patterns:
        # Use resolved path for glob to ensure we stay within safe bounds
        for match in resolved_profile_dir.glob(pattern):
            try:
                if match.is_dir():
                    shutil.rmtree(match)
                else:
                    match.unlink()
            except Exception:
                pass


def reset_profile_state(profile_name: str) -> None:
    """Clear runtime state and historical status signals for a profile."""
    _PROFILE_LOG_CACHE.pop(profile_name, None)

    with pg_cursor() as cur:
        if cur is None:
            return

        queries = [
            ("DELETE FROM public.profile_runtime_state WHERE profile_name = %s", (profile_name,)),
            (
                "UPDATE public.critical_events SET resolved_at = NOW() "
                "WHERE profile_name = %s AND resolved_at IS NULL",
                (profile_name,),
            ),
            ("DELETE FROM public.error_traces WHERE profile_name = %s", (profile_name,)),
            ("DELETE FROM public.system_activity_log WHERE profile_name = %s", (profile_name,)),
        ]

        for query, params in queries:
            try:
                cur.execute(query, params)
            except Exception:
                # Table may not exist in some environments.
                continue


def set_profile_session_start(profile_name: str, when: datetime | None = None) -> None:
    """Persist session start time for a profile."""
    profile_dir = get_profile_dir(profile_name)
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        timestamp = when or datetime.now(UTC)
        (profile_dir / _SESSION_MARKER).write_text(
            timestamp.isoformat(timespec="seconds"),
            encoding="utf-8",
        )
    except Exception:
        pass


def reset_all_profiles() -> tuple[list[str], list[str]]:
    """Reset all profiles (cache + runtime state + session marker)."""
    reset: list[str] = []
    errors: list[str] = []

    for profile_name in list_all_profiles():
        profile_dir = get_profile_dir(profile_name)
        if not profile_dir.exists():
            continue

        try:
            clear_profile_cache(profile_dir)
            reset_profile_state(profile_name)
            set_profile_session_start(profile_name)
            reset.append(profile_name)
        except Exception as exc:
            errors.append(f"{profile_name}: {exc!s}")

    return reset, errors


def get_profile_session_start(profile_name: str) -> datetime | None:
    """Read session start time for a profile."""
    profile_dir = get_profile_dir(profile_name)
    marker = profile_dir / _SESSION_MARKER
    if not marker.exists():
        return None
    try:
        raw = marker.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw.replace("Z", ""))
    except Exception:
        return None
