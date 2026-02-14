"""
OCR Dashboard V3 - Process Management Service
Handles subprocess management for OCR workers.
"""

import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..utils.security import validate_hostname, validate_ssh_opts, validate_username
from . import profiles as profile_service
from .remote_config import get_effective_remote_config

logger = logging.getLogger(__name__)

# Add src to path for ActivityLogger import
src_path = Path(__file__).parents[2] / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    from ocr_engine.utils.activity_logger import ActivityLogger

    HAS_ACTIVITY_LOGGER = True
except ImportError:
    HAS_ACTIVITY_LOGGER = False

try:
    from ocr_engine.ocr.engine.proxy_config import load_proxy_config

    HAS_PROXY_CONFIG = True
except ImportError:
    HAS_PROXY_CONFIG = False

# Global state for tracking running processes
current_processes: list[subprocess.Popen] = []

# Cache for iter_runpy_processes to avoid excessive /proc scanning
_runpy_cache: tuple[float, list[tuple[int, str | None]]] | None = None
_RUNPY_CACHE_TTL = 2.0  # seconds
current_profile_processes: dict[str, subprocess.Popen] = {}
current_remote_profiles: dict[str, dict] = {}
profile_start_attempts: dict[str, float] = {}
postprocess_process: subprocess.Popen | None = None
precheck_process: subprocess.Popen | None = None


def _get_remote_state_file(profile_name: str) -> Path:
    cwd = Path(__file__).parents[2]
    return cwd / "logs" / "profiles" / f"{profile_name}.remote"


def _save_remote_state(profile_name: str, data: dict) -> None:
    try:
        f = _get_remote_state_file(profile_name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _load_remote_state(profile_name: str) -> dict | None:
    try:
        f = _get_remote_state_file(profile_name)
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _clear_remote_state(profile_name: str) -> None:
    try:
        (_get_remote_state_file(profile_name)).unlink(missing_ok=True)
    except Exception:
        pass


def _get_tailscale_ip() -> str | None:
    """Get the local Tailscale IP address if available."""
    try:
        # Linux specific
        cmd = ["ip", "-4", "addr", "show", "tailscale0"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "inet " in line:
                    parts = line.strip().split()
                    if len(parts) > 1:
                        return parts[1].split("/")[0]
    except Exception:
        pass
    return None


PROFILE_START_TRACK_SEC = 240

# X11 display config file path
X11_DISPLAY_CONFIG_FILE = Path.home() / ".cache" / "ocr-dashboard-v3" / "x11_display.json"

# Mapping from host config keys to environment variables
HOST_ENV_MAPPING = {
    "host": "OCR_REMOTE_HOST",
    "user": "OCR_REMOTE_USER",
    "ssh": "OCR_REMOTE_SSH_OPTS",
    "repo": "OCR_REMOTE_REPO_DIR",
    "python": "OCR_REMOTE_VENV_CMD",
    # Browser specific
    "profileRoot": "OCR_REMOTE_BROWSER_PROFILE_ROOT",
    "portBase": "OCR_REMOTE_BROWSER_PORT_BASE",
    "portSpan": "OCR_REMOTE_BROWSER_PORT_SPAN",
    "localPortBase": "OCR_REMOTE_BROWSER_LOCAL_PORT_BASE",
    "tunnel": "OCR_REMOTE_BROWSER_TUNNEL",
    "chrome": "OCR_REMOTE_BROWSER_CHROME_BIN",
}

HOST_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "ssh": ("ssh_opts", "sshOpts"),
    "repo": ("repo_dir", "repoDir"),
    "profileRoot": ("profile_root",),
    "portBase": ("port_base",),
    "portSpan": ("port_span",),
    "localPortBase": ("local_port_base",),
}


def load_x11_display() -> str | None:
    """Load X11 display from config file."""
    try:
        if X11_DISPLAY_CONFIG_FILE.exists():
            data = json.loads(X11_DISPLAY_CONFIG_FILE.read_text(encoding="utf-8"))
            display = data.get("display", "").strip()
            if display:
                return display
    except Exception:
        pass
    return None


def _resolve_host_value(host: dict, json_key: str) -> str | None:
    """Resolve a host config value, checking aliases if the primary key is missing."""
    val = host.get(json_key)
    if val is None:
        for alias_key in HOST_ENV_ALIASES.get(json_key, ()):
            if alias_key in host:
                val = host.get(alias_key)
                break
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def _apply_selected_host_env(env: dict[str, str], host: dict) -> None:
    """Apply configuration from a selected remote host to env."""
    env["OCR_REMOTE_RUN_ENABLED"] = "1"
    for json_key, env_key in HOST_ENV_MAPPING.items():
        resolved = _resolve_host_value(host, json_key)
        if resolved:
            env[env_key] = resolved


def _apply_global_remote_config(env: dict[str, str], config: dict) -> None:
    """Apply global remote config (non-host-specific) to env."""
    for key, value in config.items():
        if key == "OCR_REMOTE_HOSTS_LIST" or value is None:
            continue
        if isinstance(value, bool):
            env[key] = "1" if value else "0"
        else:
            text = str(value).strip()
            if text:
                env[key] = text


def _apply_remote_hosts_env(env: dict[str, str], remote_host_id: str | int | None = None) -> None:
    """Apply persisted remote host settings to environment."""
    config = get_effective_remote_config()

    if remote_host_id is not None:
        hosts = config.get("OCR_REMOTE_HOSTS_LIST")
        if isinstance(hosts, list):
            selected = next((h for h in hosts if str(h.get("id")) == str(remote_host_id)), None)
            if selected:
                _apply_selected_host_env(env, selected)
                return

    _apply_global_remote_config(env, config)


def _is_absolute_or_remote(path: str) -> bool:
    """Check if a path is absolute, UNC, or remote SSH-style."""
    if path.startswith(("~", "\\\\")):
        return True
    if re.match(r"^[A-Za-z]:[/\\]", path):
        return True
    return ":" in path and "@" in path.split(":", 1)[0]


def _legacy_source_concat(source_path_text: str, source_root: object) -> str:
    """Legacy string-concat fallback for source path resolution."""
    source_root_text = str(source_root or "").strip()
    if not source_root_text:
        return source_path_text
    if source_path_text.startswith(source_root_text):
        return source_path_text
    if _is_absolute_or_remote(source_path_text):
        return source_path_text
    rel = source_path_text.lstrip("/\\")
    if not rel:
        return source_root_text
    return source_root_text.rstrip("/\\") + "/" + rel


def _compose_source_path(source_path: object, source_root: object = None) -> str | None:
    """Resolve/compose a source path.

    Backward-compat rules used by tests:
    - jeśli source_path jest puste → zwróć source_root (jeśli podano), inaczej None
    - jeśli source_path wygląda na SSH/UNC/~ lub Windows-drive → ZWRÓĆ BEZ ZMIAN (passthrough)
    - jeśli source_root jest podany i source_path jest względne → sklej legacy (usuń leading slash)
    - jeśli source_root NIE jest podany → zwróć source_path bez zmian (test 12)
    """
    source_path_text = str(source_path or "").strip()
    source_root_text = str(source_root or "").strip()

    # Empty → return base (tests expect this)
    if not source_path_text:
        return source_root_text or None

    # Passthrough for "absolute/remote" (SSH, ~, UNC, Windows drive)
    # Test 5 expects exact SSH string returned (including trailing slash)
    if _is_absolute_or_remote(source_path_text):
        return source_path_text

    # Backward compat: jeśli podano source_root i path jest względny → legacy concat
    if source_root_text:
        return _legacy_source_concat(source_path_text, source_root_text)

    # IMPORTANT (test 12): without base, keep the relative path as-is
    return source_path_text


def is_start_recent(profile_name: str, window_sec: int = 20) -> bool:
    """Check if a profile start was attempted recently."""
    ts = profile_start_attempts.get(profile_name)
    if not ts:
        return False
    return (time.time() - ts) <= window_sec


def pid_is_running(pid: int | None) -> bool:
    """Check if a process with given PID is running."""
    if pid is None:
        return False
    try:
        # Validate PID is an integer to prevent path traversal
        pid = int(pid)
    except (ValueError, TypeError):
        return False

    try:
        # Treat zombies as not running to avoid stale "active" status.
        stat_path = Path("/proc") / str(pid) / "stat"
        if stat_path.exists():
            try:
                state = stat_path.read_text(encoding="utf-8", errors="ignore").split()[2]
                if state == "Z":
                    return False
            except Exception:
                pass
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _get_pid_env_value(pid: int, key: str) -> str | None:
    """Read a single env value from /proc for a PID."""
    try:
        # Validate PID is an integer
        pid = int(pid)
        env_bytes = (Path("/proc") / str(pid) / "environ").read_bytes()
    except Exception:
        return None
    for item in env_bytes.split(b"\0"):
        if item.startswith(f"{key}=".encode()):
            return item.split(b"=", 1)[1].decode("utf-8", "ignore")
    return None


def pid_is_headed(pid: int | None) -> bool:
    """Check if a process is running in headed mode."""
    if pid is None:
        return False
    value = _get_pid_env_value(pid, "OCR_HEADED")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def terminate_pid(pid: int) -> None:
    """Terminate a process by PID."""
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def terminate_proc(proc: subprocess.Popen) -> None:
    """Terminate a subprocess."""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass


def find_pids_by_patterns(patterns: list[str]) -> set[int]:
    """Find PIDs matching command line patterns."""
    pids = set()
    proc_root = Path("/proc")

    if not proc_root.exists():
        return pids

    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "ignore").replace("\0", " ")
            for pattern in patterns:
                if pattern in cmdline:
                    pids.add(pid)
                    break
        except Exception:
            continue

    return pids


