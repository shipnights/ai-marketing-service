"""Tests for the reusable auth dependencies in app/api/v1/deps.py.

`get_team_guard` is not yet wired into any production route, so it is
exercised through a minimal FastAPI app built here. That pins its contract
before the first route starts depending on it.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.deps import CurrentUser, get_current_user, get_team_guard
from app.db.tables import MAIN_TABLE
from backend.fixtures.factories import make_team, make_user


@pytest.fixture
def guard_client(dynamodb):
    app = FastAPI()

    @app.get("/teams/{team_id}/secret")
    def team_secret(team_id: str, user: CurrentUser = Depends(get_team_guard)):
        return {"team_id": team_id, "user_id": user.user_id}

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
