"""Integration tests for /api/v1/ads/* (unified ad reporting & sync)."""

import pytest
from freezegun import freeze_time

from app.db import ad_accounts, ad_stats
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle
from app.services.ads.schema import AdAccountInfo
from backend.fixtures.fakes import FakeConnector, insight, install_fake_connector

P = "meta_ads"


def _seed_stats(team):
    ad_stats.store_insights(team, [
        insight(P, "act_1", "c1", "2024-01-10", spend=30.0, clicks=10, impressions=1000, entity_name="Alpha"),
        insight(P, "act_1", "c1", "2024-01-11", spend=20.0, clicks=5, impressions=500, entity_name="Alpha"),
        insight("google_ads", "g1", "g9", "2024-01-10", spend=80.0, clicks=40, impressions=2000, entity_name="Search"),
    ])


# -- accounts ------------------------------------------------------------------------------

def test_accounts_lists_all_platforms_team_scoped(app_client, dynamodb, auth_headers, test_team_id):
    for plat, acct in ((P, "m1"), ("google_ads", "g1")):
        ad_accounts.upsert_ad_account(test_team_id, AdAccountInfo(platform=plat, account_id=acct, name=acct))
    ad_accounts.upsert_ad_account("other", AdAccountInfo(platform=P, account_id="x", name="x"))
    rows = app_client.get("/api/v1/ads/accounts", headers=auth_headers).json()["data"]
    assert {r["account_id"] for r in rows} == {"m1", "g1"}
    assert all("pk" not in r and "sk" not in r for r in rows)


# -- date-range validation (shared by overview/campaigns/insights) -------------------------

