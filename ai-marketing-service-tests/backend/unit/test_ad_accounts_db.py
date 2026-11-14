"""Unit tests for app/db/ad_accounts.py - OAuth connections & ad accounts."""

import pytest
from cryptography.fernet import Fernet

import app.utils.crypto as crypto
from app.db import ad_accounts as db
from app.db.tables import ACCOUNTS_TABLE
from app.services.ads.base import TokenBundle
from app.services.ads.schema import AdAccountInfo

TEAM = "t1"


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "_fernet", None)


def _raw(dynamodb, sk):
    return dynamodb.Table(ACCOUNTS_TABLE).get_item(Key={"pk": f"TEAM#{TEAM}", "sk": sk}).get("Item")


def _token(access="acc-1", refresh="ref-1", expires_at=1_900_000_000):
    return TokenBundle(access_token=access, refresh_token=refresh, expires_at=expires_at, scope="ads_read")


# -- connections -------------------------------------------------------------------

@pytest.mark.usefixtures("encryption_key")
def test_save_connection_encrypts_tokens_at_rest(dynamodb):
    assert db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1") is True
    raw = _raw(dynamodb, "ADCONN#meta_ads")
    assert raw["access_token_enc"].startswith("enc:")
    assert raw["refresh_token_enc"].startswith("enc:")
    assert "acc-1" not in str(raw) and "ref-1" not in str(raw)   # no plaintext anywhere
    assert raw["status"] == "active"
    assert raw["connected_by"] == "u1"
    assert raw["scope"] == "ads_read"


@pytest.mark.usefixtures("encryption_key")
def test_load_token_decrypts_roundtrip(dynamodb):
    db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1")
    tok = db.load_token(TEAM, "meta_ads")
    assert (tok.access_token, tok.refresh_token, tok.expires_at, tok.scope) == (
        "acc-1", "ref-1", 1_900_000_000, "ads_read")
    assert isinstance(tok.expires_at, int)            # Decimal from DynamoDB is coerced


def test_load_token_not_connected_returns_none(dynamodb):
    assert db.load_token(TEAM, "meta_ads") is None


def test_load_token_undecryptable_returns_none(dynamodb, monkeypatch):
    """Stored ciphertext that can no longer be decrypted (key lost/rotated)
    yields None so callers treat the platform as disconnected."""
    dynamodb.Table(ACCOUNTS_TABLE).put_item(Item={
        "pk": f"TEAM#{TEAM}", "sk": "ADCONN#meta_ads", "platform": "meta_ads",
        "access_token_enc": "enc:garbage"})
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "_fernet", None)
    assert db.load_token(TEAM, "meta_ads") is None


def test_save_connection_scope_override_wins(dynamodb):
    db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1", scopes="ads_management")
    assert _raw(dynamodb, "ADCONN#meta_ads")["scope"] == "ads_management"


def test_reconnect_preserves_connected_at_and_clears_error(dynamodb):
    db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1")
    first = _raw(dynamodb, "ADCONN#meta_ads")
    db.mark_connection_error(TEAM, "meta_ads", "token expired")
    assert _raw(dynamodb, "ADCONN#meta_ads")["status"] == "error"

    db.save_connection(TEAM, "meta_ads", _token(access="acc-2"), connected_by="u2")
    again = _raw(dynamodb, "ADCONN#meta_ads")
    assert again["connected_at"] == first["connected_at"]   # original connect time kept
    assert again["status"] == "active"
    assert again["last_error"] is None
    assert db.load_token(TEAM, "meta_ads").access_token == "acc-2"


def test_mark_connection_error_truncates_message(dynamodb):
    db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1")
    db.mark_connection_error(TEAM, "meta_ads", "x" * 2000)
    assert len(_raw(dynamodb, "ADCONN#meta_ads")["last_error"]) == 500


def test_store_refreshed_token_replaces_tokens_and_reactivates(dynamodb):
    db.save_connection(TEAM, "google_ads", _token(), connected_by="u1")
    db.mark_connection_error(TEAM, "google_ads", "expired")
    db.store_refreshed_token(TEAM, "google_ads", _token(access="acc-new", expires_at=2_000_000_000))
    tok = db.load_token(TEAM, "google_ads")
    assert tok.access_token == "acc-new"
    assert tok.expires_at == 2_000_000_000
    raw = _raw(dynamodb, "ADCONN#google_ads")
    assert raw["status"] == "active" and raw["last_error"] is None


