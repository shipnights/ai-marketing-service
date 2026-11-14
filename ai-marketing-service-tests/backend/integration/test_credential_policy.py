"""Credential-policy contract tests (gap #9).

Contract source: the registration form in the frontend enforces
`minLength={8}` (ai-marketing-service-frontend/src/app/auth/page.tsx). The
backend must enforce the same rule - a client can always bypass the UI - on
every endpoint that sets a password.
"""

import pytest

from app.auth.jwt import create_temp_token
from app.auth.password import hash_password
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_email_index_item, make_team, make_user

MIN_LEN = 8


def _register(app_client, email, password):
    return app_client.post("/api/v1/auth/register/step-1", json={"email": email, "password": password})


def _full_wizard(app_client, email, password):
    token = _register(app_client, email, password).json()["data"]["tempToken"]
    app_client.post("/api/v1/auth/register/step-2", json={"temp_token": token, "full_name": "X"})
    token = app_client.post("/api/v1/auth/register/step-3",
                            json={"temp_token": token, "team_name": "T"}).json()["data"]["tempToken"]
    return app_client.post("/api/v1/auth/register/step-4", json={"temp_token": token})


def _seed_user(dynamodb, email="pw@example.com", password="original1"):
    team = make_team()
    user = make_user(email=email, team_id=team["team_id"], password_hash=hash_password(password),
                     email_verified=True, token_version=1)
    for item in (team, user, make_email_index_item(user["user_id"], email)):
        dynamodb.Table(MAIN_TABLE).put_item(Item=item)
    return user


# -- password length ------------------------------------------------------------------------

def test_password_at_minimum_length_accepted(app_client, dynamodb):
    assert _register(app_client, "ok@example.com", "a" * MIN_LEN).status_code == 200


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: /auth/register/step-1 accepts any password, including empty and "
    "1-character ones. The frontend requires >= 8 characters but the API does "
    "not enforce it, so any non-UI client can create trivially guessable accounts."))
@pytest.mark.parametrize("password", ["", "a", "a" * (MIN_LEN - 1)])
def test_short_password_rejected_at_registration(app_client, dynamodb, password):
    r = _register(app_client, "short@example.com", password)
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: /auth/reset-password accepts a new password of any length - the "
    "reset path bypasses the (UI-only) 8-character rule entirely."))
@pytest.mark.parametrize("password", ["", "abc"])
def test_short_password_rejected_at_reset(app_client, dynamodb, password):
    user = _seed_user(dynamodb)
    r = app_client.post("/api/v1/auth/reset-password",
                        json={"token": create_temp_token(user["user_id"], "pw@example.com"),
                              "new_password": password})
    assert r.status_code == 422


def test_long_password_roundtrip(app_client, dynamodb):
    """1000-char passwords must hash and verify fully (no silent truncation
    like bcrypt's 72-byte limit) - a different 1000-char suffix must fail."""
    pw = "x" * 999 + "1"
    assert _full_wizard(app_client, "long@example.com", pw).status_code == 200
    assert app_client.post("/api/v1/auth/login", json={"email": "long@example.com", "password": pw}).status_code == 200
    wrong = "x" * 999 + "2"
    assert app_client.post("/api/v1/auth/login", json={"email": "long@example.com", "password": wrong}).status_code == 401


def test_unicode_password_roundtrip(app_client, dynamodb):
    pw = "p\u00e4ssw\u00f6rd-\u5bc6\u7801"
    assert _full_wizard(app_client, "uni@example.com", pw).status_code == 200
    assert app_client.post("/api/v1/auth/login", json={"email": "uni@example.com", "password": pw}).status_code == 200


def test_password_is_not_trimmed(app_client, dynamodb):
    """Whitespace is significant: ' secret12 ' must not log in as 'secret12'."""
    _full_wizard(app_client, "ws@example.com", " secret12 ")
    assert app_client.post("/api/v1/auth/login", json={"email": "ws@example.com", "password": "secret12"}).status_code == 401


# -- email identity -----------------------------------------------------------------------------

def test_email_domain_is_normalised(app_client, dynamodb):
    """The domain part is case-insensitive (RFC 5321) and is normalised."""
    assert _register(app_client, "case@Example.COM", "password1").status_code == 200
    assert _register(app_client, "case@example.com", "password1").status_code == 409


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: emails are only domain-normalised, so 'Dup@example.com' and "
    "'dup@example.com' register as TWO separate accounts for the same mailbox "
    "(all major providers treat the local part case-insensitively)."))
def test_email_local_part_case_does_not_create_duplicate_account(app_client, dynamodb):
    assert _register(app_client, "Dup@example.com", "password1").status_code == 200
    assert _register(app_client, "dup@example.com", "password1").status_code == 409


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: login looks emails up case-sensitively, so a user who registered "
    "as 'Mixed@example.com' cannot log in typing 'mixed@example.com'."))
def test_login_is_case_insensitive_on_email(app_client, dynamodb):
    assert _full_wizard(app_client, "Mixed@example.com", "password1").status_code == 200
    r = app_client.post("/api/v1/auth/login", json={"email": "mixed@example.com", "password": "password1"})
    assert r.status_code == 200


@pytest.mark.parametrize("email", ["no-at-sign", "a@b", "@example.com", "a@@example.com", ""])
def test_malformed_emails_rejected(app_client, email):
    assert _register(app_client, email, "password1").status_code == 422
