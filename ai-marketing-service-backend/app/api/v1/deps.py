"""Shared FastAPI dependencies for API v1."""

import logging
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import (
    CLAIM_TEAM_ID,
    CLAIM_TOKEN_VERSION,
    CLAIM_USER_ID,
    decode_access_token,
)
from app.config import ENABLE_DEV_AUTH_HEADER, JWT_SECRET

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

__all__ = ["get_current_user", "CurrentUser"]


@dataclass
class CurrentUser:
    user_id: str
    team_id: str
    email: str


def get_current_user(
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_team_id: str | None = Header(None, alias="X-Team-Id"),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> CurrentUser:
    """
    Resolve the current user from a Bearer JWT, or from X-User-Id / X-Team-Id
    headers when ENABLE_DEV_AUTH_HEADER is true (dev/tests only).

    Returns a CurrentUser with user_id, team_id, and email.
    """
    # ── Dev bypass ────────────────────────────────────────────────────────────
    if ENABLE_DEV_AUTH_HEADER and x_user_id and x_user_id.strip():
        return CurrentUser(
            user_id=x_user_id.strip(),
            team_id=(x_team_id or "dev-team").strip(),
            email="dev@localhost",
        )

    # ── JWT auth ──────────────────────────────────────────────────────────────
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail={"message": "Not authenticated", "code": "UNAUTHORIZED"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not JWT_SECRET:
        logger.error("JWT_SECRET is not set — cannot validate token")
        raise HTTPException(status_code=503, detail="Authentication not configured")

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid or expired token", "code": "INVALID_TOKEN"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get(CLAIM_USER_ID) or payload.get("sub")
    team_id = payload.get(CLAIM_TEAM_ID)
    email = payload.get("email", "")

    if not user_id or not team_id:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid token payload", "code": "INVALID_TOKEN"},
        )

    return CurrentUser(user_id=str(user_id), team_id=str(team_id), email=str(email))


def get_team_guard(team_id: str, current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Verify the authenticated user belongs to the team_id path param."""
    if current_user.team_id != team_id:
        raise HTTPException(
            status_code=403,
            detail={"message": "Access denied to this team", "code": "FORBIDDEN"},
        )
    return current_user


def require_credits(cost: int) -> Depends:
    """
    Dependency factory that checks the team has at least `cost` AI credits.

    Usage:
        @router.post("/expensive")
        def endpoint(current_user: CurrentUser = require_credits(5)):
            ...
    """
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        from app.db.main import get_item
        from app.db.tables import MAIN_TABLE

        team = get_item(f"TEAM#{current_user.team_id}", "PROFILE", table_name=MAIN_TABLE)
        balance = int((team or {}).get("ai_credits", 0))
        if balance < cost:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "INSUFFICIENT_CREDITS",
                    "message": f"Not enough AI credits. Need {cost}, have {balance}.",
                    "required": cost,
                    "balance": balance,
                },
            )
        return current_user

    return Depends(_check)
