"""
Additional tests for app.utils.security module.

Tests for profile_name, worker_id, wsl_distro, path, and ssh_opts validation.
"""


import pytest

from app.utils.security import (
    validate_path,
    validate_profile_name,
    validate_worker_id,
    validate_wsl_distro,
)


class TestValidateProfileName:
    """Test validate_profile_name function."""

    def test_accepts_valid_profile_names(self):
        """Should accept valid profile names."""
        assert validate_profile_name("profile1") == "profile1"
        assert validate_profile_name("my-profile") == "my-profile"
        assert validate_profile_name("my_profile") == "my_profile"
        assert validate_profile_name("profile.test") == "profile.test"

    def test_rejects_empty_profile_name(self):
        """Should reject empty profile name."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_profile_name("")

    def test_rejects_path_traversal(self):
        """Should reject path traversal attempts."""
        # The regex check fails first, so we get "Invalid profile name" error
        with pytest.raises(ValueError, match="Invalid profile name"):
            validate_profile_name("../etc/passwd")

        # These have ".." which is caught by the explicit check
        with pytest.raises(ValueError, match="path traversal"):
            validate_profile_name("profile..test")

        # Forward slash is caught by regex first
        with pytest.raises(ValueError, match="Invalid profile name"):
            validate_profile_name("profile/subdir")

        # Backslash is also caught by regex first
        with pytest.raises(ValueError, match="Invalid profile name"):
            validate_profile_name("profile\\subdir")

    def test_rejects_too_long_name(self):
        """Should reject profile names longer than 64 characters."""
        long_name = "a" * 65
        with pytest.raises(ValueError, match="too long"):
            validate_profile_name(long_name)

    def test_rejects_invalid_characters(self):
        """Should reject invalid characters."""
        with pytest.raises(ValueError, match="Invalid profile name"):
            validate_profile_name("profile@test")

        with pytest.raises(ValueError, match="Invalid profile name"):
            validate_profile_name("profile name")


class TestValidateWorkerId:
    """Test validate_worker_id function."""

    def test_accepts_numeric_worker_id(self):
        """Should accept numeric worker IDs."""
        assert validate_worker_id("1") == "1"
        assert validate_worker_id("123") == "123"

    def test_accepts_w_prefix_worker_id(self):
        """Should accept w-prefixed worker IDs."""
        assert validate_worker_id("w1") == "w1"
        assert validate_worker_id("w123") == "w123"

    def test_accepts_allowed_keywords(self):
        """Should accept allowed keywords."""
        assert validate_worker_id("limit") == "limit"
        assert validate_worker_id("pause") == "pause"
        assert validate_worker_id("session") == "session"
        assert validate_worker_id("login") == "login"
        assert validate_worker_id("expired") == "expired"

    def test_case_insensitive_keywords(self):
        """Should accept keywords case-insensitively."""
        assert validate_worker_id("LIMIT") == "limit"
        assert validate_worker_id("Pause") == "pause"

    def test_rejects_empty_worker_id(self):
        """Should reject empty worker ID."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_worker_id("")

    def test_rejects_invalid_format(self):
        """Should reject invalid worker ID formats."""
        with pytest.raises(ValueError, match="Invalid worker ID"):
            validate_worker_id("invalid-id")

        with pytest.raises(ValueError, match="Invalid worker ID"):
            validate_worker_id("w")

        with pytest.raises(ValueError, match="Invalid worker ID"):
            validate_worker_id("worker123")


class TestValidateWslDistro:
    """Test validate_wsl_distro function."""

    def test_accepts_valid_distro_names(self):
        """Should accept valid WSL distro names."""
        assert validate_wsl_distro("Ubuntu") == "Ubuntu"
        assert validate_wsl_distro("Ubuntu-20.04") == "Ubuntu-20.04"
        assert validate_wsl_distro("Debian_11") == "Debian_11"

    def test_returns_empty_for_none(self):
        """Should return empty string for None."""
        assert validate_wsl_distro(None) == ""

    def test_returns_empty_for_empty_string(self):
        """Should return empty string for empty input."""
        assert validate_wsl_distro("") == ""

    def test_rejects_invalid_characters(self):
        """Should reject invalid characters."""
        with pytest.raises(ValueError, match="Invalid WSL distro"):
            validate_wsl_distro("Ubuntu 20.04")

        with pytest.raises(ValueError, match="Invalid WSL distro"):
            validate_wsl_distro("Ubuntu;rm -rf /")

    def test_rejects_too_long_name(self):
        """Should reject distro names longer than 64 characters."""
        long_name = "a" * 65
        with pytest.raises(ValueError, match="too long"):
            validate_wsl_distro(long_name)


class TestValidatePath:
    """Test validate_path function."""

    def test_accepts_valid_path(self):
        """Should accept valid paths."""
        result = validate_path("/home/user/file.txt")
        assert result == "/home/user/file.txt"

    def test_rejects_empty_path(self):
        """Should reject empty path."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_path("")

    def test_rejects_null_bytes(self):
        """Should reject paths with null bytes."""
        with pytest.raises(ValueError, match="null bytes"):
            validate_path("/home/user\x00/file.txt")

    def test_validates_path_within_base_dir(self, tmp_path):
        """Should validate path is within base directory."""
        base = tmp_path / "base"
        base.mkdir()

        # Valid path within base
        result = validate_path("subdir/file.txt", str(base))
        assert result.startswith(str(base))

    def test_rejects_path_traversal_with_base_dir(self, tmp_path):
        """Should reject path traversal attempts when base_dir is set."""
        base = tmp_path / "base"
        base.mkdir()

        with pytest.raises(ValueError, match="Path traversal"):
            validate_path("../etc/passwd", str(base))

    def test_strips_whitespace(self):
        """Should strip whitespace from path."""
        result = validate_path("  /home/user/file.txt  ")
        assert result == "/home/user/file.txt"
