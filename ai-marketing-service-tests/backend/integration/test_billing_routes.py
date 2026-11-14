"""Integration tests for /api/v1/billing/* - catalog, status, checkout, webhook.

The webhook tests send payloads signed exactly like Stripe does (HMAC-SHA256
over "<timestamp>.<body>") and let the real Stripe SDK verify them - no
network, no mocking of the verification step itself.
"""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
import stripe

from app import config
from app.db import billing as billing_db
from app.db.tables import MAIN_TABLE
from app.services.billing import plans
from backend.fixtures.factories import make_team

WHSEC = "whsec_test_secret"


@pytest.fixture
def team(dynamodb, test_team_id):
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id=test_team_id, ai_credits=40))
    return test_team_id


@pytest.fixture
def stripe_on(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", WHSEC)


@pytest.fixture
def stripe_stub(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe.Customer, "create", lambda **kw: SimpleNamespace(id="cus_new"))

    def session(**kw):
        calls.append(kw)
        return SimpleNamespace(url="https://checkout.stripe.test/x")

    monkeypatch.setattr(stripe.checkout.Session, "create", session)
    monkeypatch.setattr(stripe.billing_portal.Session, "create",
                        lambda **kw: SimpleNamespace(url="https://billing.stripe.test/p"))
    return calls


def _signed(event: dict, secret: str = WHSEC, ts: int | None = None):
    payload = json.dumps(event)
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return payload, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def _pack_event(team_id, eid="evt_pack_1", credits="100"):
    return {"id": eid, "object": "event", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_1", "object": "checkout.session", "customer": None,
                                "metadata": {"kind": "credit_pack", "team_id": team_id,
                                             "credits": credits, "pack_key": "growth"}}}}


def _balance(dynamodb, team_id):
    return dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": f"TEAM#{team_id}", "sk": "PROFILE"})["Item"]["ai_credits"]


# -- catalog / status -----------------------------------------------------------------------

def test_plans_catalog(app_client, auth_headers, monkeypatch):
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "monthly", "price_SECRET_team")
    monkeypatch.setitem(plans.CREDIT_PACKS["pro"], "price_id", "price_SECRET_pro")
    d = app_client.get("/api/v1/billing/plans", headers=auth_headers).json()["data"]
    assert [p["key"] for p in d["plans"]] == ["solo", "team", "agency"]
    assert {p["key"] for p in d["credit_packs"]} == {"starter", "growth", "pro", "agency"}
    assert d["stripe_configured"] is False and d["publishable_key"] is None
    # Checkout resolves price ids server-side; configured ids are never exposed.
    assert "price_SECRET" not in json.dumps(d)
    assert {p["key"]: p["configured"] for p in d["plans"]}["team"] is True


def test_subscription_defaults_for_new_team(app_client, team, auth_headers):
    d = app_client.get("/api/v1/billing/subscription", headers=auth_headers).json()["data"]
    assert d == {"plan": "free", "status": "inactive", "interval": None, "current_period_end": None,
                 "cancel_at_period_end": False, "credits_balance": 40, "has_customer": False}


def test_subscription_reflects_billing_record(app_client, team, auth_headers):
    billing_db.set_stripe_customer(team, "cus_1")
    billing_db.upsert_subscription(team, {"plan": "team", "status": "active", "interval": "annual",
                                          "current_period_end": 1_900_000_000, "cancel_at_period_end": True})
    d = app_client.get("/api/v1/billing/subscription", headers=auth_headers).json()["data"]
    assert (d["plan"], d["status"], d["interval"], d["cancel_at_period_end"], d["has_customer"]) == (
        "team", "active", "annual", True, True)


def test_credits_balance_and_ledger(app_client, team, auth_headers):
    billing_db.adjust_credits(team, 10, reason="bonus")
    d = app_client.get("/api/v1/billing/credits", headers=auth_headers).json()["data"]
    assert d["balance"] == 50
    assert [e["reason"] for e in d["ledger"]] == ["bonus"]
    assert float(d["ledger"][0]["delta"]) == 10


