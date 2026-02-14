"""
Tests for app.utils.log_utils module.

Tests log file reading and processing utilities.
"""



from app.utils.log_utils import (
    get_log_with_errors,
    is_error_line,
    read_log_file_lines,
    read_log_file_tail,
)


class TestReadLogFileTail:
    """Test read_log_file_tail function."""

    def test_reads_last_n_lines(self, tmp_path):
        """Should read the last N lines from log file."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")

        result = read_log_file_tail(log_file, 3)

        assert result["log"] == "Line 3\nLine 4\nLine 5\n"

    def test_caps_tail_at_2000_lines(self, tmp_path):
        """Should cap tail at 2000 lines maximum."""
        log_file = tmp_path / "test.log"
        lines = "\n".join([f"Line {i}" for i in range(3000)])
        log_file.write_text(lines)

        result = read_log_file_tail(log_file, 5000)

        # Should only read last 2000 lines
        log_lines = result["log"].strip().split("\n")
        assert len(log_lines) == 2000

    def test_handles_nonexistent_file(self, tmp_path):
        """Should return error message for nonexistent file."""
        log_file = tmp_path / "nonexistent.log"

        result = read_log_file_tail(log_file, 10)

        assert "[Error reading log:" in result["log"]

    def test_handles_encoding_errors(self, tmp_path):
        """Should handle encoding errors gracefully."""
        log_file = tmp_path / "test.log"
        # Write some valid UTF-8 text
        log_file.write_text("Valid line\nAnother line\n", encoding="utf-8")

        result = read_log_file_tail(log_file, 10)

        assert "Valid line" in result["log"]


class TestReadLogFileLines:
    """Test read_log_file_lines function."""

    def test_reads_all_lines(self, tmp_path):
        """Should read all lines from log file."""
        log_file = tmp_path / "test.log"
        log_file.write_text("Line 1\nLine 2\nLine 3\n")

        result = read_log_file_lines(log_file)

        assert len(result) == 3
        assert result[0] == "Line 1\n"
        assert result[2] == "Line 3\n"

    def test_handles_nonexistent_file(self, tmp_path):
        """Should return error message for nonexistent file."""
        log_file = tmp_path / "nonexistent.log"

        result = read_log_file_lines(log_file)

        assert len(result) == 1
        assert "[Error reading log:" in result[0]


class TestIsErrorLine:
    """Test is_error_line function."""

    def test_detects_error_keyword(self):
        """Should detect ERROR keyword."""
        assert is_error_line("2024-01-01 ERROR: Something went wrong")
        assert is_error_line("[ERROR] Failed to process")

    def test_detects_critical_keyword(self):
        """Should detect CRITICAL keyword."""
        assert is_error_line("CRITICAL: System failure")

    def test_detects_traceback(self):
        """Should detect Traceback keyword."""
        assert is_error_line("Traceback (most recent call last):")

    def test_detects_exception_keyword(self):
        """Should detect Exception keyword in line."""
        assert is_error_line("Exception occurred in module")
        assert is_error_line("Caught Exception: something failed")

    def test_returns_false_for_normal_lines(self):
        """Should return False for normal log lines."""
        assert not is_error_line("INFO: Processing started")
        assert not is_error_line("DEBUG: Variable value = 42")
        assert not is_error_line("Normal log message")


class TestGetLogWithErrors:
    """Test get_log_with_errors function."""

    def test_returns_log_and_errors(self, tmp_path):
        """Should return both log tail and error lines."""
        log_file = tmp_path / "test.log"
        content = """INFO: Starting process
ERROR: Failed to connect
INFO: Retrying
CRITICAL: System down
INFO: Process complete
"""
        log_file.write_text(content)

        result = get_log_with_errors(log_file, tail=3, error_tail=10)

        assert result["exists"] is True
        assert "Process complete" in result["log"]
        assert "ERROR: Failed to connect" in result["errors"]
        assert "CRITICAL: System down" in result["errors"]
        assert result["error_lines"] == 2

    def test_handles_nonexistent_file(self, tmp_path):
        """Should return empty result for nonexistent file."""
        log_file = tmp_path / "nonexistent.log"

        result = get_log_with_errors(log_file)

        assert result["exists"] is False
        assert result["log"] == ""
        assert result["errors"] == ""
        assert result["lines"] == 0
        assert result["error_lines"] == 0

    def test_caps_error_tail(self, tmp_path):
        """Should cap error tail at maximum."""
        log_file = tmp_path / "test.log"
        # Create many error lines
        errors = "\n".join([f"ERROR: Error {i}" for i in range(3000)])
        log_file.write_text(errors)

        result = get_log_with_errors(log_file, error_tail=5000)

        # Should cap at 2000
        error_lines = result["errors"].strip().split("\n")
        assert len(error_lines) == 2000

    def test_filters_errors_correctly(self, tmp_path):
        """Should only include error lines in errors field."""
        log_file = tmp_path / "test.log"
        content = """INFO: Line 1
ERROR: Error 1
INFO: Line 2
CRITICAL: Critical 1
DEBUG: Line 3
"""
        log_file.write_text(content)

        result = get_log_with_errors(log_file)

        assert "INFO" not in result["errors"]
        assert "DEBUG" not in result["errors"]
        assert "ERROR: Error 1" in result["errors"]
        assert "CRITICAL: Critical 1" in result["errors"]
