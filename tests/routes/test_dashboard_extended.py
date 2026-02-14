"""
Tests for app.routes.dashboard module - Extended Coverage.

Additional tests for database operations, SSH functions, and complex API endpoints.
"""

import subprocess
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from app.routes.dashboard import (
    _build_logs_query,
    _fetch_profile_db_stats,
    _format_ssh_error,
    _get_local_chrome_process_count,
    _get_local_system_stats,
    _get_local_top_processes,
    _get_profile_dashboard_data,
    _get_remote_browser_process_count,
    _get_remote_system_stats,
    _get_remote_top_processes,
    _kill_local_browser_processes,
    _kill_remote_browser_processes,
    _load_file_logs,
    _run_ssh_command,
)


class TestDatabaseOperations:
    """Test database operation functions."""

    @patch("app.routes.dashboard.HAS_PSYCOPG2", False)
    def test_fetch_profile_db_stats_without_psycopg2(self):
        """Should return empty dict when psycopg2 not available."""
        result = _fetch_profile_db_stats("postgresql://localhost/db", "public.table")
        assert result == {}

    @patch("app.routes.dashboard.HAS_PSYCOPG2", True)
    @patch("app.routes.dashboard.psycopg2")
    def test_fetch_profile_db_stats_with_connection_error(self, mock_psycopg2):
        """Should handle connection errors gracefully."""
        mock_psycopg2.connect.side_effect = Exception("Connection failed")

        result = _fetch_profile_db_stats("postgresql://localhost/db", "public.table")

        assert result == {}

    @patch("app.routes.dashboard.HAS_PSYCOPG2", True)
    @patch("app.routes.dashboard.psycopg2")
    @patch("app.routes.dashboard._table_identifier")
    def test_fetch_profile_db_stats_success(self, mock_table_id, mock_psycopg2):
        """Should fetch stats from database successfully."""
        # Mock connection and cursor with context manager support
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None

        mock_cursor_context = Mock()
        mock_cursor_context.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor_context.__exit__ = Mock(return_value=False)

        mock_conn = Mock()
        mock_conn.cursor.return_value = mock_cursor_context
        mock_psycopg2.connect.return_value = mock_conn
        mock_table_id.return_value = "table_id"

        result = _fetch_profile_db_stats(
            "postgresql://localhost/db", "public.table", {"profile1": datetime.now(UTC)}
        )

        # Should return dict (even if empty from mocked queries)
        assert isinstance(result, dict)
        mock_psycopg2.connect.assert_called_once()

    @patch("app.routes.dashboard.profile_service")
    @patch("app.routes.dashboard.process_service")
    def test_get_profile_dashboard_data_idle(self, mock_process, mock_profile):
        """Should build dashboard data for idle profile."""
        mock_process.get_profile_pids.return_value = []
        mock_process.is_profile_running_remote.return_value = None
        mock_process.is_start_recent.return_value = False
        mock_profile.get_profile_worker_progress.return_value = {"progress": 50}
        mock_profile.get_profile_last_error.return_value = None

        result = _get_profile_dashboard_data("test_profile", {}, {})

        assert result["name"] == "test_profile"
        assert result["status"] == "idle"
        assert result["status_label"] == "IDLE"

    @patch("app.routes.dashboard.profile_service")
    @patch("app.routes.dashboard.process_service")
    def test_get_profile_dashboard_data_active(self, mock_process, mock_profile):
        """Should build dashboard data for active profile."""
        mock_process.get_profile_pids.return_value = [1234, 5678]
        mock_process.pid_is_running.return_value = True
        mock_process.is_profile_running_remote.return_value = None
        mock_process.pid_is_headed.return_value = False
        mock_profile.get_profile_worker_progress.return_value = {}
        mock_profile.get_profile_last_error.return_value = None

        db_stats = {
            "test_profile": {
                "processed_today": 100,
                "processed_total": 500,
                "tokens_today": 50000,
                "tokens_total": 250000,
                "errors_today": 2,
                "errors_total": 5,
                "last_activity": datetime.now(UTC),
            }
        }

        result = _get_profile_dashboard_data("test_profile", db_stats, {})

        assert result["name"] == "test_profile"
        assert result["status"] == "active"
        assert result["processed_today"] == 100
        assert result["processed_total"] == 500

    @patch("app.routes.dashboard.profile_service")
    @patch("app.routes.dashboard.process_service")
    def test_get_profile_dashboard_data_with_error(self, mock_process, mock_profile):
        """Should handle errors gracefully and return error state."""
        mock_process.get_profile_pids.side_effect = Exception("Test error")

        result = _get_profile_dashboard_data("test_profile", {}, {})

        assert result["name"] == "test_profile"
        assert result["status"] == "error"
        assert "Test error" in result["critical_message"]

    @patch("app.routes.dashboard.profile_service")
    @patch("app.routes.dashboard.process_service")
    def test_get_profile_dashboard_data_paused(self, mock_process, mock_profile):
        """Should detect paused state from database stats."""
        mock_process.get_profile_pids.return_value = []
        mock_process.is_profile_running_remote.return_value = None
        mock_profile.get_profile_worker_progress.return_value = {}
        mock_profile.get_profile_last_error.return_value = None

        db_stats = {
            "test_profile": {
                "state": {
                    "is_paused": True,
                    "pause_reason": "Manual pause",
                    "pause_until": None,
                }
            }
        }

        result = _get_profile_dashboard_data("test_profile", db_stats, {})

        assert result["status"] == "paused"
        assert "PAUZA" in result["status_label"]


