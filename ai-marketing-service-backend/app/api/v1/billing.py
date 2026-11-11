"""Billing routes: plan catalog, subscription status, Stripe checkout/portal,
AI-credit balance & ledger, and the Stripe webhook receiver.

The webhook (`POST /billing/webhook`) is unauthenticated by design — it is verified
by Stripe signature instead. Exempt it from any API-key guard at the edge.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.v1.deps import CurrentUser, get_current_user
from app.config import STRIPE_PUBLISHABLE_KEY
from app.db import billing as billing_db
from app.db.teams import get_team
from app.responses import success_response
from app.services.billing import stripe_service
from app.services.billing.plans import public_catalog
from app.services.billing.webhooks import handle_event

logger = logging.getLogger(__name__)
router = APIRouter()


def _credits_balance(team_id: str) -> int:
    team = get_team(team_id)
    return int((team or {}).get("ai_credits", 0))


# ── Catalog & status ──────────────────────────────────────────────────────────

@router.get("/plans")
def plans(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return success_response({
        **public_catalog(),
        "stripe_configured": stripe_service.is_configured(),
        "publishable_key": STRIPE_PUBLISHABLE_KEY or None,
    })


@router.get("/subscription")
def subscription(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    record = billing_db.get_billing(current_user.team_id)
    return success_response({
        "plan": record.get("plan", "free"),
        "status": record.get("status", "inactive"),
        "interval": record.get("interval"),
        "current_period_end": record.get("current_period_end"),
        "cancel_at_period_end": record.get("cancel_at_period_end", False),
        "credits_balance": _credits_balance(current_user.team_id),
        "has_customer": bool(record.get("stripe_customer_id")),
    })


@router.get("/credits")
def credits(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return success_response({
        "balance": _credits_balance(current_user.team_id),
        "ledger": billing_db.list_credit_ledger(current_user.team_id),
    })


# ── Checkout / portal ─────────────────────────────────────────────────────────

class CheckoutBody(BaseModel):
    plan_key: str
    interval: str = "monthly"  # monthly | annual


@router.post("/checkout")
def checkout(body: CheckoutBody, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    if body.interval not in ("monthly", "annual"):
        raise HTTPException(status_code=422, detail={"message": "interval must be monthly|annual", "code": "BAD_INTERVAL"})
    try:
        url = stripe_service.create_subscription_checkout(
            current_user.team_id, current_user.email, body.plan_key, body.interval
        )
    except stripe_service.StripeNotConfigured as e:
        raise HTTPException(status_code=503, detail={"message": str(e), "code": "STRIPE_NOT_CONFIGURED"}) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"message": str(e), "code": "BAD_PLAN"}) from e
    return success_response({"url": url})


class CreditCheckoutBody(BaseModel):
    pack_key: str


@router.post("/credits/checkout")
def credits_checkout(body: CreditCheckoutBody, current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        url = stripe_service.create_credit_checkout(current_user.team_id, current_user.email, body.pack_key)
    except stripe_service.StripeNotConfigured as e:
        raise HTTPException(status_code=503, detail={"message": str(e), "code": "STRIPE_NOT_CONFIGURED"}) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"message": str(e), "code": "BAD_PACK"}) from e
    return success_response({"url": url})


@router.post("/portal")
def portal(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        url = stripe_service.create_portal_session(current_user.team_id)
    except stripe_service.StripeNotConfigured as e:
        raise HTTPException(status_code=503, detail={"message": str(e), "code": "STRIPE_NOT_CONFIGURED"}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e), "code": "NO_CUSTOMER"}) from e
    return success_response({"url": url})


# ── Stripe webhook (unauthenticated; verified by signature) ───────────────────

@router.post("/webhook")
async def webhook(request: Request) -> dict[str, Any]:
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe_service.construct_event(payload, signature)
    except stripe_service.StripeNotConfigured as e:
        raise HTTPException(status_code=503, detail={"message": str(e), "code": "STRIPE_NOT_CONFIGURED"}) from e
    except Exception as e:  # signature verification / parse failure
        logger.warning("Stripe webhook verification failed: %s", e)
        raise HTTPException(status_code=400, detail={"message": "Invalid signature", "code": "BAD_SIGNATURE"}) from e

    result = handle_event(event)
    return {"received": True, **result}