def iter_runpy_processes() -> list[tuple[int, str | None]]:
    """Return list of (pid, profile_suffix or None) for run.py processes."""
    global _runpy_cache

    # Return cached result if fresh
    if _runpy_cache is not None:
        cache_time, cached_results = _runpy_cache
        if time.time() - cache_time < _RUNPY_CACHE_TTL:
            return cached_results

    # Scan /proc and cache results
    results = []
    proc_root = Path("/proc")

    if not proc_root.exists():
        _runpy_cache = (time.time(), results)
        return results

    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            # Skip zombies (defunct processes).
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

            profile = None
            try:
                env_bytes = (entry / "environ").read_bytes()
                for item in env_bytes.split(b"\0"):
                    if item.startswith(b"OCR_PROFILE_SUFFIX="):
                        profile = item.split(b"=", 1)[1].decode("utf-8", "ignore")
                        break
            except Exception:
                profile = None

            results.append((pid, profile))
        except Exception:
            continue

    # Update cache
    _runpy_cache = (time.time(), results)
    return results


def get_profile_pids(safe_profile: str) -> set[int]:
    """Collect all PIDs belonging to a profile (run.py processes)."""
    pids = set()
    for pid, profile in iter_runpy_processes():
        if profile == safe_profile:
            pids.add(pid)
    return pids


def is_profile_running_remote(profile_name: str) -> dict | None:
    """Check if profile is running remotely and return host info."""
    # First check memory
    info = current_remote_profiles.get(profile_name)
    if info:
        return info
    # Fallback to file
    return _load_remote_state(profile_name)


def stop_profile_processes(safe_profile: str, wait_timeout: float = 0.0) -> None:
    """Stop all processes for a profile."""
    # Clear remote state if exists
    _clear_remote_state(safe_profile)
    current_remote_profiles.pop(safe_profile, None)

    pids = get_profile_pids(safe_profile)
    for pid in pids:
        terminate_pid(pid)

    if wait_timeout > 0:
        start_time = time.time()
        while time.time() - start_time < wait_timeout:
            still_running = False
            for pid in pids:
                if pid_is_running(pid):
                    still_running = True
                    break
            if not still_running:
                break
            time.sleep(0.1)

    # Force kill if still running
    still_running_pids = [pid for pid in pids if pid_is_running(pid)]
    if still_running_pids:
        print(f"⚠️ [Stop] Force killing stuck PIDs for '{safe_profile}': {still_running_pids}")
        for pid in still_running_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        time.sleep(1.0)  # Wait for kernel to clean up

    # Log stop event to database
    if HAS_ACTIVITY_LOGGER and pids:
        try:
            logger_db = ActivityLogger()
            logger_db.log_stop(
                component="profile_worker",
                profile_name=safe_profile,
                triggered_by="api",
                exit_signal="SIGTERM",
                reason="Zatrzymano profil przez dashboard",
            )
        except Exception as log_error:
            print(f"Warning: Could not log stop event: {log_error}")

    profile_start_attempts.pop(safe_profile, None)


