"""Tests for app/middleware.py - API-key guard and security headers."""

import pytest

import app.middleware as mw

API_KEY = "test-api-key-123"


@pytest.fixture
def api_key_on(monkeypatch):
    monkeypatch.setattr(mw, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(mw, "API_KEY", API_KEY)


# -- ApiKeyMiddleware -----------------------------------------------------------

@pytest.mark.usefixtures("api_key_on")
class TestApiKeyGuard:
    def test_missing_key_401(self, app_client, auth_headers):
        r = app_client.get("/api/v1/projects", headers=auth_headers)
        assert r.status_code == 401
        assert r.json()["code"] == "INVALID_API_KEY"

    def test_wrong_key_401(self, app_client, auth_headers):
        r = app_client.get("/api/v1/projects", headers={**auth_headers, "X-API-Key": "nope"})
        assert r.status_code == 401
        assert r.json()["code"] == "INVALID_API_KEY"

    def test_key_prefix_is_not_enough(self, app_client, auth_headers):
        """Comparison must be exact, not prefix-based."""
        r = app_client.get("/api/v1/projects", headers={**auth_headers, "X-API-Key": API_KEY[:-1]})
        assert r.status_code == 401

    def test_correct_key_passes_through(self, app_client, auth_headers):
        r = app_client.get("/api/v1/projects", headers={**auth_headers, "X-API-Key": API_KEY})
        assert r.status_code == 200

    def test_header_name_is_case_insensitive(self, app_client, auth_headers):
        r = app_client.get("/api/v1/projects", headers={**auth_headers, "x-api-key": API_KEY})
        assert r.status_code == 200

    @pytest.mark.parametrize("path", ["/", "/health", "/ready", "/openapi.json", "/docs"])
    def test_exempt_paths_do_not_need_key(self, app_client, path):
        r = app_client.get(path)
        assert r.status_code == 200

    def test_guard_runs_before_auth(self, app_client):
        """No key + no auth -> the API-key error wins (401 INVALID_API_KEY)."""
        r = app_client.get("/api/v1/auth/me")
        assert r.json()["code"] == "INVALID_API_KEY"

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: /api/v1/billing/webhook is not exempt from the API-key guard. Stripe "
        "cannot send X-API-Key, so every webhook is rejected with 401 when "
        "REQUIRE_API_KEY is on (billing.py even says to exempt it)."))
    def test_stripe_webhook_is_exempt(self, app_client):
        r = app_client.post("/api/v1/billing/webhook", content=b"{}",
                            headers={"stripe-signature": "t=1,v1=x"})
        assert r.status_code != 401

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: OAuth callbacks (/api/v1/integrations/{platform}/callback) arrive as "
        "browser redirects from the ad platform without X-API-Key, so connecting "
        "any ad account fails with 401 when REQUIRE_API_KEY is on."))
    def test_oauth_callback_is_exempt(self, app_client):
        r = app_client.get("/api/v1/integrations/meta_ads/callback?error=access_denied",
                           follow_redirects=False)
        assert r.status_code == 302


def test_guard_disabled_when_require_flag_off(app_client, auth_headers, monkeypatch):
    monkeypatch.setattr(mw, "REQUIRE_API_KEY", False)
    monkeypatch.setattr(mw, "API_KEY", API_KEY)
    assert app_client.get("/api/v1/projects", headers=auth_headers).status_code == 200


def test_guard_disabled_when_key_empty(app_client, auth_headers, monkeypatch):
    """REQUIRE_API_KEY with an empty key must not lock everyone out."""
    monkeypatch.setattr(mw, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(mw, "API_KEY", "")
    assert app_client.get("/api/v1/projects", headers=auth_headers).status_code == 200


def test_skip_helper_matches_documented_paths():
    assert mw._skip_api_key_check("/health")
    assert mw._skip_api_key_check("/docs/oauth2-redirect")
    assert mw._skip_api_key_check("/redoc")
    assert not mw._skip_api_key_check("/api/v1/projects")
    assert not mw._skip_api_key_check("/healthz")   # exact match, not prefix


# -- SecurityHeadersMiddleware --------------------------------------------------

def test_security_headers_on_error_responses_too(app_client):
    r = app_client.get("/api/v1/auth/me")  # 401
    assert r.status_code == 401
    for name, value in mw.SECURITY_HEADERS.items():
        assert r.headers[name] == value


def test_hsts_only_in_production(app_client, monkeypatch):
    assert mw.HSTS_HEADER not in app_client.get("/health").headers
    monkeypatch.setattr(mw, "IS_PRODUCTION", True)
    assert app_client.get("/health").headers[mw.HSTS_HEADER] == mw.HSTS_VALUE


def test_request_id_generated_when_absent_and_unique(app_client):
    a = app_client.get("/health").headers["X-Request-Id"]
    b = app_client.get("/health").headers["X-Request-Id"]
    assert a and b and a != b
