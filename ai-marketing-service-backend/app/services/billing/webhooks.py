"""Stripe webhook event handling — the source of truth for subscription state.

Handled events:
    checkout.session.completed     → credit-pack purchases grant credits
    customer.subscription.created  → set plan/status
    customer.subscription.updated  → set plan/status/period/cancel flag
    customer.subscription.deleted  → downgrade to free
    invoice.paid                   → grant the plan's monthly credit allotment (renewals)
    invoice.payment_failed         → mark past_due

Credit grants are idempotent via billing_db.claim_event(event_id).
"""

import logging
from typing import Any

from app.db import billing as billing_db
from app.services.billing.plans import get_plan, pack_for_price_id, plan_for_price_id

logger = logging.getLogger(__name__)


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    handler = _HANDLERS.get(etype)
    if not handler:
        return {"handled": False, "type": etype}
    try:
        result = handler(event, obj) or {}
    except Exception:
        logger.exception("Error handling Stripe event %s", etype)
        raise
    return {"handled": True, "type": etype, **result}


def _resolve_team(obj: dict, customer_id: str | None) -> str | None:
    team_id = (obj.get("metadata") or {}).get("team_id")
    if team_id:
        return team_id
    if customer_id:
        return billing_db.get_team_by_customer(customer_id)
    return None


# ── Subscriptions ─────────────────────────────────────────────────────────────

def _on_subscription_change(event: dict, sub: dict) -> dict:
    customer_id = sub.get("customer")
    team_id = _resolve_team(sub, customer_id)
    if not team_id:
        logger.warning("subscription event with no resolvable team: %s", sub.get("id"))
        return {}

    price_id = _first_price_id(sub)
    plan_key, interval = plan_for_price_id(price_id) if price_id else (None, None)
    status = sub.get("status", "active")

    billing_db.upsert_subscription(team_id, {
        "plan": plan_key or "unknown",
        "interval": interval,
        "status": status,
        "stripe_subscription_id": sub.get("id"),
        "current_period_end": sub.get("current_period_end"),
        "cancel_at_period_end": sub.get("cancel_at_period_end", False),
    })
    return {"team_id": team_id, "plan": plan_key, "status": status}


def _on_subscription_deleted(event: dict, sub: dict) -> dict:
    team_id = _resolve_team(sub, sub.get("customer"))
    if not team_id:
        return {}
    billing_db.upsert_subscription(team_id, {
        "plan": "free", "status": "canceled",
        "stripe_subscription_id": sub.get("id"), "cancel_at_period_end": False,
    })
    return {"team_id": team_id, "plan": "free"}


# ── Invoices (renewals → credit grants) ───────────────────────────────────────

def _on_invoice_paid(event: dict, invoice: dict) -> dict:
    # Only subscription invoices grant the recurring monthly allotment.
    if invoice.get("billing_reason") not in ("subscription_create", "subscription_cycle", "subscription_update"):
        return {}
    team_id = _resolve_team(invoice, invoice.get("customer"))
    if not team_id:
        return {}
    price_id = _invoice_price_id(invoice)
    plan_key, _ = plan_for_price_id(price_id) if price_id else (None, None)
    if not plan_key:
        return {}
    credits = get_plan(plan_key).get("monthly_credits", 0)
    if credits and billing_db.claim_event(event["id"]):
        billing_db.adjust_credits(
            team_id, credits, reason=f"{plan_key} plan monthly credits",
            source="stripe", ref=invoice.get("id"),
        )
    return {"team_id": team_id, "plan": plan_key, "credits_granted": credits}


def _on_invoice_failed(event: dict, invoice: dict) -> dict:
    team_id = _resolve_team(invoice, invoice.get("customer"))
    if team_id:
        billing_db.upsert_subscription(team_id, {"status": "past_due"})
    return {"team_id": team_id}


# ── Checkout (one-time credit packs) ──────────────────────────────────────────

def _on_checkout_completed(event: dict, session: dict) -> dict:
    meta = session.get("metadata") or {}
    if meta.get("kind") != "credit_pack":
        return {}  # subscription checkouts are reconciled via subscription events
    team_id = _resolve_team(session, session.get("customer"))
    if not team_id:
        return {}
    credits = int(meta.get("credits", 0) or 0)
    if credits and billing_db.claim_event(event["id"]):
        billing_db.adjust_credits(
            team_id, credits, reason=f"Credit pack: {meta.get('pack_key')}",
            source="stripe", ref=session.get("id"),
        )
    return {"team_id": team_id, "credits_granted": credits}


# ── Helpers to dig price ids out of Stripe objects ────────────────────────────

def _first_price_id(sub: dict) -> str | None:
    try:
        return sub["items"]["data"][0]["price"]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def _invoice_price_id(invoice: dict) -> str | None:
    try:
        return invoice["lines"]["data"][0]["price"]["id"]
    except (KeyError, IndexError, TypeError):
        return None


_HANDLERS = {
    "checkout.session.completed": _on_checkout_completed,
    "customer.subscription.created": _on_subscription_change,
    "customer.subscription.updated": _on_subscription_change,
    "customer.subscription.deleted": _on_subscription_deleted,
    "invoice.paid": _on_invoice_paid,
    "invoice.payment_failed": _on_invoice_failed,
}
