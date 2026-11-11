"""JWT creation and validation."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt

from app.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET

logger = logging.getLogger(__name__)

CLAIM_USER_ID = "user_id"
CLAIM_TEAM_ID = "team_id"
CLAIM_EMAIL = "email"
CLAIM_TOKEN_TYPE = "token_type"
CLAIM_TOKEN_VERSION = "tv"
CLAIM_JTI = "jti"

TEMP_TOKEN_EXPIRE_SECONDS = 3600  # 1 hour for registration wizard

__all__ = [
    "create_access_token",
    "create_refresh_token",
    "create_temp_token",
    "decode_access_token",
    "decode_refresh_token",
    "decode_temp_token",
    "CLAIM_USER_ID",
    "CLAIM_TEAM_ID",
    "CLAIM_TOKEN_VERSION",
]


def _encode_token(payload: dict[str, Any], expire_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {**payload, "exp": now + expire_delta, "iat": now}
    token = pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token if isinstance(token, str) else token.decode("utf-8")


def create_access_token(user_id: str, team_id: str, email: str, token_version: int = 1) -> str:
    """Create a 7-day access token containing user_id and team_id."""
    return _encode_token(
        {
            "sub": user_id,
            CLAIM_USER_ID: user_id,
            CLAIM_TEAM_ID: team_id,
            CLAIM_EMAIL: email,
            CLAIM_TOKEN_TYPE: "access",
            CLAIM_TOKEN_VERSION: token_version,
        },
        timedelta(minutes=JWT_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: str, team_id: str, email: str, token_version: int = 1) -> str:
    """Create a 30-day refresh token with a unique JTI for revocation tracking."""
    return _encode_token(
        {
            "sub": user_id,
            CLAIM_USER_ID: user_id,
            CLAIM_TEAM_ID: team_id,
            CLAIM_EMAIL: email,
            CLAIM_TOKEN_TYPE: "refresh",
            CLAIM_JTI: str(uuid.uuid4()),
            CLAIM_TOKEN_VERSION: token_version,
        },
        timedelta(days=30),
    )


def create_temp_token(user_id: str, email: str) -> str:
    """Create a 1-hour temp token used during the registration wizard steps."""
    return _encode_token(
        {
            "sub": user_id,
            CLAIM_USER_ID: user_id,
            CLAIM_EMAIL: email,
            CLAIM_TOKEN_TYPE: "temp",
        },
        timedelta(seconds=TEMP_TOKEN_EXPIRE_SECONDS),
    )


def _decode_token(token: str) -> dict[str, Any] | None:
    if not JWT_SECRET:
        logger.warning("JWT_SECRET not set; token validation will fail")
        return None
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get(CLAIM_USER_ID) or payload.get("sub")
        if not user_id:
            return None
        return payload
    except pyjwt.PyJWTError:
        return None


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an access token. Returns payload or None."""
    payload = _decode_token(token)
    if not payload:
        return None
    token_type = payload.get(CLAIM_TOKEN_TYPE)
    if token_type and token_type != "access":
        return None
    return payload


def decode_temp_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a temp registration token. Returns payload or None."""
    payload = _decode_token(token)
    if payload and payload.get(CLAIM_TOKEN_TYPE) == "temp":
        return payload
    return None


def decode_refresh_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a refresh token, checking JTI revocation. Returns payload or None."""
    payload = _decode_token(token)
    if not payload or payload.get(CLAIM_TOKEN_TYPE) != "refresh":
        return None

    jti = payload.get(CLAIM_JTI)
    if not jti:
        return None

    from app.db.tokens import is_refresh_token_revoked
    if is_refresh_token_revoked(jti):
        return None

    return payload
