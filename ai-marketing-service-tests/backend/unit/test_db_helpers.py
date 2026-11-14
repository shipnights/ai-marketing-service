"""Unit tests for DynamoDB CRUD helpers (requires moto dynamodb fixture)."""

import pytest

from app.db.main import delete_item, get_item, put_item, query_by_pk_sk_begins, query_gsi, update_item
from app.db.tables import MAIN_TABLE


# ── put_item / get_item ────────────────────────────────────────────────────────

def test_put_and_get_item(dynamodb):
    item = {"pk": "TEST#1", "sk": "PROFILE", "name": "Alice"}
    assert put_item(item, table_name=MAIN_TABLE) is True
    fetched = get_item("TEST#1", "PROFILE", table_name=MAIN_TABLE)
    # The whole item must round-trip, keys included - not just one attribute.
    assert fetched == item


def test_put_item_converts_floats_to_decimal(dynamodb):
    """_convert_floats must make float attributes storable (DynamoDB rejects float)."""
    from decimal import Decimal

    item = {"pk": "TEST#float", "sk": "PROFILE", "score": 4.5, "nested": {"ratio": 0.25}, "arr": [1.5]}
    assert put_item(item, table_name=MAIN_TABLE) is True
    fetched = get_item("TEST#float", "PROFILE", table_name=MAIN_TABLE)
    assert fetched["score"] == Decimal("4.5")
    assert fetched["nested"]["ratio"] == Decimal("0.25")
    assert fetched["arr"] == [Decimal("1.5")]


def test_get_item_missing_returns_none(dynamodb):
    result = get_item("NONEXISTENT#1", "PROFILE", table_name=MAIN_TABLE)
    assert result is None


def test_put_item_overwrites(dynamodb):
    put_item({"pk": "TEST#2", "sk": "PROFILE", "val": "old", "obsolete": True}, table_name=MAIN_TABLE)
    put_item({"pk": "TEST#2", "sk": "PROFILE", "val": "new"}, table_name=MAIN_TABLE)
    item = get_item("TEST#2", "PROFILE", table_name=MAIN_TABLE)
    assert item["val"] == "new"
    # put_item REPLACES the item (it does not merge): stale fields must vanish.
    assert "obsolete" not in item


# ── update_item ───────────────────────────────────────────────────────────────

def test_update_item(dynamodb):
    put_item({"pk": "TEST#3", "sk": "PROFILE", "score": 1, "label": "a"}, table_name=MAIN_TABLE)
    # "status" and "name" are DynamoDB reserved words - this only works if
    # update_item escapes attribute names via ExpressionAttributeNames.
    updated = update_item(
        "TEST#3", "PROFILE", {"score": 99, "status": "active", "name": "n"}, table_name=MAIN_TABLE
    )
    assert updated is not None
    assert updated["score"] == 99
    assert updated["status"] == "active"
    assert updated["name"] == "n"
    assert updated["label"] == "a"  # untouched field preserved
    # And the change is persisted, not just echoed via ReturnValues.
    assert get_item("TEST#3", "PROFILE", table_name=MAIN_TABLE) == updated


def test_update_item_no_updates_returns_current(dynamodb):
    original = {"pk": "TEST#4", "sk": "PROFILE", "x": 5}
    put_item(original, table_name=MAIN_TABLE)
    result = update_item("TEST#4", "PROFILE", {}, table_name=MAIN_TABLE)
    # Exact equality proves no write happened (no stray updated_at etc.).
    assert result == original


# ── delete_item ───────────────────────────────────────────────────────────────

def test_delete_item(dynamodb):
    put_item({"pk": "TEST#5", "sk": "PROFILE", "val": "bye"}, table_name=MAIN_TABLE)
    put_item({"pk": "TEST#5", "sk": "OTHER", "val": "stay"}, table_name=MAIN_TABLE)
    assert delete_item("TEST#5", "PROFILE", table_name=MAIN_TABLE) is True
    assert get_item("TEST#5", "PROFILE", table_name=MAIN_TABLE) is None
    # Only the addressed sk is deleted - siblings under the same pk survive.
    assert get_item("TEST#5", "OTHER", table_name=MAIN_TABLE) is not None


def test_delete_nonexistent_returns_true(dynamodb):
    # DynamoDB delete is idempotent — always succeeds
    assert delete_item("MISSING#X", "PROFILE", table_name=MAIN_TABLE) is True


def test_helpers_return_failure_values_on_db_error(dynamodb):
    """Success values (True/item/[]) must be distinguishable from DB failure.

    Regression guard for the table-name mismatch incident: against a
    nonexistent table every helper must signal failure, never success.
    """
    missing = "no-such-table"
    assert put_item({"pk": "A", "sk": "B"}, table_name=missing) is False
    assert delete_item("A", "B", table_name=missing) is False
    assert update_item("A", "B", {"x": 1}, table_name=missing) is None


