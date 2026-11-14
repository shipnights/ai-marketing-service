"""Project-listing & list-size contract tests (gaps #15 and #16).

Contract source for #15: the dashboard (frontend src/app/dashboard/page.tsx)
has tabs All / Active / Draft / Archived. "All" calls GET /projects with no
filter and the dedicated "Archived" tab passes ?status=archived - so the
default list MUST include archived projects.

Contract source for #16: the frontend has no pagination UI and expects the
complete list in one response; the Agency plan allows unlimited projects and
ad accounts.
"""

import pytest

from app.db import ad_accounts
from app.db.tables import ACCOUNTS_TABLE, MAIN_TABLE
from app.services.ads.base import TokenBundle
from app.services.ads.schema import AdAccountInfo
from backend.fixtures.factories import make_project


def _seed(dynamodb, team_id, **kw):
    p = make_project(team_id=team_id, **kw)
    dynamodb.Table(MAIN_TABLE).put_item(Item=p)
    return p


def _batch_projects(dynamodb, team_id, n, prefix, status):
    with dynamodb.Table(MAIN_TABLE).batch_writer() as batch:
        for i in range(n):
            batch.put_item(Item=make_project(team_id=team_id, project_id=f"{prefix}-{i:04d}", status=status))


# -- #15 archived projects & status semantics --------------------------------------------

def test_default_list_includes_every_status(app_client, dynamodb, auth_headers, test_team_id):
    """Dashboard 'All' tab == GET /projects without a filter."""
    for status in ("draft", "active", "archived"):
        _seed(dynamodb, test_team_id, name=status, status=status)
    rows = app_client.get("/api/v1/projects", headers=auth_headers).json()["data"]
    assert sorted(p["status"] for p in rows) == ["active", "archived", "draft"]


@pytest.mark.parametrize("status", ["draft", "active", "archived"])
def test_each_dashboard_tab_returns_only_its_status(app_client, dynamodb, auth_headers, test_team_id, status):
    for s in ("draft", "active", "archived"):
        _seed(dynamodb, test_team_id, name=s, status=s)
    rows = app_client.get(f"/api/v1/projects?status={status}", headers=auth_headers).json()["data"]
    assert [p["status"] for p in rows] == [status]


def test_archived_project_can_be_reactivated(app_client, dynamodb, auth_headers, test_team_id):
    """activate_project docstring: 'Transition a draft/archived project to active'."""
    p = _seed(dynamodb, test_team_id, status="archived")
    r = app_client.post(f"/api/v1/projects/{p['project_id']}/activate", headers=auth_headers)
    assert r.json()["data"]["status"] == "active"


def test_client_cannot_choose_initial_status(app_client, auth_headers):
    """Status only changes via activate/archive; a status in the create body is ignored."""
    r = app_client.post("/api/v1/projects", json={"name": "X", "status": "archived"}, headers=auth_headers)
    assert r.json()["data"]["status"] == "draft"


def test_client_cannot_change_status_via_patch(app_client, dynamodb, auth_headers, test_team_id):
    p = _seed(dynamodb, test_team_id, status="draft")
    r = app_client.patch(f"/api/v1/projects/{p['project_id']}", json={"status": "archived"}, headers=auth_headers)
    # Only unknown fields were sent -> nothing to update.
    assert r.status_code == 422
    got = app_client.get(f"/api/v1/projects/{p['project_id']}", headers=auth_headers).json()["data"]
    assert got["status"] == "draft"


def test_archived_project_remains_editable(app_client, dynamodb, auth_headers, test_team_id):
    """Pins CURRENT behaviour (no product rule forbids it): archived projects
    can still be renamed. Revisit if archive is meant to be read-only."""
    p = _seed(dynamodb, test_team_id, status="archived")
    r = app_client.patch(f"/api/v1/projects/{p['project_id']}", json={"name": "Renamed"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["data"]["status"] == "archived"


# -- #16 silent truncation at 100 items ----------------------------------------------------------

def test_listing_up_to_100_projects_is_complete(app_client, dynamodb, auth_headers, test_team_id):
    _batch_projects(dynamodb, test_team_id, 100, "p", "active")
    assert len(app_client.get("/api/v1/projects", headers=auth_headers).json()["data"]) == 100


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: list_projects() makes ONE query_by_pk_sk_begins call (limit=100) "
    "and discards the pagination cursor, so the 101st+ project is silently "
    "missing from the dashboard. The API exposes no cursor either."))
def test_more_than_100_projects_all_listed(app_client, dynamodb, auth_headers, test_team_id):
    _batch_projects(dynamodb, test_team_id, 101, "p", "active")
    assert len(app_client.get("/api/v1/projects", headers=auth_headers).json()["data"]) == 101


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT (worse variant): the status filter is applied in Python AFTER the "
    "100-item cut. With 100 archived projects sorting first, ?status=active "
    "returns [] even though an active project exists."))
def test_status_filter_not_defeated_by_truncation(app_client, dynamodb, auth_headers, test_team_id):
    _batch_projects(dynamodb, test_team_id, 100, "a-archived", "archived")
    _seed(dynamodb, test_team_id, project_id="z-active", name="Live", status="active")
    rows = app_client.get("/api/v1/projects?status=active", headers=auth_headers).json()["data"]
    assert [p["name"] for p in rows] == ["Live"]


def _batch_ad_accounts(dynamodb, team_id, n, platform="google_ads"):
    with dynamodb.Table(ACCOUNTS_TABLE).batch_writer() as batch:
        for i in range(n):
            batch.put_item(Item={"pk": f"TEAM#{team_id}", "sk": f"ADACCT#{platform}#acc-{i:04d}",
                                 "platform": platform, "account_id": f"acc-{i:04d}", "enabled": True})


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: list_ad_accounts() is capped at 100 the same way, so /ads/accounts "
    "and sync_team() silently ignore accounts beyond the first 100 (a Google "
    "Ads manager account can easily exceed that)."))
def test_more_than_100_ad_accounts_all_listed(app_client, dynamodb, auth_headers, test_team_id):
    _batch_ad_accounts(dynamodb, test_team_id, 105)
    assert len(app_client.get("/api/v1/ads/accounts", headers=auth_headers).json()["data"]) == 105


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: disconnect() deletes only the accounts list_ad_accounts() returns "
    "(first 100), leaving orphaned ADACCT rows behind after 'Disconnected'."))
def test_disconnect_removes_all_accounts_beyond_100(app_client, dynamodb, auth_headers, test_team_id):
    ad_accounts.save_connection(test_team_id, "google_ads", TokenBundle(access_token="t"), connected_by="u")
    _batch_ad_accounts(dynamodb, test_team_id, 105)
    assert app_client.delete("/api/v1/integrations/google_ads", headers=auth_headers).status_code == 200
    leftovers = dynamodb.Table(ACCOUNTS_TABLE).query(
        KeyConditionExpression="pk = :p AND begins_with(sk, :s)",
        ExpressionAttributeValues={":p": f"TEAM#{test_team_id}", ":s": "ADACCT#"})["Items"]
    assert leftovers == []
