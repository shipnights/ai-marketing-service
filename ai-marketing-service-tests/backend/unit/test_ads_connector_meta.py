"""Unit tests for the Meta Ads connector (Graph / Marketing API), HTTP mocked
with respx - any unmocked request fails the test."""

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import app.services.ads.meta as meta
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle

BASE = "https://graph.facebook.com/v21.0"
TOKEN = TokenBundle(access_token="tok-123")


@pytest.fixture
def connector(monkeypatch):
    monkeypatch.setattr(meta, "META_APP_ID", "app-1")
    monkeypatch.setattr(meta, "META_APP_SECRET", "secret-1")
    monkeypatch.setattr(meta, "META_ADS_REDIRECT_URI", "https://api.example/cb")
    return meta.MetaAdsConnector()


def _q(request):
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


# -- OAuth ------------------------------------------------------------------------------

def test_is_configured_requires_all_credentials(monkeypatch, connector):
    assert connector.is_configured
    monkeypatch.setattr(meta, "META_APP_SECRET", "")
    assert not connector.is_configured


def test_authorize_url(connector):
    url = connector.authorize_url("st4te")
    assert url.startswith("https://www.facebook.com/v21.0/dialog/oauth?")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q == {"client_id": "app-1", "redirect_uri": "https://api.example/cb", "state": "st4te",
                 "scope": "ads_read,ads_management,business_management", "response_type": "code"}


def test_authorize_url_redirect_override(connector):
    assert "redirect_uri=https%3A%2F%2Fother%2Fcb" in connector.authorize_url("s", "https://other/cb")


def test_exchange_code_two_step_long_lived(connector, respx_mock):
    route = respx_mock.get(f"{BASE}/oauth/access_token").mock(side_effect=[
        httpx.Response(200, json={"access_token": "short-tok"}),
        httpx.Response(200, json={"access_token": "long-tok", "expires_in": 5_184_000}),
    ])
    tok = connector.exchange_code("the-code")
    assert route.call_count == 2
    step1, step2 = _q(route.calls[0].request), _q(route.calls[1].request)
    assert step1["code"] == "the-code" and step1["client_secret"] == "secret-1"
    assert step2["grant_type"] == "fb_exchange_token" and step2["fb_exchange_token"] == "short-tok"
    assert tok.access_token == "long-tok"
    assert tok.refresh_token == "long-tok"            # Meta: long-lived token is the refresh seed
    assert tok.expires_at == pytest.approx(int(time.time()) + 5_184_000, abs=5)


def test_exchange_code_missing_access_token_raises(connector, respx_mock):
    respx_mock.get(f"{BASE}/oauth/access_token").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ConnectorError, match="no access_token"):
        connector.exchange_code("c")


def test_exchange_code_network_error_raises_connector_error(connector, respx_mock):
    respx_mock.get(f"{BASE}/oauth/access_token").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ConnectorError):
        connector.exchange_code("c")