def test_list_connections_only_returns_connections_for_team(dynamodb):
    db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1")
    db.save_connection(TEAM, "google_ads", _token(), connected_by="u1")
    db.save_connection("other", "reddit_ads", _token(), connected_by="u9")
    db.upsert_ad_account(TEAM, AdAccountInfo(platform="meta_ads", account_id="a1", name="A"))
    assert {c["platform"] for c in db.list_connections(TEAM)} == {"meta_ads", "google_ads"}


# -- ad accounts -------------------------------------------------------------------

def _info(platform="meta_ads", account_id="act_1", name="Main", **kw):
    return AdAccountInfo(platform=platform, account_id=account_id, name=name, **kw)


def test_upsert_new_account_defaults_enabled(dynamodb):
    assert db.upsert_ad_account(TEAM, _info(currency="EUR", timezone="Europe/Berlin")) is True
    acct = db.get_ad_account(TEAM, "meta_ads", "act_1")
    assert acct["enabled"] is True
    assert acct["currency"] == "EUR"
    assert acct["timezone"] == "Europe/Berlin"
    assert acct["last_synced_at"] is None


def test_upsert_preserves_operator_choices_across_rediscovery(dynamodb):
    """Re-discovery must not re-enable an account the operator disabled, nor
    reset its sync cursor or creation time - but it must refresh metadata."""
    db.upsert_ad_account(TEAM, _info(name="Old name"))
    db.set_account_enabled(TEAM, "meta_ads", "act_1", False)
    db.mark_account_synced(TEAM, "meta_ads", "act_1")
    before = db.get_ad_account(TEAM, "meta_ads", "act_1")

    db.upsert_ad_account(TEAM, _info(name="New name", status="disabled"))
    after = db.get_ad_account(TEAM, "meta_ads", "act_1")
    assert after["enabled"] is False
    assert after["last_synced_at"] == before["last_synced_at"]
    assert after["created_at"] == before["created_at"]
    assert after["name"] == "New name"
    assert after["account_status"] == "disabled"


def test_list_ad_accounts_platform_filter(dynamodb):
    db.upsert_ad_account(TEAM, _info("meta_ads", "m1"))
    db.upsert_ad_account(TEAM, _info("google_ads", "g1"))
    db.upsert_ad_account(TEAM, _info("google_ads", "g2"))
    assert {a["account_id"] for a in db.list_ad_accounts(TEAM)} == {"m1", "g1", "g2"}
    assert {a["account_id"] for a in db.list_ad_accounts(TEAM, "google_ads")} == {"g1", "g2"}
    assert db.list_ad_accounts(TEAM, "reddit_ads") == []


def test_mark_account_synced_sets_timestamp(dynamodb):
    db.upsert_ad_account(TEAM, _info())
    db.mark_account_synced(TEAM, "meta_ads", "act_1")
    assert db.get_ad_account(TEAM, "meta_ads", "act_1")["last_synced_at"]


def test_disconnect_removes_connection_and_only_that_platforms_accounts(dynamodb):
    db.save_connection(TEAM, "meta_ads", _token(), connected_by="u1")
    db.save_connection(TEAM, "google_ads", _token(), connected_by="u1")
    db.upsert_ad_account(TEAM, _info("meta_ads", "m1"))
    db.upsert_ad_account(TEAM, _info("meta_ads", "m2"))
    db.upsert_ad_account(TEAM, _info("google_ads", "g1"))

    assert db.disconnect(TEAM, "meta_ads") is True
    assert db.get_connection(TEAM, "meta_ads") is None
    assert db.list_ad_accounts(TEAM, "meta_ads") == []
    # Other platform untouched.
    assert db.get_connection(TEAM, "google_ads") is not None
    assert [a["account_id"] for a in db.list_ad_accounts(TEAM, "google_ads")] == ["g1"]


@pytest.mark.xfail(strict=True, reason=(
    "BUG: set_account_enabled() uses update_item, which UPSERTS. Toggling a "
    "nonexistent account creates a phantom ADACCT row and returns it, so the "
    "route's 404 branch is unreachable. Needs ConditionExpression="
    "attribute_exists(pk)."))
def test_set_enabled_on_missing_account_returns_none_and_creates_nothing(dynamodb):
    assert db.set_account_enabled(TEAM, "meta_ads", "ghost", False) is None
    assert db.get_ad_account(TEAM, "meta_ads", "ghost") is None
