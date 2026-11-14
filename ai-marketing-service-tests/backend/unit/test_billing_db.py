"""Unit tests for app/db/billing.py - Stripe mapping, subscription, credit ledger."""

import pytest

from app.db import billing as db
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_team

TEAM = "t1"


def _seed_team(dynamodb, credits=100):
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id=TEAM, ai_credits=credits))


def _balance(dynamodb):
    return dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": f"TEAM#{TEAM}", "sk": "PROFILE"})["Item"]["ai_credits"]


# -- customer mapping ------------------------------------------------------------------

def test_set_stripe_customer_writes_both_directions(dynamodb):
    db.set_stripe_customer(TEAM, "cus_123")
    assert db.get_billing(TEAM)["stripe_customer_id"] == "cus_123"
    assert db.get_team_by_customer("cus_123") == TEAM


def test_unknown_customer_maps_to_none(dynamodb):
    assert db.get_team_by_customer("cus_nope") is None


# -- subscription -------------------------------------------------------------------------

def test_get_billing_defaults_to_free(dynamodb):
    assert db.get_billing(TEAM) == {"plan": "free", "status": "inactive", "stripe_customer_id": None}


def test_upsert_subscription_merges_and_strips_keys(dynamodb):
    db.set_stripe_customer(TEAM, "cus_1")
    db.upsert_subscription(TEAM, {"plan": "team", "status": "active"})
    db.upsert_subscription(TEAM, {"status": "past_due"})          # partial update
    rec = db.get_billing(TEAM)
    assert rec["plan"] == "team"                 # preserved
    assert rec["status"] == "past_due"           # updated
    assert rec["stripe_customer_id"] == "cus_1"  # preserved
    assert "pk" not in rec and "sk" not in rec
    assert rec["updated_at"]


# -- adjust_credits ---------------------------------------------------------------------------

def test_grant_credits_returns_new_balance_and_writes_ledger(dynamodb):
    _seed_team(dynamodb, 100)
    assert db.adjust_credits(TEAM, 50, reason="pack", source="stripe", ref="cs_1") == 150
    assert _balance(dynamodb) == 150
    ledger = db.list_credit_ledger(TEAM)
    assert len(ledger) == 1
    e = ledger[0]
    assert (e["delta"], e["balance_after"], e["reason"], e["source"], e["ref"]) == (50, 150, "pack", "stripe", "cs_1")
    assert "pk" not in e and "sk" not in e


def test_spend_credits_within_balance(dynamodb):
    _seed_team(dynamodb, 10)
    assert db.adjust_credits(TEAM, -10, reason="ai") == 0      # exactly to zero is fine


def test_overspend_rejected_without_side_effects(dynamodb):
    _seed_team(dynamodb, 5)
    assert db.adjust_credits(TEAM, -6, reason="ai") is None
    assert _balance(dynamodb) == 5                       # unchanged
    assert db.list_credit_ledger(TEAM) == []             # no ledger row for a refused spend


def test_allow_negative_permits_overdraft(dynamodb):
    _seed_team(dynamodb, 5)
    assert db.adjust_credits(TEAM, -8, reason="refund clawback", allow_negative=True) == -3


def test_spend_on_missing_team_rejected(dynamodb):
    assert db.adjust_credits("ghost", -1, reason="ai") is None


@pytest.mark.xfail(strict=True, reason=(
    "BUG: adjust_credits() uses an unconditional ADD for grants, so granting "
    "credits to a team_id with no PROFILE (e.g. a typo'd/stale team_id in Stripe "
    "metadata) silently creates a phantom TEAM#.../PROFILE item holding the "
    "credits. Needs ConditionExpression=attribute_exists(pk)."))
def test_grant_to_missing_team_creates_nothing(dynamodb):
    assert db.adjust_credits("ghost", 50, reason="pack") is None
    item = dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": "TEAM#ghost", "sk": "PROFILE"}).get("Item")
    assert item is None


def test_ledger_is_newest_first_and_limited(dynamodb):
    _seed_team(dynamodb, 0)
    for i in range(1, 4):
        db.adjust_credits(TEAM, i, reason=f"r{i}")
    ledger = db.list_credit_ledger(TEAM)
    assert [e["reason"] for e in ledger] == ["r3", "r2", "r1"]
    assert [e["balance_after"] for e in ledger] == [6, 3, 1]
    assert len(db.list_credit_ledger(TEAM, limit=2)) == 2


def test_ledger_is_team_scoped(dynamodb):
    _seed_team(dynamodb, 0)
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id="t2", ai_credits=0))
    db.adjust_credits("t2", 5, reason="other")
    assert db.list_credit_ledger(TEAM) == []


# -- claim_event (webhook idempotency) -------------------------------------------------

def test_claim_event_first_time_true_then_false(dynamodb):
    assert db.claim_event("evt_1") is True
    assert db.claim_event("evt_1") is False
    assert db.claim_event("evt_2") is True           # independent events


def test_claim_event_propagates_unexpected_errors(dynamodb, monkeypatch):
    """Only ConditionalCheckFailed means 'already seen'; other DB errors must
    surface (so Stripe retries) rather than be mistaken for a duplicate."""
    from botocore.exceptions import ClientError

    class BrokenTable:
        def put_item(self, **kw):
            raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "PutItem")

    monkeypatch.setattr(db.dynamodb, "Table", lambda name: BrokenTable())
    with pytest.raises(ClientError):
        db.claim_event("evt_x")
