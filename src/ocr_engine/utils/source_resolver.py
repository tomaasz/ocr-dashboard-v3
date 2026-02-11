"""
Source path resolution for OCR engine.

Thin wrapper — imports shared logic from app.services.source_resolver
when available, otherwise provides standalone FilesystemProvider.

Used by GeminiEngine to resolve OCR_SOURCE_DIR to a local Path.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_ROOT = "/data/sources"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp")

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9_-]*):(.+)$", re.IGNORECASE)

_ABSOLUTE_PATH_RE = re.compile(
    r"^("
    r"/"
    r"|~"
    r"|[A-Za-z]:[\\/]"
    r"|\\\\[^\\]"
    r"|[a-zA-Z0-9._-]+@[^:]+:"
    r")"
)


@dataclass(slots=True)
class FileEntry:
    """Represents a file available for OCR processing."""

    name: str
    full_path: str
    size_bytes: int | None = None
    mtime: float | None = None


class FilesystemProvider:
    """Provider for locally-mounted directories."""

    def __init__(self, path: Path):
        self._path = path.expanduser().resolve()

    def list_files(self, extensions: tuple[str, ...] | None = None) -> list[FileEntry]:
        exts = extensions or IMAGE_EXTENSIONS
        if not self._path.exists():
            return []
        files = []
        for f in sorted(self._path.iterdir(), key=lambda p: p.name):
            if f.is_file() and f.suffix.lower() in exts:
                try:
                    stat = f.stat()
                    files.append(
                        FileEntry(
                            name=f.name,
                            full_path=str(f),
                            size_bytes=stat.st_size,
                            mtime=stat.st_mtime,
                        )
                    )
                except OSError:
                    files.append(FileEntry(name=f.name, full_path=str(f)))
        return files

    def get_file_path(self, filename: str) -> Path:
        return self._path / filename

    def exists(self) -> bool:
        return self._path.is_dir()

    def file_count(self) -> int:
        if not self._path.exists():
            return 0
        return sum(
            1 for f in self._path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        )

    @property
    def canonical_id(self) -> str:
        return str(self._path)

    @property
    def path(self) -> Path:
        return self._path


def resolve_source_dir(source_dir_raw: str) -> Path:
    """Resolve OCR_SOURCE_DIR to a local filesystem Path.

    Handles:
    - Relative path: prepends SOURCE_ROOT
    - Absolute path: returns as-is
    - Scheme-prefixed: raises (not supported in engine directly)
    - SSH notation: warns and returns as-is (legacy)
    """
    text = source_dir_raw.strip()
    if not text:
        raise ValueError("OCR_SOURCE_DIR is empty")

    # Scheme prefix — not supported directly in engine
    if _SCHEME_RE.match(text):
        scheme = _SCHEME_RE.match(text).group(1).lower()
        if scheme == "url":
            raise ValueError(
                f"URL sources not supported in engine directly. "
                f"Dashboard should resolve '{text}' to local path first."
            )
        logger.warning("Unknown scheme '%s' in OCR_SOURCE_DIR, treating as path", scheme)

    # Absolute path
    if _ABSOLUTE_PATH_RE.match(text):
        if "@" in text and ":" in text:
            logger.warning(
                "⚠️ SSH notation in OCR_SOURCE_DIR: '%s'. Use standardized mount at %s instead.",
                text,
                DEFAULT_SOURCE_ROOT,
            )
        return Path(text).expanduser().resolve()

    # Relative path → prepend SOURCE_ROOT
    source_root = Path(os.environ.get("OCR_SOURCE_ROOT", DEFAULT_SOURCE_ROOT))
    full_path = source_root / text.lstrip("/\\")
    return full_path.resolve()
