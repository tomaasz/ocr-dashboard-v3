"""
Tests for app.routes.limits module.

Tests limit checking routes and helper functions.
"""

import pytest

from app.routes.limits import (
    _coerce_profiles,
    _is_limited,
    _parse_parallel,
    _sanitize_host_id,
    _sanitize_profiles,
)


class TestCoerceProfiles:
    """Test _coerce_profiles function."""

    def test_returns_empty_list_for_none(self):
        """Should return empty list for None."""
        assert _coerce_profiles(None) == []

    def test_returns_list_for_string(self):
        """Should wrap string in list."""
        assert _coerce_profiles("profile1") == ["profile1"]

    def test_returns_list_for_list(self):
        """Should convert list items to strings."""
        assert _coerce_profiles(["profile1", "profile2"]) == ["profile1", "profile2"]

    def test_filters_empty_strings(self):
        """Should filter out empty strings."""
        assert _coerce_profiles(["profile1", "", "  ", "profile2"]) == ["profile1", "profile2"]

    def test_converts_non_string_items(self):
        """Should convert non-string items to strings."""
        assert _coerce_profiles([123, "profile"]) == ["123", "profile"]

    def test_returns_empty_for_other_types(self):
        """Should return empty list for other types."""
        assert _coerce_profiles(123) == []
        assert _coerce_profiles({}) == []


class TestParseParallel:
    """Test _parse_parallel function."""

    def test_returns_none_for_none(self):
        """Should return None for None input."""
        assert _parse_parallel(None) is None

    def test_parses_integer(self):
        """Should parse integer value."""
        assert _parse_parallel(5) == 5

    def test_parses_string_integer(self):
        """Should parse string integer."""
        assert _parse_parallel("10") == 10

    def test_raises_for_invalid_string(self):
        """Should raise HTTPException for invalid string."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _parse_parallel("invalid")

        assert exc_info.value.status_code == 400
        assert "parallel" in exc_info.value.detail.lower()

    def test_raises_for_invalid_type(self):
        """Should raise HTTPException for invalid type."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _parse_parallel([1, 2, 3])

        assert exc_info.value.status_code == 400


class TestSanitizeHostId:
    """Test _sanitize_host_id function."""

    def test_accepts_valid_host_id(self):
        """Should accept valid host ID."""
        assert _sanitize_host_id("host-123") == "host-123"
        assert _sanitize_host_id("host_123") == "host_123"
        assert _sanitize_host_id("host.123") == "host.123"

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        assert _sanitize_host_id("  host-123  ") == "host-123"

    def test_rejects_invalid_characters(self):
        """Should reject host IDs with invalid characters."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_host_id("host@123")

        assert exc_info.value.status_code == 400
        assert "hosta" in exc_info.value.detail.lower()

    def test_rejects_special_characters(self):
        """Should reject special characters."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _sanitize_host_id("host;rm -rf /")


class TestSanitizeProfiles:
    """Test _sanitize_profiles function."""

    def test_accepts_valid_profile_names(self):
        """Should accept valid profile names."""
        profiles = ["profile1", "profile-2", "profile_3", "Profile (Test)"]
        result = _sanitize_profiles(profiles)
        assert result == profiles

    def test_rejects_invalid_profile_name(self):
        """Should reject profile names with invalid characters."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_profiles(["valid", "invalid@profile"])

        assert exc_info.value.status_code == 400
        assert "profilu" in exc_info.value.detail.lower()

    def test_returns_empty_for_empty_list(self):
        """Should return empty list for empty input."""
        assert _sanitize_profiles([]) == []


class TestIsLimited:
    """Test _is_limited function."""

    def test_returns_true_when_paused_with_limit_reason(self):
        """Should return True when paused due to limit."""
        state = {"is_paused": True, "pause_reason": "Pro limit reached"}
        critical = {}

        assert _is_limited(state, critical) is True

    def test_returns_true_for_critical_limit_event(self):
        """Should return True for critical pro_limit_reached event."""
        state = {}
        critical = {"event_type": "pro_limit_reached"}

        assert _is_limited(state, critical) is True

    def test_returns_false_when_paused_without_limit(self):
        """Should return False when paused for other reasons."""
        state = {"is_paused": True, "pause_reason": "Manual pause"}
        critical = {}

        assert _is_limited(state, critical) is False

    def test_returns_false_when_not_paused(self):
        """Should return False when not paused."""
        state = {"is_paused": False}
        critical = {}

        assert _is_limited(state, critical) is False

    def test_handles_missing_fields(self):
        """Should handle missing fields gracefully."""
        assert _is_limited({}, {}) is False

    def test_case_insensitive_pause_reason(self):
        """Should check pause reason case-insensitively."""
        state = {"is_paused": True, "pause_reason": "LIMIT REACHED"}
        critical = {}

        assert _is_limited(state, critical) is True

    def test_case_insensitive_event_type(self):
        """Should check event type case-insensitively."""
        state = {}
        critical = {"event_type": "PRO_LIMIT_REACHED"}

        assert _is_limited(state, critical) is True