def record_profile_start(profile_name: str) -> None:
    """Record that a profile start was attempted."""
    if profile_name:
        profile_start_attempts[profile_name] = time.time()


def start_profile_process(
    profile_name: str,
    headed: bool = False,
    windows: int | None = None,
    tabs_per_window: int | None = None,
    config: dict[str, object] | None = None,
) -> tuple[bool, str]:
    """Start the OCR worker process for a profile.

    Supports two execution modes:
    - Local: runs python3 run.py locally
    - Remote (worker): runs via SSH on a remote host
    """

    # Check if already running
    pids = get_profile_pids(profile_name)
    if pids:
        # Check if actually running
        running_pids = [pid for pid in pids if pid_is_running(pid)]
        if running_pids:
            return False, f"Profil '{profile_name}' już pracuje (PID: {running_pids})"

    # Sanitize profile name to prevent path traversal (defense in depth)
    profile_name = os.path.basename(profile_name)

    # Check for remote execution mode
    execution_mode = config.get("execution_mode") if config else None
    remote_host_id = config.get("remote_host_id") if config else None

    # If execution_mode is "worker" and we have a remote_host_id, run remotely
    if execution_mode in ("worker", "wsl_worker") and remote_host_id:
        return _start_remote_profile_process(
            profile_name,
            remote_host_id,
            headed=headed,
            windows=windows,
            tabs_per_window=tabs_per_window,
            config=config,
        )

    # Otherwise, run locally (default behavior)
    try:
        # If this profile was previously started as "remote", its state marker can linger.
        # Clear it before starting locally to avoid UI/host-load misreporting.
        _clear_remote_state(profile_name)
        current_remote_profiles.pop(profile_name, None)

        # Prepare environment
        env = os.environ.copy()

        # Check for remote host selection in config (for browser mode)
        _apply_remote_hosts_env(env, remote_host_id)

        env["OCR_PROFILE_SUFFIX"] = profile_name
        env["OCR_HEADED"] = "1" if headed else "0"

        # Set windows and tabs config
        if windows is not None:
            env["OCR_WINDOWS"] = str(windows)
        if tabs_per_window is not None:
            env["OCR_TABS_PER_WINDOW"] = str(tabs_per_window)
        if config:
            _apply_profile_env(env, config)

        # Load X11 display for headed mode
        if headed:
            x11_display = load_x11_display()
            if x11_display:
                env["DISPLAY"] = x11_display
            elif "DISPLAY" not in env:
                env["DISPLAY"] = ":0"  # Fallback default

        # Determine working directory (project root)
        cwd = Path(__file__).parents[2]  # app/services/process.py -> app/services -> app -> root
        if not (cwd / "run.py").exists():
            return False, "Nie znaleziono pliku run.py"

        # Add src directory to PYTHONPATH so ocr_engine module can be found
        src_dir = cwd / "src"
        if src_dir.exists():
            # Prepend src to PYTHONPATH to ensure it takes precedence
            existing_path = env.get("PYTHONPATH", "")
            if existing_path:
                env["PYTHONPATH"] = f"{src_dir}:{existing_path}"
            else:
                env["PYTHONPATH"] = str(src_dir)

        # Run process with venv Python interpreter
        # Use venv/bin/python if it exists, otherwise fall back to .venv/bin/python or python3
        python_bin = None
        for venv_name in ("venv", ".venv"):
            venv_python = cwd / venv_name / "bin" / "python"
            if venv_python.exists():
                python_bin = str(venv_python)
                break

        if not python_bin:
            python_bin = "python3"  # Fallback to system python if no venv found

        cmd = [python_bin, "run.py"]

        # Prepare log file
        log_dir = cwd / "logs" / "profiles"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{profile_name}.log"
        with log_file.open("a", encoding="utf-8") as log_fp:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        # Record start
        current_processes.append(process)
        current_profile_processes[profile_name] = process
        record_profile_start(profile_name)

        # Log to database
        if HAS_ACTIVITY_LOGGER:
            try:
                logger_db = ActivityLogger()
                logger_db.log_start(
                    component="profile_worker",
                    profile_name=profile_name,
                    configuration={"headed": headed, "pid": process.pid},
                    triggered_by="api",
                    reason="Uruchomiono profil przez dashboard",
                )
            except Exception as log_error:
                print(f"Warning: Could not log start event: {log_error}")

        # Quick sanity check: if the process dies immediately, surface an error
        time.sleep(0.2)
        if not pid_is_running(process.pid):
            return False, f"Proces profilu '{profile_name}' zakończył się tuż po starcie"

        profile_service.set_profile_session_start(profile_name)
        return True, f"Uruchomiono profil '{profile_name}' (PID: {process.pid})"

    except Exception as e:
        return False, f"Błąd uruchamiania procesu: {e}"


def _extract_host_config(host: dict) -> dict[str, str]:
    """Extract and normalize host configuration fields from a remote host dict."""
    return {
        "host_addr": str(host.get("host") or host.get("address") or "").strip(),
        "host_user": str(host.get("user") or "root").strip(),
        "ssh_opts": str(
            host.get("ssh") or host.get("sshOpts") or host.get("ssh_opts") or ""
        ).strip(),
        "repo_dir": str(
            host.get("repo") or host.get("repoDir") or host.get("repo_dir") or ""
        ).strip(),
        "python_cmd": str(host.get("python") or "python3").strip(),
        "profile_root": str(host.get("profileRoot") or host.get("profile_root") or "").strip(),
        "nas_source": str(host.get("nasSource") or host.get("nas_source") or "").strip(),
    }


