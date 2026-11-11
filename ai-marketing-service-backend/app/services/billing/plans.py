"""Subscription plan & credit-pack catalog.

Stripe Price IDs are injected from env
(create the Prices in the Stripe dashboard, then set the IDs). Checkout is always
initiated server-side by plan key + interval — the client never passes a price id.
"""

from app import config

# ── Subscription plans ────────────────────────────────────────────────────────

PLANS: dict[str, dict] = {
    "solo": {
        "key": "solo",
        "name": "Solo",
        "monthly_credits": 50,
        "price_monthly_usd": 49,
        "price_annual_usd": 39,  # per month, billed annually
        "prices": {
            "monthly": config.STRIPE_PRICE_SOLO_MONTHLY,
            "annual": config.STRIPE_PRICE_SOLO_ANNUAL,
        },
        "limits": {
            "projects": 2,
            "social_accounts": 3,
            "ad_accounts": 3,
            "team_members": 1,
            "storage_gb": 5,
        },
        "features": ["ai_content", "ai_video_720p", "auto_publish", "ad_stats", "basic_analytics"],
    },
    "team": {
        "key": "team",
        "name": "Team",
        "monthly_credits": 200,
        "price_monthly_usd": 99,
        "price_annual_usd": 79,
        "prices": {
            "monthly": config.STRIPE_PRICE_TEAM_MONTHLY,
            "annual": config.STRIPE_PRICE_TEAM_ANNUAL,
        },
        "limits": {
            "projects": 10,
            "social_accounts": 10,
            "ad_accounts": 10,
            "team_members": 5,
            "storage_gb": 25,
        },
        "features": [
            "ai_content", "ai_video_1080p", "auto_publish", "ad_stats", "ad_management",
            "website_analysis", "profile_analysis", "marketing_plan_30d",
            "competitor_monitoring", "team_collaboration", "roas_tracking",
        ],
    },
    "agency": {
        "key": "agency",
        "name": "Agency",
        "monthly_credits": 600,
        "price_monthly_usd": 249,
        "price_annual_usd": 199,
        "prices": {
            "monthly": config.STRIPE_PRICE_AGENCY_MONTHLY,
            "annual": config.STRIPE_PRICE_AGENCY_ANNUAL,
        },
        "limits": {
            "projects": None,        # unlimited
            "social_accounts": None,
            "ad_accounts": None,
            "team_members": None,
            "storage_gb": 100,
        },
        "features": [
            "ai_content", "ai_video_1080p", "auto_publish", "ad_stats", "ad_management",
            "website_analysis", "profile_analysis", "marketing_plan_90d", "marketing_plan_adapt",
            "competitor_monitoring", "team_collaboration", "roas_tracking",
            "ab_testing", "white_label", "api_access", "webhooks", "priority_support",
        ],
    },
}

FREE_PLAN = {
    "key": "free",
    "name": "Free / Trial",
    "monthly_credits": 0,
    "limits": {"projects": 1, "social_accounts": 1, "ad_accounts": 1, "team_members": 1, "storage_gb": 1},
    "features": ["ai_content"],
}

# ── One-time AI credit top-up packs ───────────────────────────────────────────

CREDIT_PACKS: dict[str, dict] = {
    "starter": {"key": "starter", "name": "Starter Pack", "credits": 25, "price_usd": 12,
                "price_id": config.STRIPE_PRICE_CREDITS_STARTER},
    "growth": {"key": "growth", "name": "Growth Pack", "credits": 100, "price_usd": 39,
               "price_id": config.STRIPE_PRICE_CREDITS_GROWTH},
    "pro": {"key": "pro", "name": "Pro Pack", "credits": 300, "price_usd": 99,
            "price_id": config.STRIPE_PRICE_CREDITS_PRO},
    "agency": {"key": "agency", "name": "Agency Pack", "credits": 1000, "price_usd": 299,
               "price_id": config.STRIPE_PRICE_CREDITS_AGENCY},
}


# ── Lookups ───────────────────────────────────────────────────────────────────

def get_plan(plan_key: str) -> dict:
    return PLANS.get(plan_key, FREE_PLAN)


def price_id_for(plan_key: str, interval: str) -> str | None:
    plan = PLANS.get(plan_key)
    if not plan:
        return None
    return plan["prices"].get(interval) or None


def plan_for_price_id(price_id: str) -> tuple[str | None, str | None]:
    """Reverse a Stripe price id back to (plan_key, interval) — used by webhooks."""
    for key, plan in PLANS.items():
        for interval, pid in plan["prices"].items():
            if pid and pid == price_id:
                return key, interval
    return None, None


def pack_for_price_id(price_id: str) -> dict | None:
    for pack in CREDIT_PACKS.values():
        if pack["price_id"] and pack["price_id"] == price_id:
            return pack
    return None


def public_catalog() -> dict:
    """Plan/pack info safe to expose to the frontend (omits nothing secret —
    price ids are public, but checkout still resolves them server-side)."""
    return {
        "plans": [
            {k: v for k, v in p.items() if k != "prices"} | {
                "configured": bool(p["prices"].get("monthly") or p["prices"].get("annual")),
            }
            for p in PLANS.values()
        ],
        "credit_packs": [
            {k: v for k, v in pack.items() if k != "price_id"} | {"configured": bool(pack["price_id"])}
            for pack in CREDIT_PACKS.values()
        ],
    }
