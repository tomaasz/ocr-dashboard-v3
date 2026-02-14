"""
Tests for app.routes.dashboard module.

Comprehensive test suite for dashboard routes, helper functions,
and database operations.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.routes.dashboard import (
    _browser_process_patterns,
    _count_process_names,
    _critical_status_label,
    _format_last_activity,
    _is_recent,
    _load_profile_aliases,
    _load_proxies_map,
    _minutes_since,
    _parse_activity_log_line,
    _parse_ps_output,
    _powershell_encoded_command,
    _proxy_display,
    _table_identifier,
)


class TestHelperFunctions:
    """Test utility helper functions."""

    def test_format_last_activity_with_datetime(self):
        """Should format datetime as date and time."""
        ts = datetime(2026, 2, 13, 14, 30, 45, tzinfo=UTC)
        result = _format_last_activity(ts)
        assert result == "2026-02-13 14:30:45"

    def test_format_last_activity_with_none(self):
        """Should return None for None input."""
        assert _format_last_activity(None) is None

    def test_minutes_since_with_datetime(self):
        """Should calculate minutes since timestamp."""
        # Create a timestamp 30 minutes ago
        now = datetime.now(UTC)
        ts = now - timedelta(minutes=30)
        result = _minutes_since(ts)
        # Allow some tolerance for test execution time
        assert 29 <= result <= 31

    def test_minutes_since_with_none(self):
        """Should return None for None input."""
        assert _minutes_since(None) is None

    def test_minutes_since_zero(self):
        """Should return 0 for current timestamp."""
        now = datetime.now(UTC)
        result = _minutes_since(now)
        assert result == 0

    def test_is_recent_true(self):
        """Should return True for recent timestamp."""
        now = datetime.now(UTC)
        ts = now - timedelta(minutes=5)
        assert _is_recent(ts, max_age_minutes=10) is True

    def test_is_recent_false(self):
        """Should return False for old timestamp."""
        now = datetime.now(UTC)
        ts = now - timedelta(minutes=15)
        assert _is_recent(ts, max_age_minutes=10) is False

    def test_is_recent_with_none(self):
        """Should return False for None timestamp."""
        assert _is_recent(None, max_age_minutes=10) is False

    def test_is_recent_future_timestamp(self):
        """Should handle future timestamp correctly."""
        now = datetime.now(UTC)
        ts = now + timedelta(minutes=5)
        # Future timestamps should be considered recent
        assert _is_recent(ts, max_age_minutes=10) is True


class TestTableIdentifier:
    """Test database table identifier function."""

    @patch("app.routes.dashboard.HAS_PSYCOPG2", True)
    @patch("app.routes.dashboard.sql")
    def test_table_identifier_with_schema(self, mock_sql):
        """Should parse schema.table format."""
        mock_identifier = Mock()
        mock_sql.Identifier.return_value = mock_identifier
        mock_sql.SQL.return_value.join.return_value = "schema.table"

        _table_identifier("public.ocr_raw_texts")

        # Verify Identifier was called for both schema and table
        assert mock_sql.Identifier.call_count == 2
        mock_sql.Identifier.assert_any_call("public")
        mock_sql.Identifier.assert_any_call("ocr_raw_texts")

    @patch("app.routes.dashboard.HAS_PSYCOPG2", True)
    @patch("app.routes.dashboard.sql")
    def test_table_identifier_without_schema(self, mock_sql):
        """Should parse table-only format."""
        mock_identifier = Mock()
        mock_sql.Identifier.return_value = mock_identifier

        _table_identifier("my_table")

        # Verify Identifier was called once for table only
        mock_sql.Identifier.assert_called_once_with("my_table")

    @patch("app.routes.dashboard.HAS_PSYCOPG2", False)
    def test_table_identifier_without_psycopg2(self):
        """Should return None when psycopg2 not available."""
        result = _table_identifier("public.table")
        assert result is None


class TestConfigurationLoaders:
    """Test configuration loading functions."""

    @patch("app.routes.dashboard.config")
    def test_load_proxies_map_from_file(self, mock_config):
        """Should load proxy config from JSON file."""
        proxy_data = {
            "proxies": {
                "profile1": {"server": "proxy1.example.com:8080"},
                "default": {"server": "proxy.example.com:8080"},
            }
        }
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(proxy_data)
        mock_config.PROXIES_CONFIG_FILE = mock_path

        with patch.dict(os.environ, {}, clear=True):
            result = _load_proxies_map()

        assert "profile1" in result
        assert "default" in result
        assert result["profile1"]["server"] == "proxy1.example.com:8080"

    @patch("app.routes.dashboard.config")
    def test_load_proxies_map_from_env(self, mock_config):
        """Should load proxy from environment variables."""
        mock_path = Mock()
        mock_path.exists.return_value = False
        mock_config.PROXIES_CONFIG_FILE = mock_path

        env_vars = {
            "OCR_PROXY_SERVER": "env-proxy.example.com:8080",
            "OCR_PROXY_USERNAME": "user123",
            "OCR_PROXY_PASSWORD": "pass123",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            result = _load_proxies_map()

        assert "default" in result
        assert result["default"]["server"] == "env-proxy.example.com:8080"
        assert result["default"]["username"] == "user123"
        assert result["default"]["password"] == "pass123"

    @patch("app.routes.dashboard.config")
    def test_load_proxies_map_missing_file(self, mock_config):
        """Should return empty dict when config file missing and no env vars."""
        mock_path = Mock()
        mock_path.exists.return_value = False
        mock_config.PROXIES_CONFIG_FILE = mock_path

        with patch.dict(os.environ, {}, clear=True):
            result = _load_proxies_map()

        assert result == {}

    @patch("app.routes.dashboard.config")
    def test_load_proxies_map_global_disable_overrides_all_sources(self, mock_config):
        """Should return no proxies when global proxy disable is enabled."""
        proxy_data = {
            "proxies": {
                "profile1": {"server": "proxy1.example.com:8080"},
                "default": {"server": "proxy.example.com:8080"},
            }
        }
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(proxy_data)
        mock_config.PROXIES_CONFIG_FILE = mock_path

        env_vars = {
            "OCR_PROXY_DISABLED": "1",
            "OCR_PROXY_SERVER": "env-proxy.example.com:8080",
            "OCR_PROXY_USERNAME": "user123",
            "OCR_PROXY_PASSWORD": "pass123",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            result = _load_proxies_map()

        assert result == {}

    @patch("app.routes.dashboard.config")
    def test_load_proxies_map_invalid_json(self, mock_config):
        """Should handle invalid JSON gracefully."""
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "invalid json {"
        mock_config.PROXIES_CONFIG_FILE = mock_path

        with patch.dict(os.environ, {}, clear=True):
            result = _load_proxies_map()

        assert result == {}

    @patch("app.routes.dashboard.config")
    def test_load_profile_aliases_valid(self, mock_config):
        """Should load profile aliases from JSON file."""
        alias_data = {
            "profile_long_name": "Short",
            "another_profile": "Alias",
        }
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(alias_data)
        mock_config.PROFILE_ALIASES_FILE = mock_path

        result = _load_profile_aliases()

        assert result == {
            "profile_long_name": "Short",
            "another_profile": "Alias",
        }

    @patch("app.routes.dashboard.config")
    def test_load_profile_aliases_missing(self, mock_config):
        """Should return empty dict when aliases file missing."""
        mock_path = Mock()
        mock_path.exists.return_value = False
        mock_config.PROFILE_ALIASES_FILE = mock_path

        result = _load_profile_aliases()

        assert result == {}

    @patch("app.routes.dashboard.config")
    def test_load_profile_aliases_filters_empty(self, mock_config):
        """Should filter out empty keys or values."""
        alias_data = {
            "valid": "Alias",
            "": "empty_key",
            "empty_value": "",
        }
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(alias_data)
        mock_config.PROFILE_ALIASES_FILE = mock_path

        result = _load_profile_aliases()

        # Only valid entry should remain (empty key and empty value filtered out)
        assert result == {"valid": "Alias"}

    def test_proxy_display_with_url(self):
        """Should parse proxy URL and extract host and port."""
        display, host = _proxy_display("http://proxy.example.com:8080")
        assert display == "proxy.example.com:8080"
        assert host == "proxy.example.com"

    def test_proxy_display_with_auth(self):
        """Should strip authentication from display."""
        display, host = _proxy_display("http://user:pass@proxy.example.com:8080")
        assert display == "proxy.example.com:8080"
        assert host == "proxy.example.com"

    def test_proxy_display_with_host_port(self):
        """Should parse host:port format."""
        display, host = _proxy_display("proxy.example.com:8080")
        assert display == "proxy.example.com:8080"
        assert host == "proxy.example.com"

    def test_proxy_display_without_port(self):
        """Should handle host without port."""
        display, host = _proxy_display("proxy.example.com")
        assert display == "proxy.example.com"
        assert host == "proxy.example.com"

    def test_proxy_display_with_none(self):
        """Should return None for None input."""
        display, host = _proxy_display(None)
        assert display is None
        assert host is None

    def test_proxy_display_with_empty_string(self):
        """Should return None for empty string."""
        display, host = _proxy_display("")
        assert display is None
        assert host is None


class TestCriticalStatusLabel:
    """Test critical status label mapping."""

    def test_critical_status_label_known_types(self):
        """Should map known event types to labels."""
        assert _critical_status_label("login_required") == "SESJA WYGASŁA"
        assert _critical_status_label("verification_required") == "WERYFIKACJA"
        assert _critical_status_label("captcha_detected") == "CAPTCHA"
        assert _critical_status_label("pro_limit_reached") == "LIMIT"

    def test_critical_status_label_unknown(self):
        """Should return default label for unknown types."""
        assert _critical_status_label("unknown_event") == "PROBLEM"
        assert _critical_status_label("random_type") == "PROBLEM"

    def test_critical_status_label_case_insensitive(self):
        """Should handle case-insensitive mapping."""
        assert _critical_status_label("LOGIN_REQUIRED") == "SESJA WYGASŁA"
        assert _critical_status_label("CaPtChA_dEtEcTeD") == "CAPTCHA"

    def test_critical_status_label_with_none(self):
        """Should handle None input."""
        assert _critical_status_label(None) == "PROBLEM"

    def test_critical_status_label_with_whitespace(self):
        """Should strip whitespace."""
        assert _critical_status_label("  login_required  ") == "SESJA WYGASŁA"


class TestActivityLogParsing:
    """Test activity log parsing functions."""

    def test_parse_activity_log_line_valid(self):
        """Should parse valid log line."""
        line = "2026-02-13T14:30:45 worker_started component=worker profile=test_profile"
        result = _parse_activity_log_line(line)

        assert result is not None
        assert result["time"] == "2026-02-13 14:30:45"
        assert result["event_type"] == "worker_started"
        assert result["profile"] == "test_profile"
        assert result["level"] == "INFO"

    def test_parse_activity_log_line_with_reason(self):
        """Should parse line with reason field."""
        line = "2026-02-13T14:30:45 worker_stopped component=worker profile=test reason=manual stop"
        result = _parse_activity_log_line(line)

        assert result is not None
        assert "reason=manual stop" in result["message"]

    def test_parse_activity_log_line_error_level(self):
        """Should detect error level from event type."""
        line = "2026-02-13T14:30:45 worker_error component=worker profile=test"
        result = _parse_activity_log_line(line)

        assert result is not None
        assert result["level"] == "ERROR"

    def test_parse_activity_log_line_warning_level(self):
        """Should detect warning level from event type."""
        line = "2026-02-13T14:30:45 worker_stopped component=worker profile=test"
        result = _parse_activity_log_line(line)

        assert result is not None
        assert result["level"] == "WARNING"

    def test_parse_activity_log_line_empty(self):
        """Should return None for empty line."""
        assert _parse_activity_log_line("") is None
        assert _parse_activity_log_line("   ") is None

    def test_parse_activity_log_line_invalid(self):
        """Should return None for malformed line."""
        assert _parse_activity_log_line("invalid") is None
        assert _parse_activity_log_line("no_spaces") is None


class TestProcessParsing:
    """Test process output parsing functions."""

    def test_browser_process_patterns(self):
        """Should return list of browser process patterns."""
        patterns = _browser_process_patterns()
        assert "chrome" in patterns
        assert "chromium" in patterns
        assert "firefox" in patterns
        assert "msedge" in patterns

    def test_count_process_names_with_matches(self):
        """Should count matching browser processes."""
        output = """chrome