@pytest.mark.xfail(strict=True, reason=(
    "BUG (API contract, app-wide): every route is annotated `-> dict[str, Any]`, "
    "which FastAPI uses as a response model; Pydantic's JSON mode serialises "
    "Decimal - the type DynamoDB returns for EVERY number - as a string. Clients "
    "receive delta='10', balance_after='50', current_period_end='1900000000'. "
    "Convert Decimals (or drop the return annotations) before returning."))
def test_ledger_numbers_are_json_numbers(app_client, team, auth_headers):
    billing_db.adjust_credits(team, 10, reason="bonus")
    entry = app_client.get("/api/v1/billing/credits", headers=auth_headers).json()["data"]["ledger"][0]
    assert entry["delta"] == 10
    assert entry["balance_after"] == 50


@pytest.mark.parametrize("method,path", [
    ("get", "plans"), ("get", "subscription"), ("get", "credits"),
    ("post", "checkout"), ("post", "credits/checkout"), ("post", "portal")])
def test_endpoints_require_auth(app_client, method, path):
    assert getattr(app_client, method)(f"/api/v1/billing/{path}").status_code in (401, 422)


# -- checkout -------------------------------------------------------------------------------

def test_checkout_invalid_interval(app_client, auth_headers):
    r = app_client.post("/api/v1/billing/checkout", json={"plan_key": "team", "interval": "weekly"},
                        headers=auth_headers)
    assert r.status_code == 422 and r.json()["code"] == "BAD_INTERVAL"


def test_checkout_stripe_not_configured_503(app_client, auth_headers, monkeypatch):
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "monthly", "price_team_m")
    r = app_client.post("/api/v1/billing/checkout", json={"plan_key": "team"}, headers=auth_headers)
    assert r.status_code == 503 and r.json()["code"] == "STRIPE_NOT_CONFIGURED"


def test_checkout_unpriced_plan_422(app_client, auth_headers, stripe_on, stripe_stub):
    r = app_client.post("/api/v1/billing/checkout", json={"plan_key": "team"}, headers=auth_headers)
    assert r.status_code == 422 and r.json()["code"] == "BAD_PLAN"
    assert stripe_stub == []


