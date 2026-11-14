"""DynamoDB client and generic table operations.

All functions accept an optional `table_name` argument. When omitted they
operate on MAIN_TABLE. Domain-specific modules (db/projects.py, db/ideas.py,
etc.) pass their own table name so the same generic helpers serve all tables.
"""

import logging
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import AWS_REGION, DYNAMODB_ENDPOINT_URL
from app.db.tables import MAIN_TABLE

logger = logging.getLogger(__name__)

# ── DynamoDB resource (singleton) ─────────────────────────────────────────────
_kwargs: dict[str, Any] = {"region_name": AWS_REGION}
if DYNAMODB_ENDPOINT_URL:
    _kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL

dynamodb = boto3.resource("dynamodb", **_kwargs)

__all__ = [
    "get_item",
    "get_item_strict",
    "increment_attr",
    "put_item",
    "put_items_batch",
    "update_item",
    "delete_item",
    "query_by_pk",
    "query_by_pk_sk_begins",
    "query_by_pk_sk_between",
    "query_gsi",
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_table(table_name: str | None = None):
    return dynamodb.Table(table_name or MAIN_TABLE)


def _convert_floats(obj: Any) -> Any:
    """Recursively convert float → Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(item) for item in obj]
    return obj


# ── Public CRUD helpers ───────────────────────────────────────────────────────

def get_item(pk: str, sk: str, *, table_name: str | None = None) -> dict[str, Any] | None:
    """Return a single item by pk+sk, or None if not found."""
    t = _get_table(table_name)
    try:
        response = t.get_item(Key={"pk": pk, "sk": sk})
        return response.get("Item")
    except ClientError:
        logger.exception("get_item failed: pk=%s sk=%s", pk, sk)
        return None


def get_item_strict(pk: str, sk: str, *, table_name: str | None = None) -> dict[str, Any] | None:
    """Return a single item by pk+sk, or None if the item does not exist.

    Unlike get_item(), this variant PROPAGATES ClientError instead of
    swallowing it. Use it where "lookup failed" must not be mistaken for
    "item absent" (e.g. security checks such as token revocation, readiness
    probes).
    """
    t = _get_table(table_name)
    response = t.get_item(Key={"pk": pk, "sk": sk})
    return response.get("Item")


def put_item(item: dict[str, Any], *, table_name: str | None = None) -> bool:
    """Write an item. Returns True on success."""
    t = _get_table(table_name)
    try:
        t.put_item(Item=_convert_floats(item))
        return True
    except ClientError:
        logger.exception("put_item failed")
        return False


def update_item(
    pk: str,
    sk: str,
    updates: dict[str, Any],
    *,
    table_name: str | None = None,
) -> dict[str, Any] | None:
    """Update specific attributes of an item. Returns the updated item."""
    if not updates:
        return get_item(pk, sk, table_name=table_name)

    t = _get_table(table_name)
    try:
        expr_attr_names: dict[str, str] = {}
        expr_attr_values: dict[str, Any] = {}
        set_parts: list[str] = []

        for i, (attr_name, attr_value) in enumerate(updates.items()):
            name_ph = f"#attr{i}"
            val_ph = f":val{i}"
            expr_attr_names[name_ph] = attr_name
            expr_attr_values[val_ph] = _convert_floats(attr_value)
            set_parts.append(f"{name_ph} = {val_ph}")

        response = t.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes")
    except ClientError:
        logger.exception("update_item failed: pk=%s sk=%s", pk, sk)
        return None


def increment_attr(
    pk: str,
    sk: str,
    attr: str,
    *,
    by: int = 1,
    also_set: dict[str, Any] | None = None,
    table_name: str | None = None,
) -> dict[str, Any] | None:
    """Atomically increment a numeric attribute (DynamoDB ADD) and optionally
    SET additional attributes in the same write. Returns the updated item, or
    None on failure.

    ADD treats a missing attribute as 0, so an item without the counter ends
    up at `by` after the first call.
    """
    t = _get_table(table_name)
    try:
        expr_attr_names: dict[str, str] = {"#ctr": attr}
        expr_attr_values: dict[str, Any] = {":by": by}
        update_expr = "ADD #ctr :by"

        if also_set:
            set_parts: list[str] = []
            for i, (name, value) in enumerate(also_set.items()):
                expr_attr_names[f"#set{i}"] = name
                expr_attr_values[f":set{i}"] = _convert_floats(value)
                set_parts.append(f"#set{i} = :set{i}")
            update_expr += " SET " + ", ".join(set_parts)

        response = t.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes")
    except ClientError:
        logger.exception("increment_attr failed: pk=%s sk=%s attr=%s", pk, sk, attr)
        return None


def delete_item(pk: str, sk: str, *, table_name: str | None = None) -> bool:
    """Delete an item by pk+sk. Returns True on success."""
    t = _get_table(table_name)
    try:
        t.delete_item(Key={"pk": pk, "sk": sk})
        return True
    except ClientError:
        logger.exception("delete_item failed: pk=%s sk=%s", pk, sk)
        return False


def query_by_pk(
    pk: str,
    *,
    limit: int = 100,
    scan_index_forward: bool = True,
    table_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return all items with the given partition key."""
    t = _get_table(table_name)
    try:
        response = t.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": pk},
            Limit=limit,
            ScanIndexForward=scan_index_forward,
        )
        return response.get("Items", [])
    except ClientError:
        logger.exception("query_by_pk failed: pk=%s", pk)
        return []


