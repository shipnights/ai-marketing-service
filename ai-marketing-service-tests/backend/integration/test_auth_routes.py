"""Integration tests for all /api/v1/auth/* endpoints."""

import jwt as pyjwt
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
    def test_success(self, app_client, dynamodb):
        resp = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "new@example.com", "password": "strongpass99"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "tempToken" in data
        assert "userId" in data

        # The user must actually be PERSISTED (this test used to pass even
        # when every DynamoDB write failed silently).
        user = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"USER#{data['userId']}", "sk": "PROFILE"}
        ).get("Item")
        assert user is not None
        assert user["email"] == "new@example.com"
        assert user["email_verified"] is False
        assert user["registration_step"] == 1
        # Password must be stored as an argon2 hash, never as plaintext.
        assert user["password_hash"].startswith("$argon2id$")
        assert "strongpass99" not in user["password_hash"]
        # The email lookup item must exist too, or duplicate detection breaks.
        email_item = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": "EMAIL#new@example.com", "sk": "USER"}
        ).get("Item")
        assert email_item is not None
        assert email_item["user_id"] == data["userId"]
        # No credentials in the response beyond the temp token.
        assert "password_hash" not in data

    def test_duplicate_email_409(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="taken@example.com")
        resp = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "taken@example.com", "password": "anything123"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "EMAIL_CONFLICT"

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
        data = r.json()["data"]
        return data["tempToken"], data["userId"]

    def test_success(self, app_client, dynamodb):
        token, user_id = self._get_temp_token(app_client)
        resp = app_client.post(
            "/api/v1/auth/register/step-2",
            json={"temp_token": token, "full_name": "Jane Doe", "role": "marketer"},
        )
        assert resp.status_code == 200
        # Profile fields must be persisted, not just acknowledged.
        user = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"USER#{user_id}", "sk": "PROFILE"}
        ).get("Item")
        assert user["full_name"] == "Jane Doe"
        assert user["role"] == "marketer"
        assert user["registration_step"] == 2

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

    def test_success(self, app_client, dynamodb):
        token = self._setup(app_client)
        resp = app_client.post(
            "/api/v1/auth/register/step-3",
            json={"temp_token": token, "team_name": "Jane's Brand"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "teamId" in data
        assert "tempToken" in data

        # Team profile persisted with starter credits.
        team = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"TEAM#{data['teamId']}", "sk": "PROFILE"}
        ).get("Item")
        assert team is not None
        assert team["team_name"] == "Jane's Brand"
        assert team["ai_credits"] == 500
        # The registering user is recorded as the team OWNER...
        owner_id = team["owner_user_id"]
        member = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"TEAM#{data['teamId']}", "sk": f"MEMBER#{owner_id}"}
        ).get("Item")
        assert member is not None
        assert member["role"] == "owner"
        # ...and the user record is linked back to the team.
        user = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"USER#{owner_id}", "sk": "PROFILE"}
        ).get("Item")
        assert user["team_id"] == data["teamId"]

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

    def test_success(self, app_client, dynamodb):
        token = self._complete_wizard(app_client)
        resp = app_client.post(
            "/api/v1/auth/register/step-4",
            json={"temp_token": token},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data

        # The issued tokens must carry the right identity claims.
        claims = pyjwt.decode(data["accessToken"], options={"verify_signature": False})
        assert claims["user_id"] == data["userId"]
        assert claims["team_id"] == data["teamId"]
        assert claims["token_type"] == "access"
        refresh_claims = pyjwt.decode(data["refreshToken"], options={"verify_signature": False})
        assert refresh_claims["token_type"] == "refresh"
        # Finishing the wizard marks the account verified.
        user = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"USER#{data['userId']}", "sk": "PROFILE"}
        ).get("Item")
        assert user["email_verified"] is True
        assert user["registration_step"] == 4

    def test_skipped_steps_400(self, app_client):
        """A valid temp token without steps 2-3 must not mint real tokens."""
        r1 = app_client.post(
            "/api/v1/auth/register/step-1",
            json={"email": "skipper@example.com", "password": "pass12345"},
        )
        token = r1.json()["data"]["tempToken"]
        resp = app_client.post("/api/v1/auth/register/step-4", json={"temp_token": token})
        assert resp.status_code == 400
        assert resp.json()["code"] == "INCOMPLETE_REGISTRATION"

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
        assert data["accessToken"] != data["refreshToken"]
        # Claims must identify the right user/team and token types.
        access = pyjwt.decode(data["accessToken"], options={"verify_signature": False})
        assert access["token_type"] == "access"
        assert access["user_id"] == data["userId"]
        refresh = pyjwt.decode(data["refreshToken"], options={"verify_signature": False})
        assert refresh["token_type"] == "refresh"
        # Never leak credentials in a response body.
        assert "password_hash" not in resp.text

    def test_wrong_password_401(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="wp@example.com", password="realpass")
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "wp@example.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "INVALID_CREDENTIALS"

    def test_unknown_email_401(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="wp@example.com", password="realpass")
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "anything"},
        )
        assert resp.status_code == 401
        # Anti-enumeration: unknown email and wrong password must be
        # indistinguishable - same status AND same body.
        wrong_pw = app_client.post(
            "/api/v1/auth/login",
            json={"email": "wp@example.com", "password": "wrongpass"},
        )
        assert resp.json() == wrong_pw.json()

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
        assert resp.json()["code"] == "EMAIL_NOT_VERIFIED"

    def test_verified_user_without_team_403(self, app_client, dynamodb):
        """Verified account that never finished team setup cannot log in."""
        user = make_user(
            email="noteam@example.com",
            team_id="",
            password_hash=hash_password("pass123"),
            email_verified=True,
        )
        dynamodb.Table(MAIN_TABLE).put_item(Item=user)
        dynamodb.Table(MAIN_TABLE).put_item(
            Item=make_email_index_item(user["user_id"], "noteam@example.com")
        )
        resp = app_client.post(
            "/api/v1/auth/login",
            json={"email": "noteam@example.com", "password": "pass123"},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "INCOMPLETE_REGISTRATION"


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
        # Rotation must issue a NEW refresh token, distinct from the old one.
        assert "refreshToken" in data
        assert data["refreshToken"] != tokens["refreshToken"]
        assert data["userId"] == tokens["userId"]

    def test_invalid_token_401(self, app_client):
        resp = app_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "bad.token.here"},
        )
        assert resp.status_code == 401

    def test_access_token_rejected_401(self, app_client, dynamodb):
        """An ACCESS token must not be usable on the refresh endpoint."""
        tokens = self._get_tokens(app_client, dynamodb)
        resp = app_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["accessToken"]},
        )
        assert resp.status_code == 401

    def test_used_token_revoked(self, app_client, dynamodb):
        """After a refresh the old token must be revoked."""
        tokens = self._get_tokens(app_client, dynamodb)
        refresh = tokens["refreshToken"]
        # Use it once
        first = app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert first.status_code == 200
        # Use it again — should be 401
        resp = app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 401
        # But the ROTATED token from the first call must still work.
        rotated = first.json()["data"]["refreshToken"]
        again = app_client.post("/api/v1/auth/refresh", json={"refresh_token": rotated})
        assert again.status_code == 200


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
        # Logout must actually REVOKE the token: a 200 alone would also pass
        # if logout did nothing at all.
        reuse = app_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert reuse.status_code == 401
        # Second logout with same token still 200 (idempotent)
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
        # The reset token must never appear in the response body.
        assert "token" not in resp.text.lower()

    def test_known_email_also_200(self, app_client, dynamodb):
        _seed_verified_user(dynamodb, email="known@example.com")
        resp = app_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "known@example.com"},
        )
        assert resp.status_code == 200
        # Anti-enumeration: response for a known email must be identical to
        # the response for an unknown one.
        unknown = app_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "doesnotexist@example.com"},
        )
        assert resp.json() == unknown.json()


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

        # The OLD password must stop working immediately.
        old_login = app_client.post(
            "/api/v1/auth/login",
            json={"email": "reset@example.com", "password": "oldpass"},
        )
        assert old_login.status_code == 401


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
        assert data["email"] == "me@example.com"
        # Internal DynamoDB keys must be stripped alongside the hash.
        assert "password_hash" not in data
        assert "pk" not in data
        assert "sk" not in data

    def test_authenticated_via_bearer_jwt(self, app_client, dynamodb):
        """Exercise the real JWT path (no dev X-User-Id header)."""
        user, _ = _seed_verified_user(dynamodb, email="jwt@example.com", password="pass123")
        login = app_client.post(
            "/api/v1/auth/login",
            json={"email": "jwt@example.com", "password": "pass123"},
        )
        access = login.json()["data"]["accessToken"]
        resp = app_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["user_id"] == user["user_id"]

    def test_unauthenticated_401(self, app_client):
        resp = app_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_garbage_bearer_401(self, app_client):
        resp = app_client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert resp.status_code == 401

    def test_dev_header_ignored_when_disabled(self, app_client, monkeypatch):
        """X-User-Id must be worthless when ENABLE_DEV_AUTH_HEADER is off."""
        import app.api.v1.deps as deps

        monkeypatch.setattr(deps, "ENABLE_DEV_AUTH_HEADER", False)
        resp = app_client.get(
            "/api/v1/auth/me", headers={"X-User-Id": "intruder", "X-Team-Id": "t"}
        )
        assert resp.status_code == 401
