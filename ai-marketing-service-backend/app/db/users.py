"""User CRUD operations."""

from datetime import UTC, datetime
from typing import Any

from app.db.main import get_item, increment_attr, put_item, query_gsi, update_item
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


def bump_token_version(user_id: str) -> dict[str, Any] | None:
    """Atomically increment token_version and stamp tokens_valid_after.

    Used to invalidate every outstanding access/refresh token for a user
    (password reset, logout-all, role change, ...):
      - token_version: compared against the `tv` claim on every request;
        the atomic ADD avoids the lost-update race of read-modify-write and
        works for any number of consecutive resets (1 -> 2 -> 3 -> ...).
      - tokens_valid_after: epoch seconds; any token with iat earlier than
        this is rejected, which also covers tokens whose tv claim happens to
        collide (e.g. legacy users with no stored token_version).
    Returns the updated user item, or None on failure.
    """
    return increment_attr(
        f"USER#{user_id}",
        "PROFILE",
        "token_version",
        also_set={
            "tokens_valid_after": int(datetime.now(UTC).timestamp()),
            "updated_at": _now(),
        },
        table_name=MAIN_TABLE,
    )


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
