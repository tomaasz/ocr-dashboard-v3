"""
Common utilities for reading and processing log files.

Provides reusable functions for log file operations to reduce code duplication.
"""

from pathlib import Path


def read_log_file_tail(log_file: Path, tail: int) -> dict:
    """
    Read the last N lines from a log file.

    Args:
        log_file: Path to the log file
        tail: Number of lines to read from the end (capped at 2000)

    Returns:
        Dictionary with 'log' key containing the tail content
    """
    try:
        tail = max(1, min(int(tail), 2000))
        lines = []
        with log_file.open(encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            lines = all_lines[-tail:]

        return {"log": "".join(lines)}
    except Exception as e:
        return {"log": f"[Error reading log: {e}]"}


def read_log_file_lines(log_file: Path) -> list[str]:
    """
    Read all lines from a log file safely.

    Args:
        log_file: Path to the log file

    Returns:
        List of lines from the file, or error message if reading fails
    """
    try:
        with log_file.open(encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception as e:
        return [f"[Error reading log: {e}]"]


def is_error_line(line: str) -> bool:
    """
    Detect if a log line contains error/critical information.

    Args:
        line: Log line to check

    Returns:
        True if line contains error indicators
    """
    return "ERROR" in line or "CRITICAL" in line or "Traceback" in line or "Exception" in line


def get_log_with_errors(log_file: Path, tail: int = 200, error_tail: int = 100) -> dict:
    """
    Get log tail and recent error lines from a log file.

    Args:
        log_file: Path to the log file
        tail: Number of regular lines to include
        error_tail: Number of error lines to include

    Returns:
        Dictionary with log content, errors, and metadata
    """
    if not log_file.exists():
        return {"log": "", "errors": "", "exists": False, "lines": 0, "error_lines": 0}

    tail = max(1, min(int(tail), 2000))
    error_tail = max(1, min(int(error_tail), 2000))

    lines = read_log_file_lines(log_file)
    log_lines = lines[-tail:]
    error_lines = [line for line in lines if is_error_line(line)]
    error_lines = error_lines[-error_tail:]

    return {
        "log": "".join(log_lines),
        "errors": "".join(error_lines),
        "exists": True,
        "lines": len(log_lines),
        "error_lines": len(error_lines),
    }
