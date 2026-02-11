"""Tests for source_resolver — SourceResolver and FilesystemProvider."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.source_resolver import (
    DEFAULT_SOURCE_ROOT,
    FileEntry,
    FilesystemProvider,
    GDriveApiProvider,
    SourceResolver,
    UrlProvider,
    get_resolver,
    reset_resolver,
    resolve_source,
    resolve_source_path,
)


# ─── FilesystemProvider ───


class TestFilesystemProvider:
    def test_exists_real_dir(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path)
        assert provider.exists() is True

    def test_exists_missing_dir(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path / "nonexistent")
        assert provider.exists() is False

    def test_canonical_id(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path)
        assert provider.canonical_id == str(tmp_path.resolve())

    def test_path_property(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path)
        assert provider.path == tmp_path.resolve()

    def test_list_files_empty(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path)
        assert provider.list_files() == []

    def test_list_files_with_images(self, tmp_path: Path):
        (tmp_path / "scan_001.jpg").touch()
        (tmp_path / "scan_002.png").touch()
        (tmp_path / "notes.txt").touch()  # should be excluded

        provider = FilesystemProvider(tmp_path)
        files = provider.list_files()
        names = [f.name for f in files]

        assert "scan_001.jpg" in names
        assert "scan_002.png" in names
        assert "notes.txt" not in names

    def test_list_files_sorted(self, tmp_path: Path):
        (tmp_path / "c.jpg").touch()
        (tmp_path / "a.jpg").touch()
        (tmp_path / "b.jpg").touch()

        provider = FilesystemProvider(tmp_path)
        files = provider.list_files()
        assert [f.name for f in files] == ["a.jpg", "b.jpg", "c.jpg"]

    def test_list_files_custom_extensions(self, tmp_path: Path):
        (tmp_path / "doc.pdf").touch()
        (tmp_path / "scan.jpg").touch()

        provider = FilesystemProvider(tmp_path)
        files = provider.list_files(extensions=(".pdf",))
        assert len(files) == 1
        assert files[0].name == "doc.pdf"

    def test_file_count(self, tmp_path: Path):
        (tmp_path / "a.jpg").touch()
        (tmp_path / "b.png").touch()
        (tmp_path / "c.txt").touch()

        provider = FilesystemProvider(tmp_path)
        assert provider.file_count() == 2

    def test_file_count_empty(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path)
        assert provider.file_count() == 0

    def test_file_count_nonexistent(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path / "nope")
        assert provider.file_count() == 0

    def test_get_file_path(self, tmp_path: Path):
        provider = FilesystemProvider(tmp_path)
        result = provider.get_file_path("scan_001.jpg")
        assert result == tmp_path.resolve() / "scan_001.jpg"

    def test_list_files_includes_metadata(self, tmp_path: Path):
        f = tmp_path / "test.jpg"
        f.write_bytes(b"x" * 100)

        provider = FilesystemProvider(tmp_path)
        files = provider.list_files()
        assert len(files) == 1
        assert files[0].size_bytes == 100
        assert files[0].mtime is not None


# ─── SourceResolver ───


class TestSourceResolver:
    """Test resolution of various source path formats."""

    def test_relative_path(self, tmp_path: Path):
        resolver = SourceResolver(source_root=str(tmp_path))
        provider = resolver.resolve("nas/Nurskie/1_43_0_4")

        assert isinstance(provider, FilesystemProvider)
        assert provider.path == (tmp_path / "nas" / "Nurskie" / "1_43_0_4").resolve()

    def test_leading_slash_treated_as_absolute(self, tmp_path: Path):
        resolver = SourceResolver(source_root=str(tmp_path))
        provider = resolver.resolve("/nas/Nurskie/1_43_0_4")

        # Leading slash means absolute path — resolver treats as-is
        assert isinstance(provider, FilesystemProvider)
        assert "/nas/Nurskie/1_43_0_4" in str(provider.path)

    def test_absolute_unix_path(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("/mnt/nas_genealogy/Sources/test")

        assert isinstance(provider, FilesystemProvider)
        assert "/mnt/nas_genealogy/Sources/test" in str(provider.path)

    def test_home_path(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("~/Genealogy/Sources/test")

        assert isinstance(provider, FilesystemProvider)
        # Should expand ~ to home dir
        assert "~" not in str(provider.path)

    def test_ssh_notation_passthrough(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("user@host:~/path/to/scans")

        assert isinstance(provider, FilesystemProvider)
        # SSH paths are treated as absolute (legacy compat)

    def test_windows_drive_path(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("C:\\Users\\test\\scans")

        assert isinstance(provider, FilesystemProvider)

    def test_unc_path(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("\\\\server\\share\\scans")

        assert isinstance(provider, FilesystemProvider)

    def test_url_scheme(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("url:https://example.com/scans/img001.jpg")

        assert isinstance(provider, UrlProvider)
        assert provider.canonical_id == "url:https://example.com/scans/img001.jpg"

    def test_gdrive_api_scheme(self):
        resolver = SourceResolver(source_root="/data/sources")
        provider = resolver.resolve("gdrive-api:folder_abc123")

        assert isinstance(provider, GDriveApiProvider)
        assert provider.canonical_id == "gdrive-api:folder_abc123"

    def test_unknown_scheme_as_filesystem(self, tmp_path: Path):
        resolver = SourceResolver(source_root=str(tmp_path))
        provider = resolver.resolve("custom:some/path")

        assert isinstance(provider, FilesystemProvider)
        # Unknown scheme becomes subdir: source_root/custom/some/path
        assert "custom" in str(provider.path)

    def test_empty_path_raises(self):
        resolver = SourceResolver(source_root="/data/sources")
        with pytest.raises(ValueError, match="cannot be empty"):
            resolver.resolve("")

    def test_whitespace_only_raises(self):
        resolver = SourceResolver(source_root="/data/sources")
        with pytest.raises(ValueError, match="cannot be empty"):
            resolver.resolve("   ")

    def test_source_root_from_env(self, tmp_path: Path):
        with patch.dict(os.environ, {"OCR_SOURCE_ROOT": str(tmp_path)}):
            resolver = SourceResolver()
            provider = resolver.resolve("nas/test")
            assert str(provider.path).startswith(str(tmp_path))

    def test_source_root_default(self):
        with patch.dict(os.environ, {}, clear=True):
            resolver = SourceResolver()
            assert str(resolver.source_root) == DEFAULT_SOURCE_ROOT


class TestSourceResolverVerify:
    def test_verify_accessible(self, tmp_path: Path):
        (tmp_path / "test.jpg").touch()

        resolver = SourceResolver(source_root=str(tmp_path))
        result = resolver.verify(".")

        assert result["accessible"] is True
        assert result["provider"] == "FilesystemProvider"
        assert result["file_count"] == 1

    def test_verify_not_accessible(self, tmp_path: Path):
        resolver = SourceResolver(source_root=str(tmp_path))
        result = resolver.verify("nonexistent_folder")

        assert result["accessible"] is False
        assert result["error"] is not None

    def test_verify_empty_path(self):
        resolver = SourceResolver(source_root="/data/sources")
        result = resolver.verify("")

        assert result["accessible"] is False
        assert "empty" in result["error"].lower()


class TestResolveToLocalPath:
    def test_filesystem_returns_path(self, tmp_path: Path):
        resolver = SourceResolver(source_root=str(tmp_path))
        path = resolver.resolve_to_local_path("nas/test")
        assert isinstance(path, Path)

    def test_url_raises(self):
        resolver = SourceResolver(source_root="/data/sources")
        with pytest.raises(ValueError, match="not a filesystem"):
            resolver.resolve_to_local_path("url:https://example.com")


# ─── Module-level convenience functions ───


class TestModuleFunctions:
    def setup_method(self):
        reset_resolver()

    def teardown_method(self):
        reset_resolver()

    def test_get_resolver_singleton(self):
        r1 = get_resolver()
        r2 = get_resolver()
        assert r1 is r2

    def test_reset_resolver(self):
        r1 = get_resolver()
        reset_resolver()
        r2 = get_resolver()
        assert r1 is not r2

    def test_resolve_source(self, tmp_path: Path):
        reset_resolver()
        with patch.dict(os.environ, {"OCR_SOURCE_ROOT": str(tmp_path)}):
            reset_resolver()
            provider = resolve_source("nas/test")
            assert isinstance(provider, FilesystemProvider)

    def test_resolve_source_path(self, tmp_path: Path):
        reset_resolver()
        with patch.dict(os.environ, {"OCR_SOURCE_ROOT": str(tmp_path)}):
            reset_resolver()
            path = resolve_source_path("nas/test")
            assert isinstance(path, Path)


# ─── Backward compatibility: _compose_source_path ───


class TestComposeSourcePathCompat:
    """Verify that the updated _compose_source_path still works for existing callers."""

    def setup_method(self):
        reset_resolver()

    def teardown_method(self):
        reset_resolver()

    def test_empty_source_path_returns_none(self):
        from app.services.process import _compose_source_path

        assert _compose_source_path("", None) is None
        assert _compose_source_path(None, None) is None

    def test_empty_source_path_with_root(self):
        from app.services.process import _compose_source_path

        result = _compose_source_path("", "/some/root")
        assert result == "/some/root"

    def test_absolute_path_passthrough(self):
        from app.services.process import _compose_source_path

        result = _compose_source_path("/mnt/nas/test", None)
        # Should resolve through FilesystemProvider → canonical_id
        assert "test" in result

    def test_relative_path_resolves(self, tmp_path: Path):
        from app.services.process import _compose_source_path

        with patch.dict(os.environ, {"OCR_SOURCE_ROOT": str(tmp_path)}):
            reset_resolver()
            result = _compose_source_path("nas/test", None)
            # Result should contain the path components
            assert "nas" in result
            assert "test" in result
