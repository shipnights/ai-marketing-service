"""Smoke tests for health / ready / root endpoints."""

from fastapi.testclient import TestClient


def test_health_returns_ok(app_client: TestClient) -> None:
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


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
