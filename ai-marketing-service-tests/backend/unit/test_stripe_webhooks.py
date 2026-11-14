"""Unit tests for app/services/billing/webhooks.py - Stripe event handling.

Webhooks are the source of truth for subscription state and credit grants, so
each handler is tested for: team resolution, state written, credits granted,
and idempotency under Stripe's at-least-once delivery.
"""

import pytest

from app.db import billing as billing_db
from app.db.tables import MAIN_TABLE
from app.services.billing import plans, webhooks
from backend.fixtures.factories import make_team

TEAM = "t1"


@pytest.fixture
def team(dynamodb):
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id=TEAM, ai_credits=0))


@pytest.fixture
def priced(monkeypatch):
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "monthly", "price_team_m")
    monkeypatch.setitem(plans.PLANS["solo"]["prices"], "annual", "price_solo_y")


def _balance(dynamodb, team_id=TEAM):
    return dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": f"TEAM#{team_id}", "sk": "PROFILE"})["Item"]["ai_credits"]


def _event(etype, obj, eid="evt_1"):
    return {"id": eid, "type": etype, "data": {"object": obj}}


def _sub(price="price_team_m", status="active", meta_team=TEAM, customer=None, **kw):
    return {"id": "sub_1", "customer": customer, "status": status,
            "metadata": {"team_id": meta_team} if meta_team else {},
            "items": {"data": [{"price": {"id": price}}]},
            "current_period_end": 1_900_000_000, "cancel_at_period_end": False, **kw}


def _invoice(price="price_team_m", reason="subscription_cycle", meta_team=TEAM, customer=None):
    return {"id": "in_1", "customer": customer, "billing_reason": reason,
            "metadata": {"team_id": meta_team} if meta_team else {},
            "lines": {"data": [{"price": {"id": price}}]}}


def _pack_session(credits="100", meta_team=TEAM, customer=None, kind="credit_pack"):
    meta = {"kind": kind, "credits": credits, "pack_key": "growth"}
    if meta_team:
        meta["team_id"] = meta_team
    return {"id": "cs_1", "customer": customer, "metadata": meta}


# -- dispatch --------------------------------------------------------------------------

def test_unknown_event_type_is_acknowledged_but_not_handled(team):
    assert webhooks.handle_event(_event("charge.refunded", {})) == {"handled": False, "type": "charge.refunded"}