@pytest.mark.parametrize("endpoint", ["overview", "campaigns", "insights"])
@pytest.mark.parametrize("query,code", [
    ("since=2024-13-01", "BAD_DATE"),
    ("since=01/02/2024", "BAD_DATE"),
    ("since=2024-02-01&until=2024-01-01", "BAD_RANGE"),
    ("since=2022-01-01&until=2024-01-01", "BAD_RANGE"),        # > 366 days
])
def test_invalid_ranges_rejected(app_client, auth_headers, endpoint, query, code):
    r = app_client.get(f"/api/v1/ads/{endpoint}?{query}", headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == code


def test_max_range_boundary_accepted(app_client, auth_headers):
    # 2024 is a leap year: Jan 1 2024 -> Jan 1 2025 is exactly 366 days.
    assert app_client.get("/api/v1/ads/overview?since=2024-01-01&until=2025-01-01",
                          headers=auth_headers).status_code == 200


@freeze_time("2024-01-31")
def test_default_range_is_trailing_30_days(app_client, auth_headers):
    data = app_client.get("/api/v1/ads/overview", headers=auth_headers).json()["data"]
    assert (data["since"], data["until"]) == ("2024-01-02", "2024-01-31")


# -- overview / campaigns / insights --------------------------------------------------------

def test_overview_totals_and_breakdown(app_client, dynamodb, auth_headers, test_team_id):
    _seed_stats(test_team_id)
    _seed_stats("other-team")                                  # must not leak in
    d = app_client.get("/api/v1/ads/overview?since=2024-01-01&until=2024-01-31", headers=auth_headers).json()["data"]
    assert d["totals"]["spend"] == 130.0
    assert d["totals"]["ctr"] == round(55 / 3500, 4)
    assert d["by_platform"]["meta_ads"]["spend"] == 50.0
    assert [p["date"] for p in d["timeseries"]] == ["2024-01-10", "2024-01-11"]


def test_campaigns_ranked_and_limited(app_client, dynamodb, auth_headers, test_team_id):
    _seed_stats(test_team_id)
    r = app_client.get("/api/v1/ads/campaigns?since=2024-01-01&until=2024-01-31&limit=1", headers=auth_headers)
    (top,) = r.json()["data"]
    assert (top["campaign_id"], top["name"], top["spend"]) == ("g9", "Search", 80.0)


def test_campaigns_sort_by_ctr(app_client, dynamodb, auth_headers, test_team_id):
    _seed_stats(test_team_id)
    rows = app_client.get("/api/v1/ads/campaigns?since=2024-01-01&until=2024-01-31&sort_by=ctr",
                          headers=auth_headers).json()["data"]
    assert [r["campaign_id"] for r in rows] == ["g9", "c1"]     # 0.02 vs 0.01


def test_campaigns_invalid_sort_422(app_client, auth_headers):
    r = app_client.get("/api/v1/ads/campaigns?sort_by=password", headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "BAD_SORT"


@pytest.mark.parametrize("limit", [0, 201])
def test_campaigns_limit_bounds(app_client, auth_headers, limit):
    assert app_client.get(f"/api/v1/ads/campaigns?limit={limit}", headers=auth_headers).status_code == 422


def test_insights_filters_and_count(app_client, dynamodb, auth_headers, test_team_id):
    _seed_stats(test_team_id)
    d = app_client.get("/api/v1/ads/insights?since=2024-01-01&until=2024-01-31&platform=meta_ads",
                       headers=auth_headers).json()["data"]
    assert d["count"] == 2 and len(d["rows"]) == 2
    assert all(r["platform"] == "meta_ads" for r in d["rows"])
    assert "cpc" in d["rows"][0]                               # derived metrics attached


@pytest.mark.xfail(strict=True, reason=(
    "BUG (API contract): raw insight metrics come straight from DynamoDB as "
    "Decimal and the `-> dict[str, Any]` return annotation makes FastAPI "
    "serialise them as strings (spend='30'), while derived metrics are floats - "
    "chart code summing `spend` would concatenate strings."))
def test_insight_metrics_are_json_numbers(app_client, dynamodb, auth_headers, test_team_id):
    _seed_stats(test_team_id)
    row = app_client.get("/api/v1/ads/insights?since=2024-01-01&until=2024-01-31&platform=google_ads",
                         headers=auth_headers).json()["data"]["rows"][0]
    assert isinstance(row["spend"], (int, float))
    assert isinstance(row["clicks"], int)


@pytest.mark.parametrize("endpoint", ["accounts", "overview", "campaigns", "insights"])
def test_requires_auth(app_client, endpoint):
    assert app_client.get(f"/api/v1/ads/{endpoint}").status_code == 401


# -- live campaigns ---------------------------------------------------------------------------

def test_live_campaigns_not_connected_400(app_client, auth_headers, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector(P))
    r = app_client.get(f"/api/v1/ads/{P}/act_1/campaigns/live", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["code"] == "NOT_CONNECTED"


def test_live_campaigns_from_platform(app_client, dynamodb, auth_headers, test_team_id, monkeypatch):
    fake = install_fake_connector(monkeypatch, FakeConnector(P))
    ad_accounts.save_connection(test_team_id, P, TokenBundle(access_token="t"), connected_by="u")
    r = app_client.get(f"/api/v1/ads/{P}/act_1/campaigns/live", headers=auth_headers)
    assert r.status_code == 200
    (c,) = r.json()["data"]
    assert (c["campaign_id"], c["name"], c["status"]) == ("c1", "Spring", "active")
    assert ("fetch_campaigns", "act_1") in fake.calls


@pytest.mark.parametrize("err", [ConnectorError("upstream 500"), ConnectorAuthError("revoked")])
def test_live_campaigns_platform_error_502(app_client, dynamodb, auth_headers, test_team_id, monkeypatch, err):
    install_fake_connector(monkeypatch, FakeConnector(P, errors={"fetch_campaigns": err}))
    ad_accounts.save_connection(test_team_id, P, TokenBundle(access_token="t"), connected_by="u")
    r = app_client.get(f"/api/v1/ads/{P}/act_1/campaigns/live", headers=auth_headers)
    assert r.status_code == 502
    assert r.json()["code"] == "PLATFORM_ERROR"


@pytest.mark.xfail(strict=True, raises=Exception, reason=(
    "BUG: live_campaigns calls get_connector() outside its try-block, so an "
    "unknown platform raises an unhandled ConnectorConfigError -> HTTP 500 "
    "(integrations.py maps the same case to 404 UNKNOWN_PLATFORM)."))
def test_live_campaigns_unknown_platform_404(app_client, auth_headers):
    r = app_client.get("/api/v1/ads/snapchat_ads/act_1/campaigns/live", headers=auth_headers)
    assert r.status_code == 404


# -- sync -------------------------------------------------------------------------------------

@freeze_time("2024-01-31")
def test_sync_pulls_and_stores(app_client, dynamodb, auth_headers, test_team_id, monkeypatch):
    install_fake_connector(monkeypatch, FakeConnector(P, insights=[insight(P, "act_1", "c1", "2024-01-30", spend=9.0)]))
    ad_accounts.save_connection(test_team_id, P, TokenBundle(access_token="t"), connected_by="u")
    ad_accounts.upsert_ad_account(test_team_id, AdAccountInfo(platform=P, account_id="act_1", name="A"))
    r = app_client.post("/api/v1/ads/sync?lookback_days=7", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["message"] == "Sync complete"
    d = r.json()["data"]
    assert (d["since"], d["until"], d["accounts_synced"], d["rows"]) == ("2024-01-25", "2024-01-31", 1, 1)
    assert ad_stats.summary(test_team_id, "2024-01-01", "2024-01-31")["totals"]["spend"] == 9.0


@pytest.mark.parametrize("days", [0, 367])
def test_sync_lookback_bounds(app_client, auth_headers, days):
    assert app_client.post(f"/api/v1/ads/sync?lookback_days={days}", headers=auth_headers).status_code == 422


def test_sync_requires_auth(app_client):
    assert app_client.post("/api/v1/ads/sync").status_code == 401