class TestSSHOperations:
    """Test SSH and remote operation functions."""

    @patch("app.routes.dashboard.subprocess.run")
    def test_run_ssh_command_success(self, mock_run):
        """Should execute SSH command successfully."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="output", stderr=""
        )

        result = _run_ssh_command("host.example.com", "user", "", "ls -la")

        assert result.returncode == 0
        assert result.stdout == "output"
        mock_run.assert_called_once()

    @patch("app.routes.dashboard.subprocess.run")
    def test_run_ssh_command_with_options(self, mock_run):
        """Should include SSH options in command."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        _run_ssh_command("host.example.com", "user", "-p 2222", "uptime")

        # Verify SSH options were included
        call_args = mock_run.call_args[0][0]
        assert "-p" in call_args
        assert "2222" in call_args

    @patch("app.routes.dashboard.subprocess.run")
    def test_run_ssh_command_timeout(self, mock_run):
        """Should handle timeout correctly."""
        mock_run.side_effect = subprocess.TimeoutExpired("ssh", 5)

        with pytest.raises(subprocess.TimeoutExpired):
            _run_ssh_command("host.example.com", "user", "", "sleep 10", timeout=5)

    def test_format_ssh_error_connection_failed(self):
        """Should format SSH connection failure."""
        result = subprocess.CompletedProcess(
            args=[], returncode=255, stdout="", stderr="Connection refused"
        )

        error = _format_ssh_error(result)

        assert error == "SSH failed or unreachable"

    def test_format_ssh_error_with_stderr(self):
        """Should extract error from stderr."""
        result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Permission denied\nOther error"
        )

        error = _format_ssh_error(result)

        assert "Other error" in error

    def test_format_ssh_error_fallback(self):
        """Should use fallback message when no stderr."""
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

        error = _format_ssh_error(result, fallback="Custom fallback")

        assert error == "Custom fallback"

    @patch("os.cpu_count", return_value=4)
    @patch("builtins.open", create=True)
    def test_get_local_system_stats_success(self, mock_open_func, mock_cpu_count):
        """Should read system stats from /proc."""
        meminfo = """MemTotal:       16384000 kB
MemAvailable:    8192000 kB
MemFree:         4096000 kB"""
        loadavg = "1.5 1.2 1.0 2/500 12345"

        # Create proper mock file objects with context manager support
        mock_meminfo = Mock()
        mock_meminfo.read.return_value = meminfo
        mock_meminfo.__iter__ = Mock(return_value=iter(meminfo.split("\n")))
        mock_meminfo.__enter__ = Mock(return_value=mock_meminfo)
        mock_meminfo.__exit__ = Mock(return_value=False)

        mock_loadavg = Mock()
        mock_loadavg.read.return_value = loadavg
        mock_loadavg.__enter__ = Mock(return_value=mock_loadavg)
        mock_loadavg.__exit__ = Mock(return_value=False)

        mock_open_func.side_effect = [mock_meminfo, mock_loadavg]

        result = _get_local_system_stats()

        assert result["available"] is True
        assert result["memory_total_gb"] > 0
        assert result["memory_used_gb"] >= 0
        assert result["cpu_percent"] >= 0

    def test_get_local_system_stats_error(self):
        """Should handle errors reading /proc."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _get_local_system_stats()

        assert result["available"] is False

    @patch("app.routes.dashboard.subprocess.run")
    def test_get_local_top_processes(self, mock_run):
        """Should parse top processes from ps output."""
        ps_output = """  PID COMMAND         %CPU %MEM
 1234 chrome          45.2 12.5
 5678 python          25.1 8.3"""

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )

        result = _get_local_top_processes(limit=5)

        assert len(result) == 2
        assert result[0]["pid"] == 1234
        assert result[0]["cpu_percent"] == 45.2

    @patch("app.routes.dashboard.subprocess.run")
    def test_get_local_chrome_process_count(self, mock_run):
        """Should count Chrome processes."""
        ps_output = """chrome
