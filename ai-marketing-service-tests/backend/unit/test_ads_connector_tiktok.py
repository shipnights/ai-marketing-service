"""Unit tests for the TikTok Ads connector (Business API v1.3), HTTP mocked."""

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import app.services.ads.tiktok as tiktok
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle

BASE = "https://business-api.tiktok.com/open_api/v1.3"
TOKEN = TokenBundle(access_token="tt-access")


@pytest.fixture
def connector(monkeypatch):
    monkeypatch.setattr(tiktok, "TIKTOK_ADS_APP_ID", "app-7")
    monkeypatch.setattr(tiktok, "TIKTOK_ADS_SECRET", "sec-7")
    monkeypatch.setattr(tiktok, "TIKTOK_ADS_REDIRECT_URI", "https://api.example/cb")
    return tiktok.TikTokAdsConnector()


def ok(data):
    return httpx.Response(200, json={"code": 0, "message": "OK", "data": data})


def _q(request):
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


# -- OAuth --------------------------------------------------------------------------------

def test_authorize_url(connector):
    url = connector.authorize_url("st")
    assert url.startswith("https://business-api.tiktok.com/portal/auth?")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q == {"app_id": "app-7", "redirect_uri": "https://api.example/cb", "state": "st"}


def test_exchange_code_long_lived_token(connector, respx_mock):
    route = respx_mock.post(f"{BASE}/oauth2/access_token/").mock(
        return_value=ok({"access_token": "tt-1", "scope": [4, 5], "advertiser_ids": ["1"]}))
    tok = connector.exchange_code("auth-code")
    assert json.loads(route.calls[0].request.content) == {
        "app_id": "app-7", "secret": "sec-7", "auth_code": "auth-code"}
    assert tok.access_token == "tt-1"
    assert tok.refresh_token is None and tok.expires_at is None     # long-lived, no refresh
    assert tok.scope == "4,5"


def test_exchange_code_missing_token(connector, respx_mock):
    respx_mock.post(f"{BASE}/oauth2/access_token/").mock(return_value=ok({}))
    with pytest.raises(ConnectorError, match="no access_token"):
        connector.exchange_code("c")


def test_refresh_is_unsupported_without_network(connector):
    """TikTok tokens don't refresh; must fail fast (respx would flag any call)."""
    with pytest.raises(ConnectorError, match="reconnect"):
        connector.refresh("anything")


# -- envelope / error mapping ---------------------------------------------------------------

@pytest.mark.parametrize("code", sorted(tiktok._AUTH_CODES))
def test_auth_envelope_codes_map_to_auth_error(connector, respx_mock, code):
    respx_mock.get(f"{BASE}/campaign/get/").mock(
        return_value=httpx.Response(200, json={"code": code, "message": "token expired"}))
    with pytest.raises(ConnectorAuthError):
        connector.fetch_campaigns(TOKEN, "adv1")


def test_non_auth_envelope_code_is_generic(connector, respx_mock):
    respx_mock.get(f"{BASE}/campaign/get/").mock(
        return_value=httpx.Response(200, json={"code": 40002, "message": "bad param"}))
    with pytest.raises(ConnectorError) as exc:
        connector.fetch_campaigns(TOKEN, "adv1")
    assert not isinstance(exc.value, ConnectorAuthError)


@pytest.mark.parametrize("response,exc", [
    (httpx.Response(401), ConnectorAuthError),
    (httpx.Response(502, text="<html>gateway</html>"), ConnectorError),      # non-JSON
    (httpx.Response(500, json={"code": 0, "data": {}}), ConnectorError),      # code 0 but HTTP error
])
def test_http_level_errors(connector, respx_mock, response, exc):
    respx_mock.get(f"{BASE}/campaign/get/").mock(return_value=response)
    with pytest.raises(exc):
        connector.fetch_campaigns(TOKEN, "adv1")


def test_network_error(connector, respx_mock):
    respx_mock.get(f"{BASE}/campaign/get/").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ConnectorError):
        connector.fetch_campaigns(TOKEN, "adv1")


# -- data ---------------------------------------------------------------------------------

