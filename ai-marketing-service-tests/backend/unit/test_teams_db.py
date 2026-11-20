"""Unit tests for app/db/teams.py - team CRUD and membership."""

from app.db import teams
from app.db.tables import MAIN_TABLE


# -- team CRUD -------------------------------------------------------------------

def test_create_team_defaults(dynamodb):
    assert teams.create_team("t1", "Acme", owner_user_id="u1") is True
    team = teams.get_team("t1")
    assert team["team_name"] == "Acme"
    assert team["owner_user_id"] == "u1"
    assert team["created_at"] == team["updated_at"]


def test_get_missing_team_returns_none(dynamodb):
    assert teams.get_team("ghost") is None


def test_update_team_stamps_updated_at_and_preserves_fields(dynamodb):
    teams.create_team("t1", "Acme", owner_user_id="u1")
    before = teams.get_team("t1")
    updated = teams.update_team("t1", {"team_name": "Acme 2"})
    assert updated["team_name"] == "Acme 2"
    assert updated["owner_user_id"] == "u1"
    assert updated["updated_at"] >= before["updated_at"]
    assert updated["created_at"] == before["created_at"]


# -- membership --------------------------------------------------------------------

def test_add_and_list_members_only_returns_member_rows(dynamodb):
    teams.create_team("t1", "Acme", owner_user_id="u1")
    teams.add_team_member("t1", "u1", role="owner")
    teams.add_team_member("t1", "u2")
    # A project under the same partition must not be mistaken for a member.
    dynamodb.Table(MAIN_TABLE).put_item(Item={"pk": "TEAM#t1", "sk": "PROJECT#p1"})
    members = teams.get_team_members("t1")
    assert {(m["user_id"], m["role"]) for m in members} == {("u1", "owner"), ("u2", "member")}


def test_members_are_team_scoped(dynamodb):
    teams.add_team_member("t1", "u1")
    teams.add_team_member("t2", "u2")
    assert [m["user_id"] for m in teams.get_team_members("t1")] == ["u1"]


def test_update_member_role(dynamodb):
    teams.add_team_member("t1", "u2")
    assert teams.update_member_role("t1", "u2", "admin")["role"] == "admin"
    assert teams.get_team_members("t1")[0]["role"] == "admin"


def test_remove_member(dynamodb):
    teams.add_team_member("t1", "u1", role="owner")
    teams.add_team_member("t1", "u2")
    assert teams.remove_team_member("t1", "u2") is True
    assert [m["user_id"] for m in teams.get_team_members("t1")] == ["u1"]


def test_remove_missing_member_is_idempotent(dynamodb):
    assert teams.remove_team_member("t1", "nobody") is True