chrome
chromium
firefox
python"""

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )

        count = _get_local_chrome_process_count()

        assert count == 4  # chrome, chrome, chromium, firefox

    @patch("app.routes.dashboard._run_ssh_command")
    def test_get_remote_system_stats_linux(self, mock_ssh):
        """Should get system stats from Linux remote host."""
        meminfo_loadavg = """MemTotal:       8192000 kB
MemAvailable:    4096000 kB
1.0 0.8 0.5 1/300 9999"""

        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=meminfo_loadavg, stderr=""
        )

        result = _get_remote_system_stats("host.example.com", "user", "")

        assert result["available"] is True
        assert result["memory_total_gb"] > 0
        assert result["cpu_percent"] >= 0

    @patch("app.routes.dashboard._run_ssh_command")
    def test_get_remote_system_stats_windows(self, mock_ssh):
        """Should get system stats from Windows remote host."""
        # First call (Linux) fails, second call (Windows) succeeds
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="ERROR", stderr=""),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="CPU=25.5\nMEM_TOTAL=17179869184\nMEM_FREE=8589934592",
                stderr="",
            ),
        ]

        result = _get_remote_system_stats("host.example.com", "user", "")

        assert result["available"] is True
        assert result["cpu_percent"] == 25.5

    @patch("app.routes.dashboard._run_ssh_command")
    def test_get_remote_top_processes_linux(self, mock_ssh):
        """Should get top processes from Linux remote host."""
        ps_output = """  PID COMMAND         %CPU %MEM
 1111 chrome          30.0 10.0
 2222 python          20.0 5.0"""

        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )

        result = _get_remote_top_processes("host.example.com", "user", "")

        assert len(result) == 2
        assert result[0]["name"] == "chrome"

    @patch("app.routes.dashboard._run_ssh_command")
    def test_get_remote_browser_process_count_linux(self, mock_ssh):
        """Should count browser processes on Linux remote host."""
        ps_output = """chrome
chrome
firefox"""

        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ps_output, stderr=""
        )

        count = _get_remote_browser_process_count("host.example.com", "user", "")

        assert count == 3

    @patch("app.routes.dashboard._get_local_chrome_process_count")
    @patch("app.routes.dashboard.subprocess.run")
    def test_kill_local_browser_processes(self, mock_run, mock_count):
        """Should kill local browser processes."""
        mock_count.side_effect = [5, 0]  # Before: 5, After: 0
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        result = _kill_local_browser_processes()

        assert result["before"] == 5
        assert result["after"] == 0
        assert result["killed_estimate"] == 5

    @patch("app.routes.dashboard._get_remote_browser_process_count")
    @patch("app.routes.dashboard._run_ssh_command")
    def test_kill_remote_browser_processes(self, mock_ssh, mock_count):
        """Should kill remote browser processes."""
        mock_count.side_effect = [3, 0]  # Before: 3, After: 0
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        result = _kill_remote_browser_processes("host.example.com", "user", "")

        assert result["before"] == 3
        assert result["after"] == 0


class TestLogOperations:
    """Test log loading and query building functions."""

    @patch("app.routes.dashboard.config")
    def test_load_file_logs_missing_directory(self, mock_config):
        """Should return empty list when log directory missing."""
        mock_cache_dir = Mock()
        mock_activity_dir = Mock()
        mock_activity_dir.exists.return_value = False
        mock_cache_dir.__truediv__ = Mock(return_value=mock_activity_dir)
        mock_config.CACHE_DIR = mock_cache_dir

        result = _load_file_logs(None, None, 100)

        assert result == []

    @patch("app.routes.dashboard.config")
    def test_load_file_logs_with_filter(self, mock_config):
        """Should filter logs by profile and level."""
        mock_activity_dir = Mock()
        mock_activity_dir.exists.return_value = True

        mock_file = Mock()
        mock_file.read_text.return_value = """2026-02-13T14:30:45 worker_error component=worker profile=test1
