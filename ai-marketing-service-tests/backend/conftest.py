"""
Pytest fixtures for ai-marketing-service backend tests.

Run backend from the repo root with:
    pip install -e ../ai-marketing-service-backend
    pytest
"""

import os
import tempfile
import uuid

import boto3
import pytest
import respx
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
# app.main creates MEDIA_LOCAL_PATH at import time (default "./uploads", i.e.
# relative to the CWD) - keep it out of the repository.
os.environ.setdefault("MEDIA_LOCAL_PATH", tempfile.mkdtemp(prefix="ai-mkt-test-uploads-"))

# Table names used by the app under test.
# NOTE: these names must match the env vars read by app/config.py
# (DYNAMODB_<NAME>_TABLE), otherwise the app talks to tables that were
# never created in moto and every DynamoDB call fails with
# ResourceNotFoundException.
MAIN_TABLE = os.environ.setdefault("DYNAMODB_MAIN_TABLE", "marketing-main-test")
CONTENT_TABLE = os.environ.setdefault("DYNAMODB_CONTENT_TABLE", "marketing-content-test")
SCHEDULE_TABLE = os.environ.setdefault("DYNAMODB_SCHEDULE_TABLE", "marketing-schedule-test")
ANALYTICS_TABLE = os.environ.setdefault("DYNAMODB_ANALYTICS_TABLE", "marketing-analytics-test")
ACCOUNTS_TABLE = os.environ.setdefault("DYNAMODB_ACCOUNTS_TABLE", "marketing-accounts-test")


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

# ---------------------------------------------------------------------------
# External ad-platform API mocks (respx)
#
# Each fixture yields a respx router scoped to one platform's hosts. Routes
# are declared by the test; ANY request that is not explicitly mocked raises,
# so a test can never reach the real network. `assert_all_called` is off so a
# test may declare optional routes.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_meta_api():
    """Intercept https://graph.facebook.com (Graph / Marketing API).

    Usage: mock_meta_api.get("/v21.0/me/adaccounts").respond(json={...})
    """
    with respx.mock(base_url="https://graph.facebook.com", assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_tiktok_api():
    """Intercept the TikTok Business API (ads) at business-api.tiktok.com.

    Usage: mock_tiktok_api.get("/open_api/v1.3/campaign/get/").respond(json={"code": 0, ...})
    """
    with respx.mock(base_url="https://business-api.tiktok.com", assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_google_ads_api():
    """Intercept Google Ads REST (googleads.googleapis.com) and Google OAuth
    (oauth2.googleapis.com) - routes must use absolute URLs."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_reddit_api():
    """Intercept Reddit Ads (ads-api.reddit.com) and Reddit OAuth
    (www.reddit.com) - routes must use absolute URLs."""
    with respx.mock(assert_all_called=False) as router:
        yield router


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
