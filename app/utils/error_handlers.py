"""
Common error handling utilities for API routes.

Provides reusable error handling patterns to reduce code duplication.
"""

from fastapi import HTTPException


def handle_validation_error(e: ValueError, default_message: str = "Nieprawidłowe dane") -> None:
    """
    Handle validation errors by raising appropriate HTTPException.

    Args:
        e: The ValueError that was raised
        default_message: Default message if exception message is empty

    Raises:
        HTTPException: 400 Bad Request with error details
    """
    message = str(e) if str(e) else default_message
    raise HTTPException(status_code=400, detail=message) from e


def handle_server_error(e: Exception, context: str = "") -> None:
    """
    Handle unexpected server errors by raising appropriate HTTPException.

    Args:
        e: The exception that was raised
        context: Optional context about where the error occurred

    Raises:
        HTTPException: 500 Internal Server Error with error details
    """
    message = f"{context}: {e}" if context else str(e)
    raise HTTPException(status_code=500, detail=message) from e


def handle_not_found(resource: str, identifier: str) -> None:
    """
    Handle resource not found errors.

    Args:
        resource: Type of resource (e.g., "Profil", "Host")
        identifier: Resource identifier

    Raises:
        HTTPException: 404 Not Found
    """
    raise HTTPException(status_code=404, detail=f"{resource} '{identifier}' nie istnieje")


def handle_bad_request(message: str) -> None:
    """
    Handle bad request errors.

    Args:
        message: Error message to return

    Raises:
        HTTPException: 400 Bad Request
    """
    raise HTTPException(status_code=400, detail=message)


def validate_and_handle(validator_func, value, error_message: str = "Nieprawidłowe dane"):
    """
    Validate input and handle errors in one call.

    Args:
        validator_func: Validation function to call
        value: Value to validate
        error_message: Error message if validation fails

    Returns:
        Validated value

    Raises:
        HTTPException: 400 if validation fails
    """
    try:
        return validator_func(value)
    except ValueError as e:
        handle_validation_error(e, error_message)