def _build_remote_env_dict(
    profile_name: str,
    headed: bool,
    windows: int | None,
    tabs_per_window: int | None,
    profile_root: str,
    config: dict[str, object] | None,
) -> dict[str, str]:
    """Build the environment variables dict for a remote profile launch."""
    env_dict: dict[str, str] = {
        "OCR_PROFILE_SUFFIX": str(profile_name),
        "OCR_HEADED": "1" if headed else "0",
        "PYTHONUNBUFFERED": "1",
    }

    if windows is not None:
        env_dict["OCR_WINDOWS"] = str(windows)
    if tabs_per_window is not None:
        env_dict["OCR_TABS_PER_WINDOW"] = str(tabs_per_window)
    if profile_root:
        env_dict["OCR_BROWSER_PROFILE_ROOT"] = str(profile_root)

    _load_proxy_env(env_dict, profile_name)
    _apply_config_env(env_dict, config)
    _inherit_ocr_env_vars(env_dict)
    _patch_dsn_for_remote(env_dict)

    return env_dict


def _load_proxy_env(env_dict: dict[str, str], profile_name: str) -> None:
    """Load proxy configuration for a profile into env_dict."""
    if not HAS_PROXY_CONFIG:
        return
    try:
        project_root = Path(__file__).parents[2]
        proxies_file = project_root / "config" / "proxies.json"
        proxy_cfg = load_proxy_config(profile_name, proxies_file)
        if not proxy_cfg:
            return
        for cfg_key, env_key in (
            ("server", "OCR_PROXY_SERVER"),
            ("username", "OCR_PROXY_USERNAME"),
            ("password", "OCR_PROXY_PASSWORD"),
        ):
            if proxy_cfg.get(cfg_key):
                env_dict[env_key] = str(proxy_cfg[cfg_key])
    except Exception:
        pass


def _apply_config_env(env_dict: dict[str, str], config: dict[str, object] | None) -> None:
    """Apply profile config values to the env dict."""
    if not config:
        return
    effective_source = _compose_source_path(config.get("source_path"))
    if effective_source:
        env_dict["OCR_SOURCE_DIR"] = effective_source
    if config.get("pg_enabled"):
        env_dict["OCR_PG_ENABLED"] = "1"
    if config.get("pg_dsn"):
        env_dict["OCR_PG_DSN"] = str(config["pg_dsn"])
    if config.get("auto_advance") is not None:
        env_dict["OCR_AUTO_ADVANCE"] = "1" if config["auto_advance"] else "0"
    if config.get("continue_mode") is not None:
        env_dict["OCR_CONTINUE"] = "1" if config["continue_mode"] else "0"


_DB_ENV_KEYS = frozenset(
    ("GEMINI_OCR_PG_DSN", "PG_DSN", "PGUSER", "PGPASSWORD", "PGHOST", "PGPORT", "PGDATABASE")
)


def _inherit_ocr_env_vars(env_dict: dict[str, str]) -> None:
    """Inherit OCR_* and database env vars from the current process."""
    for k, v in os.environ.items():
        if k not in env_dict and (k.startswith("OCR_") or k in _DB_ENV_KEYS):
            env_dict[k] = v


def _patch_dsn_for_remote(env_dict: dict[str, str]) -> None:
    """Replace localhost/127.0.0.1 in DSN values with the machine's Tailscale IP."""
    tailscale_ip = _get_tailscale_ip()
    if not tailscale_ip:
        return
    for key in ("OCR_PG_DSN", "GEMINI_OCR_PG_DSN", "PG_DSN", "PGHOST"):
        val = env_dict.get(key)
        if val and ("localhost" in val or "127.0.0.1" in val):
            env_dict[key] = val.replace("localhost", tailscale_ip).replace(
                "127.0.0.1", tailscale_ip
            )


def _build_ssh_parts(
    host_user: str, host_addr: str, ssh_opts: str, remote_cmd: str, *, timeout: int = 10
) -> list[str]:
    """Build an SSH command list from the given parameters."""
    parts = ["ssh", "-o", f"ConnectTimeout={timeout}", "-o", "StrictHostKeyChecking=no"]
    if ssh_opts:
        parts.extend(shlex.split(ssh_opts))
    parts.append(f"{host_user}@{host_addr}")
    parts.append(remote_cmd)
    return parts


def _verify_remote_runpy(
    host_user: str, host_addr: str, ssh_opts: str, repo_dir: str
) -> str | None:
    """Check that run.py exists on a remote host. Returns error message or None on success."""
    check_cmd = f"test -f {shlex.quote(repo_dir)}/run.py && echo OK"
    parts = _build_ssh_parts(host_user, host_addr, ssh_opts, check_cmd, timeout=5)
    result = subprocess.run(parts, capture_output=True, text=True, timeout=15, check=False)
    if "OK" not in result.stdout:
        return result.stderr.strip() or "run.py nie znaleziony na hoście"
    return None


def _ensure_remote_mount(
    host_user: str, host_addr: str, ssh_opts: str, nas_source: str
) -> tuple[str | None, str | None]:
    """Ensure NAS is mounted on remote host via SSHFS.

    Returns:
        (mount_point, error_message)
        If success, mount_point is the path, error_message is None.
        If failure, mount_point is None, error_message contains details.
    """
    if not nas_source or ":" not in nas_source:
        return None, "Nieprawidłowy format ścieżki NAS (oczekiwano user@host:/path)"

    mount_point = "~/ocr_mounts/sources"

    remote_script = (
        f"if mount | grep -q {shlex.quote(mount_point)}; then "
        f"echo MOUNTED; "
        f"else "
        f"mkdir -p {shlex.quote(mount_point)} && "
        f"sshfs -o allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 "
        f"{shlex.quote(nas_source)} {shlex.quote(mount_point)} && "
        f"echo MOUNTED; "
        f"fi"
    )

    parts = _build_ssh_parts(host_user, host_addr, ssh_opts, remote_script, timeout=30)

    try:
        result = subprocess.run(parts, capture_output=True, text=True, timeout=45)
        if result.returncode == 0 and "MOUNTED" in result.stdout:
            return mount_point, None

        err = result.stderr.strip() or result.stdout.strip() or "Nieznany błąd montowania"
        return None, f"Błąd SSHFS: {err}"

    except subprocess.TimeoutExpired:
        return None, "Timeout podczas montowania NAS (sprawdź klucze SSH do NASa)"
    except Exception as e:
        return None, f"Błąd procesu montowania: {e}"


