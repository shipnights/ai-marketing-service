"""Unit tests for app/services/ads/sync.py - token lifecycle + insight sync.

Runs against moto DynamoDB (real ad_accounts / ad_stats code) with a
FakeConnector swapped into the registry.
"""

import time

import pytest
from freezegun import freeze_time

from app.db import ad_accounts, ad_stats
from app.services.ads import sync
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle
from app.services.ads.schema import AdAccountInfo
from backend.fixtures.fakes import FakeConnector, insight, install_fake_connector

TEAM = "t1"
P = "meta_ads"


def _connect(token=None, accounts=("act_1",), platform=P):
    ad_accounts.save_connection(TEAM, platform, token or TokenBundle(access_token="live-tok"), connected_by="u1")
    for a in accounts:
        ad_accounts.upsert_ad_account(TEAM, AdAccountInfo(platform=platform, account_id=a, name=a))


# -- date_range -------------------------------------------------------------------------

@freeze_time("2024-03-31 12:00:00")
@pytest.mark.parametrize("days,since", [(1, "2024-03-31"), (7, "2024-03-25"), (30, "2024-03-02"), (0, "2024-03-31")])
def test_date_range_is_inclusive_lookback(days, since):
    assert sync.date_range(days) == (since, "2024-03-31")


@freeze_time("2024-03-31")
def test_date_range_defaults_to_config_lookback():
    assert sync.date_range() == sync.date_range(sync.ADS_SYNC_LOOKBACK_DAYS)


# -- sync_account -------------------------------------------------------------------------

def test_not_connected(dynamodb, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector(P))
    assert sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-31") == {
        "platform": P, "account_id": "act_1", "error": "not_connected"}


def test_happy_path_stores_insights_and_marks_synced(dynamodb, monkeypatch):
    fake = install_fake_connector(monkeypatch, FakeConnector(P, insights=[
        insight(P, "act_1", "c1", "2024-01-02", spend=5.0),
        insight(P, "act_1", "c2", "2024-01-02", spend=7.0),
        insight(P, "act_9", "c9", "2024-01-02", spend=99.0)]))       # other account: not returned
    _connect()
    result = sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-31")
    assert result == {"platform": P, "account_id": "act_1", "rows": 2}
    assert ("fetch_insights", "act_1", "2024-01-01", "2024-01-31", "live-tok") in fake.calls
    assert ad_stats.summary(TEAM, "2024-01-01", "2024-01-31")["totals"]["spend"] == 12.0
    assert ad_accounts.get_ad_account(TEAM, P, "act_1")["last_synced_at"]


def test_valid_token_is_not_refreshed(dynamodb, monkeypatch):
    fake = install_fake_connector(monkeypatch, FakeConnector(P))
    _connect(TokenBundle(access_token="live-tok", refresh_token="r", expires_at=int(time.time()) + 3600))
    sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-01")
    assert not any(c[0] == "refresh" for c in fake.calls)


def test_expired_token_is_refreshed_persisted_and_used(dynamodb, monkeypatch):
    fake = install_fake_connector(monkeypatch, FakeConnector(
        P, refreshed=TokenBundle(access_token="new-tok", refresh_token=None, expires_at=int(time.time()) + 3600)))
    _connect(TokenBundle(access_token="old-tok", refresh_token="keep-me", expires_at=int(time.time()) - 10))
    sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-01")
    assert ("refresh", "keep-me") in fake.calls
    fetch = [c for c in fake.calls if c[0] == "fetch_insights"][0]
    assert fetch[-1] == "new-tok"                     # fetched with the refreshed token
    stored = ad_accounts.load_token(TEAM, P)
    assert stored.access_token == "new-tok"           # persisted for next time
    assert stored.refresh_token == "keep-me"          # platform omitted it -> old one kept


def test_expired_token_without_refresh_token_is_used_as_is(dynamodb, monkeypatch):
    fake = install_fake_connector(monkeypatch, FakeConnector(P))
    _connect(TokenBundle(access_token="old-tok", refresh_token=None, expires_at=int(time.time()) - 10))
    sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-01")
    assert not any(c[0] == "refresh" for c in fake.calls)
    assert [c for c in fake.calls if c[0] == "fetch_insights"][0][-1] == "old-tok"


