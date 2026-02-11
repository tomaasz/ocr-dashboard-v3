"""Cleanup helpers for temporary folders."""

from __future__ import annotations

import shutil
from pathlib import Path

DEFAULT_CLEANUP_TARGETS = ["jobs", "logs", "ui_health"]


def cleanup_folders(
    project_root: Path,
    targets: list[str],
    force: bool,
) -> tuple[list[str], list[str]]:
    """Remove contents of selected folders under the project root.

    Args:
        project_root: Root directory of the project.
        targets: Folder keys to clean.
        force: Reserved for future behavior tweaks.

    Returns:
        Tuple of (cleaned_targets, error_messages).
    """
    _ = force  # Currently unused; kept for API compatibility.

    folder_map = {
        "jobs": project_root / "jobs",
        "logs": project_root / "logs",
        "artifacts": project_root / "artifacts",
        "ui_health": project_root / "artifacts" / "screenshots" / "ui_health",
        "test-results": project_root / "test-results",
        "pycache": project_root / "__pycache__",
    }

    cleaned: list[str] = []
    errors: list[str] = []

    for target in targets:
        if target not in folder_map:
            errors.append(f"Nieznany folder: {target}")
            continue

        folder = folder_map[target]
        if not folder.exists():
            continue

        try:
            if target == "pycache":
                for pycache_dir in project_root.rglob("__pycache__"):
                    try:
                        shutil.rmtree(pycache_dir)
                    except Exception:
                        continue
                cleaned.append(target)
                continue

            for item in folder.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception as exc:
                    errors.append(f"{target}/{item.name}: {exc!s}")
            cleaned.append(target)
        except Exception as exc:
            errors.append(f"{target}: {exc!s}")

    return cleaned, errors
