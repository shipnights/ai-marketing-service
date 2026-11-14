"""
Create all DynamoDB tables for local development.
Idempotent — skips tables that already exist.

Usage:
    python scripts/create_tables.py
"""

import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.getenv("DYNAMODB_ENDPOINT_URL", "http://localhost:8001") or None

ENV = os.getenv("ENV", "dev")

MAIN_TABLE = os.getenv("DYNAMODB_MAIN_TABLE", f"ai-marketing-{ENV}-main")
CONTENT_TABLE = os.getenv("DYNAMODB_CONTENT_TABLE", f"ai-marketing-{ENV}-content")
SCHEDULE_TABLE = os.getenv("DYNAMODB_SCHEDULE_TABLE", f"ai-marketing-{ENV}-schedule")
ANALYTICS_TABLE = os.getenv("DYNAMODB_ANALYTICS_TABLE", f"ai-marketing-{ENV}-analytics")
ACCOUNTS_TABLE = os.getenv("DYNAMODB_ACCOUNTS_TABLE", f"ai-marketing-{ENV}-accounts")

dynamodb = boto3.client(
    "dynamodb",
    region_name=AWS_REGION,
    endpoint_url=DYNAMODB_ENDPOINT_URL,
)


def table_exists(name: str) -> bool:
    try:
        dynamodb.describe_table(TableName=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def create_table(name: str, spec: dict) -> None:
    if table_exists(name):
        print(f"  SKIP  {name} (already exists)")
        return
    dynamodb.create_table(TableName=name, **spec)
    dynamodb.get_waiter("table_exists").wait(TableName=name)
    print(f"  OK    {name}")


def enable_ttl(name: str, attribute: str = "ttl") -> None:
    """Enable DynamoDB TTL on `attribute` (numeric epoch seconds).

    Idempotent: skips when TTL is already enabled. Used by the revocation
    store (REVOKED_TOKEN#... items) so revoked-token markers are reaped
    automatically once the underlying refresh token has expired anyway.
    """
    status = dynamodb.describe_time_to_live(TableName=name)
    current = status.get("TimeToLiveDescription", {}).get("TimeToLiveStatus", "DISABLED")
    if current in ("ENABLED", "ENABLING"):
        print(f"  SKIP  TTL on {name} (already {current.lower()})")
        return
    dynamodb.update_time_to_live(
        TableName=name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": attribute},
    )
    print(f"  OK    TTL enabled on {name} (attribute: {attribute})")


def main() -> None:
    print(f"Creating DynamoDB tables (endpoint: {DYNAMODB_ENDPOINT_URL})\n")

    # ── MAIN TABLE — users, teams, projects ───────────────────────────────────
    # pk  | sk
    # USER#{user_id}     | PROFILE          → user record
    # EMAIL#{email}      | USER             → email → user_id lookup
    # TEAM#{team_id}     | PROFILE          → team record
    # TEAM#{team_id}     | MEMBER#{user_id} → team membership
    # TEAM#{team_id}     | PROJECT#{project_id} → project record
    # PROJECT#{project_id} | PROFILE        → direct project lookup (via GSI)
    # REVOKED_TOKEN#{jti} | REVOKED         → revoked refresh tokens
    # TEAM#{team_id}      | BILLING         → subscription + stripe customer id
    # TEAM#{team_id}      | CREDIT#{ts}#{seq} → AI-credit ledger entry
    # STRIPECUST#{cid}    | TEAM            → stripe customer → team reverse lookup
    # STRIPEEVENT#{eid}   | SEEN            → webhook idempotency marker
    create_table(MAIN_TABLE, {
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "project_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "project_id-index",
                "KeySchema": [{"AttributeName": "project_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "user_id-index",
                "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "email-index",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    })

    # ── CONTENT TABLE — ideas, posts, media ───────────────────────────────────
    # pk  | sk
    # PROJECT#{project_id} | IDEA#{idea_id}   → idea record
    # PROJECT#{project_id} | POST#{post_id}   → post record
    # PROJECT#{project_id} | MEDIA#{media_id} → media record
    # POST#{post_id}       | PROFILE           → direct post lookup (via GSI)
    create_table(CONTENT_TABLE, {
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "post_id", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "post_id-index",
                "KeySchema": [{"AttributeName": "post_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    })

    # ── SCHEDULE TABLE — schedules, publish logs ──────────────────────────────
    # pk  | sk
    # TEAM#{team_id}   | SCHEDULE#{scheduled_at}#{post_id} → schedule record
    # POST#{post_id}   | SCHEDULE                           → direct lookup (GSI)
    # POST#{post_id}   | LOG#{timestamp}                    → publish log entry
    create_table(SCHEDULE_TABLE, {
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "post_id", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "post_id-index",
                "KeySchema": [{"AttributeName": "post_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    })

    # ── ANALYTICS TABLE — post metrics, AI insights, ad insights ──────────────
    # pk  | sk
    # POST#{post_id}      | METRICS    → post performance metrics
    # PROJECT#{project_id} | INSIGHT   → latest AI insight report
    # ADSTATS#{team_id}   | {date}#{platform}#{account_id}#{level}#{entity_id}
    #                                  → one normalized ad insight row (date-sorted)
    create_table(ANALYTICS_TABLE, {
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
    })

    # ── ACCOUNTS TABLE — connected social & ad accounts ───────────────────────
    # pk  | sk
    # TEAM#{team_id} | ACCOUNT#{platform}#{account_id}   → connected social account
    # TEAM#{team_id} | ADCONN#{platform}                 → ad-platform OAuth connection (encrypted tokens)
    # TEAM#{team_id} | ADACCT#{platform}#{account_id}    → discovered ad account
    create_table(ACCOUNTS_TABLE, {
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "platform", "AttributeType": "S"},
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "platform-index",
                "KeySchema": [{"AttributeName": "platform", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    })

    # Revoked-token markers carry a numeric `ttl` attribute (epoch seconds).
    enable_ttl(MAIN_TABLE, attribute="ttl")

    print("\nAll tables ready.")


if __name__ == "__main__":
    main()
