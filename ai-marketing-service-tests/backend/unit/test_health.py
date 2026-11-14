"""Smoke tests for health / ready / root endpoints."""

from fastapi.testclient import TestClient


def test_health_returns_ok(app_client: TestClient) -> None:
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    # Middleware contract: security headers + request-id on every response.
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "X-Request-Id" in r.headers


def test_request_id_is_echoed_back(app_client: TestClient) -> None:
    r = app_client.get("/health", headers={"X-Request-Id": "trace-123"})
    assert r.headers["X-Request-Id"] == "trace-123"


def test_root_returns_service_info(app_client: TestClient) -> None:
    r = app_client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "ai-marketing-service"
    assert "version" in data


def test_ready_returns_ok(app_client: TestClient) -> None:
    r = app_client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_with_db_check_ok(app_client: TestClient, monkeypatch) -> None:
    """With ENABLE_READINESS_DB_CHECK on, /ready actually pings DynamoDB."""
    monkeypatch.setenv("ENABLE_READINESS_DB_CHECK", "1")
    r = app_client.get("/ready")
    assert r.status_code == 200


def test_ready_with_db_check_unavailable_returns_503(app_client: TestClient, monkeypatch) -> None:
    """If the DB ping fails, readiness must report 503, not a fake ok."""
    import app.db.main as db_main

    def _boom(*args, **kwargs):
        raise RuntimeError("dynamodb down")

    monkeypatch.setenv("ENABLE_READINESS_DB_CHECK", "1")
    monkeypatch.setattr(db_main, "get_item_strict", _boom)
    r = app_client.get("/ready")
    assert r.status_code == 503
