"""Unit tests for the Google Ads connector (REST v17 + OAuth2), HTTP mocked."""

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import app.services.ads.google as google
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle

API = "https://googleads.googleapis.com/v17"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN = TokenBundle(access_token="g-access")


@pytest.fixture
def connector(monkeypatch):
    monkeypatch.setattr(google, "GOOGLE_ADS_CLIENT_ID", "cid")
    monkeypatch.setattr(google, "GOOGLE_ADS_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(google, "GOOGLE_ADS_DEVELOPER_TOKEN", "devtok")
    monkeypatch.setattr(google, "GOOGLE_ADS_REDIRECT_URI", "https://api.example/cb")
    monkeypatch.setattr(google, "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
    return google.GoogleAdsConnector()


def _form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# -- OAuth ------------------------------------------------------------------------------

def test_is_configured_requires_developer_token(monkeypatch, connector):
    assert connector.is_configured
    monkeypatch.setattr(google, "GOOGLE_ADS_DEVELOPER_TOKEN", "")
    assert not connector.is_configured


def test_authorize_url_requests_offline_access(connector):
    q = {k: v[0] for k, v in parse_qs(urlparse(connector.authorize_url("s1")).query).items()}
    assert q["scope"] == "https://www.googleapis.com/auth/adwords"
    assert q["access_type"] == "offline"          # required to get a refresh token
    assert q["prompt"] == "consent"
    assert (q["client_id"], q["state"], q["redirect_uri"]) == ("cid", "s1", "https://api.example/cb")


def test_exchange_code(connector, respx_mock):
    route = respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "a1", "refresh_token": "r1", "expires_in": 3599,
        "scope": "https://www.googleapis.com/auth/adwords", "token_type": "Bearer"}))
    tok = connector.exchange_code("code-9")
    form = _form(route.calls[0].request)
    assert form == {"grant_type": "authorization_code", "code": "code-9", "client_id": "cid",
                    "client_secret": "csecret", "redirect_uri": "https://api.example/cb"}
    assert (tok.access_token, tok.refresh_token) == ("a1", "r1")
    assert tok.expires_at == pytest.approx(int(time.time()) + 3599, abs=5)


def test_refresh_reattaches_refresh_token(connector, respx_mock):
    route = respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "a2"}))
    tok = connector.refresh("r1")
    assert _form(route.calls[0].request)["grant_type"] == "refresh_token"
    assert tok.refresh_token == "r1"                 # Google omits it on refresh


@pytest.mark.parametrize("err", ["invalid_grant", "unauthorized_client", "invalid_client"])
def test_revoked_grant_maps_to_auth_error(connector, respx_mock, err):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": err}))
    with pytest.raises(ConnectorAuthError):
        connector.refresh("r1")


