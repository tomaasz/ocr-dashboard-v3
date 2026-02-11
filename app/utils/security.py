"""
OCR Dashboard V2 - Security Utilities
Input validation functions to prevent command injection and path traversal.
"""

import re


def validate_hostname(hostname: str | None) -> str:
    """
    Validate hostname or IP address to prevent command injection.
    Returns sanitized hostname or raises ValueError.
    """
    if not hostname:
        raise ValueError("Hostname cannot be empty")

    hostname = hostname.strip()

    # Allow only: alphanumeric, dots, hyphens, colons (for IPv6), underscores
    if not re.match(r"^[a-zA-Z0-9.\-:_]+$", hostname):
        raise ValueError(f"Invalid hostname format: {hostname}")

    # Additional check: no command injection patterns
    dangerous_patterns = [";", "|", "&", "$", "`", "(", ")", "<", ">", "\n", "\r"]
    if any(char in hostname for char in dangerous_patterns):
        raise ValueError(f"Hostname contains dangerous characters: {hostname}")

    return hostname


def validate_username(username: str | None) -> str:
    """
    Validate username to prevent command injection.
    Returns sanitized username or raises ValueError.
    """
    if not username:
        raise ValueError("Username cannot be empty")

    username = username.strip()

    # Allow only: alphanumeric, underscore, hyphen, dot
    if not re.match(r"^[a-zA-Z0-9_\-.]+$", username):
        raise ValueError(f"Invalid username format: {username}")

    if len(username) > 64:
        raise ValueError(f"Username too long: {username}")

    return username


def validate_profile_name(profile: str) -> str:
    """
    Strict profile name validation to prevent path traversal.
    Returns sanitized profile name or raises ValueError.
    """
    if not profile:
        raise ValueError("Profile name cannot be empty")

    profile = profile.strip()

    # Allow only: alphanumeric, underscore, hyphen, dot
    if not re.match(r"^[a-zA-Z0-9_\-.]+$", profile):
        raise ValueError(f"Invalid profile name format: {profile}")

    # Prevent path traversal attempts
    if ".." in profile or "/" in profile or "\\" in profile:
        raise ValueError(f"Profile name contains path traversal characters: {profile}")

    if len(profile) > 64:
        raise ValueError(f"Profile name too long: {profile}")

    return profile


def validate_worker_id(worker_id: str) -> str:
    """
    Validate worker ID to prevent injection attacks.
    Returns sanitized worker_id or raises ValueError.
    """
    if not worker_id:
        raise ValueError("Worker ID cannot be empty")

    worker_id = worker_id.strip().lower()

    # Allow specific safe patterns
    allowed_keywords = {"limit", "pause", "session", "login", "expired"}

    if worker_id in allowed_keywords:
        return worker_id

    # Check if it's a digit or w+digit
    if re.match(r"^w?\d+$", worker_id):
        return worker_id

    raise ValueError(f"Invalid worker ID format: {worker_id}")


def validate_wsl_distro(distro: str | None) -> str:
    """
    Validate WSL distro name to prevent command injection.
    Returns sanitized distro name or empty string if None.
    """
    if not distro:
        return ""

    distro = distro.strip()

    # Allow only: alphanumeric, dot, hyphen, underscore
    if not re.match(r"^[a-zA-Z0-9._-]+$", distro):
        raise ValueError(f"Invalid WSL distro name format: {distro}")

    if len(distro) > 64:
        raise ValueError(f"WSL distro name too long: {distro}")

    return distro


def validate_path(path: str, base_dir: str = None) -> str:
    """
    Validate file path to prevent path traversal.
    If base_dir is provided, ensures path is within base_dir.
    """
    import os

    if not path:
        raise ValueError("Path cannot be empty")

    path = path.strip()

    # Prevent null bytes
    if "\x00" in path:
        raise ValueError("Path contains null bytes")

    if base_dir:
        abs_base = os.path.abspath(base_dir)
        abs_path = os.path.abspath(os.path.join(base_dir, path))

        # Verify resolved path is inside base directory
        if os.path.commonpath([abs_base, abs_path]) != abs_base:
            raise ValueError(f"Path traversal attempt detected: {path}")

        return abs_path

    return path


def validate_ssh_opts(opts: str) -> str:
    """
    Validate SSH options to prevent command injection.
    Allows only safe options and prevents ProxyCommand/LocalCommand.
    """
    if not opts:
        return ""

    # Allow alphanumeric, spaces, dashes in reasonable format
    # But explicitly ban dangerous characters
    if any(c in opts for c in [";", "|", "&", "$", "`", "(", ")", "<", ">", "\n", "\r"]):
        raise ValueError("SSH options contain dangerous characters")

    # Check for dangerous options
    # -o ProxyCommand=... could execute commands
    opts_check = opts.lower()
    if (
        "proxycommand" in opts_check
        or "localcommand" in opts_check
        or "permitlocalcommand" in opts_check
    ):
        raise ValueError("SSH options contain forbidden directives (ProxyCommand/LocalCommand)")

    return opts.strip()
