"""User CRUD operations."""

from datetime import UTC, datetime
from typing import Any

from app.db.main import get_item, put_item, query_gsi, update_item
from app.db.tables import MAIN_TABLE


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Write ─────────────────────────────────────────────────────────────────────

def create_user(user: dict[str, Any]) -> bool:
    """Insert a user profile item and its email lookup item."""
    user_id = user["user_id"]
    email = user["email"]

    item = {
        "pk": f"USER#{user_id}",
        "sk": "PROFILE",
        "user_id": user_id,
        **user,
        "created_at": user.get("created_at", _now()),
        "updated_at": user.get("updated_at", _now()),
    }
    ok = put_item(item, table_name=MAIN_TABLE)

    # Email → user lookup
    put_item(
        {"pk": f"EMAIL#{email}", "sk": "USER", "user_id": user_id, "email": email},
        table_name=MAIN_TABLE,
    )
    return ok


def update_user(user_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update user fields. Always stamps updated_at."""
    updates = {**updates, "updated_at": _now()}
    return update_item(f"USER#{user_id}", "PROFILE", updates, table_name=MAIN_TABLE)


# ── Read ──────────────────────────────────────────────────────────────────────

def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    return get_item(f"USER#{user_id}", "PROFILE", table_name=MAIN_TABLE)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Look up user via email index item, then fetch full profile."""
    items = query_gsi("email-index", "email", email, table_name=MAIN_TABLE)
    if not items:
        return None
    user_id = items[0].get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)