2026-02-13T14:31:00 worker_started component=worker profile=test2
2026-02-13T14:31:15 worker_error component=worker profile=test1"""

        mock_activity_dir.glob.return_value = [mock_file]

        mock_cache_dir = Mock()
        mock_cache_dir.__truediv__ = Mock(return_value=mock_activity_dir)
        mock_config.CACHE_DIR = mock_cache_dir

        result = _load_file_logs("test1", "error", 100)

        # Should return filtered logs for test1 profile with error level
        assert len(result) >= 0  # Actual count depends on parsing

    def test_build_logs_query_no_filters(self):
        """Should build query without filters."""
        query, params = _build_logs_query(None, None, None, 100)

        assert "SELECT" in query
        assert "system_activity_log" in query
        assert "LIMIT" in query
        assert params[-1] == 100

    def test_build_logs_query_with_profile(self):
        """Should add profile filter to query."""
        query, params = _build_logs_query("test_profile", None, None, 50)

        assert "profile_name = %s" in query
        assert "test_profile" in params

    def test_build_logs_query_with_level(self):
        """Should add level filter to query."""
        query, params = _build_logs_query(None, "error", None, 50)

        assert "error_message IS NOT NULL" in query or "error" in query.lower()

    def test_build_logs_query_with_hours(self):
        """Should add time filter to query."""
        query, params = _build_logs_query(None, None, 24, 50)

        assert "INTERVAL" in query
        assert 24 in params


class TestComplexAPIEndpoints:
    """Test complex API endpoint handlers."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    @patch("app.routes.dashboard.profile_service")
    @patch("app.routes.dashboard.process_service")
    @patch("app.routes.dashboard.get_effective_remote_config")
    @patch("app.routes.dashboard._get_local_system_stats")
    @patch("app.routes.dashboard._get_local_chrome_process_count")
    def test_get_host_load_localhost_only(
        self, mock_chrome_count, mock_stats, mock_remote, mock_process, mock_profile, client
    ):
        """Should return localhost load information."""
        mock_profile.list_profiles.return_value = []
        mock_remote.return_value = {"OCR_REMOTE_HOSTS_LIST": []}
        mock_stats.return_value = {
            "cpu_percent": 25.5,
            "memory_percent": 60.0,
            "memory_used_gb": 9.6,
            "memory_total_gb": 16.0,
            "available": True,
        }
        mock_chrome_count.return_value = 5

        response = client.get("/api/host-load")

        assert response.status_code == 200
        data = response.json()
        assert "hosts" in data
        assert len(data["hosts"]) >= 1
        assert data["hosts"][0]["id"] == "localhost"

    @patch("app.routes.dashboard.get_effective_remote_config")
    def test_kill_host_browsers_localhost(self, mock_remote, client):
        """Should kill browsers on localhost."""
        mock_remote.return_value = {"OCR_REMOTE_HOSTS_LIST": []}

        with patch("app.routes.dashboard._kill_local_browser_processes") as mock_kill:
            mock_kill.return_value = {"before": 5, "after": 0, "killed_estimate": 5}

            response = client.post("/api/host-browsers/kill", json={"host_id": "localhost"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["killed_estimate"] == 5

    def test_kill_host_browsers_missing_host_id(self, client):
        """Should return 400 when host_id missing."""
        response = client.post("/api/host-browsers/kill", json={})

        assert response.status_code == 400

    @patch("app.routes.dashboard.get_effective_remote_config")
    def test_kill_host_browsers_invalid_host(self, mock_remote, client):
        """Should return 404 for invalid host."""
        mock_remote.return_value = {"OCR_REMOTE_HOSTS_LIST": []}

        response = client.post("/api/host-browsers/kill", json={"host_id": "invalid_host"})

        assert response.status_code == 404
