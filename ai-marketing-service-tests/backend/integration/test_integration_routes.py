"""Integration tests for /api/v1/integrations/* (ad-platform OAuth & accounts).

A FakeConnector replaces the real platform connector in the registry, so the
full route -> db path is exercised without any network access.
"""

import time
from urllib.parse import parse_qs, urlparse

import jwt as pyjwt
import pytest
from freezegun import freeze_time

from app.config import JWT_SECRET
from app.db import ad_accounts
from app.services.ads.base import ConnectorAuthError, ConnectorError, TokenBundle
from app.services.ads.schema import AD_PLATFORMS, AdAccountInfo
from backend.fixtures.fakes import FakeConnector, install_fake_connector

P = "meta_ads"


def _redirect_params(resp):
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("http://localhost:3000/dashboard/integrations?")
    return {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}


def _state_for(app_client, headers, platform=P):
    r = app_client.get(f"/api/v1/integrations/{platform}/connect", headers=headers)
    assert r.status_code == 200
    url = r.json()["data"]["authorize_url"]
    return parse_qs(urlparse(url).query)["state"][0]


def _connect(team, platform=P, accounts=("act_1",)):
    ad_accounts.save_connection(team, platform, TokenBundle(access_token="tok"), connected_by="u")
    for a in accounts:
        ad_accounts.upsert_ad_account(team, AdAccountInfo(platform=platform, account_id=a, name=a))


# -- list ---------------------------------------------------------------------------------

class TestListIntegrations:
    def test_lists_all_platforms_disconnected_by_default(self, app_client, auth_headers):
        r = app_client.get("/api/v1/integrations", headers=auth_headers)
        assert r.status_code == 200
        rows = {i["platform"]: i for i in r.json()["data"]}
        assert set(rows) == set(AD_PLATFORMS)
        for row in rows.values():
            assert row["connected"] is False
            assert row["configured"] is False
            assert row["account_count"] == 0 and row["connection_status"] is None

    def test_reflects_connection_and_account_counts(self, app_client, dynamodb, auth_headers, test_team_id):
        _connect(test_team_id, accounts=("a1", "a2", "a3"))
        ad_accounts.set_account_enabled(test_team_id, P, "a3", False)
        ad_accounts.mark_connection_error(test_team_id, P, "expired")
        rows = {i["platform"]: i for i in app_client.get("/api/v1/integrations", headers=auth_headers).json()["data"]}
        meta = rows[P]
        assert meta["connected"] is True
        assert (meta["connection_status"], meta["last_error"]) == ("error", "expired")
        assert (meta["account_count"], meta["enabled_account_count"]) == (3, 2)
        assert rows["google_ads"]["connected"] is False

    def test_other_teams_connections_invisible(self, app_client, dynamodb, auth_headers):
        _connect("someone-else")
        rows = {i["platform"]: i for i in app_client.get("/api/v1/integrations", headers=auth_headers).json()["data"]}
        assert rows[P]["connected"] is False

    def test_requires_auth(self, app_client):
        assert app_client.get("/api/v1/integrations").status_code == 401


# -- connect --------------------------------------------------------------------------------