def _start_remote_profile_process(
    profile_name: str,
    remote_host_id: str,
    headed: bool = False,
    windows: int | None = None,
    tabs_per_window: int | None = None,
    config: dict[str, object] | None = None,
) -> tuple[bool, str]:
    """Start OCR worker on a remote host via SSH.

    This executes run.py on the remote host, keeping the local dashboard lightweight.
    """
    host = _resolve_remote_host(remote_host_id)
    if not host:
        return False, f"Nie znaleziono konfiguracji hosta: {remote_host_id}"

    hc = _extract_host_config(host)
    host_addr, host_user = hc["host_addr"], hc["host_user"]
    ssh_opts, repo_dir = hc["ssh_opts"], hc["repo_dir"]
    python_cmd, profile_root = hc["python_cmd"], hc["profile_root"]
    nas_source = hc["nas_source"]

    if not host_addr:
        return False, f"Brak adresu hosta dla '{remote_host_id}'"
    if not repo_dir:
        return False, f"Brak katalogu repozytorium dla hosta '{remote_host_id}'"

    try:
        host_addr = validate_hostname(host_addr)
        host_user = validate_username(host_user)
        ssh_opts = validate_ssh_opts(ssh_opts)
    except ValueError as exc:
        return False, str(exc)

    env_dict = _build_remote_env_dict(
        profile_name, headed, windows, tabs_per_window, profile_root, config
    )

    # If valid NAS source is configured, try to mount it
    if nas_source and ":" in str(nas_source):
        mount_point, mount_err = _ensure_remote_mount(
            host_user, host_addr, ssh_opts, str(nas_source)
        )
        if mount_point:
            env_dict["OCR_SOURCE_DIR"] = mount_point

            cwd = Path(__file__).parents[2]
            log_dir = cwd / "logs" / "profiles"
            log_file = log_dir / f"{profile_name}.log"
            try:
                with log_file.open("a", encoding="utf-8") as log_fp:
                    log_fp.write(f"[AUTO-MOUNT] Zamontowano NAS {nas_source} w {mount_point}\n")
            except Exception:
                pass
        else:
            cwd = Path(__file__).parents[2]
            log_dir = cwd / "logs" / "profiles"
            log_file = log_dir / f"{profile_name}.log"
            try:
                with log_file.open("a", encoding="utf-8") as log_fp:
                    log_fp.write(f"[AUTO-MOUNT ERROR] Nie udało się zamontować NAS: {mount_err}\n")
            except Exception:
                pass

    env_str = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env_dict.items())
    python_part = python_cmd or "python3"
    remote_log = f"logs/profiles/{profile_name}.log"
    remote_cmd = (
        f"cd {shlex.quote(repo_dir)} && mkdir -p logs/profiles && "
        f"source venv/bin/activate 2>/dev/null || . venv/bin/activate 2>/dev/null || true && "
        f"{env_str} nohup {python_part} run.py >> {remote_log} 2>&1 &"
    )

    ssh_cmd_parts = _build_ssh_parts(host_user, host_addr, ssh_opts, remote_cmd)

    cwd = Path(__file__).parents[2]
    log_dir = cwd / "logs" / "profiles"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{profile_name}.log"

    with log_file.open("a", encoding="utf-8") as log_fp:
        log_fp.write(f"\n{'=' * 60}\n")
        log_fp.write(f"[REMOTE START] Host: {host_addr} ({remote_host_id})\n")
        log_fp.write(f"[REMOTE START] User: {host_user}\n")
        log_fp.write(f"[REMOTE START] Repo: {repo_dir}\n")
        log_fp.write(f"[REMOTE START] Command: {' '.join(ssh_cmd_parts[:4])} ...\n")
        log_fp.write(f"{'=' * 60}\n")

    try:
        verify_err = _verify_remote_runpy(host_user, host_addr, ssh_opts, repo_dir)
        if verify_err:
            with log_file.open("a", encoding="utf-8") as log_fp:
                log_fp.write(f"[REMOTE ERROR] {verify_err}\n")
            return False, f"Na hoście '{remote_host_id}' nie znaleziono run.py w {repo_dir}"

        proc = subprocess.Popen(
            ssh_cmd_parts,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        time.sleep(1.5)

        poll_result = proc.poll()
        if poll_result is not None and poll_result != 0:
            stderr_output = ""
            try:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            error_msg = stderr_output or "Błąd SSH"
            with log_file.open("a", encoding="utf-8") as log_fp:
                log_fp.write(f"[REMOTE ERROR] Exit code: {poll_result}\n")
                log_fp.write(f"[REMOTE ERROR] {error_msg}\n")
            return False, f"Błąd uruchamiania na hoście '{remote_host_id}': {error_msg[:200]}"

        with log_file.open("a", encoding="utf-8") as log_fp:
            log_fp.write(f"[REMOTE SUCCESS] Proces uruchomiony na {host_addr}\n")

        remote_info = {
            "host_id": remote_host_id,
            "host_addr": host_addr,
            "host_user": host_user,
            "repo_dir": repo_dir,
            "started_at": time.time(),
        }
        current_remote_profiles[profile_name] = remote_info
        _save_remote_state(profile_name, remote_info)

        record_profile_start(profile_name)

        if HAS_ACTIVITY_LOGGER:
            try:
                logger_db = ActivityLogger()
                logger_db.log_start(
                    component="profile_worker",
                    profile_name=profile_name,
                    configuration={
                        "headed": headed,
                        "remote": True,
                        "host": remote_host_id,
                        "host_addr": host_addr,
                    },
                    triggered_by="api",
                    reason=f"Uruchomiono profil zdalnie na hoście {remote_host_id}",
                )
            except Exception as log_error:
                print(f"Warning: Could not log start event: {log_error}")

        profile_service.set_profile_session_start(profile_name)
        return (
            True,
            f"Uruchomiono profil '{profile_name}' zdalnie na hoście '{remote_host_id}' ({host_addr})",
        )

    except subprocess.TimeoutExpired:
        with log_file.open("a", encoding="utf-8") as log_fp:
            log_fp.write("[REMOTE ERROR] Timeout podczas połączenia SSH\n")
        return False, f"Timeout podczas połączenia z hostem '{remote_host_id}'"
    except Exception as e:
        with log_file.open("a", encoding="utf-8") as log_fp:
            log_fp.write(f"[REMOTE ERROR] {e}\n")
        return False, f"Błąd zdalnego uruchamiania: {e}"


def start_limit_precheck(
    profiles: list[str] | None = None,
    *,
    quick: bool = False,
    parallel: int | None = None,
) -> tuple[bool, str]:
    """Start the limit precheck worker script in background."""
    global precheck_process

    if precheck_process and precheck_process.poll() is None:
        return False, "Precheck już działa"

    try:
        env = os.environ.copy()
        cmd = ["python3", "scripts/precheck_limits.py"]

        if profiles:
            cmd += ["--profiles", ",".join(profiles)]
        if quick:
            cmd.append("--quick")
        if parallel is not None:
            env["OCR_PRECHECK_PARALLEL"] = str(int(parallel))

        cwd = Path(__file__).parents[2]
        if not (cwd / "scripts" / "precheck_limits.py").exists():
            return False, "Nie znaleziono scripts/precheck_limits.py"

        log_dir = cwd / "logs" / "limits"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "precheck.log"

        with log_file.open("a", encoding="utf-8") as log_fp:
            precheck_process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        return True, f"Uruchomiono precheck limitów (PID: {precheck_process.pid})"
    except Exception as e:
        return False, f"Błąd uruchamiania precheck: {e}"


def stop_limit_precheck() -> tuple[bool, str]:
    """Stop the running limit precheck process if it exists."""
    global precheck_process
    if precheck_process and precheck_process.poll() is None:
        try:
            terminate_proc(precheck_process)
        except Exception:
            pass
        precheck_process = None
        return True, "Zatrzymano precheck"
    return False, "Precheck nie działa"


def _extract_precheck_json(output: str) -> dict | None:
    """Extract the last JSON blob printed by precheck_limits.py from stdout."""
    if not output:
        return None
    for match in reversed(list(re.finditer(r"\{", output))):
        blob = output[match.start() :].strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "profiles" in data:
            return data
    return None


def _resolve_remote_host(host_id: str | int) -> dict | None:
    config = get_effective_remote_config()
    hosts = config.get("OCR_REMOTE_HOSTS_LIST")
    if not isinstance(hosts, list):
        return None
    return next((h for h in hosts if str(h.get("id")) == str(host_id)), None)


def _build_precheck_remote_cmd(
    host: dict[str, object],
    profiles: list[str] | None,
    quick: bool,
    parallel: int | None,
) -> str:
    repo_dir = str(host.get("repo") or host.get("repoDir") or "").strip()
    python_cmd = str(host.get("python") or "python3").strip()
    profile_root = str(host.get("profileRoot") or "").strip()

    python_part = python_cmd or "python3"
    if not re.search(r"[\s;&|`$()]", python_part):
        python_part = shlex.quote(python_part)

    args: list[str] = ["scripts/precheck_limits.py"]
    if profile_root:
        args.extend(["--profiles-dir", profile_root])
    if profiles:
        args.extend(["--profiles", ",".join(profiles)])
    if quick:
        args.append("--quick")
    if parallel is not None:
        args.extend(["--parallel", str(int(parallel))])

    cmd_args = " ".join([python_part] + [shlex.quote(a) for a in args])
    return f"cd {shlex.quote(repo_dir)} && {cmd_args}"


def _exec_remote_precheck_sequence(
    host_addr: str,
    host_user: str,
    ssh_opts: str,
    repo_dir: str,
    remote_cmd: str,
    timeout_sec: int,
) -> tuple[bool, str]:
    # Check if script exists
    check_cmd = f"test -f {shlex.quote(repo_dir)}/scripts/precheck_limits.py"
    check_ssh = ["ssh"]
    if ssh_opts:
        check_ssh.extend(shlex.split(ssh_opts))
    check_ssh.extend([f"{host_user}@{host_addr}", check_cmd])

    try:
        cr = subprocess.run(check_ssh, capture_output=True, text=True, timeout=30, check=False)
        if cr.returncode != 0:
            return (
                False,
                f"Na hoście nie znaleziono scripts/precheck_limits.py (repo: {repo_dir})",
            )

        # Run main command
        ssh_cmd = ["ssh"]
        if ssh_opts:
            ssh_cmd.extend(shlex.split(ssh_opts))
        ssh_cmd.extend([f"{host_user}@{host_addr}", remote_cmd])

        res = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=timeout_sec, check=False
        )
        if res.returncode != 0:
            return (
                False,
                res.stderr.strip() or "Nie udało się uruchomić prechecka na hoście",
            )

        return True, res.stdout
    except subprocess.TimeoutExpired:
        return False, "Timeout podczas zdalnego sprawdzania limitów"


