"""Unit tests for app/services/billing/stripe_service.py.

The Stripe SDK is stubbed: no network, no real keys. We assert on the exact
arguments sent to Stripe, since those drive what customers are charged.
"""

from types import SimpleNamespace

import pytest
import stripe

from app import config
from app.db import billing as billing_db
from app.services.billing import plans, stripe_service

TEAM = "t1"


@pytest.fixture
def stripe_on(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(config, "STRIPE_SUCCESS_URL", "https://app/ok")
    monkeypatch.setattr(config, "STRIPE_CANCEL_URL", "https://app/cancel")
    monkeypatch.setattr(config, "STRIPE_PORTAL_RETURN_URL", "https://app/billing")


@pytest.fixture
def stripe_calls(monkeypatch):
    """Stub Customer/Checkout/Portal creation and record the kwargs."""
    calls = {"customer": [], "checkout": [], "portal": []}

    def customer_create(**kw):
        calls["customer"].append(kw)
        return SimpleNamespace(id=f"cus_{len(calls['customer'])}")

    def checkout_create(**kw):
        calls["checkout"].append(kw)
        return SimpleNamespace(url="https://checkout.stripe.test/s/1")

    def portal_create(**kw):
        calls["portal"].append(kw)
        return SimpleNamespace(url="https://billing.stripe.test/p/1")

    monkeypatch.setattr(stripe.Customer, "create", customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create", checkout_create)
    monkeypatch.setattr(stripe.billing_portal.Session, "create", portal_create)
    return calls


# -- configuration ---------------------------------------------------------------

def test_not_configured_without_secret_key(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    assert stripe_service.is_configured() is False
    with pytest.raises(stripe_service.StripeNotConfigured):
        stripe_service._stripe()


def test_configured_sets_api_key(stripe_on):
    assert stripe_service.is_configured() is True
    assert stripe_service._stripe().api_key == "sk_test_dummy"


# -- ensure_customer ---------------------------------------------------------------

def test_ensure_customer_creates_once_then_reuses(dynamodb, stripe_on, stripe_calls):
    cid = stripe_service.ensure_customer(TEAM, "a@b.c", name="Acme")
    assert cid == "cus_1"
    assert stripe_calls["customer"][0] == {"email": "a@b.c", "name": "Acme", "metadata": {"team_id": TEAM}}
    # Mapping persisted both ways (webhooks resolve team by customer).
    assert billing_db.get_team_by_customer("cus_1") == TEAM
    # Second call reuses - no duplicate Stripe customers.
    assert stripe_service.ensure_customer(TEAM, "a@b.c") == "cus_1"
    assert len(stripe_calls["customer"]) == 1


def test_ensure_customer_name_defaults_to_email(dynamodb, stripe_on, stripe_calls):
    stripe_service.ensure_customer(TEAM, "a@b.c")
    assert stripe_calls["customer"][0]["name"] == "a@b.c"


# -- subscription checkout -------------------------------------------------------------

def test_subscription_checkout_sends_server_side_price(dynamodb, stripe_on, stripe_calls, monkeypatch):
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "annual", "price_team_y")
    url = stripe_service.create_subscription_checkout(TEAM, "a@b.c", "team", "annual")
    assert url == "https://checkout.stripe.test/s/1"
    kw = stripe_calls["checkout"][0]
    assert kw["mode"] == "subscription"
    assert kw["customer"] == "cus_1"
    assert kw["line_items"] == [{"price": "price_team_y", "quantity": 1}]
    assert kw["metadata"] == {"team_id": TEAM, "plan_key": "team", "interval": "annual"}
    assert kw["subscription_data"]["metadata"]["team_id"] == TEAM
    assert (kw["success_url"], kw["cancel_url"]) == ("https://app/ok", "https://app/cancel")


def test_subscription_checkout_unpriced_plan_raises_before_calling_stripe(dynamodb, stripe_on, stripe_calls):
    with pytest.raises(ValueError, match="No Stripe price"):
        stripe_service.create_subscription_checkout(TEAM, "a@b.c", "team", "monthly")
    assert stripe_calls["customer"] == [] and stripe_calls["checkout"] == []


def test_subscription_checkout_unknown_plan_raises(dynamodb, stripe_on, stripe_calls):
    with pytest.raises(ValueError):
        stripe_service.create_subscription_checkout(TEAM, "a@b.c", "platinum", "monthly")


def test_checkout_when_stripe_unconfigured_raises(dynamodb, monkeypatch, stripe_calls):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "monthly", "price_team_m")
    with pytest.raises(stripe_service.StripeNotConfigured):
        stripe_service.create_subscription_checkout(TEAM, "a@b.c", "team", "monthly")


# -- credit-pack checkout ---------------------------------------------------------------

def test_credit_checkout_is_one_time_payment_with_credit_metadata(dynamodb, stripe_on, stripe_calls, monkeypatch):
    monkeypatch.setitem(plans.CREDIT_PACKS["growth"], "price_id", "price_growth")
    stripe_service.create_credit_checkout(TEAM, "a@b.c", "growth")
    kw = stripe_calls["checkout"][0]
    assert kw["mode"] == "payment"
    assert kw["line_items"] == [{"price": "price_growth", "quantity": 1}]
    # The webhook grants credits from this metadata - it must be exact.
    assert kw["metadata"] == {"team_id": TEAM, "kind": "credit_pack", "pack_key": "growth", "credits": "100"}
    assert kw["payment_intent_data"]["metadata"] == {"team_id": TEAM, "credits": "100"}


@pytest.mark.parametrize("pack", ["growth", "nonexistent"])
def test_credit_checkout_unpriced_or_unknown_pack_raises(dynamodb, stripe_on, stripe_calls, pack):
    with pytest.raises(ValueError):
        stripe_service.create_credit_checkout(TEAM, "a@b.c", pack)
    assert stripe_calls["checkout"] == []


# -- portal ---------------------------------------------------------------------------------

def test_portal_requires_existing_customer(dynamodb, stripe_on, stripe_calls):
    with pytest.raises(ValueError, match="No Stripe customer"):
        stripe_service.create_portal_session(TEAM)
    assert stripe_calls["portal"] == []


def test_portal_session_for_existing_customer(dynamodb, stripe_on, stripe_calls):
    billing_db.set_stripe_customer(TEAM, "cus_9")
    assert stripe_service.create_portal_session(TEAM) == "https://billing.stripe.test/p/1"
    assert stripe_calls["portal"][0] == {"customer": "cus_9", "return_url": "https://app/billing"}


# -- construct_event ---------------------------------------------------------------------

def test_construct_event_requires_webhook_secret(stripe_on, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "")
    with pytest.raises(stripe_service.StripeNotConfigured, match="WEBHOOK_SECRET"):
        stripe_service.construct_event(b"{}", "t=1,v1=x")


def test_construct_event_rejects_bad_signature(stripe_on, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    with pytest.raises(stripe.SignatureVerificationError):
        stripe_service.construct_event(b'{"id": "evt_1"}', "t=1,v1=deadbeef")
