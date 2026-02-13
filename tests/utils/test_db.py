"""
Tests for app.utils.db module.

Tests PostgreSQL connection utilities.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.db import (
    execute_query,
    execute_single,
    execute_write,
    get_pg_connection,
    pg_cursor,
)


class TestGetPgConnection:
    """Test get_pg_connection function."""

    @patch.dict("os.environ", {"OCR_PG_DSN": ""}, clear=True)
    def test_returns_none_when_dsn_not_set(self):
        """Should return None when OCR_PG_DSN is not set."""
        result = get_pg_connection()
        assert result is None


class TestPgCursor:
    """Test pg_cursor context manager."""

    @patch("app.utils.db.get_pg_connection")
    def test_yields_none_when_no_connection(self, mock_get_conn):
        """Should yield None when connection is not available."""
        mock_get_conn.return_value = None

        with pg_cursor() as cur:
            assert cur is None

    @patch("app.utils.db.get_pg_connection")
    def test_yields_cursor_when_connected(self, mock_get_conn):
        """Should yield cursor when connection is available."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        with pg_cursor() as cur:
            assert cur == mock_cursor

    @patch("app.utils.db.get_pg_connection")
    def test_closes_connection_after_use(self, mock_get_conn):
        """Should close connection after context exits."""
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        with pg_cursor():
            pass

        mock_conn.close.assert_called_once()

    @patch("app.utils.db.get_pg_connection")
    def test_closes_connection_on_exception(self, mock_get_conn):
        """Should close connection even if exception occurs."""
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        try:
            with pg_cursor():
                raise ValueError("Test error")
        except ValueError:
            pass

        mock_conn.close.assert_called_once()


class TestExecuteQuery:
    """Test execute_query function."""

    @patch("app.utils.db.pg_cursor")
    def test_returns_empty_list_when_no_connection(self, mock_pg_cursor):
        """Should return empty list when cursor is None."""
        mock_pg_cursor.return_value.__enter__.return_value = None

        result = execute_query("SELECT * FROM test")

        assert result == []

    @patch("app.utils.db.pg_cursor")
    def test_executes_query_and_returns_results(self, mock_pg_cursor):
        """Should execute query and return all results."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "data")]
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_query("SELECT * FROM test")

        assert result == [(1, "test"), (2, "data")]
        mock_cursor.execute.assert_called_once_with("SELECT * FROM test", ())

    @patch("app.utils.db.pg_cursor")
    def test_executes_query_with_params(self, mock_pg_cursor):
        """Should execute query with parameters."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1, "test")]
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_query("SELECT * FROM test WHERE id = %s", (1,))

        mock_cursor.execute.assert_called_once_with("SELECT * FROM test WHERE id = %s", (1,))


class TestExecuteSingle:
    """Test execute_single function."""

    @patch("app.utils.db.pg_cursor")
    def test_returns_none_when_no_connection(self, mock_pg_cursor):
        """Should return None when cursor is None."""
        mock_pg_cursor.return_value.__enter__.return_value = None

        result = execute_single("SELECT * FROM test")

        assert result is None

    @patch("app.utils.db.pg_cursor")
    def test_executes_query_and_returns_single_result(self, mock_pg_cursor):
        """Should execute query and return single result."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1, "test")
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_single("SELECT * FROM test LIMIT 1")

        assert result == (1, "test")
        mock_cursor.execute.assert_called_once()

    @patch("app.utils.db.pg_cursor")
    def test_executes_query_with_params(self, mock_pg_cursor):
        """Should execute query with parameters."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1, "test")
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_single("SELECT * FROM test WHERE id = %s", (1,))

        mock_cursor.execute.assert_called_once_with("SELECT * FROM test WHERE id = %s", (1,))


class TestExecuteWrite:
    """Test execute_write function."""

    @patch("app.utils.db.pg_cursor")
    def test_returns_zero_when_no_connection(self, mock_pg_cursor):
        """Should return 0 when cursor is None."""
        mock_pg_cursor.return_value.__enter__.return_value = None

        result = execute_write("UPDATE test SET value = 1")

        assert result == 0

    @patch("app.utils.db.pg_cursor")
    def test_executes_write_and_returns_rowcount(self, mock_pg_cursor):
        """Should execute write query and return affected row count."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 5
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_write("UPDATE test SET value = 1")

        assert result == 5
        mock_cursor.execute.assert_called_once()

    @patch("app.utils.db.pg_cursor")
    def test_executes_write_with_params(self, mock_pg_cursor):
        """Should execute write query with parameters."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_write("UPDATE test SET value = %s WHERE id = %s", (10, 1))

        mock_cursor.execute.assert_called_once_with(
            "UPDATE test SET value = %s WHERE id = %s", (10, 1)
        )

    @patch("app.utils.db.pg_cursor")
    def test_handles_none_rowcount(self, mock_pg_cursor):
        """Should handle None rowcount gracefully."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = None
        mock_pg_cursor.return_value.__enter__.return_value = mock_cursor

        result = execute_write("DELETE FROM test")

        assert result == 0