def _parse_precheck_results(data: dict) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for item in data.get("profiles", []) or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        profile = str(item[0])
        status = str(item[1])
        limited = status.startswith("LIMIT")
        error = status.startswith("ERROR")
        reset_time = "-"
        if status.startswith("LIMIT until "):
            reset_time = status.replace("LIMIT until ", "").strip() or "-"
        results.append(
            {
                "profile": profile,
                "status": status,
                "limited": limited,
                "error": error,
                "pro_remaining": "-",
                "reset_time": reset_time,
            }
        )
    return results


def run_remote_limit_precheck(
    host_id: str | int,
    profiles: list[str] | None = None,
    *,
    quick: bool = False,
    parallel: int | None = None,
    timeout_sec: int = 900,
) -> tuple[bool, str, list[dict[str, object]]]:
    """Run limit precheck on a remote host via SSH and return parsed results."""
    host = _resolve_remote_host(host_id)
    if not host:
        return False, f"Nie znaleziono hosta: {host_id}", []

    # Prepare inputs
    repo_dir = str(host.get("repo") or host.get("repoDir") or "").strip()
    if not repo_dir:
        return False, "Brak ustawionego katalogu repozytorium dla hosta", []

    host_addr = str(host.get("host") or host.get("address") or "").strip()
    host_user = str(host.get("user") or "root").strip()
    ssh_opts = str(host.get("ssh") or host.get("sshOpts") or "").strip()

    try:
        host_addr = validate_hostname(host_addr)
        host_user = validate_username(host_user)
    except ValueError as exc:
        return False, str(exc), []

    # Execute
    remote_cmd = _build_precheck_remote_cmd(host, profiles, quick, parallel)
    ok, output = _exec_remote_precheck_sequence(
        host_addr, host_user, ssh_opts, repo_dir, remote_cmd, timeout_sec
    )

    if not ok:
        return False, output, []

    # Parse
    data = _extract_precheck_json(output)
    if not data:
        return False, "Nie udało się odczytać wyników zdalnego prechecka", []

    results = _parse_precheck_results(data)
    return True, "OK", results


