"""
Tests for app.services.remote_config module.

Tests remote configuration storage and retrieval.
"""

import json
from unittest.mock import patch

import pytest

from app.services.remote_config import (
    _coerce_value,
    _parse_env_var,
    get_effective_remote_config,
    load_remote_config,
    save_remote_config,
)


class TestCoerceValue:
    """Test _coerce_value function."""

    def test_returns_none_for_none(self):
        """Should return None for None input."""
        assert _coerce_value(None) is None

    def test_preserves_bool(self):
        """Should preserve boolean values."""
        assert _coerce_value(True) is True
        assert _coerce_value(False) is False

    def test_preserves_list(self):
        """Should preserve list values."""
        test_list = [1, 2, 3]
        assert _coerce_value(test_list) == test_list

    def test_preserves_dict(self):
        """Should preserve dict values."""
        test_dict = {"key": "value"}
        assert _coerce_value(test_dict) == test_dict

    def test_returns_none_for_empty_string(self):
        """Should return None for empty string."""
        assert _coerce_value("") is None
        assert _coerce_value("   ") is None

    def test_returns_none_for_none_string(self):
        """Should return None for 'none' string."""
        assert _coerce_value("none") is None
        assert _coerce_value("None") is None
        assert _coerce_value("NONE") is None

    def test_returns_stripped_string(self):
        """Should return stripped string for other values."""
        assert _coerce_value("  test  ") == "test"
        assert _coerce_value(123) == "123"


class TestParseEnvVar:
    """Test _parse_env_var function."""

    def test_parses_bool_keys_true(self):
        """Should parse boolean keys correctly for true values."""
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "1") is True
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "true") is True
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "yes") is True
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "on") is True

    def test_parses_bool_keys_false(self):
        """Should parse boolean keys correctly for false values."""
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "0") is False
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "false") is False
        assert _parse_env_var("OCR_REMOTE_RUN_ENABLED", "no") is False

    def test_parses_hosts_list_json(self):
        """Should parse OCR_REMOTE_HOSTS_LIST as JSON."""
        hosts = json.dumps([{"host": "test1"}, {"host": "test2"}])
        result = _parse_env_var("OCR_REMOTE_HOSTS_LIST", hosts)
        assert result == [{"host": "test1"}, {"host": "test2"}]

    def test_returns_empty_list_for_invalid_json(self):
        """Should return empty list for invalid JSON in hosts list."""
        result = _parse_env_var("OCR_REMOTE_HOSTS_LIST", "invalid json")
        assert result == []

    def test_returns_none_for_none_string(self):
        """Should return None for 'none' string in regular keys."""
        assert _parse_env_var("OCR_REMOTE_HOST", "none") is None
        assert _parse_env_var("OCR_REMOTE_HOST", "None") is None

    def test_returns_string_for_regular_keys(self):
        """Should return string for regular keys."""
        assert _parse_env_var("OCR_REMOTE_HOST", "example.com") == "example.com"


class TestLoadRemoteConfig:
    """Test load_remote_config function."""

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_loads_valid_config(self, mock_file):
        """Should load and parse valid configuration."""
        mock_file.exists.return_value = True
        config = {"OCR_REMOTE_HOST": "test.com", "OCR_REMOTE_USER": "user"}
        mock_file.read_text.return_value = json.dumps(config)

        result = load_remote_config()

        assert result == config

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_returns_empty_dict_if_file_not_exists(self, mock_file):
        """Should return empty dict if file doesn't exist."""
        mock_file.exists.return_value = False

        result = load_remote_config()

        assert result == {}

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_returns_empty_dict_on_json_error(self, mock_file):
        """Should return empty dict on JSON parse error."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "invalid json"

        result = load_remote_config()

        assert result == {}

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_coerces_values(self, mock_file):
        """Should coerce values using _coerce_value."""
        mock_file.exists.return_value = True
        config = {"OCR_REMOTE_HOST": "  test.com  ", "OCR_REMOTE_USER": "none"}
        mock_file.read_text.return_value = json.dumps(config)

        result = load_remote_config()

        assert result["OCR_REMOTE_HOST"] == "test.com"
        assert result["OCR_REMOTE_USER"] is None


class TestSaveRemoteConfig:
    """Test save_remote_config function."""

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_saves_and_returns_clean_config(self, mock_file):
        """Should save configuration and return cleaned version."""
        payload = {
            "OCR_REMOTE_HOST": "test.com",
            "OCR_REMOTE_USER": "user",
            "UNKNOWN_KEY": "should_be_filtered",
        }

        result = save_remote_config(payload)

        assert "OCR_REMOTE_HOST" in result
        assert "OCR_REMOTE_USER" in result
        assert "UNKNOWN_KEY" not in result
        mock_file.write_text.assert_called_once()

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_creates_parent_directory(self, mock_file):
        """Should create parent directory if it doesn't exist."""
        payload = {"OCR_REMOTE_HOST": "test.com"}

        save_remote_config(payload)

        mock_file.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE")
    def test_coerces_values_before_saving(self, mock_file):
        """Should coerce values before saving."""
        payload = {"OCR_REMOTE_HOST": "  test.com  ", "OCR_REMOTE_USER": "none"}

        result = save_remote_config(payload)

        assert result["OCR_REMOTE_HOST"] == "test.com"
        assert result["OCR_REMOTE_USER"] is None


class TestGetEffectiveRemoteConfig:
    """Test get_effective_remote_config function."""

    @patch("app.services.remote_config.load_remote_config")
    @patch.dict("os.environ", {}, clear=True)
    def test_returns_stored_config_when_no_env(self, mock_load):
        """Should return stored config when no environment variables."""
        mock_load.return_value = {"OCR_REMOTE_HOST": "stored.com"}

        result = get_effective_remote_config()

        assert result["OCR_REMOTE_HOST"] == "stored.com"

    @patch("app.services.remote_config.load_remote_config")
    @patch.dict("os.environ", {"OCR_REMOTE_HOST": "env.com"})
    def test_falls_back_to_env_when_not_stored(self, mock_load):
        """Should fall back to environment when value not in stored config."""
        mock_load.return_value = {}

        result = get_effective_remote_config()

        assert result["OCR_REMOTE_HOST"] == "env.com"

    @patch("app.services.remote_config.load_remote_config")
    @patch.dict("os.environ", {"OCR_REMOTE_HOST": "env.com"})
    def test_prefers_stored_over_env(self, mock_load):
        """Should prefer stored config over environment."""
        mock_load.return_value = {"OCR_REMOTE_HOST": "stored.com"}

        result = get_effective_remote_config()

        assert result["OCR_REMOTE_HOST"] == "stored.com"

    @patch("app.services.remote_config.load_remote_config")
    @patch.dict("os.environ", {"OCR_REMOTE_RUN_ENABLED": "true"})
    def test_parses_env_bool_values(self, mock_load):
        """Should parse boolean environment variables."""
        mock_load.return_value = {}

        result = get_effective_remote_config()

        assert result["OCR_REMOTE_RUN_ENABLED"] is True
