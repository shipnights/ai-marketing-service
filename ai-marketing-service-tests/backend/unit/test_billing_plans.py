"""Unit tests for app/services/billing/plans.py - plan & credit-pack catalog."""

import pytest

from app.services.billing import plans


@pytest.fixture
def priced(monkeypatch):
    """Configure Stripe price ids for a couple of plans/packs."""
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "monthly", "price_team_m")
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "annual", "price_team_y")
    monkeypatch.setitem(plans.CREDIT_PACKS["growth"], "price_id", "price_growth")


def test_get_plan_known_and_unknown():
    assert plans.get_plan("agency")["monthly_credits"] == 600
    assert plans.get_plan("does-not-exist") is plans.FREE_PLAN


def test_plans_are_internally_consistent():
    """Annual pricing is a discount; higher tiers get more credits."""
    for p in plans.PLANS.values():
        assert p["price_annual_usd"] < p["price_monthly_usd"]
        assert p["key"] in plans.PLANS
    credits = [plans.PLANS[k]["monthly_credits"] for k in ("solo", "team", "agency")]
    assert credits == sorted(credits)


def test_price_id_for_unconfigured_is_none():
    # No STRIPE_PRICE_* env in tests -> nothing is purchasable.
    assert plans.price_id_for("team", "monthly") is None
    assert plans.price_id_for("nope", "monthly") is None


def test_price_id_for_configured(priced):
    assert plans.price_id_for("team", "monthly") == "price_team_m"
    assert plans.price_id_for("team", "annual") == "price_team_y"
    assert plans.price_id_for("team", "weekly") is None


def test_plan_for_price_id_reverse_lookup(priced):
    assert plans.plan_for_price_id("price_team_y") == ("team", "annual")
    assert plans.plan_for_price_id("price_unknown") == (None, None)


def test_empty_price_id_never_matches_unconfigured_plans():
    """An empty-string price must not match every plan whose price is unset."""
    assert plans.plan_for_price_id("") == (None, None)
    assert plans.pack_for_price_id("") is None


def test_pack_for_price_id(priced):
    assert plans.pack_for_price_id("price_growth")["credits"] == 100
    assert plans.pack_for_price_id("price_other") is None


def test_public_catalog_hides_price_ids_and_flags_configuration(priced):
    cat = plans.public_catalog()
    for p in cat["plans"]:
        assert "prices" not in p
    for pack in cat["credit_packs"]:
        assert "price_id" not in pack
    configured = {p["key"]: p["configured"] for p in cat["plans"]}
    assert configured == {"solo": False, "team": True, "agency": False}
    assert {p["key"]: p["configured"] for p in cat["credit_packs"]}["growth"] is True