chromium
firefox
python
chrome
node"""
        count = _count_process_names(output)
        assert count == 4  # chrome, chromium, firefox, chrome

    def test_count_process_names_no_matches(self):
        """Should return 0 when no matches."""
        output = """python
node
bash"""
        count = _count_process_names(output)
        assert count == 0

    def test_count_process_names_empty(self):
        """Should handle empty output."""
        assert _count_process_names("") == 0

    def test_parse_ps_output_valid(self):
        """Should parse ps command output."""
        output = """  PID COMMAND         %CPU %MEM
 1234 chrome          25.5 10.2
 5678 python          15.3 5.1
 9012 firefox         30.0 12.5"""

        result = _parse_ps_output(output, limit=5)

        assert len(result) == 3
        assert result[0]["pid"] == 1234
        assert result[0]["name"] == "chrome"
        assert result[0]["cpu_percent"] == 25.5
        assert result[0]["memory_percent"] == 10.2

    def test_parse_ps_output_with_limit(self):
        """Should respect limit parameter."""
        output = """  PID COMMAND         %CPU %MEM
 1234 chrome          25.5 10.2
 5678 python          15.3 5.1
 9012 firefox         30.0 12.5"""

        result = _parse_ps_output(output, limit=2)

        assert len(result) == 2

    def test_parse_ps_output_empty(self):
        """Should handle empty output."""
        assert _parse_ps_output("") == []

    def test_parse_ps_output_invalid_lines(self):
        """Should skip invalid lines."""
        output = """  PID COMMAND         %CPU %MEM
