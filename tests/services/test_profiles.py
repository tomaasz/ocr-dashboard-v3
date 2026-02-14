"""
Tests for app.services.profiles module.

Tests profile management business logic.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.profiles import (
    create_profile,
    delete_profile,
    get_profile_dir,
    get_profile_session_start,
    list_all_profiles,
    list_profiles,
    profile_exists,
    reset_profile_state,
    set_default_profile_hidden,
    set_profile_session_start,
)


@pytest.fixture
def mock_cache_dir(tmp_path, monkeypatch):
    """Mock CACHE_DIR to use temporary directory."""
    from app.services import profiles

    monkeypatch.setattr(profiles, "CACHE_DIR", tmp_path)
    return tmp_path


class TestGetProfileDir:
    """Test get_profile_dir function."""

    def test_returns_default_profile_dir(self, mock_cache_dir):
        """Should return gemini-profile for default profile."""
        result = get_profile_dir("default")
        assert result == mock_cache_dir / "gemini-profile"

    def test_returns_named_profile_dir(self, mock_cache_dir):
        """Should return gemini-profile-{name} for named profiles."""
        result = get_profile_dir("test-profile")
        assert result == mock_cache_dir / "gemini-profile-test-profile"

    def test_sanitizes_path_traversal(self, mock_cache_dir):
        """Should sanitize path traversal attempts."""
        result = get_profile_dir("../../../etc/passwd")
        # os.path.basename should strip path components
        assert ".." not in str(result)


class TestProfileExists:
    """Test profile_exists function."""

    def test_returns_true_for_existing_profile(self, mock_cache_dir):
        """Should return True if profile directory exists."""
        profile_dir = mock_cache_dir / "gemini-profile-test"
        profile_dir.mkdir(parents=True)

        assert profile_exists("test") is True

    def test_returns_false_for_nonexistent_profile(self, mock_cache_dir):
        """Should return False if profile directory doesn't exist."""
        assert profile_exists("nonexistent") is False


class TestListProfiles:
    """Test list_profiles function."""

    def test_lists_named_profiles(self, mock_cache_dir):
        """Should list all named profiles."""
        (mock_cache_dir / "gemini-profile-test1").mkdir()
        (mock_cache_dir / "gemini-profile-test2").mkdir()

        profiles = list_profiles()

        assert "test1" in profiles
        assert "test2" in profiles
        assert profiles == sorted(profiles)  # Should be sorted

    def test_excludes_default_when_not_requested(self, mock_cache_dir):
        """Should exclude default profile when include_default=False."""
        (mock_cache_dir / "gemini-profile").mkdir()
        (mock_cache_dir / "gemini-profile-test").mkdir()

        profiles = list_profiles(include_default=False)

        assert "default" not in profiles
        assert "test" in profiles

    def test_includes_default_when_requested(self, mock_cache_dir):
        """Should include default profile when include_default=True."""
        (mock_cache_dir / "gemini-profile").mkdir()
        (mock_cache_dir / "gemini-profile-test").mkdir()

        profiles = list_profiles(include_default=True)

        assert "default" in profiles
        assert "test" in profiles

    def test_excludes_hidden_default(self, mock_cache_dir):
        """Should exclude default profile when hidden marker exists."""
        (mock_cache_dir / "gemini-profile").mkdir()
        (mock_cache_dir / ".hide_default_profile").write_text("1")

        profiles = list_profiles(include_default=True)

        assert "default" not in profiles

    def test_ignores_non_profile_directories(self, mock_cache_dir):
        """Should ignore directories that don't match profile pattern."""
        (mock_cache_dir / "gemini-profile-test").mkdir()
        (mock_cache_dir / "other-directory").mkdir()
        (mock_cache_dir / "file.txt").write_text("test")

        profiles = list_profiles()

        assert "test" in profiles
        assert len(profiles) == 1


class TestListAllProfiles:
    """Test list_all_profiles function."""

    def test_includes_default_even_if_hidden(self, mock_cache_dir):
        """Should include default profile even when hidden."""
        (mock_cache_dir / "gemini-profile").mkdir()
        (mock_cache_dir / ".hide_default_profile").write_text("1")
        (mock_cache_dir / "gemini-profile-test").mkdir()

        profiles = list_all_profiles()

        assert "default" in profiles
        assert "test" in profiles


