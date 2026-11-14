"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.logging_config import configure_logging

configure_logging()

from app import __version__
from app.api.v1 import router as api_v1_router
from app.config import (
    ANTHROPIC_API_KEY,
    CORS_ORIGINS,
    DEBUG,
    DISABLE_DOCS,
    ENABLE_DEV_AUTH_HEADER,
    IS_PRODUCTION,
    JWT_SECRET,
    MEDIA_LOCAL_PATH,
    RATE_LIMIT,
    STORAGE_DRIVER,
)
from app.errors import error_response
from app.middleware import ApiKeyMiddleware, RequestIdMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)
logging.getLogger("app").setLevel(logging.INFO)

_MIN_JWT_SECRET_LEN = 32


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup validation and teardown."""
    if IS_PRODUCTION and not ENABLE_DEV_AUTH_HEADER:
        if not JWT_SECRET:
            logger.error("JWT_SECRET is not set — authentication will not work in production")
        elif len(JWT_SECRET) < _MIN_JWT_SECRET_LEN:
            logger.warning("JWT_SECRET should be at least %d characters", _MIN_JWT_SECRET_LEN)

    if IS_PRODUCTION and not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY is not set — AI features will be unavailable")

    if ENABLE_DEV_AUTH_HEADER and IS_PRODUCTION:
        logger.error("ENABLE_DEV_AUTH_HEADER is set in production — this is a security risk!")

    logger.warning(
        "AI Marketing Service %s starting (env=%s, dev_auth=%s)",
        __version__,
        "production" if IS_PRODUCTION else "development",
        ENABLE_DEV_AUTH_HEADER,
    )

    # Ensure local uploads directory exists
    if STORAGE_DRIVER == "local":
        os.makedirs(MEDIA_LOCAL_PATH, exist_ok=True)

    yield


app = FastAPI(
    title="AI Marketing Service",
    description="Backend API for the AI Marketing platform",
    version=__version__,
    docs_url=None if DISABLE_DOCS else "/docs",
    redoc_url=None if DISABLE_DOCS else "/redoc",
    lifespan=lifespan,
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
if RATE_LIMIT:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT])
    app.state.limiter = limiter

    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content=error_response(429, "Too many requests. Please try again later.", code="RATE_LIMIT_EXCEEDED"),
        )

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

# ── Middleware (applied in reverse order of .add_middleware calls) ─────────────
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (local media uploads) ───────────────────────────────────────
if STORAGE_DRIVER == "local":
    os.makedirs(MEDIA_LOCAL_PATH, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=MEDIA_LOCAL_PATH), name="uploads")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(api_v1_router, prefix="/api/v1", tags=["v1"])


# ── Exception handlers ────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        loc = " > ".join(str(x) for x in err.get("loc", []))
        errors.append({"field": loc, "message": err.get("msg", "Invalid value")})
    return JSONResponse(
        status_code=422,
        content=error_response(422, "Validation error", code="VALIDATION_ERROR", errors=errors),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message", str(detail))
        code = detail.get("code")
        body = error_response(exc.status_code, message, code=code)
        body["detail"] = detail
    else:
        body = error_response(exc.status_code, str(detail) if detail else "An error occurred")
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception: %s", exc)
    if DEBUG:
        body = error_response(500, str(exc), code="INTERNAL_ERROR")
        body["type"] = type(exc).__name__
        return JSONResponse(status_code=500, content=body)
    return JSONResponse(
        status_code=500,
        content=error_response(500, "Internal server error", code="INTERNAL_ERROR"),
    )


# ── System routes ─────────────────────────────────────────────────────────────
@app.get("/", tags=["root"])
async def root() -> dict:
    return {"service": "ai-marketing-service", "version": __version__}


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
async def ready() -> dict:
    """Readiness probe. Optionally pings DynamoDB when ENABLE_READINESS_DB_CHECK=1."""
    if os.getenv("ENABLE_READINESS_DB_CHECK", "").strip().lower() in ("1", "true", "yes"):
        try:
            # get_item_strict propagates DB errors; the non-strict get_item
            # swallows them, which made this 503 path unreachable.
            from app.db.main import get_item_strict
            get_item_strict("__ready__", "__check__")
        except Exception as e:
            logger.warning("Readiness DB check failed: %s", e)
            raise HTTPException(status_code=503, detail="Dependency unavailable") from e
    return {"status": "ok"}
