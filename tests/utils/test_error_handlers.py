"""
Tests for app.utils.error_handlers module.

Tests common error handling utilities used across route files.
"""

import pytest
from fastapi import HTTPException

from app.utils.error_handlers import (
    handle_bad_request,
    handle_not_found,
    handle_server_error,
    handle_validation_error,
    validate_and_handle,
)


class TestHandleValidationError:
    """Test handle_validation_error function."""

    def test_raises_400_with_error_message(self):
        """Should raise HTTPException with 400 status and error message."""
        error = ValueError("Invalid input")

        with pytest.raises(HTTPException) as exc_info:
            handle_validation_error(error)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid input"

    def test_uses_default_message_when_error_empty(self):
        """Should use default message when exception message is empty."""
        error = ValueError("")

        with pytest.raises(HTTPException) as exc_info:
            handle_validation_error(error, "Default message")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Default message"

    def test_custom_default_message(self):
        """Should use custom default message."""
        error = ValueError("")

        with pytest.raises(HTTPException) as exc_info:
            handle_validation_error(error, "Custom default")

        assert exc_info.value.detail == "Custom default"


class TestHandleServerError:
    """Test handle_server_error function."""

    def test_raises_500_with_error_message(self):
        """Should raise HTTPException with 500 status and error message."""
        error = Exception("Server error")

        with pytest.raises(HTTPException) as exc_info:
            handle_server_error(error)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Server error"

    def test_includes_context_in_message(self):
        """Should include context in error message when provided."""
        error = Exception("Database connection failed")

        with pytest.raises(HTTPException) as exc_info:
            handle_server_error(error, "Database operation")

        assert exc_info.value.status_code == 500
        assert "Database operation" in exc_info.value.detail
        assert "Database connection failed" in exc_info.value.detail


class TestHandleNotFound:
    """Test handle_not_found function."""

    def test_raises_404_with_resource_info(self):
        """Should raise HTTPException with 404 status and resource info."""
        with pytest.raises(HTTPException) as exc_info:
            handle_not_found("Profile", "test-profile")

        assert exc_info.value.status_code == 404
        assert "Profile" in exc_info.value.detail
        assert "test-profile" in exc_info.value.detail


class TestHandleBadRequest:
    """Test handle_bad_request function."""

    def test_raises_400_with_message(self):
        """Should raise HTTPException with 400 status and message."""
        with pytest.raises(HTTPException) as exc_info:
            handle_bad_request("Invalid request")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid request"


class TestValidateAndHandle:
    """Test validate_and_handle function."""

    def test_returns_validated_value_on_success(self):
        """Should return validated value when validation succeeds."""

        def validator(value):
            return value.upper()

        result = validate_and_handle(validator, "test")
        assert result == "TEST"

    def test_raises_400_on_validation_error(self):
        """Should raise HTTPException with 400 when validation fails."""

        def validator(value):
            raise ValueError("Invalid value")

        with pytest.raises(HTTPException) as exc_info:
            validate_and_handle(validator, "test")

        assert exc_info.value.status_code == 400
        assert "Invalid value" in exc_info.value.detail

    def test_uses_custom_error_message(self):
        """Should use custom error message when provided."""

        def validator(value):
            raise ValueError("Original error")

        with pytest.raises(HTTPException) as exc_info:
            validate_and_handle(validator, "test", "Custom error message")

        assert exc_info.value.detail == "Custom error message"
