"""Standardized error response format."""

from typing import Any

_STATUS_TO_CODE: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    402: "PAYMENT_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
}


def error_response(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build standardized error response: { status, message, code, errors? }."""
    return {
        "status": "error",
        "message": message,
        "code": code or _STATUS_TO_CODE.get(status_code, "ERROR"),
        **({"errors": errors} if errors else {}),
    }
