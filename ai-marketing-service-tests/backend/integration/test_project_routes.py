"""Integration tests for /api/v1/projects/* endpoints."""

import pytest

from backend.fixtures.factories import make_project, make_team, make_user
from app.db.tables import MAIN_TABLE


# ── Helpers ────────────────────────────────────────────────────────────────────

def _seed_project(dynamodb, team_id: str, **overrides) -> dict:
    project = make_project(team_id=team_id, **overrides)
    dynamodb.Table(MAIN_TABLE).put_item(Item=project)
    return project


def _seed_team(dynamodb, team_id: str):
    team = make_team(team_id=team_id, ai_credits=500)
    dynamodb.Table(MAIN_TABLE).put_item(Item=team)
    return team


# ── List projects ──────────────────────────────────────────────────────────────

class TestListProjects:
    def test_empty_list(self, app_client, auth_headers):
        resp = app_client.get("/api/v1/projects", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_returns_own_team_projects(self, app_client, dynamodb, auth_headers, test_team_id):
        _seed_project(dynamodb, test_team_id, name="Project A", status="active")
        _seed_project(dynamodb, test_team_id, name="Project B", status="draft")
        # Project for another team — must NOT appear
        _seed_project(dynamodb, "other-team-id", name="Other Team Project")

        resp = app_client.get("/api/v1/projects", headers=auth_headers)
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()["data"]}
        assert names == {"Project A", "Project B"}

    def test_status_filter(self, app_client, dynamodb, auth_headers, test_team_id):
        _seed_project(dynamodb, test_team_id, name="Active", status="active")
        _seed_project(dynamodb, test_team_id, name="Draft", status="draft")

        resp = app_client.get("/api/v1/projects?status=active", headers=auth_headers)
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["data"]]
        assert "Active" in names
        assert "Draft" not in names

    def test_unauthenticated_401(self, app_client):
        resp = app_client.get("/api/v1/projects")
        assert resp.status_code == 401


# ── Create project ─────────────────────────────────────────────────────────────

class TestCreateProject:
    def test_success(self, app_client, auth_headers):
        resp = app_client.post(
            "/api/v1/projects",
            json={"name": "My Project", "platforms": ["instagram", "tiktok"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "My Project"
        assert data["status"] == "draft"
        assert "project_id" in data

    def test_invalid_platform_422(self, app_client, auth_headers):
        resp = app_client.post(
            "/api/v1/projects",
            json={"name": "Bad", "platforms": ["snapchat"]},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_unauthenticated_401(self, app_client):
        resp = app_client.post("/api/v1/projects", json={"name": "X"})
        assert resp.status_code == 401

    def test_creates_with_description(self, app_client, auth_headers):
        resp = app_client.post(
            "/api/v1/projects",
            json={"name": "Described", "description": "A detailed desc"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["description"] == "A detailed desc"


# ── Get project ────────────────────────────────────────────────────────────────

class TestGetProject:
    def test_success(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, name="Found")
        resp = app_client.get(
            f"/api/v1/projects/{project['project_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Found"

    def test_not_found_404(self, app_client, auth_headers):
        resp = app_client.get("/api/v1/projects/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    def test_wrong_team_404(self, app_client, dynamodb, auth_headers):
        """Project from another team is not visible."""
        project = _seed_project(dynamodb, "other-team", name="Hidden")
        resp = app_client.get(
            f"/api/v1/projects/{project['project_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── Update project ─────────────────────────────────────────────────────────────

class TestUpdateProject:
    def test_success(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, name="Old Name")
        resp = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={"name": "New Name"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "New Name"

    def test_no_updates_422(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id)
        resp = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_invalid_platform_422(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id)
        resp = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={"platforms": ["snapchat"]},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_not_found_404(self, app_client, auth_headers):
        resp = app_client.patch(
            "/api/v1/projects/missing",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── Activate / Archive ─────────────────────────────────────────────────────────

class TestActivate:
    def test_activate_draft(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, status="draft")
        resp = app_client.post(
            f"/api/v1/projects/{project['project_id']}/activate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "active"

    def test_activate_already_active(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, status="active")
        resp = app_client.post(
            f"/api/v1/projects/{project['project_id']}/activate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "active"

    def test_not_found_404(self, app_client, auth_headers):
        resp = app_client.post("/api/v1/projects/nope/activate", headers=auth_headers)
        assert resp.status_code == 404


class TestArchive:
    def test_archive_active(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, status="active")
        resp = app_client.post(
            f"/api/v1/projects/{project['project_id']}/archive",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "archived"

    def test_archive_already_archived(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, status="archived")
        resp = app_client.post(
            f"/api/v1/projects/{project['project_id']}/archive",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_not_found_404(self, app_client, auth_headers):
        resp = app_client.post("/api/v1/projects/nope/archive", headers=auth_headers)
        assert resp.status_code == 404