# ── query_by_pk_sk_begins ──────────────────────────────────────────────────────

def test_query_by_pk_sk_begins(dynamodb):
    put_item({"pk": "TEAM#t1", "sk": "PROJECT#p1", "name": "P1"}, table_name=MAIN_TABLE)
    put_item({"pk": "TEAM#t1", "sk": "PROJECT#p2", "name": "P2"}, table_name=MAIN_TABLE)
    put_item({"pk": "TEAM#t1", "sk": "PROFILE", "name": "Team"}, table_name=MAIN_TABLE)

    items, cursor = query_by_pk_sk_begins("TEAM#t1", "PROJECT#", table_name=MAIN_TABLE)
    names = {i["name"] for i in items}
    assert names == {"P1", "P2"}
    assert cursor is None  # everything fit on one page


def test_query_by_pk_sk_begins_paginates(dynamodb):
    for i in range(3):
        put_item({"pk": "TEAM#pg", "sk": f"PROJECT#p{i}", "name": f"P{i}"}, table_name=MAIN_TABLE)

    page1, cursor = query_by_pk_sk_begins("TEAM#pg", "PROJECT#", limit=2, table_name=MAIN_TABLE)
    assert len(page1) == 2
    assert cursor is not None  # more results remain -> cursor returned

    page2, cursor2 = query_by_pk_sk_begins(
        "TEAM#pg", "PROJECT#", limit=2, exclusive_start_key=cursor, table_name=MAIN_TABLE
    )
    assert {i["name"] for i in page1} | {i["name"] for i in page2} == {"P0", "P1", "P2"}
    assert cursor2 is None


def test_query_by_pk_sk_begins_empty(dynamodb, caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger="app.db.main"):
        items, cursor = query_by_pk_sk_begins("TEAM#missing", "PROJECT#", table_name=MAIN_TABLE)
    assert items == []
    assert cursor is None
    # Distinguish "genuinely empty" from the swallowed-error path, which
    # also returns ([], None) but logs an ERROR.
    assert not caplog.records


# ── query_gsi ──────────────────────────────────────────────────────────────────

def test_query_gsi_email_index(dynamodb):
    put_item(
        {"pk": "EMAIL#alice@example.com", "sk": "USER", "email": "alice@example.com", "user_id": "u1"},
        table_name=MAIN_TABLE,
    )
    # A different email must NOT come back for this key.
    put_item(
        {"pk": "EMAIL#bob@example.com", "sk": "USER", "email": "bob@example.com", "user_id": "u2"},
        table_name=MAIN_TABLE,
    )
    results = query_gsi("email-index", "email", "alice@example.com", table_name=MAIN_TABLE)
    assert len(results) == 1
    assert results[0]["user_id"] == "u1"


def test_query_gsi_honours_limit(dynamodb):
    # Two distinct items sharing the same GSI hash key (email).
    for i in range(2):
        put_item(
            {"pk": f"EMAIL#dup@example.com#{i}", "sk": "USER", "email": "dup@example.com", "user_id": f"u{i}"},
            table_name=MAIN_TABLE,
        )
    assert len(query_gsi("email-index", "email", "dup@example.com", table_name=MAIN_TABLE)) == 2
    assert len(query_gsi("email-index", "email", "dup@example.com", limit=1, table_name=MAIN_TABLE)) == 1


def test_query_gsi_no_results(dynamodb, caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger="app.db.main"):
        results = query_gsi("email-index", "email", "nobody@nowhere.com", table_name=MAIN_TABLE)
    assert results == []
    assert not caplog.records  # empty result, not a swallowed error


# ── increment_attr / get_item_strict ──────────────────────────────────────────

def test_increment_attr_is_atomic_counter(dynamodb):
    """increment_attr: ADD semantics (missing attr -> `by`), optional SET."""
    from app.db.main import increment_attr

    put_item({"pk": "CTR#1", "sk": "PROFILE", "token_version": 1}, table_name=MAIN_TABLE)
    updated = increment_attr(
        "CTR#1", "PROFILE", "token_version", also_set={"flag": "x"}, table_name=MAIN_TABLE
    )
    assert updated["token_version"] == 2
    assert updated["flag"] == "x"
    assert increment_attr("CTR#1", "PROFILE", "token_version", table_name=MAIN_TABLE)["token_version"] == 3
    # Missing attribute is treated as 0.
    put_item({"pk": "CTR#2", "sk": "PROFILE"}, table_name=MAIN_TABLE)
    assert increment_attr("CTR#2", "PROFILE", "token_version", table_name=MAIN_TABLE)["token_version"] == 1
    # Failure path returns None, not a fake item.
    assert increment_attr("CTR#1", "PROFILE", "token_version", table_name="no-such-table") is None