class TestConnect:
    def test_unknown_platform_404(self, app_client, auth_headers):
        r = app_client.get("/api/v1/integrations/snapchat_ads/connect", headers=auth_headers)
        assert r.status_code == 404
        assert r.json()["code"] == "UNKNOWN_PLATFORM"

    def test_unconfigured_platform_400(self, app_client, auth_headers):
        r = app_client.get(f"/api/v1/integrations/{P}/connect", headers=auth_headers)   # real, unconfigured
        assert r.status_code == 400
        assert r.json()["code"] == "NOT_CONFIGURED"

    def test_returns_authorize_url_with_signed_state(self, app_client, auth_headers, test_team_id, test_user_id, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P))
        state = _state_for(app_client, auth_headers)
        claims = pyjwt.decode(state, JWT_SECRET, algorithms=["HS256"])
        assert (claims["team_id"], claims["user_id"], claims["platform"]) == (test_team_id, test_user_id, P)
        assert claims["exp"] == pytest.approx(time.time() + 900, abs=10)          # 15-minute TTL
        assert claims["nonce"]

    def test_each_state_is_unique(self, app_client, auth_headers, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P))
        assert _state_for(app_client, auth_headers) != _state_for(app_client, auth_headers)

    def test_redirect_mode_returns_302(self, app_client, auth_headers, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P))
        r = app_client.get(f"/api/v1/integrations/{P}/connect?redirect=true", headers=auth_headers,
                           follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("https://fake.example/oauth?state=")

    def test_requires_auth(self, app_client):
        assert app_client.get(f"/api/v1/integrations/{P}/connect").status_code == 401


# -- callback ----------------------------------------------------------------------------------

class TestCallback:
    def _cb(self, app_client, **params):
        return app_client.get(f"/api/v1/integrations/{P}/callback", params=params, follow_redirects=False)

    def test_happy_path_saves_connection_and_discovers_accounts(self, app_client, dynamodb, auth_headers,
                                                                  test_team_id, test_user_id, monkeypatch):
        from cryptography.fernet import Fernet

        import app.utils.crypto as crypto

        # Real encryption key: prove the callback stores tokens encrypted.
        monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setattr(crypto, "_fernet", None)
        fake = install_fake_connector(monkeypatch, FakeConnector(P, accounts=[
            AdAccountInfo(platform=P, account_id="act_1", name="One"),
            AdAccountInfo(platform=P, account_id="act_2", name="Two")]))
        state = _state_for(app_client, auth_headers)
        # The callback carries NO auth header - identity comes from the state.
        params = _redirect_params(self._cb(app_client, code="auth-code", state=state))
        assert params == {"connected": P, "status": "success"}
        assert ("exchange_code", "auth-code") in fake.calls
        conn = ad_accounts.get_connection(test_team_id, P)
        assert conn["connected_by"] == test_user_id
        assert conn["scope"] == "ads_read"
        assert conn["access_token_enc"].startswith("enc:")
        assert "fake-access" not in str(conn)        # no plaintext token at rest
        assert ad_accounts.load_token(test_team_id, P).access_token == "fake-access"
        assert {a["account_id"] for a in ad_accounts.list_ad_accounts(test_team_id, P)} == {"act_1", "act_2"}

    def test_discovery_failure_still_connects(self, app_client, dynamodb, auth_headers, test_team_id, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P, errors={"list_ad_accounts": ConnectorError("x")}))
        state = _state_for(app_client, auth_headers)
        assert _redirect_params(self._cb(app_client, code="c", state=state))["status"] == "success"
        assert ad_accounts.get_connection(test_team_id, P) is not None
        assert ad_accounts.list_ad_accounts(test_team_id, P) == []

    @pytest.mark.parametrize("params,reason", [
        ({"error": "access_denied"}, "access_denied"),
        ({"state": "s"}, "missing_code"),
        ({"code": "c"}, "missing_code"),
    ])
    def test_provider_error_or_missing_params_redirect_with_error(self, app_client, params, reason):
        p = _redirect_params(self._cb(app_client, **params))
        assert (p["status"], p["reason"]) == ("error", reason)

    def test_forged_state_rejected(self, app_client, dynamodb):
        forged = pyjwt.encode({"team_id": "victim", "user_id": "x", "platform": P,
                               "exp": int(time.time()) + 600}, "attacker-secret-attacker-secret!", algorithm="HS256")
        r = self._cb(app_client, code="c", state=forged)
        assert r.status_code == 400
        assert r.json()["code"] == "BAD_STATE"
        assert ad_accounts.get_connection("victim", P) is None

    def test_state_for_other_platform_rejected(self, app_client, auth_headers, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector("google_ads"))
        state = _state_for(app_client, auth_headers, platform="google_ads")
        r = self._cb(app_client, code="c", state=state)           # replayed on meta_ads
        assert r.status_code == 400
        assert r.json()["code"] == "BAD_STATE"

    def test_expired_state_rejected(self, app_client, auth_headers, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P))
        with freeze_time("2020-01-01"):
            from app.api.v1.integrations import _sign_state
            state = _sign_state("t", "u", P)
        assert self._cb(app_client, code="c", state=state).status_code == 400

    def test_exchange_auth_error_redirects_with_auth_reason(self, app_client, dynamodb, auth_headers,
                                                             test_team_id, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P, errors={"exchange_code": ConnectorAuthError("denied")}))
        state = _state_for(app_client, auth_headers)
        p = _redirect_params(self._cb(app_client, code="c", state=state))
        assert (p["status"], p["reason"]) == ("error", "auth:denied")
        assert ad_accounts.get_connection(test_team_id, P) is None

    def test_exchange_error_reason_is_truncated(self, app_client, auth_headers, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P, errors={"exchange_code": ConnectorError("e" * 500)}))
        state = _state_for(app_client, auth_headers)
        assert len(_redirect_params(self._cb(app_client, code="c", state=state))["reason"]) == 120