def test_refresh_reexchanges_long_lived_token(connector, respx_mock):
    route = respx_mock.get(f"{BASE}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "extended"}))
    tok = connector.refresh("old-long")
    assert _q(route.calls[0].request)["fb_exchange_token"] == "old-long"
    assert tok.access_token == "extended"
    assert tok.expires_at is None                     # no expires_in -> unknown


# -- error mapping ---------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_http_auth_errors_map_to_auth_error(connector, respx_mock, status):
    respx_mock.get(f"{BASE}/me/adaccounts").mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(ConnectorAuthError):
        connector.list_ad_accounts(TOKEN)


@pytest.mark.parametrize("code", [190, 102, 463, 467])
def test_oauth_error_codes_map_to_auth_error(connector, respx_mock, code):
    respx_mock.get(f"{BASE}/me/adaccounts").mock(
        return_value=httpx.Response(400, json={"error": {"code": code, "message": "bad token"}}))
    with pytest.raises(ConnectorAuthError):
        connector.list_ad_accounts(TOKEN)


def test_other_api_errors_are_generic(connector, respx_mock):
    respx_mock.get(f"{BASE}/me/adaccounts").mock(
        return_value=httpx.Response(400, json={"error": {"code": 100, "message": "bad param"}}))
    with pytest.raises(ConnectorError) as exc:
        connector.list_ad_accounts(TOKEN)
    assert not isinstance(exc.value, ConnectorAuthError)


def test_non_json_5xx_is_generic_error(connector, respx_mock):
    respx_mock.get(f"{BASE}/me/adaccounts").mock(return_value=httpx.Response(502, text="<html>"))
    with pytest.raises(ConnectorError):
        connector.list_ad_accounts(TOKEN)


# -- data -------------------------------------------------------------------------------

def test_list_ad_accounts_follows_paging_and_maps_status(connector, respx_mock):
    next_url = f"{BASE}/me/adaccounts?after=CURSOR2&access_token=tok-123"
    route = respx_mock.get(f"{BASE}/me/adaccounts").mock(side_effect=[
        httpx.Response(200, json={
            "data": [{"id": "act_1", "account_id": "1", "name": "One", "currency": "EUR",
                      "account_status": 1, "timezone_name": "Europe/Paris"}],
            "paging": {"next": next_url}}),
        httpx.Response(200, json={"data": [{"account_id": "2", "account_status": 2}]}),
    ])
    accts = connector.list_ad_accounts(TOKEN)
    assert route.call_count == 2
    first = _q(route.calls[0].request)
    assert first["access_token"] == "tok-123" and first["limit"] == "200"
    assert "after=CURSOR2" in str(route.calls[1].request.url)
    a1, a2 = accts
    assert (a1.account_id, a1.name, a1.currency, a1.status, a1.timezone) == (
        "act_1", "One", "EUR", "active", "Europe/Paris")
    assert a2.account_id == "act_2"                   # synthesized from account_id
    assert a2.status == "disabled"


def test_fetch_campaigns_converts_cents_to_major_units(connector, respx_mock):
    respx_mock.get(f"{BASE}/act_1/campaigns").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "c1", "name": "Spring", "status": "ACTIVE", "objective": "SALES",
         "daily_budget": "2550", "start_time": "2024-01-01"},
        {"id": "c2", "name": "Lifetime", "status": "PAUSED", "lifetime_budget": "100000"},
    ]}))
    c1, c2 = connector.fetch_campaigns(TOKEN, "act_1")
    assert (c1.campaign_id, c1.status, c1.objective, c1.daily_budget) == ("c1", "active", "SALES", 25.5)
    assert c1.lifetime_budget is None
    assert (c2.status, c2.lifetime_budget, c2.daily_budget) == ("paused", 1000.0, None)


def test_fetch_insights_daily_rows_and_conversion_actions(connector, respx_mock):
    route = respx_mock.get(f"{BASE}/act_1/insights").mock(return_value=httpx.Response(200, json={"data": [{
        "campaign_id": "c1", "campaign_name": "Spring", "date_start": "2024-01-05",
        "spend": "12.34", "impressions": "1000", "clicks": "25", "reach": "900",
        "actions": [{"action_type": "purchase", "value": "3"},
                    {"action_type": "lead", "value": "2"},
                    {"action_type": "link_click", "value": "25"}],      # not a conversion
        "action_values": [{"action_type": "purchase", "value": "150.5"}],
    }]}))
    (row,) = connector.fetch_insights(TOKEN, "act_1", "2024-01-01", "2024-01-07")
    q = _q(route.calls[0].request)
    assert json.loads(q["time_range"]) == {"since": "2024-01-01", "until": "2024-01-07"}
    assert q["time_increment"] == "1" and q["level"] == "campaign"
    assert (row.platform, row.account_id, row.entity_id, row.entity_name, row.date) == (
        "meta_ads", "act_1", "c1", "Spring", "2024-01-05")
    assert (row.spend, row.impressions, row.clicks, row.reach) == (12.34, 1000, 25, 900)
    assert row.conversions == 5.0                     # purchase + lead; link_click excluded
    assert row.conversion_value == 150.5


def test_fetch_insights_tolerates_missing_and_garbage_values(connector, respx_mock):
    respx_mock.get(f"{BASE}/act_1/insights").mock(return_value=httpx.Response(200, json={"data": [
        {"campaign_id": "c1", "spend": None, "impressions": "n/a", "actions": "not-a-list"}]}))
    (row,) = connector.fetch_insights(TOKEN, "act_1", "2024-01-01", "2024-01-01")
    assert (row.spend, row.impressions, row.conversions) == (0.0, 0, 0.0)
    assert row.date == "2024-01-01"                   # falls back to `since`


def test_helpers():
    assert meta._cents_to_major("") is None and meta._cents_to_major("abc") is None
    assert meta._cents_to_major("199") == 1.99
    assert meta._to_int("12.7") == 12 and meta._to_float("x") == 0.0