def query_by_pk_sk_begins(
    pk: str,
    sk_prefix: str,
    *,
    limit: int = 100,
    scan_index_forward: bool = True,
    exclusive_start_key: dict[str, Any] | None = None,
    filter_expression: str | None = None,
    expression_attribute_values: dict[str, Any] | None = None,
    table_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Query items by pk and sk prefix (begins_with).
    Returns (items, last_evaluated_key) — last_evaluated_key is the pagination cursor.
    """
    t = _get_table(table_name)
    try:
        expr_values: dict[str, Any] = {":pk": pk, ":sk_prefix": sk_prefix}
        if expression_attribute_values:
            expr_values.update(expression_attribute_values)

        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk_prefix)",
            "ExpressionAttributeValues": expr_values,
            "Limit": limit,
            "ScanIndexForward": scan_index_forward,
        }
        if filter_expression:
            kwargs["FilterExpression"] = filter_expression
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = t.query(**kwargs)
        return response.get("Items", []), response.get("LastEvaluatedKey")
    except ClientError:
        logger.exception("query_by_pk_sk_begins failed: pk=%s sk_prefix=%s", pk, sk_prefix)
        return [], None


def put_items_batch(items: list[dict[str, Any]], *, table_name: str | None = None) -> bool:
    """Batch-write items (chunked at 25 per DynamoDB limits). Returns True on success."""
    if not items:
        return True
    t = _get_table(table_name)
    try:
        with t.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=_convert_floats(item))
        return True
    except ClientError:
        logger.exception("put_items_batch failed (%d items)", len(items))
        return False


def query_by_pk_sk_between(
    pk: str,
    sk_lo: str,
    sk_hi: str,
    *,
    limit: int = 1000,
    scan_index_forward: bool = True,
    table_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return items with the given pk whose sk is in [sk_lo, sk_hi] (inclusive).

    Auto-paginates so callers get the full range (insight queries can exceed one page).
    """
    t = _get_table(table_name)
    items: list[dict[str, Any]] = []
    start_key: dict[str, Any] | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": "pk = :pk AND sk BETWEEN :lo AND :hi",
                "ExpressionAttributeValues": {":pk": pk, ":lo": sk_lo, ":hi": sk_hi},
                "Limit": limit,
                "ScanIndexForward": scan_index_forward,
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            response = t.query(**kwargs)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key or len(items) >= limit:
                break
        return items
    except ClientError:
        logger.exception("query_by_pk_sk_between failed: pk=%s", pk)
        return []


def query_gsi(
    index_name: str,
    key_name: str,
    key_value: str,
    *,
    limit: int = 100,
    table_name: str | None = None,
) -> list[dict[str, Any]]:
    """Query a Global Secondary Index by a single key attribute."""
    t = _get_table(table_name)
    try:
        response = t.query(
            IndexName=index_name,
            KeyConditionExpression=f"{key_name} = :val",
            ExpressionAttributeValues={":val": key_value},
            Limit=limit,
        )
        return response.get("Items", [])
    except ClientError:
        logger.exception("query_gsi failed: index=%s key=%s=%s", index_name, key_name, key_value)
        return []
