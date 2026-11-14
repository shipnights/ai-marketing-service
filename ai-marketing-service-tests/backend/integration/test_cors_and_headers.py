"""CORS, HSTS and production-profile tests (gap #13).

Default CORS origins (http://localhost:3000, :3001) are tested in-process;
custom CORS_ORIGINS parsing and the ENV=production profile need a fresh app
(fixtures/subprocess_app.py) because they are read at import time.
"""

import pytest

from backend.fixtures.subprocess_app import run_app

ALLOWED = "http://localhost:3000"
EVIL = "https://evil.example"


def _preflight(client, origin, method="PATCH", headers="authorization,content-type"):
    return client.options("/api/v1/projects", headers={
        "Origin": origin, "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers})


# -- in-process: default origins -------------------------------------------------------------

def test_allowed_origin_is_echoed_with_credentials(app_client):
    r = app_client.get("/health", headers={"Origin": ALLOWED})
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert r.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in r.headers.get("vary", "")


def test_disallowed_origin_gets_no_cors_headers(app_client):
    r = app_client.get("/health", headers={"Origin": EVIL})
    assert r.status_code == 200                     # server still answers...
    assert "access-control-allow-origin" not in r.headers   # ...but the browser blocks the read


def test_wildcard_is_never_sent_with_credentials(app_client):
    """`*` + credentials is forbidden by the CORS spec and would be a
    misconfiguration; the concrete origin must be echoed instead."""
    r = app_client.get("/health", headers={"Origin": ALLOWED})
    assert r.headers["access-control-allow-origin"] != "*"


def test_preflight_allowed_origin(app_client):
    r = _preflight(app_client, ALLOWED)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED
    methods = {m.strip() for m in r.headers["access-control-allow-methods"].split(",")}
    assert {"GET", "POST", "PATCH", "DELETE"} <= methods           # everything the SPA uses
    assert {h.strip() for h in r.headers["access-control-allow-headers"].lower().split(",")} >= {
        "authorization", "content-type"}


def test_preflight_disallowed_origin_rejected(app_client):
    r = _preflight(app_client, EVIL)
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_second_default_origin_allowed(app_client):
    r = app_client.get("/health", headers={"Origin": "http://localhost:3001"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3001"


def test_cors_headers_present_on_error_responses(app_client):
    """Without CORS headers on 4xx the browser hides the error body, and the
    frontend's 401 -> silent-refresh logic could never read it."""
    r = app_client.get("/api/v1/auth/me", headers={"Origin": ALLOWED})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == ALLOWED


def test_origin_matching_is_exact(app_client):
    for origin in ("http://localhost:3000.evil.example", "http://localhost:30000", "https://localhost:3000"):
        r = app_client.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers, origin


# -- subprocess: custom CORS_ORIGINS -----------------------------------------------------------

@pytest.fixture(scope="module")
def custom_cors():
    return run_app("""
        c = TestClient(app)
        def acao(origin):
            return c.get("/health", headers={"Origin": origin}).headers.get("access-control-allow-origin")
        result = {o: acao(o) for o in ["https://app.example.com", "https://admin.example.com",
                                       "http://localhost:3000", ""]}
    """, env={"CORS_ORIGINS": " https://app.example.com , ,https://admin.example.com "})


def test_cors_origins_env_is_trimmed_and_split(custom_cors):
    assert custom_cors["https://app.example.com"] == "https://app.example.com"
    assert custom_cors["https://admin.example.com"] == "https://admin.example.com"


def test_cors_env_replaces_defaults(custom_cors):
    assert custom_cors["http://localhost:3000"] is None


# -- subprocess: ENV=production profile ------------------------------------------------------------

@pytest.fixture(scope="module")
def production():
    return run_app("""
        from app import config
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/health")
        result = {
            "hsts": r.headers.get("strict-transport-security"),
            "hsts_on_error": c.get("/api/v1/auth/me").headers.get("strict-transport-security"),
            "docs": c.get("/docs").status_code,
            "openapi": c.get("/openapi.json").status_code,
            "dev_header": c.get("/api/v1/projects", headers={"X-User-Id": "intruder"}).status_code,
            "debug": config.DEBUG,
            "dev_auth": config.ENABLE_DEV_AUTH_HEADER,
        }
    """, env={"ENV": "production", "ENABLE_DEV_AUTH_HEADER": "", "DEBUG": "", "DISABLE_DOCS": ""})


def test_production_sends_hsts_everywhere(production):
    assert production["hsts"] == "max-age=31536000; includeSubDomains; preload"
    assert production["hsts_on_error"] == production["hsts"]


def test_production_disables_interactive_docs(production):
    assert production["docs"] == 404


def test_production_disables_dev_auth_bypass_and_debug(production):
    assert production["dev_auth"] is False
    assert production["debug"] is False
    assert production["dev_header"] == 401             # X-User-Id is worthless


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT (info disclosure): DISABLE_DOCS hides /docs and /redoc in "
    "production, but /openapi.json stays public (FastAPI's openapi_url is not "
    "disabled), exposing the full API schema - every route, parameter and model."))
def test_production_hides_openapi_schema(production):
    assert production["openapi"] == 404
