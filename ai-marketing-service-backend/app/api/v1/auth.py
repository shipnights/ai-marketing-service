"""Authentication routes — register wizard, login, refresh, logout, email verification."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.api.v1.deps import CurrentUser, get_current_user
from app.auth.jwt import (
    CLAIM_EMAIL,
    CLAIM_USER_ID,
    create_access_token,
    create_refresh_token,
    create_temp_token,
    decode_refresh_token,
    decode_temp_token,
)
from app.auth.password import hash_password, verify_password
from app.db.teams import add_team_member, create_team
from app.db.tokens import revoke_refresh_token
from app.db.users import create_user, get_user_by_email, get_user_by_id, update_user
from app.responses import success_response

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _token_pair(user: dict[str, Any]) -> dict[str, Any]:
    """Return access + refresh token dict for a fully registered user."""
    user_id = user["user_id"]
    team_id = user.get("team_id", "")
    email = user.get("email", "")
    token_version = int(user.get("token_version", 1))
    return {
        "accessToken": create_access_token(user_id, team_id, email, token_version),
        "refreshToken": create_refresh_token(user_id, team_id, email, token_version),
        "userId": user_id,
        "teamId": team_id,
    }


def _require_temp_token(temp_token: str) -> dict[str, Any]:
    """Decode a temp token or raise 401."""
    payload = decode_temp_token(temp_token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid or expired registration token", "code": "INVALID_TOKEN"},
        )
    return payload


# ── Register wizard ───────────────────────────────────────────────────────────

class RegisterStep1Body(BaseModel):
    email: EmailStr
    password: str


@router.post("/register/step-1")
def register_step1(body: RegisterStep1Body) -> dict[str, Any]:
    """Step 1 — email + password. Creates unverified user; returns temp_token."""
    if get_user_by_email(body.email):
        raise HTTPException(
            status_code=409,
            detail={"message": "An account with this email already exists.", "code": "EMAIL_CONFLICT"},
        )
    user_id = str(uuid.uuid4())
    create_user(
        {
            "user_id": user_id,
            "email": body.email,
            "password_hash": hash_password(body.password),
            "email_verified": False,
            "registration_step": 1,
            "token_version": 1,
        }
    )
    temp_token = create_temp_token(user_id, body.email)
    return success_response({"tempToken": temp_token, "userId": user_id}, "Account created")


class RegisterStep2Body(BaseModel):
    temp_token: str
    full_name: str
    role: str | None = None


@router.post("/register/step-2")
def register_step2(body: RegisterStep2Body) -> dict[str, Any]:
    """Step 2 — profile info (name, role). Updates user record."""
    payload = _require_temp_token(body.temp_token)
    user_id = payload[CLAIM_USER_ID]
    update_user(user_id, {"full_name": body.full_name, "role": body.role, "registration_step": 2})
    return success_response({"tempToken": body.temp_token}, "Profile saved")


class RegisterStep3Body(BaseModel):
    temp_token: str
    team_name: str


@router.post("/register/step-3")
def register_step3(body: RegisterStep3Body) -> dict[str, Any]:
    """Step 3 — create team. Returns new tempToken with team_id embedded (stored on user)."""
    payload = _require_temp_token(body.temp_token)
    user_id = payload[CLAIM_USER_ID]
    email = payload.get(CLAIM_EMAIL, "")

    team_id = str(uuid.uuid4())
    create_team(team_id=team_id, team_name=body.team_name, owner_user_id=user_id)
    add_team_member(team_id=team_id, user_id=user_id, role="owner")
    update_user(user_id, {"team_id": team_id, "registration_step": 3})

    # Issue a new temp token — same user/email, unchanged expiry class
    new_temp = create_temp_token(user_id, email)
    return success_response({"tempToken": new_temp, "teamId": team_id}, "Team created")


class RegisterStep4Body(BaseModel):
    temp_token: str


@router.post("/register/step-4")
def register_step4(body: RegisterStep4Body) -> dict[str, Any]:
    """Step 4 — finalise registration. Returns full access + refresh tokens."""
    payload = _require_temp_token(body.temp_token)
    user_id = payload[CLAIM_USER_ID]

    user = get_user_by_id(user_id)
    if not user or not user.get("team_id"):
        raise HTTPException(
            status_code=400,
            detail={"message": "Registration incomplete — complete all steps first.", "code": "INCOMPLETE_REGISTRATION"},
        )
    update_user(user_id, {"registration_step": 4, "email_verified": True})
    user = get_user_by_id(user_id)  # re-fetch with updated fields
    return success_response(_token_pair(user), "Registration complete")


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
def login(body: LoginBody) -> dict[str, Any]:
    """Authenticate with email + password. Returns access + refresh tokens."""
    user = get_user_by_email(body.email)
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid email or password.", "code": "INVALID_CREDENTIALS"},
        )
    if not user.get("email_verified"):
        raise HTTPException(
            status_code=403,
            detail={"message": "Email not verified. Complete registration first.", "code": "EMAIL_NOT_VERIFIED"},
        )
    if not user.get("team_id"):
        raise HTTPException(
            status_code=403,
            detail={"message": "Account setup incomplete.", "code": "INCOMPLETE_REGISTRATION"},
        )
    return success_response(_token_pair(user))


# ── Token refresh ─────────────────────────────────────────────────────────────

class RefreshBody(BaseModel):
    refresh_token: str


@router.post("/refresh")
def refresh_tokens(body: RefreshBody) -> dict[str, Any]:
    """Exchange a valid refresh token for a new access + refresh token pair."""
    payload = decode_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid or expired refresh token.", "code": "INVALID_TOKEN"},
        )
    user_id = payload[CLAIM_USER_ID]
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"message": "User not found.", "code": "NOT_FOUND"})

    # Revoke old JTI and issue fresh pair
    jti = payload.get("jti")
    if jti:
        revoke_refresh_token(jti)

    return success_response(_token_pair(user))


# ── Logout ────────────────────────────────────────────────────────────────────

class LogoutBody(BaseModel):
    refresh_token: str


@router.post("/logout")
def logout(body: LogoutBody) -> dict[str, Any]:
    """Revoke the provided refresh token."""
    payload = decode_refresh_token(body.refresh_token)
    if payload:
        jti = payload.get("jti")
        if jti:
            revoke_refresh_token(jti)
    # Always return success — idempotent
    return success_response(None, "Logged out")


# ── Email verification ────────────────────────────────────────────────────────

class VerifyEmailBody(BaseModel):
    token: str  # temp token issued during registration


@router.post("/verify-email")
def verify_email(body: VerifyEmailBody) -> dict[str, Any]:
    """Mark user email as verified using a registration temp token."""
    payload = _require_temp_token(body.token)
    user_id = payload[CLAIM_USER_ID]
    update_user(user_id, {"email_verified": True})
    return success_response(None, "Email verified")


# ── Forgot password ───────────────────────────────────────────────────────────

class ForgotPasswordBody(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordBody) -> dict[str, Any]:
    """
    Request a password reset. Always returns 200 to prevent email enumeration.
    In production, this should send a reset email with a temp token.
    """
    user = get_user_by_email(body.email)
    if user:
        # Issue a temp token for reset — in production, email this to the user
        reset_token = create_temp_token(user["user_id"], body.email)
        logger.info("Password reset token issued for user %s", user["user_id"])
        # TODO: integrate email service to send the token
        _ = reset_token  # silence unused warning until email is wired up

    return success_response(
        None,
        "If an account with that email exists, a reset link has been sent.",
    )


# ── Reset password ────────────────────────────────────────────────────────────

class ResetPasswordBody(BaseModel):
    token: str  # temp token from forgot-password
    new_password: str


@router.post("/reset-password")
def reset_password(body: ResetPasswordBody) -> dict[str, Any]:
    """Reset user password using a temp token from the forgot-password flow."""
    payload = _require_temp_token(body.token)
    user_id = payload[CLAIM_USER_ID]
    update_user(
        user_id,
        {
            "password_hash": hash_password(body.new_password),
            "token_version": 2,  # invalidate all outstanding tokens
        },
    )
    return success_response(None, "Password reset successfully")


# ── Current user ──────────────────────────────────────────────────────────────

@router.get("/me")
def get_me(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Return the current authenticated user's profile."""
    user = get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"message": "User not found.", "code": "NOT_FOUND"})
    # Strip sensitive fields
    user.pop("password_hash", None)
    user.pop("pk", None)
    user.pop("sk", None)
    return success_response(user)