def test_get_item_strict_raises_on_db_error(dynamodb):
    """get_item_strict must PROPAGATE errors (fail loud), unlike get_item."""
    from botocore.exceptions import ClientError

    from app.db.main import get_item_strict

    put_item({"pk": "STRICT#1", "sk": "PROFILE", "v": 1}, table_name=MAIN_TABLE)
    assert get_item_strict("STRICT#1", "PROFILE", table_name=MAIN_TABLE)["v"] == 1
    assert get_item_strict("STRICT#missing", "PROFILE", table_name=MAIN_TABLE) is None
    with pytest.raises(ClientError):
        get_item_strict("STRICT#1", "PROFILE", table_name="no-such-table")


# ── put_items_batch / query_by_pk / query_by_pk_sk_between ─────────────────────

def test_put_items_batch_writes_more_than_25_items(dynamodb):
    """DynamoDB BatchWrite is limited to 25 items per call; the helper must
    chunk transparently (boto3 batch_writer) and persist every item."""
    from app.db.main import put_items_batch, query_by_pk

    items = [{"pk": "BATCH#1", "sk": f"ITEM#{i:03d}", "score": i + 0.5} for i in range(60)]
    assert put_items_batch(items, table_name=MAIN_TABLE) is True
    stored = query_by_pk("BATCH#1", limit=1000, table_name=MAIN_TABLE)
    assert len(stored) == 60
    assert str(stored[0]["score"]) == "0.5"          # floats converted to Decimal


def test_put_items_batch_empty_is_noop_true(dynamodb):
    from app.db.main import put_items_batch

    assert put_items_batch([], table_name=MAIN_TABLE) is True


def test_put_items_batch_failure_returns_false(dynamodb):
    from app.db.main import put_items_batch

    assert put_items_batch([{"pk": "A", "sk": "B"}], table_name="no-such-table") is False


def test_query_by_pk_order_and_limit(dynamodb):
    from app.db.main import query_by_pk

    for sk in ("A", "C", "B"):
        put_item({"pk": "ORD#1", "sk": sk}, table_name=MAIN_TABLE)
    put_item({"pk": "ORD#2", "sk": "Z"}, table_name=MAIN_TABLE)       # other partition
    assert [i["sk"] for i in query_by_pk("ORD#1", table_name=MAIN_TABLE)] == ["A", "B", "C"]
    assert [i["sk"] for i in query_by_pk("ORD#1", scan_index_forward=False, table_name=MAIN_TABLE)] == ["C", "B", "A"]
    assert len(query_by_pk("ORD#1", limit=2, table_name=MAIN_TABLE)) == 2


def test_query_by_pk_error_returns_empty_list(dynamodb):
    from app.db.main import query_by_pk

    assert query_by_pk("X", table_name="no-such-table") == []


def test_query_by_pk_sk_between_is_inclusive_and_ordered(dynamodb):
    from app.db.main import query_by_pk_sk_between

    for d in ("2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"):
        put_item({"pk": "RANGE#1", "sk": d}, table_name=MAIN_TABLE)
    got = query_by_pk_sk_between("RANGE#1", "2024-01-02", "2024-01-03", table_name=MAIN_TABLE)
    assert [i["sk"] for i in got] == ["2024-01-02", "2024-01-03"]
    desc = query_by_pk_sk_between("RANGE#1", "2024-01-01", "2024-01-04",
                                  scan_index_forward=False, table_name=MAIN_TABLE)
    assert [i["sk"] for i in desc] == ["2024-01-04", "2024-01-03", "2024-01-02", "2024-01-01"]


def test_query_by_pk_sk_between_limit_is_a_hard_cap(dynamodb):
    """Pins current behaviour: `limit` caps the TOTAL returned, not just the
    page size. ad_stats relies on the 1000 default - see the truncation xfail
    in test_ad_stats_db.py."""
    from app.db.main import query_by_pk_sk_between

    for i in range(5):
        put_item({"pk": "CAP#1", "sk": f"K{i}"}, table_name=MAIN_TABLE)
    assert len(query_by_pk_sk_between("CAP#1", "K0", "K9", limit=3, table_name=MAIN_TABLE)) == 3


def test_query_by_pk_sk_between_error_returns_empty_list(dynamodb):
    from app.db.main import query_by_pk_sk_between

    assert query_by_pk_sk_between("X", "a", "b", table_name="no-such-table") == []
