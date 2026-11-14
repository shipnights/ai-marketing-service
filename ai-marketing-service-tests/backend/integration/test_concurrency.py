"""Concurrency tests (gap #17).

Races are made DETERMINISTIC: a threading.Barrier is injected right after the
"read" step of the code under test, so every thread finishes its read before
any thread writes - the exact interleaving that exposes check-then-act bugs.
Operations that are atomic in DynamoDB (ADD / conditional writes) must stay
correct under the same pressure.

moto caveat: moto's in-memory DynamoDB has no locking, so concurrent requests
can corrupt each other INSIDE moto (lost ADDs, double-accepted conditional
puts) - something real DynamoDB never does for a single request. The autouse
fixture below serialises each moto request to restore DynamoDB's per-request
atomicity. App-level races span SEVERAL requests (read ... barrier ... write),
so they remain fully reproducible.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

import app.api.v1.auth as auth_routes
import app.db.tokens as tokens_db
import app.db.users as users_db
from app.auth.jwt import create_refresh_token
from app.auth.password import hash_password
from app.db import billing as billing_db
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_email_index_item, make_team, make_user


def run_concurrently(fn, n):
    """Run fn() in n threads at once; return list of (result | exception)."""
    def wrapped():
        try:
            return fn()
        except Exception as exc:          # collected, asserted by the test
            return exc

    with ThreadPoolExecutor(max_workers=n) as pool:
        return [f.result(timeout=30) for f in [pool.submit(wrapped) for _ in range(n)]]


def barrier_after(monkeypatch, module, name, parties):
    """Wrap module.name so each caller blocks after the real call until all
    `parties` callers have completed their read."""
    barrier = threading.Barrier(parties, timeout=10)
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return result

    monkeypatch.setattr(module, name, wrapper)


def barrier_before(monkeypatch, module, name, parties):
    barrier = threading.Barrier(parties, timeout=10)
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)


@pytest.fixture(autouse=True)
def dynamodb_request_atomicity(monkeypatch):
    """Make each moto request atomic, like a real DynamoDB request."""
    from moto.core.botocore_stubber import BotocoreStubber

    lock = threading.RLock()
    original = BotocoreStubber.__call__

    def atomic_call(self, *args, **kwargs):
        with lock:
            return original(self, *args, **kwargs)

    monkeypatch.setattr(BotocoreStubber, "__call__", atomic_call)


@pytest.fixture
def user(dynamodb):
    team = make_team(ai_credits=100)
    u = make_user(email="race@example.com", team_id=team["team_id"],
                  password_hash=hash_password("password1"), email_verified=True, token_version=1)
    for item in (team, u, make_email_index_item(u["user_id"], "race@example.com")):
        dynamodb.Table(MAIN_TABLE).put_item(Item=item)
    return u


# -- check-then-act races (defects) ------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "SECURITY DEFECT: refresh-token rotation is check-then-act. Two requests "
    "presenting the same refresh token concurrently both pass the revocation "
    "check and BOTH receive a new token pair - a stolen refresh token survives "
    "rotation by racing the legitimate client. Needs a conditional write "
    "(revoke with attribute_not_exists) as the single point of truth."))
def test_parallel_refresh_with_same_token_has_one_winner(user, monkeypatch):
    token = create_refresh_token(user["user_id"], user["team_id"], "race@example.com")
    barrier_after(monkeypatch, tokens_db, "is_refresh_token_revoked", parties=2)
    results = run_concurrently(
        lambda: auth_routes.refresh_tokens(auth_routes.RefreshBody(refresh_token=token)), n=2)
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, HTTPException) and r.status_code == 401]
    assert (len(winners), len(losers)) == (1, 1)


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: registration is check-then-act (get_user_by_email, then "
    "create_user with unconditional puts). Two concurrent sign-ups for the same "
    "email both succeed: two USER# records exist and the EMAIL# lookup points "
    "at whichever wrote last, orphaning the other account."))
def test_parallel_registration_same_email_creates_one_account(dynamodb, monkeypatch):
    barrier_after(monkeypatch, auth_routes, "get_user_by_email", parties=2)
    body = auth_routes.RegisterStep1Body(email="twin@example.com", password="password1")
    results = run_concurrently(lambda: auth_routes.register_step1(body), n=2)
    users = [i for i in dynamodb.Table(MAIN_TABLE).scan()["Items"]
             if i["sk"] == "PROFILE" and i["pk"].startswith("USER#") and i.get("email") == "twin@example.com"]
    assert len(users) == 1
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1


# -- atomic operations (must hold) --------------------------------------------------------------------

def test_parallel_token_version_bumps_are_not_lost(user, dynamodb, monkeypatch):
    """bump_token_version uses an atomic ADD: N concurrent resets -> +N."""
    barrier_before(monkeypatch, users_db, "increment_attr", parties=4)
    run_concurrently(lambda: users_db.bump_token_version(user["user_id"]), n=4)
    row = dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": f"USER#{user['user_id']}", "sk": "PROFILE"})["Item"]
    assert row["token_version"] == 5


def test_parallel_spends_never_overdraw(user, dynamodb):
    """Two concurrent 60-credit spends on a 100-credit balance: exactly one
    succeeds (conditional ADD), balance never goes negative."""
    team_id = user["team_id"]
    start = threading.Barrier(2, timeout=10)

    def spend():
        start.wait()
        return billing_db.adjust_credits(team_id, -60, reason="ai")

    results = run_concurrently(spend, n=2)
    assert sorted(results, key=lambda r: r is None) == [40, None]
    assert len(billing_db.list_credit_ledger(team_id)) == 1


def test_parallel_grants_all_applied(user, dynamodb):
    team_id = user["team_id"]
    start = threading.Barrier(10, timeout=10)

    def grant():
        start.wait()
        return billing_db.adjust_credits(team_id, 10, reason="bonus")

    results = run_concurrently(grant, n=10)
    assert all(isinstance(r, int) for r in results)
    team = dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": f"TEAM#{team_id}", "sk": "PROFILE"})["Item"]
    assert team["ai_credits"] == 200
    assert len(billing_db.list_credit_ledger(team_id)) == 10


def test_parallel_webhook_claims_have_one_winner(dynamodb):
    """claim_event uses a conditional put: a burst of Stripe redeliveries of one
    event is processed exactly once."""
    start = threading.Barrier(5, timeout=10)

    def claim():
        start.wait()
        return billing_db.claim_event("evt_burst")

    assert sorted(run_concurrently(claim, n=5)) == [False, False, False, False, True]
