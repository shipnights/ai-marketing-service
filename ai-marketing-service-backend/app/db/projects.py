"""Project CRUD operations."""

from datetime import UTC, datetime
from typing import Any

from app.db.main import get_item, put_item, query_by_pk_sk_begins, query_gsi, update_item
from app.db.tables import MAIN_TABLE


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Write ─────────────────────────────────────────────────────────────────────

def create_project(team_id: str, project_id: str, data: dict[str, Any]) -> bool:
    """Create a project item under the team and a direct-lookup item via GSI."""
    now = _now()
    item = {
        "pk": f"TEAM#{team_id}",
        "sk": f"PROJECT#{project_id}",
        "project_id": project_id,
        "team_id": team_id,
        "status": "draft",
        **data,
        "created_at": data.get("created_at", now),
        "updated_at": data.get("updated_at", now),
    }
    return put_item(item, table_name=MAIN_TABLE)


def update_project(project_id: str, team_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    updates = {**updates, "updated_at": _now()}
    return update_item(
        f"TEAM#{team_id}", f"PROJECT#{project_id}", updates, table_name=MAIN_TABLE
    )


# ── Read ──────────────────────────────────────────────────────────────────────

def get_project(project_id: str) -> dict[str, Any] | None:
    """Fetch project via project_id-index GSI."""
    items = query_gsi("project_id-index", "project_id", project_id, table_name=MAIN_TABLE)
    return items[0] if items else None


def list_projects(team_id: str, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Return all projects for a team, optionally filtered by status."""
    items, _ = query_by_pk_sk_begins(f"TEAM#{team_id}", "PROJECT#", table_name=MAIN_TABLE)
    if status_filter:
        items = [p for p in items if p.get("status") == status_filter]
    return items


def get_project_for_team(project_id: str, team_id: str) -> dict[str, Any] | None:
    """Fetch project and verify it belongs to team_id. Returns None if not found or wrong team."""
    return get_item(f"TEAM#{team_id}", f"PROJECT#{project_id}", table_name=MAIN_TABLE)
