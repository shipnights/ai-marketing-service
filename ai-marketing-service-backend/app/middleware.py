"""Custom middleware: security headers, request ID tracing, API key validation."""

import hmac
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import API_KEY, IS_PRODUCTION, REQUIRE_API_KEY
from app.errors import error_response


def _skip_api_key_check(path: str) -> bool:
    if path in ("/", "/health", "/ready", "/openapi.json"):
        return True
    if path.startswith(("/docs", "/redoc")):
        return True
    return False


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
HSTS_HEADER = "Strict-Transport-Security"
HSTS_VALUE = "max-age=31536000; includeSubDomains; preload"
REQUEST_ID_HEADER = "X-Request-Id"


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header for /api/v1/* when REQUIRE_API_KEY is True."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        if not REQUIRE_API_KEY or not API_KEY:
            return await call_next(request)
        path = request.url.path
        if _skip_api_key_check(path) or not path.startswith("/api/v1/"):
            return await call_next(request)
        provided = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if not provided or not hmac.compare_digest(provided, API_KEY):
            return JSONResponse(
                status_code=401,
                content=error_response(401, "Invalid or missing API key", code="INVALID_API_KEY"),
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add OWASP security headers to all responses."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if IS_PRODUCTION:
            response.headers[HSTS_HEADER] = HSTS_VALUE
        return response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Add X-Request-Id for request tracing."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