class TestSetDefaultProfileHidden:
    """Test set_default_profile_hidden function."""

    def test_hides_default_profile(self, mock_cache_dir):
        """Should create hidden marker file."""
        success, message = set_default_profile_hidden(True)

        assert success is True
        assert "Ukryto" in message
        assert (mock_cache_dir / ".hide_default_profile").exists()

    def test_shows_default_profile(self, mock_cache_dir):
        """Should remove hidden marker file."""
        marker = mock_cache_dir / ".hide_default_profile"
        marker.write_text("1")

        success, message = set_default_profile_hidden(False)

        assert success is True
        assert "Przywrócono" in message
        assert not marker.exists()

    def test_shows_when_not_hidden(self, mock_cache_dir):
        """Should succeed even if marker doesn't exist."""
        success, message = set_default_profile_hidden(False)

        assert success is True


class TestCreateProfile:
    """Test create_profile function."""

    def test_creates_new_profile(self, mock_cache_dir):
        """Should create new profile directory."""
        success, message = create_profile("new-profile")

        assert success is True
        assert "Utworzono" in message
        assert (mock_cache_dir / "gemini-profile-new-profile").is_dir()

    def test_fails_if_profile_exists(self, mock_cache_dir):
        """Should fail if profile already exists."""
        (mock_cache_dir / "gemini-profile-existing").mkdir()

        success, message = create_profile("existing")

        assert success is False
        assert "już istnieje" in message


class TestDeleteProfile:
    """Test delete_profile function."""

    def test_deletes_existing_profile(self, mock_cache_dir):
        """Should delete existing profile directory."""
        profile_dir = mock_cache_dir / "gemini-profile-test"
        profile_dir.mkdir()
        (profile_dir / "data.txt").write_text("test")

        success, message = delete_profile("test")

        assert success is True
        assert "Usunięto" in message
        assert not profile_dir.exists()

    def test_fails_for_default_profile(self, mock_cache_dir):
        """Should not allow deleting default profile."""
        (mock_cache_dir / "gemini-profile").mkdir()

        success, message = delete_profile("default")

        assert success is False
        assert "domyślnego profilu" in message

    def test_fails_for_nonexistent_profile(self, mock_cache_dir):
        """Should fail if profile doesn't exist."""
        success, message = delete_profile("nonexistent")

        assert success is False
        assert "nie istnieje" in message

    def test_security_check_prevents_traversal(self, mock_cache_dir):
        """Should prevent deleting directories outside cache."""
        # This test verifies the security check in delete_profile
        # The function should reject attempts to delete outside CACHE_DIR
        success, message = delete_profile("../../../etc")

        # Should either fail due to path resolution or security check
        assert success is False


class TestSessionManagement:
    """Test session start time management."""

    def test_set_and_get_session_start(self, mock_cache_dir):
        """Should persist and retrieve session start time."""
        profile_dir = mock_cache_dir / "gemini-profile-test"
        profile_dir.mkdir()

        now = datetime.now(UTC)
        set_profile_session_start("test", now)

        retrieved = get_profile_session_start("test")

        assert retrieved is not None
        assert abs((retrieved - now).total_seconds()) < 2  # Within 2 seconds

    def test_get_session_start_nonexistent(self, mock_cache_dir):
        """Should return None if no session marker exists."""
        result = get_profile_session_start("nonexistent")
        assert result is None

    def test_set_session_start_creates_directory(self, mock_cache_dir):
        """Should create profile directory if it doesn't exist."""
        set_profile_session_start("new-profile")

        profile_dir = mock_cache_dir / "gemini-profile-new-profile"
        assert profile_dir.exists()
        assert (profile_dir / ".session_start").exists()


class TestResetProfileState:
    """Test reset_profile_state function."""

    @patch("app.services.profiles.pg_cursor")
    def test_clears_database_state(self, mock_pg_cursor, mock_cache_dir):
        """Should execute database cleanup queries."""
        mock_cursor = MagicMock()
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        reset_profile_state("test-profile")

        # Should execute multiple DELETE/UPDATE queries
        assert mock_cursor.execute.call_count >= 3

    @patch("app.services.profiles.pg_cursor")
    def test_handles_database_unavailable(self, mock_pg_cursor, mock_cache_dir):
        """Should handle database being unavailable."""
        mock_pg_cursor.return_value.__enter__.return_value = None

        # Should not raise exception
        reset_profile_state("test-profile")

    @patch("app.services.profiles.pg_cursor")
    def test_handles_table_not_exists(self, mock_pg_cursor, mock_cache_dir):
        """Should handle tables not existing."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Table does not exist")
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        # Should not raise exception
        reset_profile_state("test-profile")
