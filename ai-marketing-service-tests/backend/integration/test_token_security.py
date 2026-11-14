"""Integration tests for token-security hardening.

Covers:
  - Fix 2: logout / refresh fail closed (503) when the revocation write fails
  - Fix 3: token_version is enforced; password reset invalidates ALL
           outstanding access and refresh tokens, and repeated resets keep
           incrementing the version (atomic ADD, not a hard-coded value)
  - Fix 6: user-level deny list via tokens_valid_after (one write kills every
           token issued before that moment)
  - Fix 7: regression tests for the original fail-open incident
"""

import jwt as pyjwt
import pytest

from app.auth.password import hash_password
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_email_index_item, make_team, make_user


def _seed_and_login(app_client, dynamodb, *, email, password="pass123"):
    team = make_team()
    user = make_user(
        email=email,
        team_id=team["team_id"],
        password_hash=hash_password(password),
        email_verified=True,
        token_version=1,
    )
    t = dynamodb.Table(MAIN_TABLE)
    t.put_item(Item=team)
    t.put_item(Item=user)
    t.put_item(Item=make_email_index_item(user["user_id"], email))
    r = app_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return user, r.json()["data"]


def _me(app_client, access_token):
    return app_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )


# -- Fix 2: fail closed when the revocation write fails -------------------------

class TestRevocationWriteFailClosed:
    def test_logout_503_when_revoke_write_fails(self, app_client, dynamodb, monkeypatch):
        """Logout must never claim success while the refresh token is still
        alive. A failed revocation write is a 503, not a silent 200."""
        import app.api.v1.auth as auth_routes

        _, tokens = _seed_and_login(app_client, dynamodb, email="lo503@example.com")
        monkeypatch.setattr(auth_routes, "revoke_refresh_token", lambda jti: False)
        resp = app_client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refreshToken"]}
        )
        assert resp.status_code == 503
        assert resp.json()["code"] == "SERVICE_UNAVAILABLE"
        # And indeed the token is still usable - which is exactly why the
        # client must not be told it is logged out.
        monkeypatch.undo()
        still_valid = app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refreshToken"]}
        )
        assert still_valid.status_code == 200

    def test_logout_invalid_token_still_200(self, app_client, dynamodb):
        """Idempotency is preserved: garbage tokens do not 503."""
        resp = app_client.post(
            "/api/v1/auth/logout", json={"refresh_token": "bad.token.here"}
        )
        assert resp.status_code == 200

    def test_refresh_503_when_rotation_revoke_fails(self, app_client, dynamodb, monkeypatch):
        """Rotation must not hand out a new pair while the old refresh token
        could not be revoked (that would leave TWO live refresh tokens)."""
        import app.api.v1.auth as auth_routes

        _, tokens = _seed_and_login(app_client, dynamodb, email="rf503@example.com")
        monkeypatch.setattr(auth_routes, "revoke_refresh_token", lambda jti: False)
        resp = app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refreshToken"]}
        )
        assert resp.status_code == 503
        assert "accessToken" not in resp.text


# -- Fixes 3 + 7: password reset invalidates every outstanding token ------------

