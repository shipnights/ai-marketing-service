"""
Entity factory helpers for building test data.

Usage:
    from backend.fixtures.factories import make_user, make_team, make_project

All factories return plain dicts that match the DynamoDB item schema used by
ai-marketing-service-backend.  Pass keyword args to override any field.
"""

import uuid
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def make_user(**overrides) -> dict:
    """Return a MAIN_TABLE user profile item (pk=USER#{id}, sk=PROFILE)."""
    user_id = overrides.pop("user_id", f"user-{_uid()}")
    email = overrides.pop("email", f"{_uid()}@example.com")
    return {
        "pk": f"USER#{user_id}",
        "sk": "PROFILE",
        "user_id": user_id,
        "email": email,
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$fake$hash",
        "team_id": overrides.pop("team_id", f"team-{_uid()}"),
        "ai_credits": 100,
        "created_at": _now(),
        "updated_at": _now(),
        **overrides,
    }


def make_email_index_item(user_id: str, email: str, **overrides) -> dict:
    """Return the email→user lookup item (pk=EMAIL#{email}, sk=USER)."""
    return {
        "pk": f"EMAIL#{email}",
        "sk": "USER",
        "user_id": user_id,
        "email": email,
        **overrides,
    }


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def make_team(**overrides) -> dict:
    """Return a MAIN_TABLE team profile item (pk=TEAM#{id}, sk=PROFILE)."""
    team_id = overrides.pop("team_id", f"team-{_uid()}")
    return {
        "pk": f"TEAM#{team_id}",
        "sk": "PROFILE",
        "team_id": team_id,
        "team_name": overrides.pop("team_name", "Test Team"),
        "owner_user_id": overrides.pop("owner_user_id", f"user-{_uid()}"),
        "ai_credits": 500,
        "created_at": _now(),
        "updated_at": _now(),
        **overrides,
    }


def make_team_member(team_id: str, user_id: str, **overrides) -> dict:
    """Return MAIN_TABLE team member item (pk=TEAM#{id}, sk=MEMBER#{uid})."""
    return {
        "pk": f"TEAM#{team_id}",
        "sk": f"MEMBER#{user_id}",
        "team_id": team_id,
        "user_id": user_id,
        "role": overrides.pop("role", "member"),
        "joined_at": _now(),
        **overrides,
    }


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def make_project(**overrides) -> dict:
    """Return a MAIN_TABLE project item (pk=TEAM#{team_id}, sk=PROJECT#{id})."""
    project_id = overrides.pop("project_id", f"proj-{_uid()}")
    team_id = overrides.pop("team_id", f"team-{_uid()}")
    return {
        "pk": f"TEAM#{team_id}",
        "sk": f"PROJECT#{project_id}",
        "project_id": project_id,
        "team_id": team_id,
        "name": overrides.pop("name", "Test Project"),
        "description": overrides.pop("description", "A test project"),
        "target_platforms": overrides.pop("target_platforms", ["instagram"]),
        "campaign_types": overrides.pop("campaign_types", ["brand_awareness"]),
        "status": overrides.pop("status", "active"),
        "created_at": _now(),
        "updated_at": _now(),
        **overrides,
    }


# ---------------------------------------------------------------------------
# Posts / Content
# ---------------------------------------------------------------------------

def make_post(**overrides) -> dict:
    """Return a CONTENT_TABLE post item (pk=PROJECT#{id}, sk=POST#{id})."""
    post_id = overrides.pop("post_id", f"post-{_uid()}")
    project_id = overrides.pop("project_id", f"proj-{_uid()}")
    return {
        "pk": f"PROJECT#{project_id}",
        "sk": f"POST#{post_id}",
        "post_id": post_id,
        "project_id": project_id,
        "platform": overrides.pop("platform", "instagram"),
        "content": overrides.pop("content", "Test post content #marketing"),
        "status": overrides.pop("status", "draft"),
        "created_at": _now(),
        "updated_at": _now(),
        **overrides,
    }


# ---------------------------------------------------------------------------
# Connected social accounts
# ---------------------------------------------------------------------------

def make_social_account(**overrides) -> dict:
    """Return an ACCOUNTS_TABLE item (pk=TEAM#{team_id}, sk=ACCOUNT#{platform}#{id})."""
    account_id = overrides.pop("account_id", f"acc-{_uid()}")
    team_id = overrides.pop("team_id", f"team-{_uid()}")
    platform = overrides.pop("platform", "instagram")
    return {
        "pk": f"TEAM#{team_id}",
        "sk": f"ACCOUNT#{platform}#{account_id}",
        "account_id": account_id,
        "team_id": team_id,
        "platform": platform,
        "platform_user_id": overrides.pop("platform_user_id", f"ext-{_uid()}"),
        "username": overrides.pop("username", "testuser"),
        "connected_at": _now(),
        **overrides,
    }
