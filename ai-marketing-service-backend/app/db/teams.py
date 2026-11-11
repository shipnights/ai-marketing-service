"""Team CRUD and membership operations."""

from datetime import UTC, datetime
from typing import Any

from app.db.main import delete_item, get_item, put_item, query_by_pk_sk_begins, update_item
from app.db.tables import MAIN_TABLE


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Write ─────────────────────────────────────────────────────────────────────

def create_team(team_id: str, team_name: str, owner_user_id: str, ai_credits: int = 500) -> bool:
    now = _now()
    return put_item(
        {
            "pk": f"TEAM#{team_id}",
            "sk": "PROFILE",
            "team_id": team_id,
            "team_name": team_name,
            "owner_user_id": owner_user_id,
            "ai_credits": ai_credits,
            "created_at": now,
            "updated_at": now,
        },
        table_name=MAIN_TABLE,
    )


def update_team(team_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    updates = {**updates, "updated_at": _now()}
    return update_item(f"TEAM#{team_id}", "PROFILE", updates, table_name=MAIN_TABLE)


def add_team_member(team_id: str, user_id: str, role: str = "member") -> bool:
    return put_item(
        {
            "pk": f"TEAM#{team_id}",
            "sk": f"MEMBER#{user_id}",
            "team_id": team_id,
            "user_id": user_id,
            "role": role,
            "joined_at": _now(),
        },
        table_name=MAIN_TABLE,
    )


def remove_team_member(team_id: str, user_id: str) -> bool:
    return delete_item(f"TEAM#{team_id}", f"MEMBER#{user_id}", table_name=MAIN_TABLE)


def update_member_role(team_id: str, user_id: str, new_role: str) -> dict[str, Any] | None:
    return update_item(
        f"TEAM#{team_id}",
        f"MEMBER#{user_id}",
        {"role": new_role},
        table_name=MAIN_TABLE,
    )


def deduct_team_credits(team_id: str, amount: int) -> bool:
    """Subtract `amount` from team ai_credits. Returns True on success."""
    team = get_team(team_id)
    if not team:
        return False
    new_balance = max(0, int(team.get("ai_credits", 0)) - amount)
    result = update_team(team_id, {"ai_credits": new_balance})
    return result is not None


# ── Read ──────────────────────────────────────────────────────────────────────

def get_team(team_id: str) -> dict[str, Any] | None:
    return get_item(f"TEAM#{team_id}", "PROFILE", table_name=MAIN_TABLE)


def get_team_members(team_id: str) -> list[dict[str, Any]]:
    items, _ = query_by_pk_sk_begins(
        f"TEAM#{team_id}", "MEMBER#", table_name=MAIN_TABLE
    )
    return items