# -- accounts -------------------------------------------------------------------------------------

class TestAccounts:
    def test_list_accounts_strips_keys_and_is_team_scoped(self, app_client, dynamodb, auth_headers, test_team_id):
        _connect(test_team_id, accounts=("a1",))
        _connect("other", accounts=("x9",))
        rows = app_client.get(f"/api/v1/integrations/{P}/accounts", headers=auth_headers).json()["data"]
        assert [r["account_id"] for r in rows] == ["a1"]
        assert "pk" not in rows[0] and "sk" not in rows[0]

    def test_list_accounts_unknown_platform_404(self, app_client, auth_headers):
        assert app_client.get("/api/v1/integrations/nope/accounts", headers=auth_headers).status_code == 404

    def test_refresh_not_connected_400(self, app_client, auth_headers, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P))
        r = app_client.post(f"/api/v1/integrations/{P}/accounts/refresh", headers=auth_headers)
        assert r.status_code == 400
        assert r.json()["code"] == "NOT_CONNECTED"

    def test_refresh_discovers_new_accounts(self, app_client, dynamodb, auth_headers, test_team_id, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P, accounts=[
            AdAccountInfo(platform=P, account_id="a1", name="A"), AdAccountInfo(platform=P, account_id="new", name="N")]))
        _connect(test_team_id, accounts=("a1",))
        r = app_client.post(f"/api/v1/integrations/{P}/accounts/refresh", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"] == {"discovered": 2}
        assert {a["account_id"] for a in ad_accounts.list_ad_accounts(test_team_id, P)} == {"a1", "new"}

    def test_refresh_auth_error_marks_connection(self, app_client, dynamodb, auth_headers, test_team_id, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P, errors={"list_ad_accounts": ConnectorAuthError("revoked")}))
        _connect(test_team_id)
        r = app_client.post(f"/api/v1/integrations/{P}/accounts/refresh", headers=auth_headers)
        assert r.status_code == 400
        assert r.json()["code"] == "AUTH_EXPIRED"
        assert ad_accounts.get_connection(test_team_id, P)["status"] == "error"

    def test_refresh_platform_error_502(self, app_client, dynamodb, auth_headers, test_team_id, monkeypatch):
        install_fake_connector(monkeypatch, FakeConnector(P, errors={"list_ad_accounts": ConnectorError("5xx")}))
        _connect(test_team_id)
        r = app_client.post(f"/api/v1/integrations/{P}/accounts/refresh", headers=auth_headers)
        assert r.status_code == 502
        assert r.json()["code"] == "PLATFORM_ERROR"

    def test_toggle_account(self, app_client, dynamodb, auth_headers, test_team_id):
        _connect(test_team_id, accounts=("a1",))
        r = app_client.patch(f"/api/v1/integrations/{P}/accounts/a1?enabled=false", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"]["enabled"] is False
        assert "pk" not in r.json()["data"]
        assert ad_accounts.get_ad_account(test_team_id, P, "a1")["enabled"] is False

    def test_toggle_requires_enabled_param(self, app_client, auth_headers):
        r = app_client.patch(f"/api/v1/integrations/{P}/accounts/a1", headers=auth_headers)
        assert r.status_code == 422

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: toggling a nonexistent account returns 200 and creates a phantom "
        "ADACCT row (set_account_enabled upserts), so the 404 branch never fires."))
    def test_toggle_missing_account_404(self, app_client, dynamodb, auth_headers):
        r = app_client.patch(f"/api/v1/integrations/{P}/accounts/ghost?enabled=false", headers=auth_headers)
        assert r.status_code == 404

    def test_disconnect_removes_connection_and_accounts(self, app_client, dynamodb, auth_headers, test_team_id):
        _connect(test_team_id, accounts=("a1", "a2"))
        _connect("other")
        r = app_client.delete(f"/api/v1/integrations/{P}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"] == {"platform": P}
        assert ad_accounts.get_connection(test_team_id, P) is None
        assert ad_accounts.list_ad_accounts(test_team_id, P) == []
        assert ad_accounts.get_connection("other", P) is not None      # other team untouched

    def test_disconnect_unknown_platform_404(self, app_client, auth_headers):
        assert app_client.delete("/api/v1/integrations/nope", headers=auth_headers).status_code == 404
