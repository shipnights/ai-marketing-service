"""Unit tests for app/db/teams.py - team CRUD, membership, credit deduction."""

import pytest

from app.db import teams
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_team


# -- team CRUD -------------------------------------------------------------------

def test_create_team_defaults(dynamodb):
    assert teams.create_team("t1", "Acme", owner_user_id="u1") is True
    team = teams.get_team("t1")
    assert team["team_name"] == "Acme"
    assert team["owner_user_id"] == "u1"
    assert team["ai_credits"] == 500                 # starter allotment
    assert team["created_at"] == team["updated_at"]


def test_create_team_custom_credits(dynamodb):
    teams.create_team("t1", "Acme", owner_user_id="u1", ai_credits=0)
    assert teams.get_team("t1")["ai_credits"] == 0


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


# -- deduct_team_credits -------------------------------------------------------------

def _seed(dynamodb, credits):
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id="t1", ai_credits=credits))


def test_deduct_reduces_balance(dynamodb):
    _seed(dynamodb, 100)
    assert teams.deduct_team_credits("t1", 30) is True
    assert teams.get_team("t1")["ai_credits"] == 70


def test_deduct_floors_at_zero(dynamodb):
    """Over-deduction clamps to 0 instead of going negative."""
    _seed(dynamodb, 10)
    assert teams.deduct_team_credits("t1", 25) is True
    assert teams.get_team("t1")["ai_credits"] == 0


def test_deduct_missing_team_returns_false_and_creates_nothing(dynamodb):
    assert teams.deduct_team_credits("ghost", 5) is False
    assert teams.get_team("ghost") is None


def test_deduct_zero_is_noop_on_balance(dynamodb):
    _seed(dynamodb, 42)
    teams.deduct_team_credits("t1", 0)
    assert teams.get_team("t1")["ai_credits"] == 42


@pytest.mark.xfail(strict=True, reason=(
    "BUG: deduct_team_credits is a non-atomic read-modify-write. Two concurrent "
    "requests that both read balance=100 each write 90 - one deduction is lost. "
    "billing.adjust_credits (atomic ADD + condition) or db.main.increment_attr "
    "should replace it."))
def test_concurrent_deductions_are_not_lost(dynamodb, monkeypatch):
    _seed(dynamodb, 100)
    snapshot = teams.get_team("t1")
    # Deterministically simulate the race: both callers read the same snapshot.
    monkeypatch.setattr(teams, "get_team", lambda team_id: dict(snapshot))
    teams.deduct_team_credits("t1", 10)
    teams.deduct_team_credits("t1", 10)
    monkeypatch.undo()
    assert teams.get_team("t1")["ai_credits"] == 80
