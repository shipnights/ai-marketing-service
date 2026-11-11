"""Integration tests for all /api/v1/auth/* endpoints."""

import pytest

from backend.fixtures.factories import make_team, make_user, make_email_index_item
from app.auth.password import hash_password
from app.db.tables import MAIN_TABLE


# ── Helpers ────────────────────────────────────────────────────────────────────

def _seed_verified_user(dynamodb, *, email="user@example.com", password="password123"):
    """Insert a fully registered, verified user + team into DynamoDB."""
    team = make_team()
    team_id = team["team_id"]

    user = make_user(
        email=email,
        team_id=team_id,
        password_hash=hash_password(password),
        email_verified=True,
        token_version=1,
        full_name="Test User",
    )
    user_id = user["user_id"]

    dynamodb.Table(MAIN_TABLE).put_item(Item=team)
    dynamodb.Table(MAIN_TABLE).put_item(Item=user)
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_email_index_item(user_id, email))
    return user, team


# ── Register wizard ────────────────────────────────────────────────────────────

class TestRegisterStep1:
    def test_success(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "new@example.com", "password": "strongpass99"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "tempToken" in data
        assert "userId" in data

    def test_duplicate_email_409(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="taken@example.com")
        resp = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "taken@example.com", "password": "anything123"},
        )
        assert resp.status_code == 409

    def test_invalid_email_422(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "not-an-email", "password": "pass"},
        )
        assert resp.status_code == 422


class TestRegisterStep2:
    def _get_temp_token(self, app_client):
        r = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "step2test@example.com", "password": "pass12345"},
        )
        return r.json()["data"]["tempToken"]

    def test_success(self, app_client):
        token = self._get_temp_token(app_client)
        resp = app_client.post(
            "/api/v1/auth/register/step-2",
            json={"temp_token": token, "full_name": "Jane Doe"},
        )
        assert resp.status_code == 200

    def test_invalid_temp_token_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/register/step-2",
            json={"temp_token": "bad.token.here", "full_name": "Jane"},
        )
        assert resp.status_code == 401


class TestRegisterStep3:
    def _setup(self, app_client):
        r1 = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "step3@example.com", "password": "pass12345"},
        )
        token = r1.json()["data"]["tempToken"]
        app_client.post(
            "/api/v1/auth/register/step-2",
            json={"temp_token": token, "full_name": "Jane"},
        )
        return token

    def test_success(self, app_client):
        token = self._setup(app_client)
        resp = app_client.post(
            "/api/v1/auth/register/step-3",
            json={"temp_token": token, "team_name": "Jane's Brand"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "teamId" in data
        assert "tempToken" in data

    def test_invalid_token_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/register/step-3",
            json={"temp_token": "invalid", "team_name": "Nope"},
        )
        assert resp.status_code == 401


class TestRegisterStep4:
    def _complete_wizard(self, app_client):
        r1 = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "wizard@example.com", "password": "pass12345"},
        )
        token = r1.json()["data"]["tempToken"]
        app_client.post(
            "/api/v1/auth/register/step-2",
            json={"temp_token": token, "full_name": "Wizard"},
        )
        r3 = app_client.post(
            "/api/v1/auth/register/step-3",
            json={"temp_token": token, "team_name": "Wizard Co"},
        )
        return r3.json()["data"]["tempToken"]

    def test_success(self, app_client):
        token = self._complete_wizard(app_client)
        resp = app_client.post(
            "/api/v1/auth/register/step-4",
            json={"temp_token": token},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data

    def test_invalid_token_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/register/step-4",
            json={"temp_token": "garbage"},
        )
        assert resp.status_code == 401


# ── Login ──────────────────────────────────────────────────────────────────────

class TestLogin:
    def test_success(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="login@example.com", password="correct123")
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "correct123"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data

    def test_wrong_password_401(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="wp@example.com", password="realpass")
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "wp@example.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    def test_unknown_email_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "anything"},
        )
        assert resp.status_code == 401

    def test_unverified_user_403(self, app_client, dynamodb):
        """Login with an unverified account should return 403."""
        team = make_team()
        user = make_user(
            email="unverified@example.com",
            team_id=team["team_id"],
            password_hash=hash_password("pass123"),
            email_verified=False,
        )
        dynamodb.Table(MAIN_TABLE).put_item(Item=team)
        dynamodb.Table(MAIN_TABLE).put_item(Item=user)
        dynamodb.Table(MAIN_TABLE).put_item(
            Item=make_email_index_item(user["user_id"], "unverified@example.com")
        )
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "unverified@example.com", "password": "pass123"},
        )
        assert resp.status_code == 403


# ── Refresh ────────────────────────────────────────────────────────────────────

class TestRefresh:
    def _get_tokens(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="refresh@example.com", password="pass123")
        r = app_client.post(
            "/api/v1/auth/login",
            json={"email": "refresh@example.com", "password": "pass123"},
        )
        return r.json()["data"]

    def test_success(self, app_client, dynamodb):
        tokens = self._get_tokens(app_client, dynamodb)
        resp = app_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refreshToken"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "accessToken" in data

    def test_invalid_token_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "bad.token.here"},
        )
        assert resp.status_code == 401

    def test_used_token_revoked(self, app_client, dynamodb):
        """After a refresh the old token must be revoked."""
        tokens = self._get_tokens(app_client, dynamodb)
        refresh = tokens["refreshToken"]
        # Use it once
        app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        # Use it again — should be 401
        resp = app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 401


# ── Logout ─────────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_idempotent(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="logout@example.com", password="pass123")
        r = app_client.post(
            "/api/v1/auth/login",
            json={"email": "logout@example.com", "password": "pass123"},
        )
        refresh = r.json()["data"]["refreshToken"]
        resp = app_client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
        assert resp.status_code == 200
        # Second call with same token still 200 (idempotent)
        resp2 = app_client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
        assert resp2.status_code == 200


# ── Forgot / Reset password ────────────────────────────────────────────────────

class TestForgotPassword:
    def test_always_200(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "doesnotexist@example.com"},
        )
        assert resp.status_code == 200

    def test_known_email_also_200(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="known@example.com")
        resp = app_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "known@example.com"},
        )
        assert resp.status_code == 200


class TestResetPassword:
    def test_invalid_token_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/reset-password",
            json={"token": "bad.token", "new_password": "newpass123"},
        )
        assert resp.status_code == 401

    def test_success_allows_login_with_new_password(self, app_client, dynamodb):
        from app.auth.jwt import create_temp_token

        user, _ = _seed_verified_user(dynamodb, email="reset@example.com", password="oldpass")
        token = create_temp_token(user["user_id"], "reset@example.com")

        resp = app_client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "newpass456"},
        )
        assert resp.status_code == 200

        login_resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "reset@example.com", "password": "newpass456"},
        )
        assert login_resp.status_code == 200


# ── /me ────────────────────────────────────────────────────────────────────────

class TestGetMe:
    def test_authenticated(self, app_client, dynamodb, auth_headers, test_user_id, test_team_id):
        team = make_team(team_id=test_team_id)
        user = make_user(
            user_id=test_user_id,
            team_id=test_team_id,
            email="me@example.com",
        )
        dynamodb.Table(MAIN_TABLE).put_item(Item=team)
        dynamodb.Table(MAIN_TABLE).put_item(Item=user)

        resp = app_client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["user_id"] == test_user_id
        assert "password_hash" not in data

    def test_unauthenticated_401(self, app_client):
        resp = app_client.get("/api/v1/auth/me")
        assert resp.status_code == 401
