"""Unit tests for JWT token creation and validation."""

import time
import uuid

import pytest

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
    assert len(token) > 20


def test_decode_access_token_valid():
    token = create_access_token(USER_ID, TEAM_ID, EMAIL)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload[CLAIM_USER_ID] == USER_ID
    assert payload[CLAIM_TEAM_ID] == TEAM_ID
    assert payload[CLAIM_EMAIL] == EMAIL
    assert payload[CLAIM_TOKEN_TYPE] == "access"


def test_decode_access_token_wrong_type_rejected():
    refresh = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    assert decode_access_token(refresh) is None


def test_decode_access_token_garbage_returns_none():
    assert decode_access_token("not.a.token") is None


def test_decode_access_token_empty_returns_none():
    assert decode_access_token("") is None


def test_access_token_contains_token_version():
    token = create_access_token(USER_ID, TEAM_ID, EMAIL, token_version=3)
    payload = decode_access_token(token)
    assert payload[CLAIM_TOKEN_VERSION] == 3


# ── Refresh token ──────────────────────────────────────────────────────────────

def test_create_refresh_token_returns_string():
    token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    assert isinstance(token, str)


def test_decode_refresh_token_valid(dynamodb):
    """decode_refresh_token succeeds when JTI is not revoked."""
    token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
    payload = decode_refresh_token(token)
    assert payload is not None
    assert payload[CLAIM_USER_ID] == USER_ID
    assert payload[CLAIM_TOKEN_TYPE] == "refresh"
    assert CLAIM_JTI in payload


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


# ── Temp token ─────────────────────────────────────────────────────────────────

def test_create_temp_token_returns_string():
    token = create_temp_token(USER_ID, EMAIL)
    assert isinstance(token, str)


def test_decode_temp_token_valid():
    token = create_temp_token(USER_ID, EMAIL)
    payload = decode_temp_token(token)
    assert payload is not None
    assert payload[CLAIM_USER_ID] == USER_ID
    assert payload[CLAIM_TOKEN_TYPE] == "temp"


def test_decode_temp_token_wrong_type_rejected():
    access = create_access_token(USER_ID, TEAM_ID, EMAIL)
    assert decode_temp_token(access) is None
