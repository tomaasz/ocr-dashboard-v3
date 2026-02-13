"""
Tests for app.services.update_counts module.

Tests update counts functionality and configuration management.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.update_counts import (
    _coerce_bool,
    _load_seen_paths,
    _load_update_counts_config,
    _read_last_run,
    _save_seen_paths,
    _should_run,
    _write_last_run,
)


class TestCoerceBool:
    """Test _coerce_bool function."""

    def test_returns_true_for_bool_true(self):
        """Should return True for boolean True."""
        assert _coerce_bool(True) is True

    def test_returns_false_for_bool_false(self):
        """Should return False for boolean False."""
        assert _coerce_bool(False) is False

    def test_returns_false_for_none(self):
        """Should return False for None."""
        assert _coerce_bool(None) is False

    def test_returns_true_for_string_true(self):
        """Should return True for string 'true'."""
        assert _coerce_bool("true") is True
        assert _coerce_bool("TRUE") is True
        assert _coerce_bool("True") is True

    def test_returns_true_for_string_yes(self):
        """Should return True for string 'yes'."""
        assert _coerce_bool("yes") is True
        assert _coerce_bool("YES") is True
        assert _coerce_bool("y") is True

    def test_returns_true_for_string_one(self):
        """Should return True for string '1'."""
        assert _coerce_bool("1") is True

    def test_returns_true_for_string_on(self):
        """Should return True for string 'on'."""
        assert _coerce_bool("on") is True
        assert _coerce_bool("ON") is True

    def test_returns_false_for_other_strings(self):
        """Should return False for other strings."""
        assert _coerce_bool("false") is False
        assert _coerce_bool("no") is False
        assert _coerce_bool("0") is False
        assert _coerce_bool("random") is False


class TestReadWriteLastRun:
    """Test timestamp persistence functions."""

    @patch("app.services.update_counts.UPDATE_COUNTS_TS_FILE")
    def test_read_last_run_returns_timestamp(self, mock_ts_file):
        """Should read and parse timestamp from file."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "1234567890.0\n"
        mock_ts_file.exists.return_value = True
        mock_ts_file.read_text.return_value = "1234567890.0\n"

        result = _read_last_run()

        assert result == 1234567890.0

    @patch("app.services.update_counts.UPDATE_COUNTS_TS_FILE")
    def test_read_last_run_returns_none_if_not_exists(self, mock_ts_file):
        """Should return None if file doesn't exist."""
        mock_ts_file.exists.return_value = False

        result = _read_last_run()

        assert result is None

    @patch("app.services.update_counts.UPDATE_COUNTS_TS_FILE")
    def test_read_last_run_handles_invalid_content(self, mock_ts_file):
        """Should return None if content is invalid."""
        mock_ts_file.exists.return_value = True
        mock_ts_file.read_text.return_value = "invalid"

        result = _read_last_run()

        assert result is None

    @patch("app.services.update_counts.UPDATE_COUNTS_TS_FILE")
    def test_write_last_run_persists_timestamp(self, mock_ts_file):
        """Should write timestamp to file."""
        _write_last_run(1234567890.5)

        mock_ts_file.write_text.assert_called_once()
        call_args = mock_ts_file.write_text.call_args[0][0]
        assert "1234567890" in call_args


