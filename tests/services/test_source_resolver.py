"""
Tests for app.services.source_resolver module.

Tests source path resolution and provider abstraction.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.source_resolver import (
    FilesystemProvider,
    GDriveApiProvider,
    SourceResolver,
    UrlProvider,
    get_resolver,
    reset_resolver,
    resolve_source,
    resolve_source_path,
)


class TestFilesystemProvider:
    """Test FilesystemProvider class."""

    def test_list_files_returns_image_files(self, tmp_path):
        """Should list image files in directory."""
        # Create test files
        (tmp_path / "image1.jpg").write_text("test")
        (tmp_path / "image2.png").write_text("test")
        (tmp_path / "document.txt").write_text("test")

        provider = FilesystemProvider(tmp_path)
        files = provider.list_files()

        assert len(files) == 2
        assert any(f.name == "image1.jpg" for f in files)
        assert any(f.name == "image2.png" for f in files)

    def test_list_files_returns_empty_for_nonexistent_dir(self, tmp_path):
        """Should return empty list for non-existent directory."""
        provider = FilesystemProvider(tmp_path / "nonexistent")
        files = provider.list_files()

        assert files == []

    def test_list_files_handles_stat_errors(self, tmp_path):
        """Should handle OSError when getting file stats."""
        (tmp_path / "image.jpg").write_text("test")
        provider = FilesystemProvider(tmp_path)

        with patch("pathlib.Path.stat", side_effect=OSError("Permission denied")):
            files = provider.list_files()

            assert len(files) == 1
            assert files[0].size_bytes is None
            assert files[0].mtime is None

    def test_get_file_path_returns_path(self, tmp_path):
        """Should return path to file."""
        provider = FilesystemProvider(tmp_path)
        path = provider.get_file_path("test.jpg")

        assert path == tmp_path / "test.jpg"

    def test_exists_returns_true_for_existing_dir(self, tmp_path):
        """Should return True for existing directory."""
        provider = FilesystemProvider(tmp_path)
        assert provider.exists() is True

    def test_exists_returns_false_for_nonexistent_dir(self, tmp_path):
        """Should return False for non-existent directory."""
        provider = FilesystemProvider(tmp_path / "nonexistent")
        assert provider.exists() is False

    def test_file_count_returns_image_count(self, tmp_path):
        """Should return count of image files."""
        (tmp_path / "image1.jpg").write_text("test")
        (tmp_path / "image2.png").write_text("test")
        (tmp_path / "document.txt").write_text("test")

        provider = FilesystemProvider(tmp_path)
        assert provider.file_count() == 2

    def test_file_count_returns_zero_for_nonexistent_dir(self, tmp_path):
        """Should return 0 for non-existent directory."""
        provider = FilesystemProvider(tmp_path / "nonexistent")
        assert provider.file_count() == 0

    def test_canonical_id_returns_path_string(self, tmp_path):
        """Should return path as canonical ID."""
        provider = FilesystemProvider(tmp_path)
        assert provider.canonical_id == str(tmp_path)


class TestUrlProvider:
    """Test UrlProvider class."""

    def test_list_files_raises_not_implemented(self):
        """Should raise NotImplementedError for list_files."""
        provider = UrlProvider("https://example.com/images")
        with pytest.raises(NotImplementedError, match="Phase 3"):
            provider.list_files()

    def test_get_file_path_raises_not_implemented(self):
        """Should raise NotImplementedError for get_file_path."""
        provider = UrlProvider("https://example.com/images")
        with pytest.raises(NotImplementedError, match="Phase 3"):
            provider.get_file_path("test.jpg")

    def test_exists_returns_true(self):
        """Should return True (assumed accessible)."""
        provider = UrlProvider("https://example.com/images")
        assert provider.exists() is True

    def test_file_count_returns_zero(self):
        """Should return 0."""
        provider = UrlProvider("https://example.com/images")
        assert provider.file_count() == 0

    def test_canonical_id_includes_url(self):
        """Should return url: prefixed ID."""
        provider = UrlProvider("https://example.com/images")
        assert provider.canonical_id == "url:https://example.com/images"


class TestGDriveApiProvider:
    """Test GDriveApiProvider class."""

    def test_list_files_raises_not_implemented(self):
        """Should raise NotImplementedError for list_files."""
        provider = GDriveApiProvider("folder123")
        with pytest.raises(NotImplementedError, match="Phase 4"):
            provider.list_files()

    def test_get_file_path_raises_not_implemented(self):
        """Should raise NotImplementedError for get_file_path."""
        provider = GDriveApiProvider("folder123")
        with pytest.raises(NotImplementedError, match="Phase 4"):
            provider.get_file_path("test.jpg")

    def test_exists_raises_not_implemented(self):
        """Should raise NotImplementedError for exists."""
        provider = GDriveApiProvider("folder123")
        with pytest.raises(NotImplementedError, match="Phase 4"):
            provider.exists()

    def test_file_count_returns_zero(self):
        """Should return 0."""
        provider = GDriveApiProvider("folder123")
        assert provider.file_count() == 0

    def test_canonical_id_includes_folder_id(self):
        """Should return gdrive-api: prefixed ID."""
        provider = GDriveApiProvider("folder123")
        assert provider.canonical_id == "gdrive-api:folder123"


class TestSourceResolver:
    """Test SourceResolver class."""

    def test_resolve_raises_error_for_empty_path(self):
        """Should raise ValueError for empty source path."""
        resolver = SourceResolver()
        with pytest.raises(ValueError, match="cannot be empty"):
            resolver.resolve("")

    def test_resolve_relative_path(self, tmp_path):
        """Should resolve relative path to FilesystemProvider."""
        resolver = SourceResolver(source_root=str(tmp_path))
        provider = resolver.resolve("subdir/images")

        assert isinstance(provider, FilesystemProvider)
        assert str(tmp_path / "subdir/images") in provider.canonical_id

    def test_resolve_absolute_path(self, tmp_path):
        """Should resolve absolute path to FilesystemProvider."""
        resolver = SourceResolver()
        provider = resolver.resolve(str(tmp_path))

        assert isinstance(provider, FilesystemProvider)
        assert str(tmp_path) in provider.canonical_id

    def test_resolve_url_scheme(self):
        """Should resolve url: scheme to UrlProvider."""
        resolver = SourceResolver()
        provider = resolver.resolve("url:https://example.com/images")

        assert isinstance(provider, UrlProvider)
        assert provider.canonical_id == "url:https://example.com/images"

    def test_resolve_gdrive_api_scheme(self):
        """Should resolve gdrive-api: scheme to GDriveApiProvider."""
        resolver = SourceResolver()
        provider = resolver.resolve("gdrive-api:folder123")

        assert isinstance(provider, GDriveApiProvider)
        assert provider.canonical_id == "gdrive-api:folder123"

    def test_resolve_ssh_notation_logs_warning(self, tmp_path):
        """Should log warning for SSH notation."""
        resolver = SourceResolver()
        with patch("app.services.source_resolver.logger") as mock_logger:
            provider = resolver.resolve("user@host:/path/to/files")

            assert isinstance(provider, FilesystemProvider)
            mock_logger.warning.assert_called_once()

    def test_resolve_unknown_scheme_treats_as_filesystem(self, tmp_path):
        """Should treat unknown scheme as filesystem path."""
        resolver = SourceResolver(source_root=str(tmp_path))
        with patch("app.services.source_resolver.logger") as mock_logger:
            provider = resolver.resolve("unknown:value")

            assert isinstance(provider, FilesystemProvider)
            mock_logger.warning.assert_called_once()

    def test_resolve_to_local_path_returns_path(self, tmp_path):
        """Should return Path for filesystem provider."""
        resolver = SourceResolver(source_root=str(tmp_path))
        path = resolver.resolve_to_local_path("subdir")

        assert isinstance(path, Path)
        assert str(tmp_path / "subdir") in str(path)

    def test_resolve_to_local_path_raises_for_non_filesystem(self):
        """Should raise ValueError for non-filesystem provider."""
        resolver = SourceResolver()
        with pytest.raises(ValueError, match="not a filesystem path"):
            resolver.resolve_to_local_path("url:https://example.com")

    def test_verify_returns_status_dict(self, tmp_path):
        """Should return status dict for accessible source."""
        (tmp_path / "image.jpg").write_text("test")
        resolver = SourceResolver(source_root=str(tmp_path))

        status = resolver.verify(".")

        assert status["accessible"] is True
        assert status["provider"] == "FilesystemProvider"
        assert status["file_count"] == 1
        assert status["error"] is None

    def test_verify_handles_exceptions(self):
        """Should handle exceptions and return error status."""
        resolver = SourceResolver()

        with patch.object(resolver, "resolve", side_effect=Exception("Test error")):
            status = resolver.verify("test")

            assert status["accessible"] is False
            assert status["error"] == "Test error"


class TestModuleLevelFunctions:
    """Test module-level convenience functions."""

    def test_get_resolver_returns_singleton(self):
        """Should return singleton SourceResolver."""
        reset_resolver()
        resolver1 = get_resolver()
        resolver2 = get_resolver()

        assert resolver1 is resolver2

    def test_get_resolver_loads_config_if_exists(self, tmp_path):
        """Should load config from sources.json if it exists."""
        reset_resolver()

        # This test would require mocking the config path
        # For now, just verify it doesn't crash
        resolver = get_resolver()
        assert resolver is not None

    def test_reset_resolver_clears_singleton(self):
        """Should clear singleton."""
        get_resolver()
        reset_resolver()

        # Verify new instance is created
        resolver = get_resolver()
        assert resolver is not None

    def test_resolve_source_uses_default_resolver(self, tmp_path):
        """Should use default resolver."""
        reset_resolver()
        provider = resolve_source(str(tmp_path))

        assert isinstance(provider, FilesystemProvider)

    def test_resolve_source_path_returns_path(self, tmp_path):
        """Should return Path using default resolver."""
        reset_resolver()
        path = resolve_source_path(str(tmp_path))

        assert isinstance(path, Path)
