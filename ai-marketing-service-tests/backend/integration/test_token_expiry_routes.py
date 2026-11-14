"""End-to-end token-lifecycle tests through the HTTP routes (gap #10).

Unit tests already prove decode_* rejects expired tokens; these prove every
ROUTE that consumes a token enforces expiry, plus the purpose/replay rules of
the password-reset flow.
"""

import pytest
from freezegun import freeze_time

from app.auth.jwt import create_access_token, create_refresh_token, create_temp_token
from app.auth.password import hash_password
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_email_index_item, make_team, make_user


def _seed_user(dynamodb, email="life@example.com", password="password1", email_verified=True):
    team = make_team()
    user = make_user(email=email, team_id=team["team_id"], password_hash=hash_password(password),
                     email_verified=email_verified, token_version=1)
    for item in (team, user, make_email_index_item(user["user_id"], email)):
        dynamodb.Table(MAIN_TABLE).put_item(Item=item)
    return user


def _user_row(dynamodb, user_id):
    return dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": f"USER#{user_id}", "sk": "PROFILE"})["Item"]


# -- temp (wizard / reset) tokens ----------------------------------------------------------

TEMP_TOKEN_ROUTES = [
    ("/api/v1/auth/register/step-2", lambda t: {"temp_token": t, "full_name": "X"}),
    ("/api/v1/auth/register/step-3", lambda t: {"temp_token": t, "team_name": "T"}),
    ("/api/v1/auth/register/step-4", lambda t: {"temp_token": t}),
    ("/api/v1/auth/verify-email", lambda t: {"token": t}),
    ("/api/v1/auth/reset-password", lambda t: {"token": t, "new_password": "newpass123"}),
]


@pytest.mark.parametrize("path,body", TEMP_TOKEN_ROUTES, ids=[p.rsplit("/", 1)[1] for p, _ in TEMP_TOKEN_ROUTES])
def test_expired_temp_token_rejected_on_every_route(app_client, dynamodb, path, body):
    user = _seed_user(dynamodb)
    with freeze_time("2020-01-01 00:00:00"):
        token = create_temp_token(user["user_id"], "life@example.com")
    r = app_client.post(path, json=body(token))
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_TOKEN"


def test_temp_token_valid_just_inside_one_hour_window(app_client, dynamodb):
    user = _seed_user(dynamodb)
    with freeze_time("2030-01-01 00:00:00"):
        token = create_temp_token(user["user_id"], "life@example.com")
    with freeze_time("2030-01-01 00:59:00"):
        assert app_client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
    with freeze_time("2030-01-01 01:00:01"):
        assert app_client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 401


def test_expired_reset_token_leaves_password_unchanged(app_client, dynamodb):
    user = _seed_user(dynamodb)
    with freeze_time("2020-01-01"):
        token = create_temp_token(user["user_id"], "life@example.com")
    app_client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "hijacked1"})
    login = app_client.post("/api/v1/auth/login", json={"email": "life@example.com", "password": "password1"})
    assert login.status_code == 200


# -- verify-email (previously untested route) ------------------------------------------------

def test_verify_email_marks_user_verified(app_client, dynamodb):
    user = _seed_user(dynamodb, email_verified=False)
    r = app_client.post("/api/v1/auth/verify-email",
                        json={"token": create_temp_token(user["user_id"], "life@example.com")})
    assert r.status_code == 200
    assert _user_row(dynamodb, user["user_id"])["email_verified"] is True


@pytest.mark.parametrize("token_factory", [
    lambda uid: create_access_token(uid, "t", "e"),
    lambda uid: create_refresh_token(uid, "t", "e"),
])
def test_verify_email_rejects_non_temp_tokens(app_client, dynamodb, token_factory):
    user = _seed_user(dynamodb, email_verified=False)
    r = app_client.post("/api/v1/auth/verify-email", json={"token": token_factory(user["user_id"])})
    assert r.status_code == 401
    assert _user_row(dynamodb, user["user_id"])["email_verified"] is False


# -- refresh / access tokens ----------------------------------------------------------------

def test_expired_refresh_token_rejected(app_client, dynamodb):
    user = _seed_user(dynamodb)
    with freeze_time("2020-01-01"):
        token = create_refresh_token(user["user_id"], user["team_id"], "life@example.com")
    r = app_client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 401