def _resolve_profile_source_dir(config: dict[str, object]) -> str | None:
    """Resolve the source directory for a profile, filtering unsafe paths.

    Handles:
    - Ignoring home directory paths from sticky frontend config
    - Resolving remote source root for non-local execution modes
    """
    source_path_cfg = config.get("source_path")
    if source_path_cfg:
        source_path_str = str(source_path_cfg).strip()
        # Check against home dir to prevent accidental scanning of entire home
        if source_path_str in (str(Path.home()), "/home/tomaasz"):
            source_path_cfg = None

    return _compose_source_path(source_path_cfg)


def _env_set_bool(
    env: dict[str, str],
    key: str,
    value: object,
    *,
    true_value: str = "1",
    false_value: str = "0",
) -> None:
    """Set a boolean environment variable if value is not None."""
    if value is None:
        return
    env[key] = true_value if bool(value) else false_value


def _env_set_str(env: dict[str, str], key: str, value: object) -> None:
    """Set a string environment variable if value is non-empty."""
    if value is None:
        return
    val = str(value).strip()
    if val == "":
        return
    env[key] = val


def _env_set_int(env: dict[str, str], key: str, value: object) -> None:
    """Set an integer environment variable if value is not None."""
    if value is None:
        return
    try:
        env[key] = str(int(value))
    except Exception:
        return


def _env_set_float(env: dict[str, str], key: str, value: object) -> None:
    """Set a float environment variable if value is not None."""
    if value is None:
        return
    try:
        env[key] = str(float(value))
    except Exception:
        return


