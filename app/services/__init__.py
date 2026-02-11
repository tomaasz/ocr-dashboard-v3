"""OCR Dashboard V2 - Services Package"""

from .process import (
    current_processes,
    current_profile_processes,
    current_remote_profiles,
    find_pids_by_patterns,
    get_profile_pids,
    iter_runpy_processes,
    pid_is_running,
    postprocess_process,
    profile_start_attempts,
    prune_profile_starts,
    record_profile_start,
    stop_profile_processes,
    terminate_pid,
    terminate_proc,
)
from .profiles import (
    clear_profile_cache,
    create_profile,
    delete_profile,
    get_active_chrome_profile,
    get_profile_dir,
    list_profiles,
    profile_exists,
)

__all__ = [
    # Profiles
    "list_profiles",
    "get_profile_dir",
    "profile_exists",
    "create_profile",
    "delete_profile",
    "get_active_chrome_profile",
    "clear_profile_cache",
    # Process
    "pid_is_running",
    "terminate_pid",
    "terminate_proc",
    "find_pids_by_patterns",
    "iter_runpy_processes",
    "get_profile_pids",
    "stop_profile_processes",
    "record_profile_start",
    "prune_profile_starts",
    "current_processes",
    "current_profile_processes",
    "current_remote_profiles",
    "profile_start_attempts",
    "postprocess_process",
]