def test_refresh_token_valid_until_day_30(app_client, dynamodb):
    user = _seed_user(dynamodb)
    with freeze_time("2030-01-01"):
        token = create_refresh_token(user["user_id"], user["team_id"], "life@example.com")
    with freeze_time("2030-01-30 23:00:00"):
        assert app_client.post("/api/v1/auth/refresh", json={"refresh_token": token}).status_code == 200


def test_expired_access_token_gets_401_not_403(app_client, dynamodb):
    """The frontend's silent-refresh logic triggers ONLY on 401."""
    user = _seed_user(dynamodb)
    with freeze_time("2020-01-01"):
        token = create_access_token(user["user_id"], user["team_id"], "life@example.com")
    r = app_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_TOKEN"


def test_access_token_expires_after_configured_lifetime(app_client, dynamodb):
    from app.config import JWT_EXPIRE_MINUTES

    user = _seed_user(dynamodb)
    with freeze_time("2030-01-01 00:00:00"):
        token = create_access_token(user["user_id"], user["team_id"], "life@example.com")
    hdr = {"Authorization": f"Bearer {token}"}
    with freeze_time(f"2030-01-01 00:{JWT_EXPIRE_MINUTES - 1:02d}:00"):
        assert app_client.get("/api/v1/auth/me", headers=hdr).status_code == 200
    with freeze_time(f"2030-01-01 00:{JWT_EXPIRE_MINUTES:02d}:01"):
        assert app_client.get("/api/v1/auth/me", headers=hdr).status_code == 401


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: the custom HTTPException handler in app/main.py rebuilds the "
    "JSONResponse without exc.headers, so the `WWW-Authenticate: Bearer` header "
    "set by get_current_user is dropped from every 401 (required by RFC 7235)."))
def test_401_carries_www_authenticate_header(app_client):
    r = app_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


# -- reset-token purpose & replay ------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "SECURITY DEFECT: registration-wizard temp tokens and password-reset tokens "
    "are the same token type. The wizard token - returned in the step-1/step-3 "
    "API responses and kept client-side - is accepted by /auth/reset-password, so "
    "anyone holding it can change the account password for an hour, long after "
    "registration finished."))
def test_registration_token_cannot_reset_password(app_client, dynamodb):
    wizard_token = app_client.post("/api/v1/auth/register/step-1",
                                   json={"email": "victim@example.com", "password": "original1"}
                                   ).json()["data"]["tempToken"]
    r = app_client.post("/api/v1/auth/reset-password", json={"token": wizard_token, "new_password": "attacker1"})
    assert r.status_code == 401


@pytest.mark.xfail(strict=True, reason=(
    "SECURITY DEFECT: password-reset tokens are not single-use. The same token "
    "can reset the password again and again until it expires (temp tokens carry "
    "no token_version/jti, so the post-reset version bump doesn't kill them)."))
def test_reset_token_is_single_use(app_client, dynamodb):
    user = _seed_user(dynamodb)
    token = create_temp_token(user["user_id"], "life@example.com")
    first = app_client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "firstnew1"})
    assert first.status_code == 200
    replay = app_client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "secondnew"})
    assert replay.status_code == 401


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: registration step-3 is not idempotent. Replaying it with the same "
    "wizard token (double-click, network retry, back button) creates a SECOND "
    "team with 500 free AI credits and re-points the user at it, orphaning the "
    "first team - also a free-credit farming vector."))
def test_register_step3_replay_does_not_create_second_team(app_client, dynamodb):
    token = app_client.post("/api/v1/auth/register/step-1",
                            json={"email": "twice@example.com", "password": "password1"}
                            ).json()["data"]["tempToken"]
    first = app_client.post("/api/v1/auth/register/step-3", json={"temp_token": token, "team_name": "A"})
    second = app_client.post("/api/v1/auth/register/step-3", json={"temp_token": token, "team_name": "A"})
    assert first.status_code == 200
    teams = [i for i in dynamodb.Table(MAIN_TABLE).scan()["Items"]
             if i["pk"].startswith("TEAM#") and i["sk"] == "PROFILE"]
    assert len(teams) == 1
    assert second.status_code in (200, 409) and second.json().get("data", {}).get("teamId") in (
        None, first.json()["data"]["teamId"])