def test_handler_exceptions_propagate_so_stripe_retries(team, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(billing_db, "upsert_subscription", boom)
    with pytest.raises(RuntimeError):
        webhooks.handle_event(_event("customer.subscription.updated", _sub()))


# -- subscriptions ------------------------------------------------------------------------

@pytest.mark.parametrize("etype", ["customer.subscription.created", "customer.subscription.updated"])
def test_subscription_change_sets_plan_from_price(team, priced, etype):
    result = webhooks.handle_event(_event(etype, _sub(status="trialing")))
    assert result == {"handled": True, "type": etype, "team_id": TEAM, "plan": "team", "status": "trialing"}
    rec = billing_db.get_billing(TEAM)
    assert (rec["plan"], rec["interval"], rec["status"]) == ("team", "monthly", "trialing")
    assert rec["stripe_subscription_id"] == "sub_1"
    assert rec["current_period_end"] == 1_900_000_000


def test_subscription_cancel_at_period_end_recorded(team, priced):
    webhooks.handle_event(_event("customer.subscription.updated", _sub(cancel_at_period_end=True)))
    assert billing_db.get_billing(TEAM)["cancel_at_period_end"] is True


def test_subscription_with_unknown_price_marks_plan_unknown(team, priced):
    webhooks.handle_event(_event("customer.subscription.updated", _sub(price="price_mystery")))
    assert billing_db.get_billing(TEAM)["plan"] == "unknown"


def test_subscription_with_malformed_items_does_not_crash(team, priced):
    sub = _sub()
    sub["items"] = {"data": []}
    webhooks.handle_event(_event("customer.subscription.updated", sub))
    assert billing_db.get_billing(TEAM)["plan"] == "unknown"


def test_team_resolved_via_customer_mapping_when_no_metadata(team, priced):
    billing_db.set_stripe_customer(TEAM, "cus_7")
    webhooks.handle_event(_event("customer.subscription.updated", _sub(meta_team=None, customer="cus_7")))
    assert billing_db.get_billing(TEAM)["plan"] == "team"


def test_unresolvable_team_is_a_noop(team, priced, dynamodb):
    result = webhooks.handle_event(_event("customer.subscription.updated",
                                          _sub(meta_team=None, customer="cus_unknown")))
    assert result == {"handled": True, "type": "customer.subscription.updated"}
    assert billing_db.get_billing(TEAM)["plan"] == "free"


def test_subscription_deleted_downgrades_to_free(team, priced):
    webhooks.handle_event(_event("customer.subscription.created", _sub()))
    webhooks.handle_event(_event("customer.subscription.deleted", _sub(), eid="evt_2"))
    rec = billing_db.get_billing(TEAM)
    assert (rec["plan"], rec["status"], rec["cancel_at_period_end"]) == ("free", "canceled", False)


# -- invoices ---------------------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["subscription_create", "subscription_cycle", "subscription_update"])
def test_invoice_paid_grants_monthly_credits(team, priced, dynamodb, reason):
    result = webhooks.handle_event(_event("invoice.paid", _invoice(reason=reason)))
    assert result["credits_granted"] == 200
    assert _balance(dynamodb) == 200
    entry = billing_db.list_credit_ledger(TEAM)[0]
    assert (entry["source"], entry["ref"]) == ("stripe", "in_1")


def test_invoice_paid_is_idempotent_on_redelivery(team, priced, dynamodb):
    ev = _event("invoice.paid", _invoice())
    webhooks.handle_event(ev)
    webhooks.handle_event(ev)                       # Stripe retry, same event id
    assert _balance(dynamodb) == 200
    assert len(billing_db.list_credit_ledger(TEAM)) == 1


def test_distinct_invoices_each_grant(team, priced, dynamodb):
    webhooks.handle_event(_event("invoice.paid", _invoice(), eid="evt_jan"))
    webhooks.handle_event(_event("invoice.paid", _invoice(), eid="evt_feb"))
    assert _balance(dynamodb) == 400


@pytest.mark.parametrize("reason", ["manual", "subscription_threshold", None])
def test_non_subscription_invoice_grants_nothing(team, priced, dynamodb, reason):
    assert webhooks.handle_event(_event("invoice.paid", _invoice(reason=reason))) == {
        "handled": True, "type": "invoice.paid"}
    assert _balance(dynamodb) == 0


def test_invoice_with_unknown_price_grants_nothing(team, priced, dynamodb):
    webhooks.handle_event(_event("invoice.paid", _invoice(price="price_mystery")))
    assert _balance(dynamodb) == 0


def test_invoice_payment_failed_marks_past_due(team, priced):
    webhooks.handle_event(_event("customer.subscription.created", _sub()))
    webhooks.handle_event(_event("invoice.payment_failed", _invoice(), eid="evt_2"))
    rec = billing_db.get_billing(TEAM)
    assert rec["status"] == "past_due"
    assert rec["plan"] == "team"                     # plan kept; access policy decides


# -- checkout (credit packs) -------------------------------------------------------------------

def test_credit_pack_checkout_grants_credits_once(team, dynamodb):
    ev = _event("checkout.session.completed", _pack_session())
    assert webhooks.handle_event(ev)["credits_granted"] == 100
    webhooks.handle_event(ev)                        # redelivery
    assert _balance(dynamodb) == 100
    assert billing_db.list_credit_ledger(TEAM)[0]["reason"] == "Credit pack: growth"


def test_subscription_checkout_session_is_ignored(team, dynamodb):
    result = webhooks.handle_event(_event("checkout.session.completed", _pack_session(kind="subscription")))
    assert result == {"handled": True, "type": "checkout.session.completed"}
    assert _balance(dynamodb) == 0


@pytest.mark.parametrize("credits", ["0", "", None])
def test_credit_pack_with_no_credits_grants_nothing(team, dynamodb, credits):
    webhooks.handle_event(_event("checkout.session.completed", _pack_session(credits=credits)))
    assert _balance(dynamodb) == 0
    assert billing_db.list_credit_ledger(TEAM) == []


@pytest.mark.xfail(strict=True, reason=(
    "BUG: claim_event() marks the event as processed BEFORE adjust_credits() "
    "runs. If the grant fails (e.g. a transient DynamoDB error), the webhook "
    "500s, Stripe retries, and the retry is skipped as a duplicate - the paid-for "
    "credits are permanently lost. Claim must happen after (or atomically with) "
    "the grant, e.g. via a TransactWriteItems."))
def test_failed_grant_is_retried_successfully(team, dynamodb, monkeypatch):
    ev = _event("checkout.session.completed", _pack_session())
    real = billing_db.adjust_credits

    def transient_failure(*a, **k):
        raise RuntimeError("transient DynamoDB error")

    monkeypatch.setattr(billing_db, "adjust_credits", transient_failure)
    with pytest.raises(RuntimeError):
        webhooks.handle_event(ev)
    monkeypatch.setattr(billing_db, "adjust_credits", real)
    webhooks.handle_event(ev)                        # Stripe's retry
    assert _balance(dynamodb) == 100
