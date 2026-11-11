"""Unit tests for DynamoDB CRUD helpers (requires moto dynamodb fixture)."""

import pytest

from app.db.main import delete_item, get_item, put_item, query_by_pk_sk_begins, query_gsi, update_item
from app.db.tables import MAIN_TABLE


# ── put_item / get_item ────────────────────────────────────────────────────────

def test_put_and_get_item(dynamodb):
    item = {"pk": "TEST#1", "sk": "PROFILE", "name": "Alice"}
    assert put_item(item, table_name=MAIN_TABLE) is True
    fetched = get_item("TEST#1", "PROFILE", table_name=MAIN_TABLE)
    assert fetched is not None
    assert fetched["name"] == "Alice"


def test_get_item_missing_returns_none(dynamodb):
    result = get_item("NONEXISTENT#1", "PROFILE", table_name=MAIN_TABLE)
    assert result is None


def test_put_item_overwrites(dynamodb):
    put_item({"pk": "TEST#2", "sk": "PROFILE", "val": "old"}, table_name=MAIN_TABLE)
    put_item({"pk": "TEST#2", "sk": "PROFILE", "val": "new"}, table_name=MAIN_TABLE)
    item = get_item("TEST#2", "PROFILE", table_name=MAIN_TABLE)
    assert item["val"] == "new"


# ── update_item ───────────────────────────────────────────────────────────────

def test_update_item(dynamodb):
    put_item({"pk": "TEST#3", "sk": "PROFILE", "score": 1, "label": "a"}, table_name=MAIN_TABLE)
    updated = update_item("TEST#3", "PROFILE", {"score": 99}, table_name=MAIN_TABLE)
    assert updated is not None
    assert updated["score"] == 99
    assert updated["label"] == "a"  # untouched field preserved


def test_update_item_no_updates_returns_current(dynamodb):
    put_item({"pk": "TEST#4", "sk": "PROFILE", "x": 5}, table_name=MAIN_TABLE)
    result = update_item("TEST#4", "PROFILE", {}, table_name=MAIN_TABLE)
    assert result is not None
    assert result["x"] == 5


# ── delete_item ───────────────────────────────────────────────────────────────

def test_delete_item(dynamodb):
    put_item({"pk": "TEST#5", "sk": "PROFILE", "val": "bye"}, table_name=MAIN_TABLE)
    assert delete_item("TEST#5", "PROFILE", table_name=MAIN_TABLE) is True
    assert get_item("TEST#5", "PROFILE", table_name=MAIN_TABLE) is None


def test_delete_nonexistent_returns_true(dynamodb):
    # DynamoDB delete is idempotent — always succeeds
    assert delete_item("MISSING#X", "PROFILE", table_name=MAIN_TABLE) is True


# ── query_by_pk_sk_begins ──────────────────────────────────────────────────────

def test_query_by_pk_sk_begins(dynamodb):
    put_item({"pk": "TEAM#t1", "sk": "PROJECT#p1", "name": "P1"}, table_name=MAIN_TABLE)
    put_item({"pk": "TEAM#t1", "sk": "PROJECT#p2", "name": "P2"}, table_name=MAIN_TABLE)
    put_item({"pk": "TEAM#t1", "sk": "PROFILE", "name": "Team"}, table_name=MAIN_TABLE)

    items, _ = query_by_pk_sk_begins("TEAM#t1", "PROJECT#", table_name=MAIN_TABLE)
    names = {i["name"] for i in items}
    assert names == {"P1", "P2"}


def test_query_by_pk_sk_begins_empty(dynamodb):
    items, cursor = query_by_pk_sk_begins("TEAM#missing", "PROJECT#", table_name=MAIN_TABLE)
    assert items == []
    assert cursor is None


# ── query_gsi ──────────────────────────────────────────────────────────────────

def test_query_gsi_email_index(dynamodb):
    put_item(
        {"pk": "EMAIL#alice@example.com", "sk": "USER", "email": "alice@example.com", "user_id": "u1"},
        table_name=MAIN_TABLE,
    )
    results = query_gsi("email-index", "email", "alice@example.com", table_name=MAIN_TABLE)
    assert len(results) == 1
    assert results[0]["user_id"] == "u1"


def test_query_gsi_no_results(dynamodb):
    results = query_gsi("email-index", "email", "nobody@nowhere.com", table_name=MAIN_TABLE)
    assert results == []
