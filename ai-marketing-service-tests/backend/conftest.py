"""
Pytest fixtures for ai-marketing-service backend tests.

Run backend from the repo root with:
    pip install -e ../ai-marketing-service-backend
    pytest
"""

import os
import uuid

import boto3
import pytest
from moto import mock_aws

# Force test env before any app import.
os.environ.setdefault("ENABLE_DEV_AUTH_HEADER", "true")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-32chars")
os.environ.setdefault("RATE_LIMIT", "")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
# Point the app at moto's in-memory DynamoDB (no endpoint_url needed with mock_aws)
os.environ.setdefault("DYNAMODB_ENDPOINT_URL", "")

# Table names used by the app under test.
MAIN_TABLE = os.environ.setdefault("DYNAMODB_TABLE_MAIN", "marketing-main-test")
CONTENT_TABLE = os.environ.setdefault("DYNAMODB_TABLE_CONTENT", "marketing-content-test")
SCHEDULE_TABLE = os.environ.setdefault("DYNAMODB_TABLE_SCHEDULE", "marketing-schedule-test")
ANALYTICS_TABLE = os.environ.setdefault("DYNAMODB_TABLE_ANALYTICS", "marketing-analytics-test")
ACCOUNTS_TABLE = os.environ.setdefault("DYNAMODB_TABLE_ACCOUNTS", "marketing-accounts-test")


# ---------------------------------------------------------------------------
# DynamoDB table provisioning (mirrors scripts/create_tables.py)
# ---------------------------------------------------------------------------

def _create_tables(dynamodb):
    """Create all five service tables in moto's in-memory DynamoDB."""

    # MAIN TABLE — users, teams, projects, etc.
    dynamodb.create_table(
        TableName=MAIN_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "project_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
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
        BillingMode="PAY_PER_REQUEST",
    )

    # CONTENT TABLE — generated posts and drafts
    dynamodb.create_table(
        TableName=CONTENT_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "post_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "post_id-index",
                "KeySchema": [{"AttributeName": "post_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # SCHEDULE TABLE — scheduled publishing jobs
    dynamodb.create_table(
        TableName=SCHEDULE_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "post_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "post_id-index",
                "KeySchema": [{"AttributeName": "post_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # ANALYTICS TABLE — engagement metrics
    dynamodb.create_table(
        TableName=ANALYTICS_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # ACCOUNTS TABLE — connected social accounts
    dynamodb.create_table(
        TableName=ACCOUNTS_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "platform", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "platform-index",
                "KeySchema": [{"AttributeName": "platform", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


# ---------------------------------------------------------------------------
# Session-scoped moto context (one mock per pytest session for speed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def aws_credentials():
    """Ensure boto3 uses fake credentials so moto intercepts all calls."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(scope="function")
def dynamodb(aws_credentials):
    """Function-scoped moto DynamoDB resource with all tables pre-created."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        _create_tables(resource)
        yield resource


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def app_client(dynamodb):
    """
    FastAPI TestClient with a fresh in-memory DynamoDB for every test.

    Import is deferred so env vars are already set before app loads.
    """
    from fastapi.testclient import TestClient

    # Patch the db module's boto3 resource to use the moto-backed one.
    import app.db.main as db_module
    db_module.dynamodb = dynamodb  # type: ignore[attr-defined]

    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def test_user_id() -> str:
    return f"user-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_team_id() -> str:
    return f"team-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def auth_headers(test_user_id: str, test_team_id: str) -> dict:
    """
    Dev-auth headers that bypass JWT verification.
    Requires ENABLE_DEV_AUTH_HEADER=true (set above).
    """
    return {
        "X-User-Id": test_user_id,
        "X-Team-Id": test_team_id,
    }


# ---------------------------------------------------------------------------
# Anthropic mock (prevents real API calls in unit/integration tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_anthropic(monkeypatch):
    """
    Replace anthropic.Anthropic with a lightweight stub that returns a
    canned text response. Tests that need specific output should override
    mock_anthropic.return_value directly.
    """
    import unittest.mock as mock

    stub = mock.MagicMock()
    stub.messages.create.return_value = mock.MagicMock(
        content=[mock.MagicMock(text="[MOCK ANTHROPIC RESPONSE]")],
        usage=mock.MagicMock(input_tokens=10, output_tokens=20),
        stop_reason="end_turn",
    )
    monkeypatch.setattr("anthropic.Anthropic", lambda **_: stub)
    return stub
