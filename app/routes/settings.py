"""
OCR Dashboard V2 - Settings Routes
Settings and utilities API endpoints.
"""

import concurrent.futures
import contextlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from starlette.responses import StreamingResponse

from src.ocr_engine.utils.activity_logger import ActivityLogger

from ..config import (
    AUTO_RESTART_CONFIG_FILE,
    BASE_DIR,
    CACHE_DIR,
    PENDING_CLEANUP_FILE,
    UPDATE_COUNTS_CONFIG_FILE,
    UPDATE_COUNTS_ON_NEW_PATHS,
    UPDATE_COUNTS_POLL_SEC,
    X11_DISPLAY_CONFIG_FILE,
)
from ..models.requests import CleanupRequest
from ..services import process as process_service
from ..services.cleanup import DEFAULT_CLEANUP_TARGETS, cleanup_folders
from ..services.remote_config import get_effective_remote_config, save_remote_config
from ..services.remote_deployment import RemoteDeploymentService
from ..services.source_resolver import get_resolver
from ..utils.db import execute_single
from ..utils.security import (
    validate_hostname,
    validate_ssh_opts,
    validate_username,
)

# Constants for path validation
_MIN_DRIVE_PREFIX_LEN = 2
_WINDOWS_DRIVE_ROOT_LEN = 3
_WINDOWS_DRIVE_LETTER_LEN = 2

router = APIRouter(prefix="/api", tags=["settings"])

FAVORITES_FILE = Path(__file__).parents[1].parent / "favorites.json"
ENV_FILE = BASE_DIR / ".env"
WEBSHARE_SYNC_STATUS_FILE = CACHE_DIR / "webshare_sync_last.json"

WEBSHARE_KEYS = {
    "WEBSHARE_API_TOKEN",
    "WEBSHARE_PROXY_MODE",
    "WEBSHARE_PLAN_ID",
    "WEBSHARE_PAGE_SIZE",
    "WEBSHARE_COUNTRY_CODES",
    "WEBSHARE_MIN_VALID",
    "WEBSHARE_ASSIGN_SEED",
}


def _read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip()
    return data


def _write_env_file(path: Path, updates: dict[str, str]) -> None:
    lines: list[str] = []
    existing_keys: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                lines.append(line)
                continue
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                existing_keys.add(key)
            else:
                lines.append(line)

    for key, val in updates.items():
        if key not in existing_keys:
            lines.append(f"{key}={val}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


@router.get("/favorites")
def get_favorites():
    """Get list of favorite directories."""
    try:
        if FAVORITES_FILE.exists():
            data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
            return {"favorites": data if isinstance(data, list) else []}
    except Exception:
        pass
    return {"favorites": []}


@router.post("/favorites", responses={500: {"description": "Failed to save favorites"}})
def save_favorites(favorites: list[str]):
    """Save list of favorite directories."""
    try:
        FAVORITES_FILE.write_text(
            json.dumps(favorites, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/browse")
def browse_files(path: str = "/"):
    """Browse filesystem directories."""
    try:
        target = Path(path).expanduser()
        if not target.exists():
            return {"error": "Ścieżka nie istnieje", "items": []}

        items = []
        for entry in sorted(target.iterdir()):
            try:
                items.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "is_dir": entry.is_dir(),
                    }
                )
            except Exception:
                continue

        return {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "items": items,
        }
    except Exception as e:
        return {"error": str(e), "items": []}


@router.get("/auto-restart")
def get_auto_restart_setting():
    """Get auto-restart setting."""
    try:
        if AUTO_RESTART_CONFIG_FILE.exists():
            data = json.loads(AUTO_RESTART_CONFIG_FILE.read_text(encoding="utf-8"))
            return {"enabled": bool(data.get("enabled", False))}
    except Exception:
        pass
    return {"enabled": False}


@router.post("/auto-restart", responses={500: {"description": "Failed to save configuration"}})
def set_auto_restart_setting(enabled: bool):
    """Set auto-restart setting."""
    try:
        AUTO_RESTART_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": bool(enabled),
            "updated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        }
        AUTO_RESTART_CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"success": True, "enabled": enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/x11-display")
def get_x11_display_setting():
    """Get X11 display setting."""
    try:
        if X11_DISPLAY_CONFIG_FILE.exists():
            data = json.loads(X11_DISPLAY_CONFIG_FILE.read_text(encoding="utf-8"))
            return {"display": data.get("display", "")}
    except Exception:
        pass
    return {"display": ""}


@router.post("/x11-display", responses={500: {"description": "Failed to save configuration"}})
def set_x11_display_setting(display: str):
    """Set X11 display setting."""
    try:
        X11_DISPLAY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "display": display,
            "updated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        }
        X11_DISPLAY_CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"success": True, "display": display}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings/remote-hosts")
def get_remote_hosts():
    """Get remote hosts configuration."""
    return {"config": get_effective_remote_config()}


