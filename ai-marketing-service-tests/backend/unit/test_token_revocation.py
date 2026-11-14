"""Unit tests for the token-revocation store and token-lifetime hardening.

Covers:
  - fail-closed revocation READ (store unreadable => token treated as revoked)
  - revocation WRITE failures are reported, not swallowed
  - revocation items carry a numeric `ttl` for DynamoDB TTL cleanup
  - short access-token lifetime default
"""

import time
import uuid

import jwt as pyjwt
import pytest
from botocore.exceptions import ClientError

from app.auth.jwt import create_refresh_token, decode_refresh_token
from app.db.tables import MAIN_TABLE


USER_ID = f"user-{uuid.uuid4().hex[:8]}"
TEAM_ID = f"team-{uuid.uuid4().hex[:8]}"
EMAIL = "revoke@example.com"


def _client_error(op: str = "GetItem") -> ClientError:
    return ClientError(
        {"Error": {"Code": "InternalServerError", "Message": "simulated outage"}}, op
    )


# -- Fix 1: revocation check fails CLOSED ------------------------------------

class TestRevocationFailsClosed:
    def test_is_revoked_returns_true_when_store_unreadable(self, dynamodb, monkeypatch):
        import app.db.tokens as tokens

        def _boom(*args, **kwargs):
            raise _client_error()

        monkeypatch.setattr(tokens, "get_item_strict", _boom)
        # Unknown jti + broken store => assume revoked (True), never False.
        assert tokens.is_refresh_token_revoked("any-jti") is True

    def test_revoked_token_still_rejected_during_store_outage(self, dynamodb, monkeypatch):
        """The original fail-open bug: a revoked refresh token became valid
        again while the revocation store was unreadable."""
        import app.db.tokens as tokens

        token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
        payload = decode_refresh_token(token)
        assert payload is not None
        assert tokens.revoke_refresh_token(payload["jti"]) is True
        assert decode_refresh_token(token) is None  # healthy store: rejected

        def _boom(*args, **kwargs):
            raise _client_error()

        monkeypatch.setattr(tokens, "get_item_strict", _boom)
        # Store down: the token must STILL be rejected.
        assert decode_refresh_token(token) is None

    def test_unrevoked_token_also_rejected_during_outage(self, dynamodb, monkeypatch):
        """Fail-closed is deliberately strict: during an outage we cannot
        prove ANY token is un-revoked, so none may pass."""
        import app.db.tokens as tokens

        token = create_refresh_token(USER_ID, TEAM_ID, EMAIL)
        assert decode_refresh_token(token) is not None

        def _boom(*args, **kwargs):
            raise _client_error()

        monkeypatch.setattr(tokens, "get_item_strict", _boom)
        assert decode_refresh_token(token) is None


# -- Fix 2: revocation WRITE failures are visible ------------------------------

class TestRevocationWriteFailure:
    def test_revoke_returns_false_when_write_fails(self, dynamodb, monkeypatch):
        import app.db.tokens as tokens

        monkeypatch.setattr(tokens, "put_item", lambda *a, **k: False)
        assert tokens.revoke_refresh_token("some-jti") is False

    def test_revoke_returns_true_and_persists(self, dynamodb):
        import app.db.tokens as tokens

        assert tokens.revoke_refresh_token("jti-ok") is True
        assert tokens.is_refresh_token_revoked("jti-ok") is True
        assert tokens.is_refresh_token_revoked("jti-other") is False


# -- Fix 5: revocation items are TTL-reapable ----------------------------------

class TestRevocationTtl:
    def test_revocation_item_has_numeric_ttl(self, dynamodb):
        from app.db.tokens import revoke_refresh_token

        before = int(time.time())
        assert revoke_refresh_token("jti-ttl", expires_in_days=30) is True
        item = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": "REVOKED_TOKEN#jti-ttl", "sk": "REVOKED"}
        )["Item"]
        # DynamoDB TTL requires a NUMERIC epoch attribute - an ISO string is
        # invisible to the TTL reaper and the table grows forever.
        assert int(item["ttl"]) == pytest.approx(before + 30 * 24 * 3600, abs=60)
        # Human-readable companions are still present.
        assert "revoked_at" in item and "expires_at" in item

    def test_create_tables_script_enables_ttl(self, dynamodb):
        """enable_ttl() turns TTL on for the main table (attribute `ttl`)."""
        import importlib

        import scripts.create_tables as ct

        importlib.reload(ct)  # rebind its module-level boto3 client under moto
        ct.enable_ttl(MAIN_TABLE)
        desc = ct.dynamodb.describe_time_to_live(TableName=MAIN_TABLE)[
            "TimeToLiveDescription"
        ]
        assert desc["TimeToLiveStatus"] in ("ENABLED", "ENABLING")
        assert desc["AttributeName"] == "ttl"
        # Idempotent: calling again must not raise.
        ct.enable_ttl(MAIN_TABLE)


# -- Fix 4: short-lived access tokens ------------------------------------------

class TestAccessTokenLifetime:
    def test_default_access_token_lifetime_is_30_minutes(self):
        from app.config import JWT_EXPIRE_MINUTES

        assert JWT_EXPIRE_MINUTES == 30

    def test_issued_access_token_expires_within_an_hour(self):
        from app.auth.jwt import create_access_token

        token = create_access_token(USER_ID, TEAM_ID, EMAIL)
        claims = pyjwt.decode(token, options={"verify_signature": False})
        # Leaked access tokens are not checked against the revocation store,
        # so their lifetime must stay short (sessions persist via refresh).
        assert claims["exp"] - claims["iat"] <= 3600