class TestShouldRun:
    """Test _should_run function."""

    @patch("app.services.update_counts.UPDATE_COUNTS_ON_START", True)
    @patch("app.services.update_counts._read_last_run")
    def test_returns_true_if_never_run(self, mock_read):
        """Should return True if never run before."""
        mock_read.return_value = None

        assert _should_run(1000.0) is True

    @patch("app.services.update_counts.UPDATE_COUNTS_ON_START", True)
    @patch("app.services.update_counts.UPDATE_COUNTS_MIN_INTERVAL_SEC", 60)
    @patch("app.services.update_counts._read_last_run")
    def test_returns_true_if_interval_passed(self, mock_read):
        """Should return True if minimum interval has passed."""
        mock_read.return_value = 1000.0

        assert _should_run(1100.0) is True  # 100 seconds > 60

    @patch("app.services.update_counts.UPDATE_COUNTS_ON_START", True)
    @patch("app.services.update_counts.UPDATE_COUNTS_MIN_INTERVAL_SEC", 60)
    @patch("app.services.update_counts._read_last_run")
    def test_returns_false_if_interval_not_passed(self, mock_read):
        """Should return False if minimum interval hasn't passed."""
        mock_read.return_value = 1000.0

        assert _should_run(1030.0) is False  # 30 seconds < 60

    @patch("app.services.update_counts.UPDATE_COUNTS_ON_START", False)
    def test_returns_false_if_disabled(self):
        """Should return False if UPDATE_COUNTS_ON_START is False."""
        assert _should_run(1000.0) is False


class TestLoadSaveSeenPaths:
    """Test seen paths persistence functions."""

    @patch("app.services.update_counts.UPDATE_COUNTS_SEEN_PATHS_FILE")
    def test_load_seen_paths_returns_set(self, mock_file):
        """Should load and parse seen paths from file."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "path1\npath2\npath3\n"

        result = _load_seen_paths()

        assert result == {"path1", "path2", "path3"}

    @patch("app.services.update_counts.UPDATE_COUNTS_SEEN_PATHS_FILE")
    def test_load_seen_paths_returns_empty_if_not_exists(self, mock_file):
        """Should return empty set if file doesn't exist."""
        mock_file.exists.return_value = False

        result = _load_seen_paths()

        assert result == set()

    @patch("app.services.update_counts.UPDATE_COUNTS_SEEN_PATHS_FILE")
    def test_load_seen_paths_filters_empty_lines(self, mock_file):
        """Should filter out empty lines."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "path1\n\npath2\n  \npath3"

        result = _load_seen_paths()

        assert result == {"path1", "path2", "path3"}

    @patch("app.services.update_counts.UPDATE_COUNTS_SEEN_PATHS_FILE")
    def test_save_seen_paths_persists_sorted(self, mock_file):
        """Should save paths in sorted order."""
        paths = {"path3", "path1", "path2"}

        _save_seen_paths(paths)

        mock_file.write_text.assert_called_once()
        call_args = mock_file.write_text.call_args[0][0]
        assert "path1\npath2\npath3\n" == call_args


class TestLoadUpdateCountsConfig:
    """Test configuration loading."""

    @patch("app.services.update_counts.UPDATE_COUNTS_CONFIG_FILE")
    def test_loads_valid_json_config(self, mock_file):
        """Should load and parse valid JSON configuration."""
        mock_file.exists.return_value = True
        config = {"OCR_UPDATE_COUNTS_ON_NEW_PATHS": True, "OCR_UPDATE_COUNTS_POLL_SEC": 30}
        mock_file.read_text.return_value = json.dumps(config)

        result = _load_update_counts_config()

        assert result == config

    @patch("app.services.update_counts.UPDATE_COUNTS_CONFIG_FILE")
    def test_returns_empty_dict_if_not_exists(self, mock_file):
        """Should return empty dict if file doesn't exist."""
        mock_file.exists.return_value = False

        result = _load_update_counts_config()

        assert result == {}

    @patch("app.services.update_counts.UPDATE_COUNTS_CONFIG_FILE")
    def test_returns_empty_dict_if_invalid_json(self, mock_file):
        """Should return empty dict if JSON is invalid."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "invalid json"

        result = _load_update_counts_config()

        assert result == {}

    @patch("app.services.update_counts.UPDATE_COUNTS_CONFIG_FILE")
    def test_returns_empty_dict_if_not_dict(self, mock_file):
        """Should return empty dict if JSON is not a dictionary."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = json.dumps(["list", "not", "dict"])

        result = _load_update_counts_config()

        assert result == {}
