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
    def test_empty_list(self, app_client, dynamodb, auth_headers):
        # Another team's project exists - it must not leak into our empty team.
        _seed_project(dynamodb, "someone-elses-team", name="Not Yours")
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
        items = resp.json()["data"]
        names = {p["name"] for p in items}
        assert names == {"Project A", "Project B"}
        # Internal DynamoDB keys are stripped from every item.
        assert all("pk" not in p and "sk" not in p for p in items)

    def test_status_filter(self, app_client, dynamodb, auth_headers, test_team_id):
        _seed_project(dynamodb, test_team_id, name="Active", status="active")
        _seed_project(dynamodb, test_team_id, name="Draft", status="draft")

        resp = app_client.get("/api/v1/projects?status=active", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        names = [p["name"] for p in items]
        assert names == ["Active"]
        # The filter must hold for every returned item, not just exclude one name.
        assert all(p["status"] == "active" for p in items)
        # Unknown status filters return an empty list rather than everything.
        resp2 = app_client.get("/api/v1/projects?status=bogus", headers=auth_headers)
        assert resp2.json()["data"] == []

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
        assert data["platforms"] == ["instagram", "tiktok"]
        assert "pk" not in data and "sk" not in data
        # Must be PERSISTED, not merely echoed (this endpoint once returned a
        # 500 because the write silently failed).
        got = app_client.get(f"/api/v1/projects/{data['project_id']}", headers=auth_headers)
        assert got.status_code == 200
        fetched = got.json()["data"]
        assert fetched["name"] == "My Project"
        assert fetched["platforms"] == ["instagram", "tiktok"]
        assert fetched["created_by"] == auth_headers["X-User-Id"]

    def test_invalid_platform_422(self, app_client, auth_headers):
        resp = app_client.post(
            "/api/v1/projects",
            json={"name": "Bad", "platforms": ["snapchat"]},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "INVALID_PLATFORM"
        # Nothing must have been persisted for the rejected request.
        listing = app_client.get("/api/v1/projects", headers=auth_headers)
        assert listing.json()["data"] == []

    def test_mixed_valid_and_invalid_platforms_422(self, app_client, auth_headers):
        resp = app_client.post(
            "/api/v1/projects",
            json={"name": "Mixed", "platforms": ["instagram", "snapchat"]},
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
        data = resp.json()["data"]
        assert data["description"] == "A detailed desc"
        assert data["status"] == "draft"  # default status applies
        # Re-fetch: description persisted, not just echoed.
        got = app_client.get(f"/api/v1/projects/{data['project_id']}", headers=auth_headers)
        assert got.json()["data"]["description"] == "A detailed desc"


# ── Get project ────────────────────────────────────────────────────────────────

class TestGetProject:
    def test_success(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, name="Found")
        resp = app_client.get(
            f"/api/v1/projects/{project['project_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "Found"
        assert data["project_id"] == project["project_id"]
        assert data["team_id"] == test_team_id
        assert data["status"] == project["status"]
        assert "pk" not in data and "sk" not in data

    def test_not_found_404(self, app_client, auth_headers):
        resp = app_client.get("/api/v1/projects/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"

    def test_wrong_team_404(self, app_client, dynamodb, auth_headers):
        """Project from another team is not visible."""
        project = _seed_project(dynamodb, "other-team", name="Hidden")
        resp = app_client.get(
            f"/api/v1/projects/{project['project_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_wrong_team_cannot_modify_404(self, app_client, dynamodb, auth_headers):
        """Writes are team-guarded too: PATCH/activate/archive on another
        team's project must 404 and must not change it."""
        project = _seed_project(dynamodb, "other-team", name="Hidden", status="draft")
        pid = project["project_id"]
        assert app_client.patch(
            f"/api/v1/projects/{pid}", json={"name": "Pwned"}, headers=auth_headers
        ).status_code == 404
        assert app_client.post(f"/api/v1/projects/{pid}/activate", headers=auth_headers).status_code == 404
        assert app_client.post(f"/api/v1/projects/{pid}/archive", headers=auth_headers).status_code == 404
        # Untouched in the database.
        stored = dynamodb.Table(MAIN_TABLE).get_item(
            Key={"pk": "TEAM#other-team", "sk": f"PROJECT#{pid}"}
        )["Item"]
        assert stored["name"] == "Hidden"
        assert stored["status"] == "draft"


# ── Update project ─────────────────────────────────────────────────────────────

class TestUpdateProject:
    def test_success(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, name="Old Name", description="keep me")
        resp = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={"name": "New Name"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "New Name"
        assert data["description"] == "keep me"           # untouched field preserved
        assert data["updated_at"] > project["updated_at"]  # audit stamp advanced
        # Persisted, not just echoed.
        got = app_client.get(f"/api/v1/projects/{project['project_id']}", headers=auth_headers)
        assert got.json()["data"]["name"] == "New Name"

    def test_no_updates_422(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id)
        resp = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "NO_UPDATES"
        # Explicit nulls are "no updates" too (pin the filtering behaviour).
        resp2 = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={"name": None},
            headers=auth_headers,
        )
        assert resp2.status_code == 422

    def test_invalid_platform_422(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id)
        resp = app_client.patch(
            f"/api/v1/projects/{project['project_id']}",
            json={"platforms": ["snapchat"]},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "INVALID_PLATFORM"
        # The rejected update must not have modified the project.
        got = app_client.get(f"/api/v1/projects/{project['project_id']}", headers=auth_headers)
        assert got.json()["data"]["target_platforms"] == project["target_platforms"]

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
        # The transition is persisted.
        got = app_client.get(f"/api/v1/projects/{project['project_id']}", headers=auth_headers)
        assert got.json()["data"]["status"] == "active"

    def test_activate_already_active(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, status="active")
        resp = app_client.post(
            f"/api/v1/projects/{project['project_id']}/activate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "active"
        # True no-op: short-circuit message and NO write (updated_at unchanged).
        assert resp.json()["message"] == "Already active"
        assert data["updated_at"] == project["updated_at"]

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
        # Persisted, and visible via the status filter.
        got = app_client.get(f"/api/v1/projects/{project['project_id']}", headers=auth_headers)
        assert got.json()["data"]["status"] == "archived"
        active = app_client.get("/api/v1/projects?status=active", headers=auth_headers)
        assert project["project_id"] not in [p["project_id"] for p in active.json()["data"]]

    def test_archive_already_archived(self, app_client, dynamodb, auth_headers, test_team_id):
        project = _seed_project(dynamodb, test_team_id, status="archived")
        resp = app_client.post(
            f"/api/v1/projects/{project['project_id']}/archive",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # A bare 200 would pass even if the route flipped the status back -
        # pin the body and the no-op semantics.
        assert data["status"] == "archived"
        assert resp.json()["message"] == "Already archived"
        assert data["updated_at"] == project["updated_at"]

    def test_not_found_404(self, app_client, auth_headers):
        resp = app_client.post("/api/v1/projects/nope/archive", headers=auth_headers)
        assert resp.status_code == 404