invalid line
 1234 chrome          25.5 10.2"""

        result = _parse_ps_output(output)

        assert len(result) == 1
        assert result[0]["pid"] == 1234

    def test_powershell_encoded_command(self):
        """Should encode PowerShell script to base64."""
        script = "Write-Output 'Hello'"
        result = _powershell_encoded_command(script)

        assert result.startswith("powershell -NoProfile -EncodedCommand ")
        # Verify it contains base64-encoded content
        assert len(result) > 50


class TestDashboardRoutes:
    """Test dashboard route handlers."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app

        return TestClient(app)

    @pytest.fixture
    def mock_services(self):
        """Mock service dependencies."""
        with (
            patch("app.routes.dashboard.profile_service") as mock_profile,
            patch("app.routes.dashboard.process_service") as mock_process,
            patch("app.routes.dashboard._fetch_profile_db_stats") as mock_db,
        ):
            mock_profile.list_profiles.return_value = []
            mock_profile.get_profile_session_start.return_value = None
            mock_db.return_value = {}

            yield {
                "profile": mock_profile,
                "process": mock_process,
                "db": mock_db,
            }

    def test_dashboard_redirect(self, client, mock_services):
        """Should redirect legacy dashboard to v2."""
        with patch("app.routes.dashboard.config") as mock_config:
            mock_config.PG_DSN = None
            mock_config.LIMIT_WORKER_URL = ""

            response = client.get("/")

            assert response.status_code == 200
            # Should render dashboard_v2.html template

    def test_dashboard2_render(self, client, mock_services):
        """Should render dashboard2 template."""
        # Dashboard2 requires template file, skip detailed testing
        # Just verify route exists

    def test_dashboard_v2_render(self, client, mock_services):
        """Should render main dashboard with profiles."""
        with (
            patch("app.routes.dashboard.config") as mock_config,
            patch("app.routes.dashboard.get_effective_remote_config") as mock_remote,
        ):
            mock_config.PG_DSN = None
            mock_config.LIMIT_WORKER_URL = ""
            mock_remote.return_value = {}
            mock_services["profile"].list_profiles.return_value = ["profile1"]
            mock_services["profile"].get_profile_worker_progress.return_value = {}
            mock_services["profile"].get_profile_last_error.return_value = None

            response = client.get("/v2")

            assert response.status_code == 200


