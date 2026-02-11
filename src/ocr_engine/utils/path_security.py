"""Security utilities for path validation and sanitization.

This module provides centralized functions to prevent path traversal
and other security vulnerabilities related to file system operations.
"""

import os
import re
from pathlib import Path


def sanitize_profile_name(name: str) -> str:
    """Sanitize profile name to prevent path traversal.

    Args:
        name: Profile name from user input or environment

    Returns:
        Sanitized profile name safe for use in file paths

    Examples:
        >>> sanitize_profile_name("my-profile")
        'my-profile'
        >>> sanitize_profile_name("../../../etc/passwd")
        'etcpasswd'
        >>> sanitize_profile_name("")
        'default'
    """
    if not name:
        return "default"

    # Remove dangerous patterns
    clean = name.replace("..", "")
    clean = re.sub(r'[<>:"|?*\\/]', "", clean)
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "_", clean)

    return clean or "default"


def validate_profiles_dir(path_input: str | None = None) -> Path:
    """Validate profiles directory from environment or argument.

    Security: Ensures the profiles directory is within user's home directory
    to prevent access to sensitive system locations.

    Args:
        path_input: Optional path from CLI argument or other source

    Returns:
        Validated and resolved Path object

    Raises:
        ValueError: If path is outside user's home directory

    Examples:
        >>> validate_profiles_dir()  # Uses default
        PosixPath('/home/user/.cache/ocr-dashboard-v3')
        >>> validate_profiles_dir("/home/user/custom")
        PosixPath('/home/user/custom')
    """
    default = str(Path.home() / ".cache/ocr-dashboard-v3")
    path_str = path_input or os.environ.get("PROFILES_DIR", default)

    try:
        path = Path(path_str).resolve()
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid path: {path_str} - {e}")

    # Must be within user's home directory
    home = Path.home().resolve()
    if not path.is_relative_to(home):
        raise ValueError(
            f"Profiles directory must be within home directory: {path_str}\n"
            f"Got: {path}\nExpected within: {home}"
        )

    return path


def validate_cache_dir(path_input: str | None = None, allow_tmp: bool = True) -> Path:
    """Validate cache directory path.

    Security: Ensures cache directory is in safe location (home or /tmp).

    Args:
        path_input: Optional path from CLI argument or other source
        allow_tmp: Whether to allow /tmp directory (default: True)

    Returns:
        Validated and resolved Path object

    Raises:
        ValueError: If path is in unsafe location
    """
    default = str(Path.home() / ".cache/ocr-dashboard-v3")
    path_str = path_input or os.environ.get("CACHE_DIR", default)

    try:
        path = Path(path_str).resolve()
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid cache path: {path_str} - {e}")

    # Must be within user's home or /tmp
    home = Path.home().resolve()
    tmp = Path("/tmp").resolve()

    is_in_home = path.is_relative_to(home)
    is_in_tmp = allow_tmp and path.is_relative_to(tmp)

    if not (is_in_home or is_in_tmp):
        raise ValueError(
            f"Cache directory must be in home or /tmp: {path_str}\n"
            f"Got: {path}\nExpected within: {home} or {tmp if allow_tmp else 'N/A'}"
        )

    return path


def validate_directory_arg(
    path_input: str | None = None,
    env_var: str | None = None,
    default: Path | None = None,
    must_be_in_home: bool = True,
) -> Path:
    """Validate directory path from CLI argument or environment variable.

    Generic validation function for directory arguments. Provides flexible
    validation with multiple fallback options.

    Args:
        path_input: Path from CLI argument
        env_var: Environment variable name to check if path_input is None
        default: Default path to use if both path_input and env_var are empty
        must_be_in_home: If True, requires path within user's home directory

    Returns:
        Validated and resolved Path object

    Raises:
        ValueError: If path fails validation or is in unsafe location
    """
    # Determine path source
    if path_input:
        path_str = path_input
    elif env_var:
        path_str = os.environ.get(env_var, "")
        if not path_str and default:
            return default.resolve()
    elif default:
        return default.resolve()
    else:
        raise ValueError("No path provided and no default specified")

    # Validate path
    try:
        path = Path(path_str).resolve()
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid directory path: {path_str} - {e}")

    # Ensure absolute path
    if not path.is_absolute():
        raise ValueError(f"Path must be absolute: {path_str}")

    # Check if in home directory (if required)
    if must_be_in_home:
        home = Path.home().resolve()
        if not path.is_relative_to(home):
            raise ValueError(
                f"Directory must be within home: {path_str}\nGot: {path}\nExpected within: {home}"
            )

    # Prevent access to sensitive system directories
    sensitive_dirs = [
        Path("/etc"),
        Path("/sys"),
        Path("/proc"),
        Path("/boot"),
        Path("/root"),  # Unless user IS root
    ]

    for sensitive in sensitive_dirs:
        try:
            sensitive_resolved = sensitive.resolve()
            if path == sensitive_resolved or path.is_relative_to(sensitive_resolved):
                raise ValueError(f"Access to sensitive directory not allowed: {sensitive}")
        except (ValueError, OSError):
            # Ignore if sensitive dir doesn't exist
            continue

    return path


def safe_path_join(base_dir: Path, user_input: str, prefix: str = "") -> Path:
    """Safely join base directory with user input.

    Security: Ensures resulting path stays within base directory.

    Args:
        base_dir: Base directory (must be trusted)
        user_input: User-provided path component (untrusted)
        prefix: Optional prefix for the filename

    Returns:
        Safe path within base_dir

    Raises:
        ValueError: If resulting path escapes base_dir

    Examples:
        >>> safe_path_join(Path("/cache"), "profile1", "pause_")
        PosixPath('/cache/pause_profile1')
        >>> safe_path_join(Path("/cache"), "../etc/passwd", "x")
        ValueError: Path traversal attempt
    """
    # Sanitize input
    safe_input = sanitize_profile_name(user_input)

    # Construct path
    if prefix:
        filename = f"{prefix}{safe_input}"
    else:
        filename = safe_input

    result_path = base_dir / filename

    # Validate result stays in base
    try:
        resolved = result_path.resolve()
        base_resolved = base_dir.resolve()

        if not resolved.is_relative_to(base_resolved):
            raise ValueError(f"Path traversal attempt: {user_input}")
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid path construction: {user_input} - {e}")

    return resolved
