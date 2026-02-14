"""
Source path resolution and provider abstraction.

Resolves source identifiers to concrete providers that can list & access files.

Architecture:
    source_path string → SourceResolver → SourceProvider instance
                                          ├── FilesystemProvider (NAS, GDrive mount, local)
                                          ├── UrlProvider        (HTTP/HTTPS — Phase 3)
                                          └── GDriveApiProvider  (native API — Phase 4)

Path formats:
    "nas/Nurskie/1_43"              → relative → SOURCE_ROOT/nas/Nurskie/1_43
    "/data/sources/nas/Nurskie/1"   → absolute → as-is
    "url:https://example.com/img"   → scheme   → UrlProvider
    "gdrive-api:folder_id"          → scheme   → GDriveApiProvider
    "user@host:~/path"              → legacy   → passthrough with warning
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Constants ───

DEFAULT_SOURCE_ROOT = "/data/sources"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp")

# Matches scheme prefix: "url:", "gdrive-api:", etc.
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9_-]*):(.+)$", re.IGNORECASE)

# Legacy patterns — absolute filesystem paths or SSH notation
_ABSOLUTE_PATH_RE = re.compile(
    r"^("
    r"/"  # Unix absolute
    r"|~"  # Home dir
    r"|[A-Za-z]:[\\/]"  # Windows drive
    r"|\\\\[^\\]"  # UNC path
    r"|[a-zA-Z0-9._-]+@[^:]+:"  # SSH notation user@host:path
    r")"
)


# ─── Data classes ───


@dataclass(slots=True)
class FileEntry:
    """Represents a file available for OCR processing."""

    name: str
    full_path: str
    size_bytes: int | None = None
    mtime: float | None = None


# ─── Provider interface ───


class SourceProvider(ABC):
    """Abstract interface for accessing scan files from any storage backend."""

    @abstractmethod
    def list_files(self, extensions: tuple[str, ...] | None = None) -> list[FileEntry]:
        """List available image files in this source."""

    @abstractmethod
    def get_file_path(self, filename: str) -> Path:
        """Get local filesystem path for a file (download if necessary)."""

    @abstractmethod
    def exists(self) -> bool:
        """Check if the source is accessible."""

    @abstractmethod
    def file_count(self) -> int:
        """Get total number of image files."""

    @property
    @abstractmethod
    def canonical_id(self) -> str:
        """Unique identifier for this source (used in DB, logs, dedup)."""


# ─── Filesystem provider ───


class FilesystemProvider(SourceProvider):
    """Provider for locally-mounted directories (NAS, rclone, local files)."""

    def __init__(self, path: Path):
        self._path = (
            path.resolve() if not str(path).startswith("~") else path.expanduser().resolve()
        )

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
        """Direct access to resolved filesystem path."""
        return self._path


# ─── URL provider (Phase 3) ───


class UrlProvider(SourceProvider):
    """Provider for downloading files from HTTP/HTTPS URLs."""

    def __init__(self, url: str, cache_dir: Path | None = None):
        self._url = url
        self._cache_dir = cache_dir or Path(DEFAULT_SOURCE_ROOT) / ".cache" / "url"

    def list_files(self, extensions: tuple[str, ...] | None = None) -> list[FileEntry]:
        raise NotImplementedError("URL provider — Phase 3")

    def get_file_path(self, filename: str) -> Path:
        raise NotImplementedError("URL provider — Phase 3")

    def exists(self) -> bool:
        return True  # assumed until proven otherwise

    def file_count(self) -> int:
        return 0

    @property
    def canonical_id(self) -> str:
        return f"url:{self._url}"


# ─── GDrive API provider (Phase 4) ───


class GDriveApiProvider(SourceProvider):
    """Provider for Google Drive via native API (without rclone mount)."""

    _NOT_IMPLEMENTED_MSG = "GDrive API provider — Phase 4"

    def __init__(self, folder_id: str, credentials_path: Path | None = None):
        self._folder_id = folder_id
        self._credentials_path = credentials_path

    def list_files(self, extensions: tuple[str, ...] | None = None) -> list[FileEntry]:
        raise NotImplementedError(self._NOT_IMPLEMENTED_MSG)

    def get_file_path(self, filename: str) -> Path:
        raise NotImplementedError(self._NOT_IMPLEMENTED_MSG)

    def exists(self) -> bool:
        raise NotImplementedError(self._NOT_IMPLEMENTED_MSG)

    def file_count(self) -> int:
        return 0

    @property
    def canonical_id(self) -> str:
        return f"gdrive-api:{self._folder_id}"


# ─── Source Resolver ───


class SourceResolver:
    """
    Resolves source path strings into SourceProvider instances.

    Resolution order:
    1. Scheme prefix (url:, gdrive-api:) → specialized provider
    2. Absolute path / SSH notation       → FilesystemProvider (legacy compat)
    3. Relative path                      → SOURCE_ROOT + path → FilesystemProvider
    """

    def __init__(
        self,
        source_root: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        self._source_root = Path(
            source_root or os.environ.get("OCR_SOURCE_ROOT") or DEFAULT_SOURCE_ROOT
        )
        self._config = config or {}

    @property
    def source_root(self) -> Path:
        return self._source_root

    def resolve(self, source_path: str) -> SourceProvider:
        """Resolve a source path string to a SourceProvider.

        Args:
            source_path: Relative, absolute, or scheme-prefixed path.

        Returns:
            SourceProvider ready to list/access files.

        Raises:
            ValueError: If source_path is empty.
        """
        source_path = source_path.strip()
        if not source_path:
            raise ValueError("source_path cannot be empty")

        # 1. Check for scheme prefix (url:, gdrive-api:, etc.)
        scheme_match = _SCHEME_RE.match(source_path)
        if scheme_match:
            scheme = scheme_match.group(1).lower()
            value = scheme_match.group(2).strip()
            return self._resolve_scheme(scheme, value)

        # 2. Check for absolute / legacy paths
        if _ABSOLUTE_PATH_RE.match(source_path):
            if "@" in source_path and ":" in source_path:
                logger.warning(
                    "⚠️ [SourceResolver] SSH notation detected: '%s'. "
                    "Migrate to standardized mount under %s",
                    source_path,
                    self._source_root,
                )
            return FilesystemProvider(Path(source_path))

        # 3. Relative path → join with source_root
        full_path = self._source_root / source_path.lstrip("/\\")
        return FilesystemProvider(full_path)

    def resolve_to_local_path(self, source_path: str) -> Path:
        """Convenience: resolve and return local filesystem Path.

        Raises:
            ValueError: If source is not filesystem-based.
        """
        provider = self.resolve(source_path)
        if isinstance(provider, FilesystemProvider):
            return provider.path
        raise ValueError(
            f"Source '{source_path}' is not a filesystem path (provider: {type(provider).__name__})"
        )

    def verify(self, source_path: str) -> dict[str, Any]:
        """Verify source accessibility and return status dict."""
        try:
            provider = self.resolve(source_path)
            accessible = provider.exists()
            count = provider.file_count() if accessible else None
            return {
                "accessible": accessible,
                "provider": type(provider).__name__,
                "canonical_id": provider.canonical_id,
                "file_count": count,
                "error": None if accessible else "Source not found or inaccessible",
            }
        except Exception as exc:
            return {
                "accessible": False,
                "provider": "unknown",
                "canonical_id": source_path,
                "file_count": None,
                "error": str(exc),
            }

    def _resolve_scheme(self, scheme: str, value: str) -> SourceProvider:
        if scheme == "url":
            cache_dir_str = self._config.get("url", {}).get("cache_dir")
            cache_dir = Path(cache_dir_str) if cache_dir_str else None
            return UrlProvider(value, cache_dir)

        if scheme in ("gdrive-api", "gdrive_api"):
            cred = self._config.get("gdrive_api", {}).get("credentials")
            return GDriveApiProvider(value, Path(cred) if cred else None)

        # Unknown scheme — treat as filesystem subpath
        logger.warning("Unknown scheme '%s', treating as filesystem path", scheme)
        return FilesystemProvider(self._source_root / scheme / value)


# ─── Module-level convenience ───

_resolver: SourceResolver | None = None


def get_resolver() -> SourceResolver:
    """Get or create the singleton SourceResolver."""
    global _resolver  # noqa: PLW0603
    if _resolver is None:
        config_path = Path(__file__).parents[2] / "config" / "sources.json"
        config: dict[str, Any] = {}
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                config = raw
            except Exception:
                logger.warning("Failed to load %s, using defaults", config_path)
        _resolver = SourceResolver(
            source_root=config.get("source_root"),
            config=config.get("providers", {}),
        )
    return _resolver


def reset_resolver() -> None:
    """Reset singleton (for testing)."""
    global _resolver  # noqa: PLW0603
    _resolver = None


def resolve_source(source_path: str) -> SourceProvider:
    """Resolve source path using the default resolver."""
    return get_resolver().resolve(source_path)


def resolve_source_path(source_path: str) -> Path:
    """Resolve source path to local filesystem Path."""
    return get_resolver().resolve_to_local_path(source_path)
