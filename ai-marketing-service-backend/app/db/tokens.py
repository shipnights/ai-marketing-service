"""Refresh token revocation store (DynamoDB)."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.main import get_item, put_item
from app.db.tables import MAIN_TABLE

logger = logging.getLogger(__name__)

_TOKEN_PK_PREFIX = "REVOKED_TOKEN#"
_SK = "REVOKED"


def revoke_refresh_token(jti: str, expires_in_days: int = 30) -> bool:
    """Mark a refresh token JTI as revoked. Stored with TTL for automatic cleanup."""
    expires_at = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()
    item: dict[str, Any] = {
        "pk": f"{_TOKEN_PK_PREFIX}{jti}",
        "sk": _SK,
        "jti": jti,
        "revoked_at": datetime.now(UTC).isoformat(),
        "expires_at": expires_at,
    }
    return put_item(item, table_name=MAIN_TABLE)


def is_refresh_token_revoked(jti: str) -> bool:
    """Return True if the JTI has been revoked."""
    item = get_item(f"{_TOKEN_PK_PREFIX}{jti}", _SK, table_name=MAIN_TABLE)
    return item is not None
