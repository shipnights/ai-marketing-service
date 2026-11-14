"""Unit tests for JWT token creation and validation."""

import time
import uuid

import jwt as pyjwt
import pytest
from freezegun import freeze_time

from app.config import JWT_EXPIRE_MINUTES, JWT_SECRET

from app.auth.jwt import (
    CLAIM_EMAIL,
    CLAIM_JTI,
    CLAIM_TEAM_ID,
    CLAIM_TOKEN_TYPE,
    CLAIM_TOKEN_VERSION,
    CLAIM_USER_ID,
    create_access_token,
    create_refresh_token,
    create_temp_token,
    decode_access_token,
    decode_refresh_token,
    decode_temp_token,
)


USER_ID = f"user-{uuid.uuid4().hex[:8]}"
TEAM_ID = f"team-{uuid.uuid4().hex[:8]}"
EMAIL = "test@example.com"


# ── Access token ───────────────────────────────────────────────────────────────

def test_create_access_token_returns_string():
    token = create_access_token(USER_ID, TEAM_ID, EMAIL)
    assert isinstance(token, str)
    # A JWT, not just "some long string": three dot-separated segments and a
    # verifiable HS256 header.
    assert len(token.split(".")) == 3
    assert pyjwt.get_unverified_header(token)["alg"] == "HS256"


def test_decode_access_token_valid():
    token = create_access_token(USER_ID, TEAM_ID, EMAIL)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload[CLAIM_USER_ID] == USER_ID
    assert payload[CLAIM_TEAM_ID] == TEAM_ID
    assert payload[CLAIM_EMAIL] == EMAIL
    assert payload[CLAIM_TOKEN_TYPE] == "access"
    # Standard claims: sub mirrors user_id; expiry is exactly JWT_EXPIRE_MINUTES.
    assert payload["sub"] == USER_ID
    assert payload["exp"] - payload["iat"] == JWT_EXPIRE_MINUTES * 60


def test_decode_access_token_wrong_type_rejected():
    refresh = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    assert decode_access_token(refresh) is None
    # Temp (registration/reset) tokens must not grant API access either.
    temp = create_temp_token(USER_ID, EMAIL)
    assert decode_access_token(temp) is None


def test_decode_access_token_garbage_returns_none():
    assert decode_access_token("not.a.token") is None


def test_decode_access_token_tampered_signature_rejected():
    """A token re-signed with a different secret must be rejected, even if
    its payload is perfectly well-formed."""
    token = create_access_token(USER_ID, TEAM_ID, EMAIL)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    payload[CLAIM_USER_ID] = "attacker"
    payload["sub"] = "attacker"
    forged = pyjwt.encode(payload, "wrong-secret-wrong-secret-wrong!", algorithm="HS256")
    assert decode_access_token(forged) is None


def test_decode_access_token_expired_returns_none():
    """Tokens older than JWT_EXPIRE_MINUTES must be rejected."""
    with freeze_time("2020-01-01 00:00:00"):
        token = create_access_token(USER_ID, TEAM_ID, EMAIL)
    assert decode_access_token(token) is None  # decoded "now", long past 2020


def test_decode_access_token_alg_none_rejected():
    """alg=none tokens (classic JWT attack) must never validate."""
    payload = {"sub": USER_ID, CLAIM_USER_ID: USER_ID, CLAIM_TOKEN_TYPE: "access",
               "exp": int(time.time()) + 3600, "iat": int(time.time())}
    forged = pyjwt.encode(payload, key=None, algorithm="none")
    assert decode_access_token(forged) is None


def test_decode_access_token_empty_returns_none():
    assert decode_access_token("") is None
    assert decode_access_token("   ") is None


def test_access_token_contains_token_version():
    token = create_access_token(USER_ID, TEAM_ID, EMAIL, token_version=3)
    payload = decode_access_token(token)
    assert payload[CLAIM_TOKEN_VERSION] == 3


# ── Refresh token ──────────────────────────────────────────────────────────────

def test_create_refresh_token_returns_string():
    token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    assert isinstance(token, str)
    assert len(token.split(".")) == 3
    # Every refresh token must carry a UNIQUE jti, or revocation of one
    # token would kill (or miss) others.
    jti_a = pyjwt.decode(token, options={"verify_signature": False})[CLAIM_JTI]
    token_b = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    jti_b = pyjwt.decode(token_b, options={"verify_signature": False})[CLAIM_JTI]
    assert jti_a and jti_b and jti_a != jti_b


def test_decode_refresh_token_valid(dynamodb):
    """decode_refresh_token succeeds when JTI is not revoked."""
    token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    payload = decode_refresh_token(token)
    assert payload is not None
    assert payload[CLAIM_USER_ID] == USER_ID
    assert payload[CLAIM_TOKEN_TYPE] == "refresh"
    assert CLAIM_JTI in payload
    assert payload["exp"] - payload["iat"] == 30 * 24 * 3600  # 30 days


def test_decode_refresh_token_revoked_returns_none(dynamodb):
    """decode_refresh_token returns None when JTI is in the revocation store."""
    from app.db.tokens import revoke_refresh_token

    token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    payload = decode_refresh_token(token)
    assert payload is not None
    jti = payload[CLAIM_JTI]

    revoke_refresh_token(jti)
    assert decode_refresh_token(token) is None


def test_decode_refresh_token_wrong_type_rejected(dynamodb):
    access = create_access_token(USER_ID, TEAM_ID, EMAIL)
    assert decode_refresh_token(access) is None


def test_decode_refresh_token_missing_jti_rejected(dynamodb):
    """A refresh-type token without a jti cannot be revoked - reject it."""
    payload = {"sub": USER_ID, CLAIM_USER_ID: USER_ID, CLAIM_TOKEN_TYPE: "refresh",
               "exp": int(time.time()) + 3600, "iat": int(time.time())}
    token = pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")
    assert decode_refresh_token(token) is None


# ── Temp token ─────────────────────────────────────────────────────────────────

def test_create_temp_token_returns_string():
    token = create_temp_token(USER_ID, EMAIL)
    assert isinstance(token, str)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload["exp"] - payload["iat"] == 3600  # 1 hour wizard window


def test_decode_temp_token_valid():
    token = create_temp_token(USER_ID, EMAIL)
    payload = decode_temp_token(token)
    assert payload is not None
    assert payload[CLAIM_USER_ID] == USER_ID
    assert payload[CLAIM_TOKEN_TYPE] == "temp"


def test_decode_temp_token_wrong_type_rejected():
    access = create_access_token(USER_ID, TEAM_ID, EMAIL)
    assert decode_temp_token(access) is None
