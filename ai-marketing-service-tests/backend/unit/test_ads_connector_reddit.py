"""Unit tests for the Reddit Ads connector (Ads API v3 + OAuth2), HTTP mocked."""

import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import app.services.ads.reddit as reddit
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle

API = "https://ads-api.reddit.com/api/v3"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
TOKEN = TokenBundle(access_token="rd-access")


@pytest.fixture
def connector(monkeypatch):
    monkeypatch.setattr(reddit, "REDDIT_ADS_CLIENT_ID", "rcid")
    monkeypatch.setattr(reddit, "REDDIT_ADS_CLIENT_SECRET", "rsec")
    monkeypatch.setattr(reddit, "REDDIT_ADS_REDIRECT_URI", "https://api.example/cb")
    monkeypatch.setattr(reddit, "REDDIT_USER_AGENT", "ai-marketing-tests/1.0")
    return reddit.RedditAdsConnector()


# -- OAuth ---------------------------------------------------------------------------------

def test_authorize_url_requests_permanent_access(connector):
    q = {k: v[0] for k, v in parse_qs(urlparse(connector.authorize_url("s")).query).items()}
    assert q["duration"] == "permanent"          # needed for a refresh token
    assert q["scope"] == "adsread adsedit"
    assert (q["client_id"], q["state"], q["response_type"]) == ("rcid", "s", "code")


def test_token_request_uses_basic_auth_and_user_agent(connector, respx_mock):
    route = respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": "adsread"}))
    tok = connector.exchange_code("code-1")
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"rcid:rsec").decode()
    assert req.headers["User-Agent"] == "ai-marketing-tests/1.0"
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert form == {"grant_type": "authorization_code", "code": "code-1", "redirect_uri": "https://api.example/cb"}
    assert (tok.access_token, tok.refresh_token, tok.scope) == ("a", "r", "adsread")


def test_refresh_reattaches_refresh_token(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "a2"}))
    assert connector.refresh("r-old").refresh_token == "r-old"


@pytest.mark.parametrize("status", [401, 403])
def test_token_rejected_is_auth_error(connector, respx_mock, status):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(ConnectorAuthError):
        connector.refresh("r")


def test_token_error_in_200_body_is_error(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"error": "unsupported_grant_type"}))
    with pytest.raises(ConnectorError):
        connector.refresh("r")


@pytest.mark.xfail(strict=True, reason=(
    "BUG: Reddit answers a revoked/expired refresh token with 400 "
    "{'error': 'invalid_grant'}, which is raised as a generic ConnectorError - so "
    "sync reports a transient 'fetch' error forever instead of flagging the "
    "connection for reconnect (Google maps invalid_grant to ConnectorAuthError)."))
def test_invalid_grant_is_auth_error(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(ConnectorAuthError):
        connector.refresh("revoked")


def test_token_network_error(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ConnectorError):
        connector.exchange_code("c")


# -- data -------------------------------------------------------------------------------------

def test_api_calls_send_bearer_and_user_agent(connector, respx_mock):
    route = respx_mock.get(f"{API}/ad_accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    connector.list_ad_accounts(TOKEN)
    h = route.calls[0].request.headers
    assert h["Authorization"] == "Bearer rd-access"
    assert h["User-Agent"] == "ai-marketing-tests/1.0"


def test_list_ad_accounts(connector, respx_mock):
    respx_mock.get(f"{API}/ad_accounts").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "t2_a", "name": "Main", "currency": "USD", "status": "ACTIVE", "time_zone_id": "America/New_York"},
        {"id": "t2_b", "name": "Other", "timezone": "UTC"}]}))
    a, b = connector.list_ad_accounts(TOKEN)
    assert (a.account_id, a.status, a.timezone) == ("t2_a", "active", "America/New_York")
    assert (b.status, b.timezone) == ("active", "UTC")


def test_fetch_campaigns(connector, respx_mock):
    respx_mock.get(f"{API}/ad_accounts/t2_a/campaigns").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "c1", "name": "Launch", "configured_status": "PAUSED", "objective": "CONVERSIONS"}]}))
    (c,) = connector.fetch_campaigns(TOKEN, "t2_a")
    assert (c.campaign_id, c.name, c.status, c.objective) == ("c1", "Launch", "paused", "CONVERSIONS")


def test_fetch_insights_payload_and_parsing(connector, respx_mock):
    route = respx_mock.post(f"{API}/ad_accounts/t2_a/reports").mock(return_value=httpx.Response(200, json={
        "data": {"metrics": [
            {"campaign_id": "c1", "date": "2024-01-02T00:00:00Z", "spend": 12.5, "impressions": 900,
             "clicks": 9, "conversions": 1, "conversion_value": 40}]}}))
    (row,) = connector.fetch_insights(TOKEN, "t2_a", "2024-01-01", "2024-01-07")
    body = json.loads(route.calls[0].request.content)["data"]
    assert body["starts_at"] == "2024-01-01T00:00:00Z" and body["ends_at"] == "2024-01-07T23:59:59Z"
    assert body["breakdowns"] == ["campaign_id", "date"]
    assert (row.date, row.entity_id, row.spend, row.clicks, row.conversion_value) == (
        "2024-01-02", "c1", 12.5, 9, 40.0)


@pytest.mark.parametrize("status,exc", [(401, ConnectorAuthError), (403, ConnectorAuthError), (500, ConnectorError)])
def test_api_error_mapping(connector, respx_mock, status, exc):
    respx_mock.get(f"{API}/ad_accounts").mock(return_value=httpx.Response(status))
    with pytest.raises(exc):
        connector.list_ad_accounts(TOKEN)


# -- helpers ------------------------------------------------------------------------------------

@pytest.mark.parametrize("body,expected", [
    ({"data": [{"a": 1}, "junk"]}, [{"a": 1}]),
    ({"data": {"metrics": [{"a": 1}]}}, [{"a": 1}]),
    ({"data": {"rows": [{"a": 2}]}}, [{"a": 2}]),
    ({"data": {"data": [{"a": 3}]}}, [{"a": 3}]),
    ({"data": {"other": 1}}, []),
    ({}, []),
])
def test_data_list_shapes(body, expected):
    assert reddit._data_list(body) == expected


@pytest.mark.parametrize("raw,expected", [
    (12.5, 12.5),
    ("99999.99", 99999.99),        # just below the heuristic threshold: major units
    (100_000, 0.1),                # at the threshold: treated as micros
    (25_500_000, 25.5),
    (None, 0.0),
    ("garbage", 0.0),
])
def test_norm_spend_micros_heuristic(raw, expected):
    assert reddit._norm_spend(raw) == expected


def test_safe_json_wraps_non_dict_bodies():
    assert reddit._safe_json(httpx.Response(200, json=[1, 2])) == {"data": [1, 2]}
    assert reddit._safe_json(httpx.Response(200, text="nope")) == {}
