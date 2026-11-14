"""Self-tests for the shared test infrastructure (README Phase 0.2 / 0.3):
respx platform fixtures, entity factories, and test-environment isolation."""

import os
from pathlib import Path

import httpx
import pytest
import respx

from app.db.tables import CONTENT_TABLE, SCHEDULE_TABLE
from app.services.ads.base import TokenBundle
from backend.fixtures.factories import make_audience_analysis, make_idea, make_media, make_schedule

TESTS_REPO = Path(__file__).resolve().parents[2]


# -- respx platform fixtures -------------------------------------------------------------

def test_mock_meta_api_drives_real_connector(mock_meta_api, monkeypatch):
    import app.services.ads.meta as meta

    monkeypatch.setattr(meta, "META_APP_ID", "a")
    monkeypatch.setattr(meta, "META_APP_SECRET", "s")
    mock_meta_api.get("/v21.0/me/adaccounts").respond(json={"data": [{"id": "act_9", "account_status": 1}]})
    (acct,) = meta.MetaAdsConnector().list_ad_accounts(TokenBundle(access_token="t"))
    assert acct.account_id == "act_9"


def test_mock_tiktok_api_drives_real_connector(mock_tiktok_api):
    import app.services.ads.tiktok as tiktok

    mock_tiktok_api.get("/open_api/v1.3/campaign/get/").respond(
        json={"code": 0, "data": {"list": [{"campaign_id": 5, "operation_status": "ENABLE"}]}})
    (c,) = tiktok.TikTokAdsConnector().fetch_campaigns(TokenBundle(access_token="t"), "adv")
    assert (c.campaign_id, c.status) == ("5", "active")


def test_mock_google_and_reddit_fixtures_accept_absolute_routes(mock_google_ads_api, mock_reddit_api):
    mock_reddit_api.get("https://ads-api.reddit.com/api/v3/ad_accounts").respond(json={"data": []})
    with httpx.Client() as client:
        assert client.get("https://ads-api.reddit.com/api/v3/ad_accounts").json() == {"data": []}


def test_unmocked_request_is_blocked_never_hits_network(mock_meta_api):
    """The safety net: any request without a declared route raises."""
    with pytest.raises(respx.models.AllMockedAssertionError):
        with httpx.Client() as client:
            client.get("https://graph.facebook.com/v21.0/me")


# -- factories ------------------------------------------------------------------------------

def test_make_audience_analysis_matches_documented_shape():
    a = make_audience_analysis("p1")
    assert {"primary_audience", "secondary_audience", "messaging_angles", "best_platforms",
            "best_content_types", "recommended_posting_times"} <= set(a)
    assert {"description", "psychographics", "behaviors", "platforms_used"} <= set(a["primary_audience"])
    assert make_audience_analysis("p1", best_platforms=["facebook"])["best_platforms"] == ["facebook"]


def test_make_idea_is_storable_and_uses_documented_keys(dynamodb):
    idea = make_idea("p1", status="rejected", estimated_impact="low")
    assert idea["pk"] == "PROJECT#p1" and idea["sk"] == f"IDEA#{idea['idea_id']}"
    assert idea["estimated_impact"] in ("low", "medium", "high")
    assert idea["status"] == "rejected"
    dynamodb.Table(CONTENT_TABLE).put_item(Item=idea)
    stored = dynamodb.Table(CONTENT_TABLE).get_item(Key={"pk": idea["pk"], "sk": idea["sk"]})["Item"]
    assert stored["title"] == idea["title"]


def test_make_schedule_sort_key_orders_chronologically(dynamodb):
    early = make_schedule("post-b", "t1", scheduled_at="2024-05-01T09:00:00+00:00")
    late = make_schedule("post-a", "t1", scheduled_at="2024-05-02T09:00:00+00:00")
    t = dynamodb.Table(SCHEDULE_TABLE)
    t.put_item(Item=late)
    t.put_item(Item=early)
    items = t.query(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": "TEAM#t1"})["Items"]
    assert [i["post_id"] for i in items] == ["post-b", "post-a"]        # by time, not post id
    assert make_schedule("p", "t")["scheduled_at"]                        # default: tomorrow


def test_make_media_image_vs_video(dynamodb):
    img, vid = make_media("p1"), make_media("p1", kind="video")
    assert img["duration_s"] is None and vid["duration_s"] == 15
    assert img["sk"].startswith("MEDIA#") and img["url"].startswith("http")
    dynamodb.Table(CONTENT_TABLE).put_item(Item=img)


def test_factories_generate_unique_ids():
    assert make_idea("p")["idea_id"] != make_idea("p")["idea_id"]
    assert make_media("p")["media_id"] != make_media("p")["media_id"]


# -- environment isolation ---------------------------------------------------------------------

def test_media_path_is_outside_the_repository():
    """app.main creates MEDIA_LOCAL_PATH at import; it must not land in the repo."""
    from app.config import MEDIA_LOCAL_PATH

    media = Path(MEDIA_LOCAL_PATH).resolve()
    assert media.is_dir()
    assert TESTS_REPO not in media.parents and media != TESTS_REPO / "uploads"


def test_test_environment_flags():
    from app import config

    assert config.ENABLE_DEV_AUTH_HEADER is True
    assert config.RATE_LIMIT == ""                    # limiter off for the main suite
    assert config.DYNAMODB_MAIN_TABLE == os.environ["DYNAMODB_MAIN_TABLE"]
