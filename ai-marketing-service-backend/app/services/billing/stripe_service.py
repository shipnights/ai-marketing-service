"""Stripe operations: customers, subscription checkout, credit-pack checkout, portal.

All write paths go through Stripe Checkout / Billing Portal (hosted) so we never
touch card data. Subscription state is reconciled from webhooks (see webhooks.py),
never trusted from the client redirect.
"""

import logging

from app import config
from app.db import billing as billing_db

logger = logging.getLogger(__name__)


class StripeNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(config.STRIPE_SECRET_KEY)


def _stripe():
    if not config.STRIPE_SECRET_KEY:
        raise StripeNotConfigured("STRIPE_SECRET_KEY is not set.")
    import stripe

    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def ensure_customer(team_id: str, email: str, name: str | None = None) -> str:
    """Return the team's Stripe customer id, creating the customer if needed."""
    record = billing_db.get_billing(team_id)
    customer_id = record.get("stripe_customer_id")
    if customer_id:
        return customer_id

    stripe = _stripe()
    customer = stripe.Customer.create(
        email=email,
        name=name or email,
        metadata={"team_id": team_id},
    )
    billing_db.set_stripe_customer(team_id, customer.id)
    return customer.id


def create_subscription_checkout(team_id: str, email: str, plan_key: str, interval: str) -> str:
    """Hosted Checkout for a subscription. Returns the redirect URL."""
    from app.services.billing.plans import price_id_for

    price_id = price_id_for(plan_key, interval)
    if not price_id:
        raise ValueError(f"No Stripe price configured for plan={plan_key} interval={interval}")

    stripe = _stripe()
    customer_id = ensure_customer(team_id, email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=config.STRIPE_SUCCESS_URL,
        cancel_url=config.STRIPE_CANCEL_URL,
        allow_promotion_codes=True,
        metadata={"team_id": team_id, "plan_key": plan_key, "interval": interval},
        subscription_data={"metadata": {"team_id": team_id, "plan_key": plan_key}},
    )
    return session.url


def create_credit_checkout(team_id: str, email: str, pack_key: str) -> str:
    """Hosted Checkout for a one-time AI credit pack. Returns the redirect URL."""
    from app.services.billing.plans import CREDIT_PACKS

    pack = CREDIT_PACKS.get(pack_key)
    if not pack or not pack["price_id"]:
        raise ValueError(f"No Stripe price configured for credit pack={pack_key}")

    stripe = _stripe()
    customer_id = ensure_customer(team_id, email)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{"price": pack["price_id"], "quantity": 1}],
        success_url=config.STRIPE_SUCCESS_URL,
        cancel_url=config.STRIPE_CANCEL_URL,
        metadata={
            "team_id": team_id,
            "kind": "credit_pack",
            "pack_key": pack_key,
            "credits": str(pack["credits"]),
        },
        payment_intent_data={"metadata": {"team_id": team_id, "credits": str(pack["credits"])}},
    )
    return session.url


def create_portal_session(team_id: str) -> str:
    """Stripe Billing Portal so customers can manage/cancel their subscription."""
    record = billing_db.get_billing(team_id)
    customer_id = record.get("stripe_customer_id")
    if not customer_id:
        raise ValueError("No Stripe customer for this team yet.")
    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=config.STRIPE_PORTAL_RETURN_URL
    )
    return session.url


def construct_event(payload: bytes, signature: str):
    """Verify and parse a webhook event (raises on bad signature)."""
    stripe = _stripe()
    if not config.STRIPE_WEBHOOK_SECRET:
        raise StripeNotConfigured("STRIPE_WEBHOOK_SECRET is not set.")
    return stripe.Webhook.construct_event(payload, signature, config.STRIPE_WEBHOOK_SECRET)