@pytest.mark.parametrize("failing", ["fetch_insights", "refresh"])
def test_auth_error_marks_connection_for_reconnect(dynamodb, monkeypatch, failing):
    install_fake_connector(monkeypatch, FakeConnector(P, errors={failing: ConnectorAuthError("token revoked")}))
    _connect(TokenBundle(access_token="t", refresh_token="r", expires_at=int(time.time()) - 10))
    result = sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-01")
    assert (result["error"], result["detail"]) == ("auth", "token revoked")
    conn = ad_accounts.get_connection(TEAM, P)
    assert (conn["status"], conn["last_error"]) == ("error", "token revoked")
    assert ad_accounts.get_ad_account(TEAM, P, "act_1")["last_synced_at"] is None


def test_transient_error_does_not_mark_connection(dynamodb, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector(P, errors={"fetch_insights": ConnectorError("503 upstream")}))
    _connect()
    result = sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-01")
    assert (result["error"], result["detail"]) == ("fetch", "503 upstream")
    assert ad_accounts.get_connection(TEAM, P)["status"] == "active"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: sync_account ignores store_insights()' return value. When the write "
    "fails it still reports rows synced and advances last_synced_at, so the gap "
    "is never retried - silent data loss (same swallowed-error class as the "
    "original test-suite incident)."))
def test_storage_failure_is_reported_and_not_marked_synced(dynamodb, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector(P, insights=[insight(P, "act_1")]))
    _connect()
    monkeypatch.setattr(ad_stats, "store_insights", lambda team_id, rows: False)
    result = sync.sync_account(TEAM, P, "act_1", "2024-01-01", "2024-01-31")
    assert "error" in result
    assert ad_accounts.get_ad_account(TEAM, P, "act_1")["last_synced_at"] is None


# -- sync_team ------------------------------------------------------------------------------

@freeze_time("2024-01-31")
def test_sync_team_skips_disabled_and_aggregates(dynamodb, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector(P, insights=[
        insight(P, "act_1", "c1", "2024-01-30"), insight(P, "act_1", "c2", "2024-01-30"),
        insight(P, "act_2", "c3", "2024-01-30"), insight(P, "act_3", "c4", "2024-01-30")]))
    _connect(accounts=("act_1", "act_2", "act_3"))
    ad_accounts.set_account_enabled(TEAM, P, "act_3", False)
    summary = sync.sync_team(TEAM, lookback_days=7)
    assert (summary["since"], summary["until"]) == ("2024-01-25", "2024-01-31")
    assert summary["accounts_synced"] == 2
    assert summary["rows"] == 3
    assert {r["account_id"] for r in summary["results"]} == {"act_1", "act_2"}


def test_sync_team_platform_filter(dynamodb, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector("meta_ads"))
    install_fake_connector(monkeypatch, FakeConnector("google_ads"))
    _connect(platform="meta_ads", accounts=("m1",))
    _connect(platform="google_ads", accounts=("g1",))
    summary = sync.sync_team(TEAM, platform="google_ads")
    assert [r["account_id"] for r in summary["results"]] == ["g1"]


def test_sync_team_partial_failure_counts_only_successes(dynamodb, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector("meta_ads", insights=[insight("meta_ads", "m1")]))
    install_fake_connector(monkeypatch, FakeConnector("google_ads", errors={"fetch_insights": ConnectorError("x")}))
    _connect(platform="meta_ads", accounts=("m1",))
    _connect(platform="google_ads", accounts=("g1",))
    summary = sync.sync_team(TEAM)
    assert summary["accounts_synced"] == 1
    assert summary["rows"] == 1
    assert len(summary["results"]) == 2


def test_sync_team_with_no_accounts(dynamodb):
    summary = sync.sync_team(TEAM)
    assert (summary["accounts_synced"], summary["rows"], summary["results"]) == (0, 0, [])