@router.post(
    "/settings/remote-hosts", responses={500: {"description": "Failed to save configuration"}}
)
def set_remote_hosts(payload: dict):
    """Save remote hosts configuration."""
    try:
        saved = save_remote_config(payload)
        return {"success": True, "config": saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _resolve_remote_hosts() -> list[dict]:
    config = get_effective_remote_config()
    hosts = config.get("OCR_REMOTE_HOSTS_LIST", [])
    return hosts if isinstance(hosts, list) else []


# Regex to detect Tailscale SSH authentication URLs
_TAILSCALE_AUTH_URL_RE = re.compile(r"https://login\.tailscale\.com/a/[a-zA-Z0-9]+")


def _extract_tailscale_auth_urls(text: str) -> list[str]:
    """Extract Tailscale authentication URLs from SSH output."""
    return list(set(_TAILSCALE_AUTH_URL_RE.findall(text)))


def _run_ssh_command(
    host: dict, command: str, timeout: int = 20
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run SSH command and return (result, auth_urls).

    Returns a tuple of (CompletedProcess, list of Tailscale auth URLs).
    If Tailscale requires browser authentication, auth_urls will contain the URLs.
    """
    host_addr = validate_hostname(str(host.get("host") or host.get("address") or "").strip())
    host_user = validate_username(str(host.get("user") or "root").strip())
    ssh_opts = str(host.get("ssh") or host.get("sshOpts") or "").strip()

    ssh_cmd_parts = ["ssh"]
    if ssh_opts:
        ssh_cmd_parts.extend(shlex.split(ssh_opts))
    ssh_cmd_parts.extend([f"{host_user}@{host_addr}", command])

    auth_urls: list[str] = []
    try:
        result = subprocess.run(
            ssh_cmd_parts,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        # Check for Tailscale auth URLs in output
        auth_urls = _extract_tailscale_auth_urls(result.stdout + result.stderr)
        return result, auth_urls
    except subprocess.TimeoutExpired as e:
        # On timeout, try to extract auth URLs from partial output
        partial_output = ""
        if e.stdout:
            partial_output += (
                e.stdout
                if isinstance(e.stdout, str)
                else e.stdout.decode("utf-8", errors="replace")
            )
        if e.stderr:
            partial_output += (
                e.stderr
                if isinstance(e.stderr, str)
                else e.stderr.decode("utf-8", errors="replace")
            )
        auth_urls = _extract_tailscale_auth_urls(partial_output)
        # Create a fake CompletedProcess for timeout
        result = subprocess.CompletedProcess(
            args=ssh_cmd_parts,
            returncode=-1,
            stdout=partial_output,
            stderr=f"Command timed out after {timeout}s",
        )
        return result, auth_urls


_PS_PREFIX = "powershell -NoProfile -Command "


def _build_remote_restart_command(host: dict) -> str:
    uri = "http://localhost:9090/api/restart?scope=all&cleanup=true"
    repo_dir = str(host.get("repo") or host.get("repoDir") or "").strip()

    if _is_windows_repo(host, repo_dir):
        script = (
            f"$uri='{uri}'; "
            "try { Invoke-RestMethod -Method Post -Uri $uri | Out-Null; exit 0 } "
            "catch { Write-Output $_.Exception.Message; exit 1 }"
        )
        return _PS_PREFIX + _ps_quote(script)

    return _bash_cmd(f"curl -sS -X POST {shlex.quote(uri)}")


def _restart_remote_host(host: dict, timeout: int, run_ssh: Callable = _run_ssh_command) -> dict:
    host_id = str(host.get("id") or "").strip()
    host_name = str(
        host.get("name")
        or host.get("label")
        or host.get("host")
        or host.get("address")
        or host_id
        or "remote"
    ).strip()
    command = _build_remote_restart_command(host)

    try:
        result = run_ssh(host, command, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "id": host_id,
            "name": host_name,
            "status": "unreachable",
            "message": "Timeout",
        }
    except Exception as exc:
        return {
            "id": host_id,
            "name": host_name,
            "status": "unreachable",
            "message": str(exc),
        }

    if result.returncode == 0:
        return {
            "id": host_id,
            "name": host_name,
            "status": "ok",
            "message": "OK",
        }

    error_msg = (result.stderr or result.stdout or "").strip() or "Remote restart failed"
    return {
        "id": host_id,
        "name": host_name,
        "status": "failed",
        "message": error_msg,
    }


_MIN_DRIVE_PREFIX_LEN = 2


def _is_windows_repo(_host: dict, repo_dir: str) -> bool:
    """Detect Windows-style path (e.g. C:\\...) vs Unix path."""
    if repo_dir.startswith(("/", "~")):
        return False
    return bool(
        repo_dir
        and ("\\" in repo_dir or (len(repo_dir) >= _MIN_DRIVE_PREFIX_LEN and repo_dir[1] == ":"))
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _bash_cmd(cmd: str) -> str:
    return "bash -lc " + shlex.quote(cmd)


def _win_git_cmd(repo_dir: str, args: str) -> str:
    repo_ps = _ps_quote(repo_dir)
    return _PS_PREFIX + _ps_quote(f"$p = {repo_ps}; git -C $p {args}")


def _win_test_repo_cmd(repo_dir: str) -> str:
    repo_ps = _ps_quote(repo_dir)
    script = (
        f"$p = {repo_ps}; if (Test-Path -Path (Join-Path $p '.git')) {{ exit 0 }} else {{ exit 1 }}"
    )
    return _PS_PREFIX + _ps_quote(script)


def _build_git_cmd(host: dict, repo_dir: str, git_args: str) -> str:
    """Build a git command appropriate for the host OS."""
    if _is_windows_repo(host, repo_dir):
        return _win_git_cmd(repo_dir, git_args)
    return _bash_cmd(f"cd {shlex.quote(repo_dir)} && git {git_args}")


def _run_remote_git(
    host: dict,
    repo_dir: str,
    git_args: str,
    auth_acc: list[str],
    timeout: int = 10,
) -> subprocess.CompletedProcess:
    """Run a git command on a remote host, accumulating auth URLs."""
    cmd = _build_git_cmd(host, repo_dir, git_args)
    result, urls = _run_ssh_command(host, cmd, timeout=timeout)
    auth_acc.extend(urls)
    return result


def _result_with_auth(payload: dict, auth_urls: list[str]) -> dict:
    """Attach deduplicated auth_urls to a result dict if any exist."""
    if auth_urls:
        payload["auth_urls"] = list(set(auth_urls))
    return payload


def _check_repo_exists(host: dict, repo_dir: str, auth: list[str]) -> dict | None:
    """Verify repo directory exists. Returns error dict or None if OK."""
    if _is_windows_repo(host, repo_dir):
        check_cmd = _win_test_repo_cmd(repo_dir)
    else:
        check_cmd = _bash_cmd(
            f"test -d {shlex.quote(repo_dir)} && test -d {shlex.quote(repo_dir)}/.git"
        )
    check, urls = _run_ssh_command(host, check_cmd, timeout=10)
    auth.extend(urls)
    if check.returncode != 0:
        return _result_with_auth(
            {"status": "error", "message": f"Repo nie istnieje: {repo_dir}"}, auth
        )
    return None


def _gather_branch_info(host: dict, repo_dir: str, auth: list[str]) -> tuple[str, str]:
    """Return (branch_name, commit_short) from the remote repo."""
    branch_r = _run_remote_git(host, repo_dir, "rev-parse --abbrev-ref HEAD", auth)
    branch = branch_r.stdout.strip() if branch_r.returncode == 0 else ""

    commit_r = _run_remote_git(host, repo_dir, "rev-parse --short HEAD", auth)
    commit = commit_r.stdout.strip() if commit_r.returncode == 0 else ""
    return branch, commit


def _check_remotes_and_fetch(
    host: dict, repo_dir: str, branch: str, commit: str, auth: list[str]
) -> dict | None:
    """Check remotes exist and fetch. Returns error dict or None."""
    remote_r = _run_remote_git(host, repo_dir, "remote", auth)
    remotes = [r for r in remote_r.stdout.splitlines() if r.strip()]
    if not remotes:
        return _result_with_auth(
            {
                "status": "no_remote",
                "branch": branch,
                "commit": commit,
                "message": (remote_r.stderr or "").strip() or "Brak zdalnego repo",
            },
            auth,
        )
    fetch_r = _run_remote_git(host, repo_dir, "remote update", auth, timeout=30)
    if fetch_r.returncode != 0:
        return _result_with_auth(
            {
                "status": "fetch_failed",
                "branch": branch,
                "commit": commit,
                "message": (fetch_r.stderr or fetch_r.stdout).strip() or "git fetch failed",
            },
            auth,
        )
    return None


def _compute_sync_status(behind: int, ahead: int) -> str:
    """Return a status string based on behind/ahead counts."""
    if behind == 0 and ahead == 0:
        return "up_to_date"
    if behind > 0 and ahead == 0:
        return "behind"
    if behind == 0 and ahead > 0:
        return "ahead"
    return "diverged"


def _get_repo_status(host: dict) -> dict:
    """Get git repo status for a remote host.

    Returns a dict with status info. If Tailscale auth is needed,
    includes 'auth_urls' list with authentication URLs.
    """
    auth: list[str] = []

    repo_dir = str(host.get("repo") or host.get("repoDir") or "").strip()
    if not repo_dir:
        return {"status": "error", "message": "Brak ustawionego katalogu repozytorium"}

    err = _check_repo_exists(host, repo_dir, auth)
    if err:
        return err

    branch, commit = _gather_branch_info(host, repo_dir, auth)

    err = _check_remotes_and_fetch(host, repo_dir, branch, commit, auth)
    if err:
        return err

    # Check upstream tracking
    upstream_r = _run_remote_git(host, repo_dir, "rev-parse --abbrev-ref @{u}", auth)
    if upstream_r.returncode != 0:
        return _result_with_auth(
            {
                "status": "no_upstream",
                "branch": branch,
                "commit": commit,
                "message": (upstream_r.stderr or upstream_r.stdout).strip() or "Brak upstream",
            },
            auth,
        )

    # Compare local vs remote
    rev_r = _run_remote_git(
        host, repo_dir, "rev-list --left-right --count @{u}...@", auth, timeout=15
    )
    if rev_r.returncode != 0:
        return _result_with_auth(
            {
                "status": "error",
                "branch": branch,
                "commit": commit,
                "message": (rev_r.stderr or rev_r.stdout).strip() or "git rev-list failed",
            },
            auth,
        )

    try:
        behind_str, ahead_str = rev_r.stdout.strip().split()
        behind, ahead = int(behind_str), int(ahead_str)
    except Exception:
        return _result_with_auth(
            {"status": "error", "message": "Nie udało się odczytać statusu git"}, auth
        )

    base: dict = {"branch": branch, "commit": commit, "behind": behind, "ahead": ahead}
    if auth:
        base["auth_urls"] = list(set(auth))
    return {"status": _compute_sync_status(behind, ahead), **base}


def _update_repo(host: dict) -> dict:
    """Update git repo on a remote host via git pull.

    Returns a dict with status info. If Tailscale auth is needed,
    includes 'auth_urls' list with authentication URLs.
    """
    repo_dir = str(host.get("repo") or host.get("repoDir") or "").strip()
    if not repo_dir:
        return {"status": "error", "message": "Brak ustawionego katalogu repozytorium"}

    if _is_windows_repo(host, repo_dir):
        pull_cmd = _win_git_cmd(repo_dir, "pull --ff-only")
    else:
        pull_cmd = _bash_cmd(f"cd {shlex.quote(repo_dir)} && git pull --ff-only")
    pull, auth_urls = _run_ssh_command(host, pull_cmd, timeout=60)
    if pull.returncode != 0:
        result = {
            "status": "error",
            "message": (pull.stderr or pull.stdout).strip() or "git pull failed",
        }
        if auth_urls:
            result["auth_urls"] = auth_urls
        return result
    status = _get_repo_status(host)
    status.update({"status": "updated", "message": (pull.stdout or "").strip()})
    # Merge auth_urls if any from pull
    if auth_urls:
        existing = status.get("auth_urls", [])
        status["auth_urls"] = list(set(existing + auth_urls))
    return status


@router.get("/settings/remote-hosts/repo-status")
def get_remote_repo_status():
    """Check repo status for all configured remote hosts."""
    results = []
    for host in _resolve_remote_hosts():
        host_id = str(host.get("id", ""))
        name = host.get("name") or host.get("host") or host_id
        try:
            status = _get_repo_status(host)
            status.update({"id": host_id, "name": name})
            results.append(status)
        except Exception as exc:
            results.append({"id": host_id, "name": name, "status": "error", "message": str(exc)})
    return {"hosts": results}


@router.post("/settings/remote-hosts/repo-update")
def update_remote_repo(payload: dict = Body(default_factory=dict)):
    """Update repo for a specific host or all hosts (ff-only)."""
    host_id = payload.get("host_id")
    only_outdated = bool(payload.get("only_outdated", True))
    results = []

    hosts = _resolve_remote_hosts()
    for host in hosts:
        if host_id and str(host.get("id")) != str(host_id):
            continue
        name = host.get("name") or host.get("host") or str(host.get("id", ""))
        try:
            if only_outdated:
                status = _get_repo_status(host)
                if status.get("status") not in {"behind"}:
                    status.update(
                        {"id": str(host.get("id", "")), "name": name, "status": "skipped"}
                    )
                    results.append(status)
                    continue
            update = _update_repo(host)
            update.update({"id": str(host.get("id", "")), "name": name})
            results.append(update)
        except Exception as exc:
            results.append(
                {
                    "id": str(host.get("id", "")),
                    "name": name,
                    "status": "error",
                    "message": str(exc),
                }
            )

    return {"hosts": results}


def _get_source_root() -> str:
    """Get the configured SOURCE_ROOT path."""
    try:
        return str(get_resolver().source_root)
    except Exception:
        return os.environ.get("OCR_SOURCE_ROOT", "/data/sources")


_IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".tiff",
        ".tif",
        ".webp",
        ".bmp",
        ".gif",
    }
)


@router.get("/default-source-path")
def get_default_source_path():
    """Get default source path from environment."""
    source_root = _get_source_root()

    # 1. Próba pobrania z bazy danych (pierwszy katalog wymagający OCR)
    try:
        query = """
            SELECT source_path
            FROM v_source_path_stats
            WHERE remaining_to_ocr > 0
            ORDER BY source_path ASC
            LIMIT 1
        """
        row = execute_single(query)
        if row and row[0]:
            path = str(row[0]).strip()
            if Path(path).exists():
                return {"path": path, "source_root": source_root}
    except Exception:
        pass

    # 2. Lista zmiennych środowiskowych do sprawdzenia w kolejności priorytetu
    env_vars = ["OCR_DEFAULT_SOURCE_PATH", "OCR_SOURCE_DIR"]

    for var in env_vars:
        path = os.environ.get(var, "")
        if path:
            return {"path": path, "source_root": source_root}

    # Fallback: sprawdź czy istnieje katalog 'source' w katalogu domowym
    home = Path.home()
    source_dir = home / "source"
    if source_dir.exists() and source_dir.is_dir():
        return {"path": str(source_dir), "source_root": source_root}

    # Ostateczny fallback: katalog domowy
    return {"path": str(home), "source_root": source_root}


@router.get("/source-info")
def get_source_info(path: str = ""):
    """Validate a source path and return info about it.

    Returns resolved path, file count (images), and provider type.
    """
    source_root = _get_source_root()

    if not path.strip():
        return {
            "valid": False,
            "source_root": source_root,
            "error": "Ścieżka jest pusta",
        }

    try:
        resolver = get_resolver()
        info = resolver.verify(path.strip())

        return {
            "valid": info["accessible"],
            "resolved_path": info["canonical_id"],
            "source_root": source_root,
            "provider": info["provider"],
            "file_count": info.get("file_count"),
            "error": info.get("error"),
        }
    except Exception as exc:
        return {
            "valid": False,
            "source_root": source_root,
            "error": str(exc),
        }


_RESTART_WORKER_PATTERNS = [
    "python3 run.py",
    "python run.py",
    "scripts/login_profile.py",
    "playwright/driver/node",
    "cli.js run-driver",
    "chrome-linux64/chrome",
    "chromium",
    "google-chrome",
    "Xvfb",
    "xvfb-run",
]


def _stop_workers_best_effort() -> None:
    """Terminate OCR worker processes and related browser helpers."""
    pids = process_service.find_pids_by_patterns(_RESTART_WORKER_PATTERNS)
    for pid in pids:
        process_service.terminate_pid(pid)
    time.sleep(1.0)


def _restart_web_process(project_root: Path, start_script: Path, restart_log: Path) -> None:
    """Restart only the web server process."""
    under_systemd = bool(os.environ.get("INVOCATION_ID"))
    under_wrapper = os.environ.get("OCR_RUNNING_IN_WRAPPER") == "1"

    if under_wrapper:
        print("🔄 Restart trigger received. Exiting for wrapper restart...")
    elif not under_systemd and start_script.exists():
        try:
            restart_log.parent.mkdir(parents=True, exist_ok=True)
            out = restart_log.open("a", encoding="utf-8")  # fd owned by Popen
        except Exception:
            out = subprocess.DEVNULL
        with contextlib.suppress(Exception):
            subprocess.Popen(
                ["/bin/bash", str(start_script)],
                cwd=str(project_root),
                start_new_session=True,
                stdout=out,
                stderr=subprocess.STDOUT,
            )

    os.kill(os.getpid(), signal.SIGTERM)
    try:
        time.sleep(3)
        os.kill(os.getpid(), signal.SIGKILL)
    except Exception:
        pass


def _delayed_restart(action: Callable[[], None]) -> None:
    """Wait briefly then execute the restart action."""
    time.sleep(0.5)
    action()


_SCOPE_MESSAGES = {
    "workers": "Workery zostaną zrestartowane za chwilę",
    "web": "Aplikacja web zostanie zrestartowana za chwilę",
    "all": "Całość zostanie zrestartowana za chwilę",
}


@router.post(
    "/restart",
    responses={400: {"description": "Invalid scope"}, 500: {"description": "Cleanup failed"}},
)
def restart_application(
    background_tasks: BackgroundTasks, scope: str = "all", cleanup: bool = False
):
    """Restart the application by exiting the process (systemd will restart it).

    Args:
        scope: Restart scope - "all" (default), "web", or "workers"
        cleanup: If true, defer a cleanup after restart (scope must be "all")
    """

    valid_scopes = {"all", "web", "workers"}
    if scope not in valid_scopes:
        raise HTTPException(
            status_code=400, detail=f"Invalid scope. Use: {', '.join(valid_scopes)}"
        )
    if cleanup and scope != "all":
        raise HTTPException(status_code=400, detail="Cleanup is only supported for scope=all.")

    # Best-effort activity logging
    with contextlib.suppress(Exception):
        ActivityLogger().log_restart(
            component="web_dashboard",
            reason=f"Manual restart triggered via dashboard API (scope: {scope})",
        )

    project_root = Path(__file__).parents[2]
    start_script = project_root / "scripts" / "start_web.sh"
    restart_log = project_root / "logs" / "restart.log"

    if cleanup:
        try:
            payload = {
                "targets": DEFAULT_CLEANUP_TARGETS,
                "force": True,
                "requested_at": time.time(),
                "reset_profiles": True,
            }
            PENDING_CLEANUP_FILE.parent.mkdir(parents=True, exist_ok=True)
            PENDING_CLEANUP_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Build action based on scope
    actions = {
        "workers": _stop_workers_best_effort,
        "web": lambda: _restart_web_process(project_root, start_script, restart_log),
        "all": lambda: (
            _stop_workers_best_effort(),
            _restart_web_process(project_root, start_script, restart_log),
        ),
    }
    background_tasks.add_task(_delayed_restart, actions[scope])
    return {"success": True, "message": _SCOPE_MESSAGES[scope], "scope": scope}


def _schedule_local_full_reset(background_tasks: BackgroundTasks) -> None:
    restart_application(background_tasks, scope="all", cleanup=True)


@router.post("/restart/all")
def restart_application_all(background_tasks: BackgroundTasks):
    """Trigger full reset (clean) on all configured hosts, including localhost."""
    timeout_sec = 30
    hosts = _resolve_remote_hosts()

    local_result = {
        "id": "local",
        "name": "Localhost",
        "status": "scheduled",
        "message": "Zaplanowano lokalny restart",
    }

    results: list[dict] = []
    if hosts:
        max_workers = min(len(hosts), 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_restart_remote_host, host, timeout_sec, _run_ssh_command)
                for host in hosts
            ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(
                        {
                            "id": "",
                            "name": "Unknown",
                            "status": "failed",
                            "message": str(exc),
                        }
                    )

    _schedule_local_full_reset(background_tasks)

    summary = {"ok": 0, "failed": 0, "unreachable": 0, "scheduled": 1}
    for item in results:
        status = item.get("status")
        if status in summary:
            summary[status] += 1
        else:
            summary["failed"] += 1

    return {"summary": summary, "results": [local_result, *results]}


@router.post("/cleanup")
def cleanup_folders_endpoint(request: CleanupRequest):
    """Clean up temporary folders."""
    cleaned, errors = cleanup_folders(BASE_DIR, request.targets, request.force)

    return {
        "success": len(cleaned) > 0,
        "cleaned": cleaned,
        "errors": errors if errors else None,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }


@router.get("/autorestart")
def get_autorestart():
    """Get auto-restart configuration status."""
    try:
        if AUTO_RESTART_CONFIG_FILE.exists():
            data = json.loads(AUTO_RESTART_CONFIG_FILE.read_text(encoding="utf-8"))
            return {"enabled": data.get("enabled", False)}
    except Exception:
        pass
    return {"enabled": False}


@router.post("/autorestart", responses={500: {"description": "Failed to save configuration"}})
def set_autorestart(enabled: bool):
    """Set auto-restart configuration."""
    try:
        AUTO_RESTART_CONFIG_FILE.write_text(
            json.dumps({"enabled": enabled}, indent=2), encoding="utf-8"
        )
        return {"success": True, "enabled": enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings/x11-display")
def get_x11_display():
    """Get X11 display configuration."""
    try:
        if X11_DISPLAY_CONFIG_FILE.exists():
            data = json.loads(X11_DISPLAY_CONFIG_FILE.read_text(encoding="utf-8"))
            return {"display": data.get("display", ":0")}
    except Exception:
        pass
    return {"display": ":0"}


@router.post(
    "/settings/x11-display", responses={500: {"description": "Failed to save configuration"}}
)
def set_x11_display(display: str):
    """Set X11 display configuration."""
    try:
        X11_DISPLAY_CONFIG_FILE.write_text(
            json.dumps({"display": display}, indent=2), encoding="utf-8"
        )
        return {"success": True, "display": display}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings/update-counts")
def get_update_counts_settings():
    """Get update counts watcher settings."""

    data: dict = {}
    try:
        if UPDATE_COUNTS_CONFIG_FILE.exists():
            raw = json.loads(UPDATE_COUNTS_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except Exception:
        data = {}

    on_new_paths = data.get("OCR_UPDATE_COUNTS_ON_NEW_PATHS")
    poll_sec = data.get("OCR_UPDATE_COUNTS_POLL_SEC")

    if on_new_paths is None:
        on_new_paths = UPDATE_COUNTS_ON_NEW_PATHS
    if poll_sec is None:
        poll_sec = UPDATE_COUNTS_POLL_SEC

    try:
        poll_value = int(poll_sec)
    except Exception:
        poll_value = UPDATE_COUNTS_POLL_SEC

    return {
        "on_new_paths": str(on_new_paths).strip().lower() in {"1", "true", "yes", "y", "on"},
        "poll_sec": max(0, poll_value),
    }


@router.post(
    "/settings/update-counts", responses={500: {"description": "Failed to save configuration"}}
)
def set_update_counts_settings(payload: dict = Body(default_factory=dict)):
    """Set update counts watcher settings."""
    on_new_paths = bool(payload.get("on_new_paths", True))
    try:
        poll_sec = int(payload.get("poll_sec", 60))
    except Exception:
        poll_sec = 60
    poll_sec = max(0, poll_sec)

    try:
        UPDATE_COUNTS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "OCR_UPDATE_COUNTS_ON_NEW_PATHS": on_new_paths,
            "OCR_UPDATE_COUNTS_POLL_SEC": poll_sec,
        }
        UPDATE_COUNTS_CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"success": True, "on_new_paths": on_new_paths, "poll_sec": poll_sec}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings/webshare")
def get_webshare_settings():
    """Get Webshare sync settings from .env."""
    data = _read_env_file(ENV_FILE)
    payload = {}
    for key in WEBSHARE_KEYS:
        if key in data:
            payload[key] = data[key]
        else:
            payload[key] = os.environ.get(key, "")
    return payload


@router.post(
    "/settings/webshare",
    responses={
        400: {"description": "Missing data"},
        500: {"description": "Failed to save configuration"},
    },
)
def set_webshare_settings(payload: dict = Body(default_factory=dict)):
    """Set Webshare sync settings in .env."""
    updates: dict[str, str] = {}
    for key in WEBSHARE_KEYS:
        if key in payload:
            updates[key] = str(payload.get(key, "") or "")

    if not updates:
        raise HTTPException(status_code=400, detail="Brak danych do zapisu")

    try:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        _write_env_file(ENV_FILE, updates)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings/webshare/status")
def get_webshare_status():
    """Get last Webshare sync status."""
    try:
        if WEBSHARE_SYNC_STATUS_FILE.exists():
            data = json.loads(WEBSHARE_SYNC_STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"success": None}


@router.post("/settings/webshare/sync", responses={500: {"description": "Sync execution failed"}})
def run_webshare_sync():
    """Run Webshare proxy sync script."""
    try:
        env = os.environ.copy()
        env.update(_read_env_file(ENV_FILE))
        script_path = BASE_DIR / "scripts" / "webshare_proxy_sync.py"
        result = subprocess.run(
            [str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            # Security: allow_safe_shell=False equivalent (list args)
        )
        payload = {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
        try:
            payload["timestamp"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
            WEBSHARE_SYNC_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            WEBSHARE_SYNC_STATUS_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


_MIN_LS_PARTS = 9


def _parse_ls_entries(ls_output: str) -> list[dict]:
    """Parse `ls -la` output into a list of entry dicts."""
    entries = []
    for line in ls_output.strip().split("\n")[1:]:  # Skip "total" line
        if not line.strip():
            continue
        parts = line.split(maxsplit=8)
        if len(parts) < _MIN_LS_PARTS:
            continue
        name = parts[8]
        if name in (".", ".."):
            continue
        perms = parts[0]
        is_dir = perms.startswith(("d", "l"))
        entries.append({"name": name, "is_dir": is_dir})
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return entries


def _parse_powershell_entries(ps_output: str) -> list[dict]:
    """Parse PowerShell Get-ChildItem JSON output into a list of entry dicts."""
    entries = []
    stripped = ps_output.strip()
    if not stripped:
        return entries
    try:
        data = json.loads(stripped)
        # PowerShell returns a single object (not array) when there's only one item
        if isinstance(data, dict):
            data = [data]
        for item in data:
            name = item.get("Name", "")
            if not name or name in (".", ".."):
                continue
            mode = item.get("Mode", "")
            is_dir = mode.startswith("d") if mode else False
            entries.append({"name": name, "is_dir": is_dir})
    except (json.JSONDecodeError, TypeError):
        return entries
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return entries


def _compute_parent_path(path: str, os_type: str) -> str | None:
    """Compute the parent directory path, OS-aware."""
    if os_type == "windows":
        # Normalize to backslashes for Windows
        normalized = path.replace("/", "\\")
        # Drive root like C:\ has no parent
        if len(normalized) <= _WINDOWS_DRIVE_ROOT_LEN and normalized[1:2] == ":":
            return None
        parent = normalized.rstrip("\\").rsplit("\\", 1)[0]
        # Ensure drive root keeps trailing backslash: C:
        if len(parent) == _WINDOWS_DRIVE_LETTER_LEN and parent[1] == ":":
            parent += "\\"
        return parent
    # Linux/Mac
    if path == "/":
        return None
    parent = path.rstrip("/").rsplit("/", 1)[0]
    return parent or "/"


def _build_item_path(base_path: str, name: str, os_type: str) -> str:
    """Build full path for an item, OS-aware."""
    if os_type == "windows":
        sep = "\\"
        base = base_path.rstrip("\\")
    else:
        sep = "/"
        base = base_path.rstrip("/")
    return f"{base}{sep}{name}"


@router.get(
    "/browse-directory",
    responses={
        400: {"description": "Host address not configured"},
        404: {"description": "Host not found"},
        500: {"description": "SSH command failed"},
        504: {"description": "SSH connection timeout"},
    },
)
def browse_directory(host_id: str, path: str = "/", os_type: str = "linux"):
    """Browse directories on a remote host via SSH.

    Supports Linux (ls) and Windows (PowerShell) hosts.
    """
    try:
        config = get_effective_remote_config()
        hosts = config.get("OCR_REMOTE_HOSTS_LIST", [])
        host = next((h for h in hosts if h.get("id") == host_id), None)
        if not host:
            raise HTTPException(status_code=404, detail=f"Host {host_id} not found")

        safe_path = path.strip() or ("/" if os_type != "windows" else "C:\\")
        ssh_user = validate_username(str(host.get("user", "root")).strip())
        ssh_host = validate_hostname(str(host.get("address", "")).strip())
        ssh_opts = validate_ssh_opts(str(host.get("sshOpts", "")).strip())
        if not ssh_host:
            raise HTTPException(status_code=400, detail="Host address not configured")

        # Security: Command injection prevented by:
        # 1. ssh_user validated by validate_username() — regex [a-zA-Z0-9_\-.]
        # 2. ssh_host validated by validate_hostname() — regex [a-zA-Z0-9.\-:_]
        # 3. safe_path escaped via shlex.quote() — prevents shell metacharacters
        # 4. subprocess.run() called with list args (no shell=True)
        ssh_cmd_parts = ["ssh"]
        if ssh_opts:
            ssh_cmd_parts.extend(shlex.split(ssh_opts))

        if os_type == "windows":
            # PowerShell command for Windows directory listing
            ps_cmd = (
                f'powershell -NoProfile -Command "'
                f"Get-ChildItem -Force '{safe_path}' "
                f"| Select-Object Mode,Length,Name "
                f"| ConvertTo-Json"
                f'"'
            )
            ssh_cmd_parts.extend([f"{ssh_user}@{ssh_host}", ps_cmd])
        else:
            ssh_cmd_parts.extend(
                [f"{ssh_user}@{ssh_host}", f"ls -la --color=never {shlex.quote(safe_path)}"]
            )

        # Security: Command injection prevented by:
        # 1. validate_hostname() and validate_username() ensure safe input
        # 2. shlex.quote() escapes the path
        # 3. subprocess.run() uses list (not shell string)
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use.dangerous-subprocess-use
        # deepcode ignore CommandInjection: Input is validated/quoted, arguments are passed as list
        # deepcode ignore PT: Input is validated/quoted, arguments are passed as list
        # skipcq: PYL-W1510
        result = subprocess.run(  # nosec B603
            ssh_cmd_parts,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            # Security: ssh_cmd_parts is a list, input is validated/quoted
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500, detail=result.stderr.strip() or "Failed to list directory"
            )

        if os_type == "windows":
            raw_items = _parse_powershell_entries(result.stdout)
        else:
            raw_items = _parse_ls_entries(result.stdout)

        # Build full paths for each item (matches local /api/browse format)
        items = [
            {
                "name": it["name"],
                "path": _build_item_path(safe_path, it["name"], os_type),
                "is_dir": it["is_dir"],
            }
            for it in raw_items
        ]

        return {
            "path": safe_path,
            "parent": _compute_parent_path(safe_path, os_type),
            "items": items,
            "host_id": host_id,
            "os_type": os_type,
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="SSH connection timeout") from None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/settings/remote-deployment/deploy",
    responses={
        400: {"description": "Invalid parameters"},
        500: {"description": "Deployment failed"},
    },
)
def deploy_to_remote_host(payload: dict = Body(default_factory=dict)):
    """Deploy setup script to a new remote host.

    Expects:
        os_type: ubuntu, arch, or windows
        host: hostname or IP
        user: SSH username
        ssh_opts: SSH options (optional)
        config: dict with deployment configuration
            - github_repo: GitHub repository URL
            - nas_host: NAS hostname
            - nas_share: NAS share name
            - nas_username: NAS username
            - nas_password: NAS password
            - install_postgres: yes/no
    """

    os_type = payload.get("os_type", "").strip().lower()
    host = payload.get("host", "").strip()
    user = payload.get("user", "").strip()
    ssh_opts = validate_ssh_opts(payload.get("ssh_opts", "").strip())
    config = payload.get("config", {})

    if not os_type or os_type not in ["ubuntu", "arch", "windows"]:
        raise HTTPException(status_code=400, detail="Invalid or missing os_type")
    if not host:
        raise HTTPException(status_code=400, detail="Missing host")
    if not user:
        raise HTTPException(status_code=400, detail="Missing user")

    # Validate to prevent command injection in SSH subprocess calls
    safe_host = validate_hostname(host)
    safe_user = validate_username(user)

    try:
        success, stdout, stderr = RemoteDeploymentService.execute_deployment(
            host=safe_host,
            user=safe_user,
            os_type=os_type,
            config=config,
            ssh_opts=ssh_opts,
        )

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "message": "Deployment completed successfully" if success else "Deployment failed",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings/remote-deployment/available-scripts")
def get_available_deployment_scripts():
    """Get list of available deployment scripts."""

    scripts = []
    for os_type in ["ubuntu", "arch", "windows"]:
        script_path = RemoteDeploymentService.get_setup_script(os_type)
        if script_path:
            scripts.append(
                {
                    "os_type": os_type,
                    "name": script_path.name,
                    "exists": script_path.exists(),
                    "size": script_path.stat().st_size if script_path.exists() else 0,
                }
            )

    return {"scripts": scripts}


def _sse(event_type: str, message: str, **extra: object) -> str:
    """Format a Server-Sent Event payload."""
    return f"data: {json.dumps({'type': event_type, 'message': message, **extra})}\n\n"


def _stream_proc_lines(proc):
    """Yield SSE log events from a subprocess stdout, then wait for exit."""
    for line in iter(proc.stdout.readline, ""):
        clean = line.strip()
        if clean:
            yield _sse("log", clean)
    proc.wait()


def _deploy_copy_script(deploy_svc, os_type, safe_host, safe_user, ssh_opts, config):
    """Phase 1: copy setup script to remote host."""
    yield _sse("status", "📦 Copying setup script to remote host...", phase="copy")

    script_path = deploy_svc.get_setup_script(os_type.lower())
    if not script_path:
        yield _sse("error", f"Setup script for {os_type} not found")
        return

    success, remote_path_or_error = deploy_svc.copy_script_to_remote(
        safe_host, safe_user, script_path, ssh_opts
    )
    if not success:
        yield _sse("error", f"Failed to copy script: {remote_path_or_error}")
        return
    yield _sse("log", f"✓ Script copied to {remote_path_or_error}")

    yield from _deploy_run_script(deploy_svc, os_type, safe_host, safe_user, ssh_opts, config)


def _deploy_run_script(deploy_svc, os_type, safe_host, safe_user, ssh_opts, config):
    """Phase 2: execute setup script on remote host."""
    yield _sse("status", "⚙️ Running setup script (this may take a few minutes)...", phase="setup")
    try:
        process = deploy_svc.stream_deployment(
            host=safe_host,
            user=safe_user,
            os_type=os_type.lower(),
            config=config,
            ssh_opts=ssh_opts,
        )
        yield from _stream_proc_lines(process)
        if process.returncode != 0:
            yield _sse("warning", f"Setup script exited with code {process.returncode}")
    except Exception as e:
        yield _sse("error", f"Script execution failed: {e!s}")
        return

    yield from _deploy_sync_files(deploy_svc, safe_host, safe_user, ssh_opts)


def _deploy_sync_files(deploy_svc, safe_host, safe_user, ssh_opts):
    """Phase 3: rsync project files to remote host."""
    yield _sse("status", "📁 Syncing project files via rsync...", phase="sync")
    target_dir = "~/ocr-dashboard-v3/"
    sync_ok, sync_msg = deploy_svc.sync_files_to_remote(safe_host, safe_user, target_dir, ssh_opts)
    if not sync_ok:
        yield _sse("error", f"File sync failed: {sync_msg}")
        return

    file_count = sum(
        1
        for ln in sync_msg.split("\n")
        if ln.strip() and not ln.startswith(("sending", "sent", "total"))
    )
    yield _sse("log", f"✓ Synced {file_count} files to remote host")

    yield from _deploy_install_deps(safe_host, safe_user, ssh_opts, target_dir)


def _deploy_install_deps(safe_host, safe_user, ssh_opts, target_dir):
    """Phase 4: install Python dependencies on remote host."""
    yield _sse("status", "📦 Installing Python dependencies...", phase="dependencies")

    post_install_cmd = (
        f"cd {target_dir} && "
        "if [ ! -d venv ]; then python3 -m venv venv; fi && "
        "source venv/bin/activate && "
        "pip install --upgrade pip -q && "
        "pip install -r requirements.txt -q && "
        "playwright install chromium"
    )
    ssh_post_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
    if ssh_opts:
        ssh_post_cmd.extend(shlex.split(ssh_opts))
    ssh_post_cmd.extend([f"{safe_user}@{safe_host}", post_install_cmd])

    post_process = subprocess.Popen(
        ssh_post_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    yield from _stream_proc_lines(post_process)

    if post_process.returncode == 0:
        yield _sse("log", "✓ Dependencies installed successfully")
    else:
        yield _sse("warning", f"Dependency installation exited with code {post_process.returncode}")

    yield _sse("complete", "✅ Deployment completed successfully!", success=True)


@router.get("/settings/remote-deployment/deploy-stream")
def deploy_to_remote_host_stream(
    os_type: str,
    host: str,
    user: str,
    ssh_opts: str = "",
    github_repo: str = "",
    nas_host: str = "",
    nas_share: str = "",
    nas_username: str = "",
    nas_password: str = "",
    sudo_password: str = "",
    install_postgres: str = "no",
):
    """Deploy setup script to a new remote host with SSE streaming.

    Returns Server-Sent Events with real-time deployment logs.
    """

    logger = logging.getLogger(__name__)

    safe_host = validate_hostname(host) if host else ""
    safe_user = validate_username(user) if user else ""
    safe_ssh_opts = validate_ssh_opts(ssh_opts) if ssh_opts else ""

    def generate_events():
        try:
            if not os_type or os_type.lower() not in ["ubuntu", "arch", "windows"]:
                yield _sse("error", "Invalid or missing os_type")
                return
            if not safe_host:
                yield _sse("error", "Missing host")
                return
            if not safe_user:
                yield _sse("error", "Missing user")
                return

            config = {
                "github_repo": github_repo or "LOCAL",
                "nas_host": nas_host,
                "nas_share": nas_share,
                "nas_username": nas_username,
                "nas_password": nas_password,
                "sudo_password": sudo_password,
                "install_postgres": install_postgres,
            }

            yield _sse("status", "🚀 Starting deployment...", phase="init")
            yield from _deploy_copy_script(
                RemoteDeploymentService, os_type, safe_host, safe_user, safe_ssh_opts, config
            )
        except Exception as e:
            logger.error(f"Deployment streaming error: {e}")
            yield _sse("error", str(e))

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_PROFILE_CACHE_SUBDIR = ".cache/ocr-dashboard-v3"


def _resolve_profile_dir_name(profile_name: str) -> str:
    """Return directory name for a given profile."""
    if profile_name == "default" or not profile_name:
        return "gemini-profile"
    safe = "".join(c for c in profile_name if c.isalnum() or c in "-_")
    return f"gemini-profile-{safe}"


def _ensure_remote_dir(host: str, user: str, ssh_opts: str, remote_subpath: str) -> None:
    """Create a directory on the remote host via SSH."""
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
    if ssh_opts:
        cmd.extend(shlex.split(ssh_opts))
    cmd.extend([f"{user}@{host}", f"mkdir -p ~/{remote_subpath}"])
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create target directory: {e.stderr}"
        ) from e


def _resolve_sync_paths(
    source_host: str,
    source_user: str,
    target_host: str,
    target_user_safe: str,
    target_host_addr: str,
    profile_dir_name: str,
    ssh_opts: str,
) -> tuple[str, str]:
    """Resolve source_path and target_path for profile rsync.

    Security: Path traversal prevented by:
    1. profile_dir_name sanitized by _resolve_profile_dir_name() — only [a-zA-Z0-9_-]
    2. _PROFILE_CACHE_SUBDIR is a hardcoded constant (".cache/ocr-dashboard-v3")
    3. target_user_safe validated by validate_username() — regex [a-zA-Z0-9_\\-.]
    4. target_host_addr validated by validate_hostname() — regex [a-zA-Z0-9.\\-:_]
    """
    sub = _PROFILE_CACHE_SUBDIR

    if source_host == "local" or not source_host:
        source_path = str(Path.home() / sub / profile_dir_name) + "/"
        target_path = f"{target_user_safe}@{target_host_addr}:~/{sub}/{profile_dir_name}/"

        if not Path(source_path.rstrip("/")).exists():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_dir_name}' not found locally",
            )
        _ensure_remote_dir(
            target_host_addr, target_user_safe, ssh_opts, f"{sub}/{profile_dir_name}"
        )
        return source_path, target_path

    source_user_safe = validate_username(source_user) if source_user else ""
    source_host_addr = validate_hostname(source_host)

    if target_host == "local":
        source_path = f"{source_user_safe}@{source_host_addr}:~/{sub}/{profile_dir_name}/"
        target_path_obj = Path.home() / sub / profile_dir_name

        # Security: Validate that resolved path is within user's home directory
        try:
            resolved_target = target_path_obj.resolve()
            resolved_home = Path.home().resolve()
            if not resolved_target.is_relative_to(resolved_home):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid target path: must be within user's home directory",
                )
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid target path: {e}") from e

        target_path = str(target_path_obj) + "/"
        # deepcode ignore PT: Path is validated to be within home directory
        target_path_obj.mkdir(parents=True, exist_ok=True)
        return source_path, target_path

    raise HTTPException(
        status_code=400,
        detail="Remote-to-remote sync not supported. Copy to local first, then to target.",
    )


@router.post(
    "/settings/profile-sync",
    responses={
        400: {"description": "Missing required fields"},
        404: {"description": "Profile not found"},
        500: {"description": "Sync failed"},
        504: {"description": "Sync timed out"},
    },
)
def sync_profile_to_host(payload: dict = Body(default_factory=dict)):
    """Synchronize a Chrome profile to a remote host.

    Copies profile data from source host to target host using rsync.
    Can copy from local to remote, remote to local, or remote to remote.
    """

    logger = logging.getLogger(__name__)

    source_host = payload.get("source_host", "local")
    source_user = payload.get("source_user", "")
    target_host = payload.get("target_host")
    target_user = payload.get("target_user")
    profile_name = payload.get("profile_name")
    ssh_opts = validate_ssh_opts(payload.get("ssh_opts", ""))

    if not target_host:
        raise HTTPException(status_code=400, detail="Target host is required")
    if not target_user:
        raise HTTPException(status_code=400, detail="Target user is required")
    if not profile_name:
        raise HTTPException(status_code=400, detail="Profile name is required")

    target_host_addr = validate_hostname(target_host)
    target_user_safe = validate_username(target_user)
    profile_dir_name = _resolve_profile_dir_name(profile_name)

    source_path, target_path = _resolve_sync_paths(
        source_host,
        source_user,
        target_host,
        target_user_safe,
        target_host_addr,
        profile_dir_name,
        ssh_opts,
    )

    ssh_cmd = (
        f"ssh {ssh_opts} -o StrictHostKeyChecking=no"
        if ssh_opts
        else "ssh -o StrictHostKeyChecking=no"
    )
    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "--exclude",
        "Singleton*",
        "--exclude",
        "*.lock",
        "--exclude",
        "*.tmp",
        "-e",
        ssh_cmd,
        source_path,
        target_path,
    ]

    logger.info(f"Syncing profile '{profile_name}' from {source_host} to {target_host}")
    logger.debug(f"Rsync command: {' '.join(rsync_cmd)}")

    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            logger.error(f"Rsync failed: {result.stderr}")
            return {
                "success": False,
                "message": f"Profile sync failed: {result.stderr}",
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        return {
            "success": True,
            "message": f"Profile '{profile_name}' synced successfully to {target_host}",
            "stdout": result.stdout,
            "files_transferred": sum(
                1
                for ln in result.stdout.split("\n")
                if ln.strip() and not ln.startswith(("sending", "sent"))
            ),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Profile sync timed out") from None
    except Exception as e:
        logger.error(f"Profile sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


_BYTES_PER_MB = 1024 * 1024


def _build_profile_entry(profile_dir: Path) -> dict:
    """Build a profile entry dict from a cache directory."""
    name = (
        "default"
        if profile_dir.name == "gemini-profile"
        else profile_dir.name.replace("gemini-profile-", "")
    )
    try:
        size = sum(f.stat().st_size for f in profile_dir.rglob("*") if f.is_file())
    except Exception:
        size = 0
    return {
        "name": name,
        "dir_name": profile_dir.name,
        "path": str(profile_dir),
        "size_mb": round(size / _BYTES_PER_MB, 2),
    }


@router.get("/settings/profile-sync/list-profiles")
def list_syncable_profiles():
    """List profiles available for synchronization."""
    cache_dir = Path.home() / ".cache" / "ocr-dashboard-v3"
    if not cache_dir.exists():
        return {"profiles": []}

    profiles = [
        _build_profile_entry(d)
        for d in cache_dir.iterdir()
        if d.is_dir() and d.name.startswith("gemini-profile")
    ]
    return {"profiles": profiles}
