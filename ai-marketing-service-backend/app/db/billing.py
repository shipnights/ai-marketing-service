"""Billing state: Stripe customer mapping, subscription, and AI-credit ledger.

Stored in MAIN_TABLE alongside the team:
    pk                       | sk                    | meaning
    TEAM#{team_id}           | BILLING               | subscription + stripe customer id
    TEAM#{team_id}           | CREDIT#{ts}#{seq}     | append-only credit ledger entry
    STRIPECUST#{customer_id} | TEAM                  | reverse lookup customer → team (webhooks)

The spendable balance itself stays on TEAM#/PROFILE.ai_credits (the field the rest
of the app already reads); `adjust_credits` mutates it atomically and logs a ledger row.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from app.db.main import dynamodb, get_item, put_item, query_by_pk_sk_begins, update_item
from app.db.tables import MAIN_TABLE


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Stripe customer ↔ team mapping ────────────────────────────────────────────

def set_stripe_customer(team_id: str, customer_id: str) -> None:
    update_item(
        f"TEAM#{team_id}", "BILLING",
        {"stripe_customer_id": customer_id, "updated_at": _now()},
        table_name=MAIN_TABLE,
    )
    put_item(
        {"pk": f"STRIPECUST#{customer_id}", "sk": "TEAM", "team_id": team_id},
        table_name=MAIN_TABLE,
    )


def get_team_by_customer(customer_id: str) -> str | None:
    item = get_item(f"STRIPECUST#{customer_id}", "TEAM", table_name=MAIN_TABLE)
    return item.get("team_id") if item else None


# ── Subscription ──────────────────────────────────────────────────────────────

def get_billing(team_id: str) -> dict[str, Any]:
    """Return the billing record, defaulting to a free/unsubscribed shape."""
    item = get_item(f"TEAM#{team_id}", "BILLING", table_name=MAIN_TABLE)
    if not item:
        return {"plan": "free", "status": "inactive", "stripe_customer_id": None}
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


def upsert_subscription(team_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Merge subscription fields (plan, status, period end, ids…) into BILLING."""
    return update_item(
        f"TEAM#{team_id}", "BILLING", {**data, "updated_at": _now()}, table_name=MAIN_TABLE
    )


# ── AI credit ledger ──────────────────────────────────────────────────────────

def adjust_credits(
    team_id: str,
    delta: int,
    *,
    reason: str,
    source: str = "system",
    ref: str | None = None,
    allow_negative: bool = False,
) -> int | None:
    """Atomically add `delta` (may be negative) to the team's ai_credits and write
    a ledger entry. Returns the new balance, or None on failure / insufficient funds.
    """
    table = dynamodb.Table(MAIN_TABLE)
    kwargs: dict[str, Any] = {
        "Key": {"pk": f"TEAM#{team_id}", "sk": "PROFILE"},
        "UpdateExpression": "ADD ai_credits :d SET updated_at = :u",
        "ExpressionAttributeValues": {":d": Decimal(delta), ":u": _now()},
        "ReturnValues": "ALL_NEW",
    }
    if delta < 0 and not allow_negative:
        # guard: only deduct if balance stays >= 0
        kwargs["ConditionExpression"] = "ai_credits >= :need"
        kwargs["ExpressionAttributeValues"][":need"] = Decimal(-delta)
    try:
        resp = table.update_item(**kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None  # insufficient credits
        raise

    new_balance = int(resp["Attributes"].get("ai_credits", 0))
    seq = uuid.uuid4().hex[:8]
    put_item(
        {
            "pk": f"TEAM#{team_id}",
            "sk": f"CREDIT#{_now()}#{seq}",
            "team_id": team_id,
            "delta": delta,
            "balance_after": new_balance,
            "reason": reason,
            "source": source,
            "ref": ref,
            "created_at": _now(),
        },
        table_name=MAIN_TABLE,
    )
    return new_balance


def claim_event(event_id: str) -> bool:
    """Idempotency guard for Stripe webhooks. Returns True the first time an event
    id is seen, False if already processed (so handlers can no-op on retries)."""
    table = dynamodb.Table(MAIN_TABLE)
    try:
        table.put_item(
            Item={"pk": f"STRIPEEVENT#{event_id}", "sk": "SEEN", "created_at": _now()},
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def list_credit_ledger(team_id: str, limit: int = 50) -> list[dict[str, Any]]:
    items, _ = query_by_pk_sk_begins(
        f"TEAM#{team_id}", "CREDIT#", limit=limit, scan_index_forward=False,
        table_name=MAIN_TABLE,
    )
    return [{k: v for k, v in i.items() if k not in ("pk", "sk")} for i in items]