class TestPasswordResetInvalidatesTokens:
    def _reset(self, app_client, user, email, new_password):
        from app.auth.jwt import create_temp_token

        resp = app_client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": create_temp_token(user["user_id"], email),
                "new_password": new_password,
            },
        )
        assert resp.status_code == 200

    def test_old_access_token_rejected_after_reset(self, app_client, dynamodb):
        """Regression: this used to return 200 because the tv claim was
        written into tokens but never checked anywhere."""
        user, tokens = _seed_and_login(app_client, dynamodb, email="par@example.com")
        assert _me(app_client, tokens["accessToken"]).status_code == 200

        self._reset(app_client, user, "par@example.com", "newpass456")

        resp = _me(app_client, tokens["accessToken"])
        assert resp.status_code == 401
        assert resp.json()["code"] == "TOKEN_REVOKED"

    def test_old_refresh_token_rejected_after_reset(self, app_client, dynamodb):
        user, tokens = _seed_and_login(app_client, dynamodb, email="prr@example.com")
        self._reset(app_client, user, "prr@example.com", "newpass456")

        resp = app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refreshToken"]}
        )
        assert resp.status_code == 401

    def test_new_login_after_reset_yields_working_tokens(self, app_client, dynamodb):
        user, _ = _seed_and_login(app_client, dynamodb, email="pnl@example.com")
        self._reset(app_client, user, "pnl@example.com", "newpass456")

        login = app_client.post(
            "/api/v1/auth/login",
            json={"email": "pnl@example.com", "password": "newpass456"},
        )
        assert login.status_code == 200
        fresh = login.json()["data"]
        assert _me(app_client, fresh["accessToken"]).status_code == 200
        rotated = app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": fresh["refreshToken"]}
        )
        assert rotated.status_code == 200

    def test_repeated_resets_keep_incrementing_token_version(self, app_client, dynamodb):
        """The old code hard-coded token_version=2, so a SECOND reset did not
        invalidate tokens from after the first one. The atomic increment must
        go 1 -> 2 -> 3."""
        user, _ = _seed_and_login(app_client, dynamodb, email="ptv@example.com")

        self._reset(app_client, user, "ptv@example.com", "secondpass1")
        mid_login = app_client.post(
            "/api/v1/auth/login",
            json={"email": "ptv@example.com", "password": "secondpass1"},
        )
        mid_access = mid_login.json()["data"]["accessToken"]
        assert pyjwt.decode(mid_access, options={"verify_signature": False})["tv"] == 2

        self._reset(app_client, user, "ptv@example.com", "thirdpass12")
        stored = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"USER#{user['user_id']}", "sk": "PROFILE"}
        )["Item"]
        assert stored["token_version"] == 3
        # Tokens minted between the two resets are dead as well.
        assert _me(app_client, mid_access).status_code == 401


# -- Fix 6: user-level deny list via tokens_valid_after --------------------------

class TestTokensValidAfter:
    def test_tokens_issued_before_cutoff_are_rejected(self, app_client, dynamodb):
        """A single tokens_valid_after write invalidates every earlier token,
        even when the tv claim still matches (e.g. legacy users with no
        stored token_version)."""
        user, tokens = _seed_and_login(app_client, dynamodb, email="tva@example.com")
        assert _me(app_client, tokens["accessToken"]).status_code == 200

        iat = pyjwt.decode(tokens["accessToken"], options={"verify_signature": False})["iat"]
        dynamodb.Table(MAIN_TABLE).update_item(
            Key={"pk": f"USER#{user['user_id']}", "sk": "PROFILE"},
            UpdateExpression="SET tokens_valid_after = :t",
            ExpressionAttributeValues={":t": iat + 1},  # cutoff just after issuance
        )
        resp = _me(app_client, tokens["accessToken"])
        assert resp.status_code == 401
        assert resp.json()["code"] == "TOKEN_REVOKED"
        # The refresh token from the same login dies with it.
        refresh = app_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refreshToken"]}
        )
        assert refresh.status_code == 401

    def test_reset_password_stamps_tokens_valid_after(self, app_client, dynamodb):
        from app.auth.jwt import create_temp_token

        user, _ = _seed_and_login(app_client, dynamodb, email="tvs@example.com")
        resp = app_client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": create_temp_token(user["user_id"], "tvs@example.com"),
                "new_password": "newpass456",
            },
        )
        assert resp.status_code == 200
        stored = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": f"USER#{user['user_id']}", "sk": "PROFILE"}
        )["Item"]
        assert int(stored["tokens_valid_after"]) > 0
        assert int(stored["token_version"]) == 2

    def test_token_for_deleted_user_rejected(self, app_client, dynamodb):
        """Tokens must not outlive their user record."""
        user, tokens = _seed_and_login(app_client, dynamodb, email="gone@example.com")
        dynamodb.Table(MAIN_TABLE).delete_item(
            Key={"pk": f"USER#{user['user_id']}", "sk": "PROFILE"}
        )
        assert _me(app_client, tokens["accessToken"]).status_code == 401