def test_other_token_errors_are_generic(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_request"}))
    with pytest.raises(ConnectorError) as exc:
        connector.refresh("r1")
    assert not isinstance(exc.value, ConnectorAuthError)


def test_token_response_without_access_token(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ConnectorError, match="missing access_token"):
        connector.exchange_code("c")


def test_token_network_error(connector, respx_mock):
    respx_mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(ConnectorError):
        connector.exchange_code("c")


# -- headers ------------------------------------------------------------------------------

def test_api_headers_without_mcc(connector):
    h = connector._headers(TOKEN)
    assert h["Authorization"] == "Bearer g-access"
    assert h["developer-token"] == "devtok"
    assert "login-customer-id" not in h


def test_login_customer_id_is_digits_only(connector, monkeypatch):
    monkeypatch.setattr(google, "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "123-456-7890")
    assert connector._headers(TOKEN)["login-customer-id"] == "1234567890"


# -- data -------------------------------------------------------------------------------------

def test_list_ad_accounts_describes_each_and_falls_back(connector, respx_mock):
    respx_mock.get(f"{API}/customers:listAccessibleCustomers").mock(return_value=httpx.Response(
        200, json={"resourceNames": ["customers/111", "customers/222"]}))
    respx_mock.post(f"{API}/customers/111/googleAds:searchStream").mock(return_value=httpx.Response(
        200, json=[{"results": [{"customer": {"id": "111", "descriptiveName": "Shop",
                                              "currencyCode": "GBP", "timeZone": "Europe/London"}}]}]))
    # Customer 222 is inaccessible (e.g. cancelled) -> bare record, not a failure.
    respx_mock.post(f"{API}/customers/222/googleAds:searchStream").mock(
        return_value=httpx.Response(500, json={"error": "boom"}))
    a, b = connector.list_ad_accounts(TOKEN)
    assert (a.account_id, a.name, a.currency, a.timezone) == ("111", "Shop", "GBP", "Europe/London")
    assert (b.account_id, b.name, b.currency) == ("222", "222", "USD")


def test_list_ad_accounts_auth_error_during_describe_propagates(connector, respx_mock):
    respx_mock.get(f"{API}/customers:listAccessibleCustomers").mock(return_value=httpx.Response(
        200, json={"resourceNames": ["customers/111"]}))
    respx_mock.post(f"{API}/customers/111/googleAds:searchStream").mock(return_value=httpx.Response(401))
    with pytest.raises(ConnectorAuthError):
        connector.list_ad_accounts(TOKEN)


def test_fetch_campaigns_uses_digit_cid_and_micros(connector, respx_mock):
    route = respx_mock.post(f"{API}/customers/1234567890/googleAds:searchStream").mock(
        return_value=httpx.Response(200, json=[{"results": [{
            "campaign": {"id": "9", "name": "Search", "status": "ENABLED", "advertisingChannelType": "SEARCH"},
            "campaignBudget": {"amountMicros": "25500000"}}]}]))
    (c,) = connector.fetch_campaigns(TOKEN, "123-456-7890")
    sent = json.loads(route.calls[0].request.content)
    assert "FROM campaign" in sent["query"]
    assert route.calls[0].request.headers["developer-token"] == "devtok"
    assert (c.campaign_id, c.status, c.objective, c.daily_budget) == ("9", "enabled", "SEARCH", 25.5)
    assert c.account_id == "123-456-7890"          # original id kept for display


def test_fetch_insights_daily_rows(connector, respx_mock):
    route = respx_mock.post(f"{API}/customers/111/googleAds:searchStream").mock(
        return_value=httpx.Response(200, json=[
            {"results": [{"campaign": {"id": "9", "name": "Search"},
                          "metrics": {"costMicros": "1500000", "impressions": "100", "clicks": "7",
                                      "conversions": 1.5, "conversionsValue": 30.0},
                          "segments": {"date": "2024-01-02"}}]},
            {"results": [{"campaign": {"id": "9", "name": "Search"}, "metrics": {},
                          "segments": {"date": "2024-01-03"}}]}]))
    rows = connector.fetch_insights(TOKEN, "111", "2024-01-01", "2024-01-31")
    query = json.loads(route.calls[0].request.content)["query"]
    assert "BETWEEN '2024-01-01' AND '2024-01-31'" in query
    assert [(r.date, r.spend, r.clicks) for r in rows] == [("2024-01-02", 1.5, 7), ("2024-01-03", 0.0, 0)]
    assert (rows[0].conversions, rows[0].conversion_value) == (1.5, 30.0)


def test_search_stream_non_list_body_yields_no_rows(connector, respx_mock):
    respx_mock.post(f"{API}/customers/111/googleAds:searchStream").mock(
        return_value=httpx.Response(200, json={"unexpected": "object"}))
    assert connector.fetch_insights(TOKEN, "111", "2024-01-01", "2024-01-01") == []


@pytest.mark.parametrize("status,exc", [(401, ConnectorAuthError), (403, ConnectorAuthError), (500, ConnectorError)])
def test_api_error_mapping(connector, respx_mock, status, exc):
    respx_mock.post(f"{API}/customers/111/googleAds:searchStream").mock(return_value=httpx.Response(status))
    with pytest.raises(exc):
        connector.fetch_campaigns(TOKEN, "111")


def test_helpers():
    assert google._digits("123-456 7890") == "1234567890"
    assert google._micros("2500000") == 2.5 and google._micros("") is None and google._micros("x") is None