def _apply_profile_env(env: dict[str, str], config: dict[str, object]) -> None:
    """Apply job config values to environment for single profile start."""

    _env_set_int(env, "OCR_WINDOWS", config.get("windows"))
    _env_set_int(env, "OCR_TABS_PER_WINDOW", config.get("tabs_per_window"))
    _env_set_int(env, "OCR_SCANS_PER_WORKER", config.get("scans_per_worker"))
    _env_set_int(env, "OCR_COLLECT_TIMEOUT_SEC", config.get("collect_timeout_sec"))
    _env_set_bool(env, "OCR_CLOSE_IDLE_TABS", config.get("close_idle_tabs"))
    _env_set_int(env, "OCR_MAX_TABS_PER_CONTEXT", config.get("max_tabs_per_context"))
    _env_set_bool(
        env,
        "OCR_USE_ISOLATED_CONTEXTS",
        config.get("isolated_contexts"),
        true_value="true",
        false_value="false",
    )
    _env_set_int(env, "OCR_CONTEXT_POOL_SIZE", config.get("context_pool_size"))
    _env_set_int(env, "OCR_VIEWPORT_WIDTH", config.get("viewport_width"))
    _env_set_int(env, "OCR_VIEWPORT_HEIGHT", config.get("viewport_height"))
    _env_set_bool(env, "OCR_REDUCED_MOTION", config.get("reduced_motion"))

    _env_set_bool(env, "OCR_PG_ENABLED", config.get("pg_enabled"))
    _env_set_str(env, "OCR_PG_DSN", config.get("pg_dsn"))
    _env_set_str(env, "OCR_PG_TABLE", config.get("pg_table"))

    _env_set_bool(env, "OCR_CONTINUE", config.get("continue_mode"))
    _env_set_bool(env, "OCR_AUTO_ADVANCE", config.get("auto_advance"))
    _env_set_bool(env, "OCR_PRO_ONLY", config.get("pro_only"))
    _env_set_str(env, "OCR_EXECUTION_MODE", config.get("execution_mode"))

    _env_set_str(
        env,
        "OCR_SOURCE_DIR",
        _resolve_profile_source_dir(config),
    )
    _env_set_bool(env, "OCR_CLEAN_TEMP_IMAGES", config.get("clean_temp_images"))
    _env_set_bool(
        env,
        "OCR_DEBUG_ARTIFACTS",
        config.get("debug_artifacts"),
        true_value="true",
        false_value="false",
    )
    _env_set_bool(
        env,
        "OCR_CAPTURE_VIDEO",
        config.get("capture_video"),
        true_value="true",
        false_value="false",
    )
    _env_set_str(env, "OCR_TRACING_MODE", config.get("tracing_mode"))
    _env_set_bool(env, "OCR_AUTH_ENSURE_ENABLED", config.get("auth_ensure_enabled"))
    _env_set_int(env, "OCR_AUTH_ENSURE_INTERVAL_SEC", config.get("auth_ensure_interval_sec"))
    _env_set_int(env, "OCR_MODEL_SWITCH_RETRIES", config.get("model_switch_retries"))
    _env_set_int(env, "OCR_MODEL_SWITCH_COOLDOWN_MS", config.get("model_switch_cooldown_ms"))
    _env_set_int(env, "OCR_LIMIT_CHECK_INTERVAL", config.get("limit_check_interval_sec"))
    _env_set_int(env, "OCR_PRO_PAUSE_BUFFER_SEC", config.get("pro_pause_buffer_sec"))
    _env_set_int(env, "OCR_PRO_FALLBACK_PAUSE_MIN", config.get("pro_fallback_pause_min"))
    _env_set_str(env, "OCR_BROWSER_ID", config.get("browser_id"))
    _env_set_str(env, "OCR_EXECUTION_MODE", config.get("execution_mode"))

    _env_set_int(env, "OCR_PREPROC_MAX_DIMENSION", config.get("preproc_max_dimension"))
    _env_set_int(env, "OCR_PREPROC_MEDIAN_KERNEL", config.get("preproc_median_kernel"))
    _env_set_int(env, "OCR_PREPROC_DENOISE_STRENGTH", config.get("preproc_denoise_strength"))
    _env_set_float(env, "OCR_PREPROC_CLAHE_CLIP_LIMIT", config.get("preproc_clahe_clip_limit"))
    _env_set_str(env, "OCR_PREPROC_CLAHE_GRID_SIZE", config.get("preproc_clahe_grid_size"))
    _env_set_int(env, "OCR_PREPROC_MORPH_KERNEL_SIZE", config.get("preproc_morph_kernel_size"))
    _env_set_float(env, "OCR_PREPROC_UNSHARP_AMOUNT", config.get("preproc_unsharp_amount"))
    _env_set_int(env, "OCR_PREPROC_UNSHARP_RADIUS", config.get("preproc_unsharp_radius"))
    _env_set_float(env, "OCR_PREPROC_MARGIN_PERCENT", config.get("preproc_margin_percent"))
    _env_set_int(env, "OCR_PREPROC_DARK_THRESHOLD", config.get("preproc_dark_threshold"))
    _env_set_float(
        env, "OCR_PREPROC_MARGIN_INK_RATIO_MAX", config.get("preproc_margin_ink_ratio_max")
    )
    _env_set_int(
        env, "OCR_PREPROC_MARGIN_SHADOW_MEAN_MAX", config.get("preproc_margin_shadow_mean_max")
    )
    _env_set_float(
        env, "OCR_PREPROC_BACKGROUND_KERNEL_RATIO", config.get("preproc_background_kernel_ratio")
    )
    _env_set_int(
        env, "OCR_PREPROC_BACKGROUND_KERNEL_MIN", config.get("preproc_background_kernel_min")
    )
    _env_set_float(
        env, "OCR_PREPROC_LOCAL_CONTRAST_SIGMA", config.get("preproc_local_contrast_sigma")
    )
    _env_set_float(
        env, "OCR_PREPROC_LOCAL_CONTRAST_AMOUNT", config.get("preproc_local_contrast_amount")
    )
    _env_set_int(
        env, "OCR_PREPROC_BLACKHAT_KERNEL_SIZE", config.get("preproc_blackhat_kernel_size")
    )
    _env_set_float(env, "OCR_PREPROC_BLACKHAT_STRENGTH", config.get("preproc_blackhat_strength"))
    _env_set_bool(
        env,
        "OCR_PREPROC_ENABLE_ADAPTIVE_BINARIZATION",
        config.get("preproc_enable_adaptive_binarization"),
    )
    _env_set_int(env, "OCR_PREPROC_SAUVOLA_WINDOW", config.get("preproc_sauvola_window"))
    _env_set_float(env, "OCR_PREPROC_SAUVOLA_K", config.get("preproc_sauvola_k"))
    _env_set_float(env, "OCR_PREPROC_SAUVOLA_R", config.get("preproc_sauvola_r"))
    _env_set_int(
        env, "OCR_PREPROC_TEXT_MASK_BLOCK_SIZE", config.get("preproc_text_mask_block_size")
    )
    _env_set_int(env, "OCR_PREPROC_TEXT_MASK_C", config.get("preproc_text_mask_c"))
    _env_set_int(
        env, "OCR_PREPROC_TEXT_MASK_OPEN_KERNEL", config.get("preproc_text_mask_open_kernel")
    )
    _env_set_int(
        env, "OCR_PREPROC_TEXT_MASK_CLOSE_KERNEL", config.get("preproc_text_mask_close_kernel")
    )
    _env_set_int(
        env, "OCR_PREPROC_TEXT_MASK_CLOSE_ITERS", config.get("preproc_text_mask_close_iters")
    )
    _env_set_int(
        env, "OCR_PREPROC_TEXT_MASK_DILATE_ITERS", config.get("preproc_text_mask_dilate_iters")
    )
    _env_set_float(
        env, "OCR_PREPROC_TEXT_MASK_MIN_AREA_RATIO", config.get("preproc_text_mask_min_area_ratio")
    )
    _env_set_float(env, "OCR_PREPROC_TRIM_BAND_RATIO", config.get("preproc_trim_band_ratio"))
    _env_set_float(env, "OCR_PREPROC_TRIM_INK_RATIO_MAX", config.get("preproc_trim_ink_ratio_max"))
    _env_set_float(env, "OCR_PREPROC_TRIM_MAX_RATIO", config.get("preproc_trim_max_ratio"))
    _env_set_int(env, "OCR_PREPROC_TRIM_MIN_DIMENSION", config.get("preproc_trim_min_dimension"))


def start_login_process(profile_name: str) -> tuple[bool, str]:
    """Start the login helper process for a profile."""

    pids = get_profile_pids(profile_name)
    running_pids = [pid for pid in pids if pid_is_running(pid)]
    if running_pids:
        return (
            False,
            f"Profil '{profile_name}' jest zajęty przez proces OCR (PID: {running_pids}). Zatrzymaj go najpierw.",
        )

    try:
        env = os.environ.copy()
        env["OCR_PROFILE_SUFFIX"] = profile_name
        env["OCR_HEADED"] = "1"  # Always headed for login

        cmd = ["python3", "scripts/login_profile.py"]

        cwd = Path(__file__).parents[2]
        if not (cwd / "scripts" / "login_profile.py").exists():
            return False, "Nie znaleziono pliku scripts/login_profile.py"

        log_dir = cwd / "logs" / "profiles"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{profile_name}.login.log"
        log_file.write_text("=== Inicjalizacja logowania ===\n", encoding="utf-8")

        with log_file.open("a", encoding="utf-8") as log_fp:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        return True, f"Uruchomiono logowanie dla '{profile_name}' (PID: {process.pid})"

    except Exception as e:
        return False, f"Błąd uruchamiania logowania: {e}"


def prune_profile_starts(now_ts: float | None = None) -> None:
    """Remove stale profile start attempts."""
    cutoff = PROFILE_START_TRACK_SEC
    now_val = now_ts or time.time()
    stale = [p for p, ts in profile_start_attempts.items() if (now_val - ts) > cutoff]
    for p in stale:
        profile_start_attempts.pop(p, None)
