"""Tests for the reusable auth dependencies in app/api/v1/deps.py.

`get_team_guard` and `require_credits` are not yet wired into any production
route, so they are exercised through a minimal FastAPI app built here. That
pins their contract before the first route starts depending on them.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.deps import CurrentUser, get_current_user, get_team_guard, require_credits
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_team, make_user


@pytest.fixture
def guard_client(dynamodb):
    app = FastAPI()

    @app.get("/teams/{team_id}/secret")
    def team_secret(team_id: str, user: CurrentUser = Depends(get_team_guard)):
        return {"team_id": team_id, "user_id": user.user_id}

    @app.post("/expensive")
    def expensive(user: CurrentUser = require_credits(5)):
        return {"ok": True, "team_id": user.team_id}

    @app.post("/free")
    def free(user: CurrentUser = require_credits(0)):
        return {"ok": True}

    return TestClient(app)


def _hdr(user="u1", team="t1"):
    return {"X-User-Id": user, "X-Team-Id": team}


# -- get_team_guard ---------------------------------------------------------------

class TestTeamGuard:
    def test_member_of_team_allowed(self, guard_client):
        r = guard_client.get("/teams/t1/secret", headers=_hdr(team="t1"))
        assert r.status_code == 200
        assert r.json() == {"team_id": "t1", "user_id": "u1"}

    def test_other_team_forbidden(self, guard_client):
        r = guard_client.get("/teams/t2/secret", headers=_hdr(team="t1"))
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "FORBIDDEN"

    def test_unauthenticated_401_before_team_check(self, guard_client):
        assert guard_client.get("/teams/t1/secret").status_code == 401

    def test_team_id_comparison_is_exact(self, guard_client):
        """No prefix / case-folding tricks: 't1' must not grant 't10' or 'T1'."""
        assert guard_client.get("/teams/t10/secret", headers=_hdr(team="t1")).status_code == 403
        assert guard_client.get("/teams/T1/secret", headers=_hdr(team="t1")).status_code == 403


# -- require_credits ------------------------------------------------------------------

class TestRequireCredits:
    def _team(self, dynamodb, credits):
        dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id="t1", ai_credits=credits))

    def test_sufficient_balance_allowed(self, guard_client, dynamodb):
        self._team(dynamodb, 10)
        r = guard_client.post("/expensive", headers=_hdr())
        assert r.status_code == 200

    def test_exact_balance_allowed(self, guard_client, dynamodb):
        self._team(dynamodb, 5)                        # boundary: balance == cost
        assert guard_client.post("/expensive", headers=_hdr()).status_code == 200

    def test_insufficient_balance_402_with_details(self, guard_client, dynamodb):
        self._team(dynamodb, 4)
        r = guard_client.post("/expensive", headers=_hdr())
        assert r.status_code == 402
        detail = r.json()["detail"]
        assert detail["error"] == "INSUFFICIENT_CREDITS"
        assert detail["required"] == 5
        assert detail["balance"] == 4

    def test_missing_team_treated_as_zero_balance(self, guard_client):
        r = guard_client.post("/expensive", headers=_hdr(team="ghost"))
        assert r.status_code == 402
        assert r.json()["detail"]["balance"] == 0

    def test_zero_cost_always_allowed(self, guard_client):
        assert guard_client.post("/free", headers=_hdr(team="ghost")).status_code == 200

    def test_guard_does_not_deduct(self, guard_client, dynamodb):
        """require_credits only CHECKS; deduction is the handler's job."""
        self._team(dynamodb, 10)
        guard_client.post("/expensive", headers=_hdr())
        team = dynamodb.Table(MAIN_TABLE).get_item(Key={"pk": "TEAM#t1", "sk": "PROFILE"})["Item"]
        assert team["ai_credits"] == 10

    def test_unauthenticated_401(self, guard_client):
        assert guard_client.post("/expensive").status_code == 401


# -- get_current_user: token-type confusion ---------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "SECURITY BUG: decode_access_token() accepts any JWT signed with JWT_SECRET "
    "that has NO token_type claim. The OAuth `state` token from "
    "integrations._sign_state() carries user_id+team_id and no token_type, so it "
    "works as a 15-minute Bearer access token - and it is handed to the ad platform "
    "in the authorize URL (visible in their logs/referrers)."))
def test_oauth_state_token_is_not_an_access_token(app_client, dynamodb):
    from app.api.v1.integrations import _sign_state

    dynamodb.Table(MAIN_TABLE).put_item(Item=make_team(team_id="t-sec"))
    dynamodb.Table(MAIN_TABLE).put_item(Item=make_user(user_id="u-sec", team_id="t-sec", token_version=1))
    state = _sign_state("t-sec", "u-sec", "meta_ads")
    r = app_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {state}"})
    assert r.status_code == 401
