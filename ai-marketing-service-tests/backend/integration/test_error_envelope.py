"""Error-envelope contract tests (gap #11).

Contract: every error is JSON shaped {status:"error", message, code, errors?}
(app/errors.py). The frontend (src/lib/api-client.ts) depends on it:
  * 422 -> reads body.errors[].message and joins them
  * 402 -> reads body.detail.error == "INSUFFICIENT_CREDITS", .required, .balance
  * any non-JSON error body is shown as "server returned an HTML page"
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

import app.main as main
from app.api.v1.deps import CurrentUser, require_credits
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_team


def _assert_envelope(body, code):
    assert body["status"] == "error"
    assert isinstance(body["message"], str) and body["message"]
    assert body["code"] == code


# -- 422 request validation ------------------------------------------------------------------

def test_missing_field_envelope(app_client):
    r = app_client.post("/api/v1/auth/login", json={"email": "a@b.co"})
    assert r.status_code == 422
    body = r.json()
    _assert_envelope(body, "VALIDATION_ERROR")
    assert body["message"] == "Validation error"
    assert body["errors"] == [{"field": "body > password", "message": "Field required"}]


def test_all_errors_reported_not_just_first(app_client):
    r = app_client.post("/api/v1/auth/login", json={})
    fields = {e["field"] for e in r.json()["errors"]}
    assert fields == {"body > email", "body > password"}


def test_wrong_type_reported(app_client):
    r = app_client.post("/api/v1/auth/login", json={"email": "a@b.co", "password": 12345})
    (err,) = r.json()["errors"]
    assert err["field"] == "body > password"
    assert "string" in err["message"].lower()


def test_query_param_validation_uses_envelope(app_client, auth_headers):
    r = app_client.get("/api/v1/ads/campaigns?limit=abc", headers=auth_headers)
    assert r.status_code == 422
    _assert_envelope(r.json(), "VALIDATION_ERROR")
    assert r.json()["errors"][0]["field"] == "query > limit"


def test_malformed_json_body_uses_envelope(app_client):
    r = app_client.post("/api/v1/auth/login", content=b"{not json",
                        headers={"content-type": "application/json"})
    assert r.status_code == 422
    _assert_envelope(r.json(), "VALIDATION_ERROR")
    assert r.json()["errors"][0]["message"] == "JSON decode error"


def test_every_error_message_is_non_empty_for_frontend_join(app_client):
    """api-client.ts joins errors[].message with '; ' - none may be blank."""
    r = app_client.post("/api/v1/auth/register/step-1", json={"email": "bad", "password": None})
    assert all(e["message"] for e in r.json()["errors"])


# -- HTTPException mapping -----------------------------------------------------------------

def test_dict_detail_keeps_code_message_and_detail(app_client, auth_headers):
    r = app_client.get("/api/v1/projects/missing", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    _assert_envelope(body, "NOT_FOUND")
    assert body["message"] == "Project not found."
    assert body["detail"] == {"message": "Project not found.", "code": "NOT_FOUND"}


@pytest.fixture
def handler_client(dynamodb):
    """A tiny app wired with the REAL exception handlers from app.main."""
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, main.validation_error_handler)
    app.add_exception_handler(HTTPException, main.http_exception_handler)
    app.add_exception_handler(Exception, main.global_exception_handler)

    @app.get("/string-detail")
    def string_detail():
        raise HTTPException(status_code=503, detail="Authentication not configured")

    @app.get("/no-detail")
    def no_detail():
        raise HTTPException(status_code=409, detail="")

    @app.get("/unmapped")
    def unmapped():
        raise HTTPException(status_code=418, detail="teapot")

    @app.post("/paid")
    def paid(user: CurrentUser = require_credits(5)):
        return {"ok": True}

    return TestClient(app)


def test_string_detail_gets_status_derived_code(handler_client):
    r = handler_client.get("/string-detail")
    assert r.status_code == 503
    _assert_envelope(r.json(), "SERVICE_UNAVAILABLE")
    assert r.json()["message"] == "Authentication not configured"


def test_empty_detail_gets_generic_message(handler_client):
    r = handler_client.get("/no-detail")
    _assert_envelope(r.json(), "CONFLICT")
    assert r.json()["message"] == "An error occurred"


def test_unmapped_status_gets_generic_code(handler_client):
    _assert_envelope(handler_client.get("/unmapped").json(), "ERROR")


def test_402_matches_frontend_credit_error_contract(handler_client, dynamodb):
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id="t1", ai_credits=2))
    r = handler_client.post("/paid", headers={"X-User-Id": "u1", "X-Team-Id": "t1"})
    assert r.status_code == 402
    body = r.json()
    _assert_envelope(body, "PAYMENT_REQUIRED")
    # Exactly what api-client.ts reads into `creditError`:
    assert body["detail"]["error"] == "INSUFFICIENT_CREDITS"
    assert (body["detail"]["required"], body["detail"]["balance"]) == (5, 2)
    assert body["detail"]["message"]


# -- 500 handler ------------------------------------------------------------------------------

@pytest.fixture
def exploding_client(app_client, monkeypatch):
    import app.api.v1.projects as projects

    def boom(*a, **k):
        raise RuntimeError("db-host-10.0.0.5:8000 connection refused")

    monkeypatch.setattr(projects, "list_projects", boom)
    return TestClient(main.app, raise_server_exceptions=False)


def test_500_in_production_hides_internals(exploding_client, monkeypatch):
    monkeypatch.setattr(main, "DEBUG", False)
    r = exploding_client.get("/api/v1/projects", headers={"X-User-Id": "u"})
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"status": "error", "message": "Internal server error", "code": "INTERNAL_ERROR"}
    assert "10.0.0.5" not in r.text


def test_500_in_debug_exposes_exception_for_developers(exploding_client, monkeypatch):
    """Pins current behaviour. NOTE: DEBUG defaults to True whenever ENV is not
    exactly 'production' - see the defect report."""
    monkeypatch.setattr(main, "DEBUG", True)
    body = exploding_client.get("/api/v1/projects", headers={"X-User-Id": "u"}).json()
    assert body["type"] == "RuntimeError" and "connection refused" in body["message"]


def test_debug_defaults_on_outside_production():
    """Pins the risky default: any ENV value other than 'production' (e.g.
    'prod', 'staging') enables DEBUG and therefore leaks exception text."""
    from app import config

    assert config.ENV != "production" and config.DEBUG is True


# -- framework-level errors ---------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: unknown routes (404) and wrong methods (405) bypass the envelope "
    "and return Starlette's bare {'detail': ...} - no `status`/`code`. The app "
    "registers its handler for fastapi.HTTPException, but routing errors raise "
    "the parent starlette.exceptions.HTTPException."))
@pytest.mark.parametrize("method,path,code", [
    ("get", "/api/v1/does-not-exist", "NOT_FOUND"),
    ("delete", "/health", "ERROR"),
])
def test_routing_errors_use_envelope(app_client, method, path, code):
    body = getattr(app_client, method)(path).json()
    assert body.get("status") == "error" and "code" in body


@pytest.mark.parametrize("method,path", [("get", "/api/v1/does-not-exist"), ("delete", "/health")])
def test_routing_errors_are_at_least_json(app_client, method, path):
    """Guarantees the frontend never shows its 'HTML instead of JSON' message."""
    r = getattr(app_client, method)(path)
    assert r.status_code in (404, 405)
    assert r.headers["content-type"].startswith("application/json")