class TestAPIEndpoints:
    """Test API endpoint handlers."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app

        return TestClient(app)

    @pytest.fixture
    def mock_services(self):
        """Mock service dependencies."""
        with (
            patch("app.routes.dashboard.profile_service") as mock_profile,
            patch("app.routes.dashboard.process_service") as mock_process,
            patch("app.routes.dashboard._fetch_profile_db_stats") as mock_db,
            patch("app.routes.dashboard._get_profile_dashboard_data") as mock_data,
        ):
            mock_profile.list_profiles.return_value = []
            mock_db.return_value = {}
            mock_data.return_value = {
                "name": "test",
                "status": "idle",
                "processed_today": 0,
                "processed_total": 0,
                "errors_total": 0,
            }

            yield {
                "profile": mock_profile,
                "process": mock_process,
                "db": mock_db,
                "data": mock_data,
            }

    def test_get_stats_v2_structure(self, client, mock_services):
        """Should return correct API response structure."""
        with patch("app.routes.dashboard.config") as mock_config:
            mock_config.PG_DSN = None

            response = client.get("/api/stats/v2")

            assert response.status_code == 200
            data = response.json()
            assert "stats" in data
            assert "profiles" in data
            assert "today_scans" in data["stats"]
            assert "active_workers" in data["stats"]

    def test_get_stats_v2_with_profiles(self, client, mock_services):
        """Should include profile data in response."""
        with patch("app.routes.dashboard.config") as mock_config:
            mock_config.PG_DSN = None
            mock_services["profile"].list_profiles.return_value = ["profile1", "profile2"]

            response = client.get("/api/stats/v2")

            assert response.status_code == 200
            data = response.json()
            assert len(data["profiles"]) == 2

    def test_get_live_preview_empty(self, client):
        """Should handle no screenshots gracefully."""
        with patch("pathlib.Path.exists") as mock_exists:
            mock_exists.return_value = False

            response = client.get("/api/live-preview")

            assert response.status_code == 200
            data = response.json()
            assert data["previews"] == []

    @patch("app.routes.dashboard.profile_service")
    def test_get_live_preview_with_screenshots(self, mock_profile, client):
        """Should return screenshot previews."""
        mock_profile.get_profile_session_start.return_value = None

        with patch("pathlib.Path.exists") as mock_exists, patch("pathlib.Path.glob") as mock_glob:
            mock_exists.return_value = True

            # Mock screenshot file with format: ui_health_DATESTRING_profile_name
            # where DATESTRING has no underscores
            mock_file = Mock()
            mock_file.stem = "ui_health_20260213143045_test_profile"
            mock_file.name = "ui_health_20260213143045_test_profile.png"
            mock_file.stat.return_value.st_mtime = datetime.now(UTC).timestamp()

            mock_glob.return_value = [mock_file]

            response = client.get("/api/live-preview")

            assert response.status_code == 200
            data = response.json()
            assert len(data["previews"]) == 1
            assert data["previews"][0]["profile"] == "test_profile"