def test_checkout_success_returns_url(app_client, team, auth_headers, stripe_on, stripe_stub, monkeypatch):
    monkeypatch.setitem(plans.PLANS["team"]["prices"], "monthly", "price_team_m")
    r = app_client.post("/api/v1/billing/checkout", json={"plan_key": "team"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"] == {"url": "https://checkout.stripe.test/x"}
    assert stripe_stub[0]["metadata"]["team_id"] == team       # team from auth, not client input
    assert billing_db.get_team_by_customer("cus_new") == team


def test_credit_checkout_bad_pack_422(app_client, auth_headers, stripe_on, stripe_stub):
    r = app_client.post("/api/v1/billing/credits/checkout", json={"pack_key": "mega"}, headers=auth_headers)
    assert r.status_code == 422 and r.json()["code"] == "BAD_PACK"


def test_credit_checkout_success(app_client, team, auth_headers, stripe_on, stripe_stub, monkeypatch):
    monkeypatch.setitem(plans.CREDIT_PACKS["pro"], "price_id", "price_pro")
    r = app_client.post("/api/v1/billing/credits/checkout", json={"pack_key": "pro"}, headers=auth_headers)
    assert r.status_code == 200
    assert stripe_stub[0]["metadata"]["credits"] == "300"


def test_portal_without_customer_400(app_client, team, auth_headers, stripe_on):
    r = app_client.post("/api/v1/billing/portal", headers=auth_headers)
    assert r.status_code == 400 and r.json()["code"] == "NO_CUSTOMER"


def test_portal_not_configured_503(app_client, team, auth_headers):
    billing_db.set_stripe_customer(team, "cus_1")
    r = app_client.post("/api/v1/billing/portal", headers=auth_headers)
    assert r.status_code == 503


def test_portal_success(app_client, team, auth_headers, stripe_on, stripe_stub):
    billing_db.set_stripe_customer(team, "cus_1")
    r = app_client.post("/api/v1/billing/portal", headers=auth_headers)
    assert r.json()["data"] == {"url": "https://billing.stripe.test/p"}


# -- webhook --------------------------------------------------------------------------------

def test_webhook_not_configured_503(app_client):
    r = app_client.post("/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 503


def test_webhook_bad_signature_400(app_client, team, dynamodb, stripe_on):
    payload, headers = _signed(_pack_event(team), secret="whsec_WRONG")
    r = app_client.post("/api/v1/billing/webhook", content=payload, headers=headers)
    assert r.status_code == 400 and r.json()["code"] == "BAD_SIGNATURE"
    assert _balance(dynamodb, team) == 40                      # nothing granted


def test_webhook_missing_signature_400(app_client, team, stripe_on):
    r = app_client.post("/api/v1/billing/webhook", content=json.dumps(_pack_event(team)))
    assert r.status_code == 400


def test_webhook_tampered_body_400(app_client, team, dynamodb, stripe_on):
    payload, headers = _signed(_pack_event(team, credits="1"))
    tampered = payload.replace('"credits": "1"', '"credits": "999999"')
    r = app_client.post("/api/v1/billing/webhook", content=tampered, headers=headers)
    assert r.status_code == 400
    assert _balance(dynamodb, team) == 40


def test_webhook_stale_timestamp_rejected(app_client, team, stripe_on):
    """Replay protection: signatures older than Stripe's 300 s tolerance fail."""
    payload, headers = _signed(_pack_event(team), ts=int(time.time()) - 3600)
    assert app_client.post("/api/v1/billing/webhook", content=payload, headers=headers).status_code == 400


def test_webhook_valid_event_grants_credits_once(app_client, team, dynamodb, stripe_on):
    """Unauthenticated by design (no auth headers) - trusted via signature."""
    payload, headers = _signed(_pack_event(team))
    r = app_client.post("/api/v1/billing/webhook", content=payload, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"received": True, "handled": True, "type": "checkout.session.completed",
                        "team_id": team, "credits_granted": 100}
    assert _balance(dynamodb, team) == 140
    # Stripe redelivers the same event -> acknowledged, but no double grant.
    payload2, headers2 = _signed(_pack_event(team))
    assert app_client.post("/api/v1/billing/webhook", content=payload2, headers=headers2).status_code == 200
    assert _balance(dynamodb, team) == 140


def test_webhook_unhandled_event_type_acknowledged(app_client, stripe_on):
    payload, headers = _signed({"id": "evt_x", "object": "event", "type": "customer.created",
                                "data": {"object": {"id": "cus_1", "object": "customer"}}})
    r = app_client.post("/api/v1/billing/webhook", content=payload, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"received": True, "handled": False, "type": "customer.created"}


def test_webhook_subscription_event_updates_status(app_client, team, auth_headers, stripe_on, monkeypatch):
    monkeypatch.setitem(plans.PLANS["agency"]["prices"], "annual", "price_agency_y")
    event = {"id": "evt_sub", "object": "event", "type": "customer.subscription.created",
             "data": {"object": {"id": "sub_9", "object": "subscription", "status": "active",
                                 "metadata": {"team_id": team},
                                 "items": {"object": "list", "data": [{"price": {"id": "price_agency_y"}}]}}}}
    payload, headers = _signed(event)
    assert app_client.post("/api/v1/billing/webhook", content=payload, headers=headers).status_code == 200
    d = app_client.get("/api/v1/billing/subscription", headers=auth_headers).json()["data"]
    assert (d["plan"], d["interval"], d["status"]) == ("agency", "annual", "active")