def test_list_ad_accounts_discovers_then_hydrates(connector, respx_mock):
    discover = respx_mock.get(f"{BASE}/oauth2/advertiser/get/").mock(return_value=ok(
        {"list": [{"advertiser_id": 111}, {"advertiser_id": 222}, {"advertiser_name": "no id"}]}))
    info = respx_mock.get(f"{BASE}/advertiser/info/").mock(return_value=ok({"list": [
        {"advertiser_id": 111, "name": "Brand", "currency": "EUR", "status": "STATUS_ENABLE", "timezone": "UTC"},
        {"advertiser_id": 222, "name": "Old", "status": "STATUS_DISABLE"}]}))
    a, b = connector.list_ad_accounts(TOKEN)
    dq = _q(discover.calls[0].request)
    assert (dq["app_id"], dq["secret"], dq["access_token"]) == ("app-7", "sec-7", "tt-access")
    iq = _q(info.calls[0].request)
    assert json.loads(iq["advertiser_ids"]) == ["111", "222"]      # JSON-encoded list param
    assert info.calls[0].request.headers["Access-Token"] == "tt-access"
    assert (a.account_id, a.name, a.currency, a.status) == ("111", "Brand", "EUR", "active")
    assert b.status == "disabled"


def test_list_ad_accounts_none_authorized_skips_hydration(connector, respx_mock):
    respx_mock.get(f"{BASE}/oauth2/advertiser/get/").mock(return_value=ok({"list": []}))
    assert connector.list_ad_accounts(TOKEN) == []       # no /advertiser/info/ call made


def test_fetch_campaigns_status_and_budget(connector, respx_mock):
    route = respx_mock.get(f"{BASE}/campaign/get/").mock(return_value=ok({"list": [
        {"campaign_id": 1, "campaign_name": "A", "operation_status": "ENABLE", "objective_type": "CONVERSIONS", "budget": "50.5"},
        {"campaign_id": 2, "campaign_name": "B", "operation_status": "DISABLE", "budget": ""}]}))
    a, b = connector.fetch_campaigns(TOKEN, "adv1")
    assert _q(route.calls[0].request)["advertiser_id"] == "adv1"
    assert (a.campaign_id, a.status, a.objective, a.daily_budget) == ("1", "active", "CONVERSIONS", 50.5)
    assert (b.status, b.daily_budget) == ("paused", None)


def test_fetch_insights_parses_daily_report(connector, respx_mock):
    route = respx_mock.get(f"{BASE}/report/integrated/get/").mock(return_value=ok({"list": [{
        "dimensions": {"campaign_id": 77, "stat_time_day": "2024-01-05 00:00:00"},
        "metrics": {"spend": "9.99", "impressions": "500", "clicks": "12", "conversion": "2",
                    "video_play_actions": "300"}}]}))
    (row,) = connector.fetch_insights(TOKEN, "adv1", "2024-01-01", "2024-01-07")
    q = _q(route.calls[0].request)
    assert json.loads(q["dimensions"]) == ["campaign_id", "stat_time_day"]
    assert (q["start_date"], q["end_date"], q["data_level"]) == ("2024-01-01", "2024-01-07", "AUCTION_CAMPAIGN")
    assert row.date == "2024-01-05"                      # time part stripped
    assert (row.entity_id, row.spend, row.impressions, row.clicks, row.conversions, row.video_views) == (
        "77", 9.99, 500, 12, 2.0, 300)


@pytest.mark.xfail(strict=True, reason=(
    "BUG (data quality): fetch_insights reads metrics['campaign_name'] but never "
    "requests it, so every TikTok insight row has an empty entity_name and the "
    "'top campaigns' dashboard shows nameless TikTok campaigns."))
def test_fetch_insights_requests_campaign_name(connector, respx_mock):
    route = respx_mock.get(f"{BASE}/report/integrated/get/").mock(return_value=ok({"list": []}))
    connector.fetch_insights(TOKEN, "adv1", "2024-01-01", "2024-01-01")
    assert "campaign_name" in json.loads(_q(route.calls[0].request)["metrics"])
