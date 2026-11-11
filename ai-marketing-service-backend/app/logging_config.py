"""Structured logging configuration. Set STRUCTURED_LOGGING=1 for JSON logs."""

import logging
import os

__all__ = ["configure_logging", "get_logger"]

_STRUCTURED_ENV = ("1", "true", "yes")
_USE_STRUCTURED = os.getenv("STRUCTURED_LOGGING", "").strip().lower() in _STRUCTURED_ENV


def configure_logging() -> None:
    """Configure structlog when STRUCTURED_LOGGING=1."""
    if not _USE_STRUCTURED:
        return

    import structlog

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a standard logger. Use as: logger = get_logger(__name__)"""
    return logging.getLogger(name)
