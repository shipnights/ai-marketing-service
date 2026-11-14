"""Refresh token revocation store (DynamoDB)."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from app.db.main import get_item_strict, put_item
from app.db.tables import MAIN_TABLE

logger = logging.getLogger(__name__)

_TOKEN_PK_PREFIX = "REVOKED_TOKEN#"
_SK = "REVOKED"


def revoke_refresh_token(jti: str, expires_in_days: int = 30) -> bool:
    """Mark a refresh token JTI as revoked. Returns True only if the write
    actually persisted - callers (logout/refresh) must treat False as an
    error, otherwise a token the client believes is dead stays usable.

    The item carries a numeric `ttl` attribute (epoch seconds) so DynamoDB
    TTL can reap it automatically once the underlying token has expired
    anyway (enable TTL on the main table with AttributeName="ttl").
    """
    expiry = datetime.now(UTC) + timedelta(days=expires_in_days)
    item: dict[str, Any] = {
        "pk": f"{_TOKEN_PK_PREFIX}{jti}",
        "sk": _SK,
        "jti": jti,
        "revoked_at": datetime.now(UTC).isoformat(),
        "expires_at": expiry.isoformat(),  # human-readable
        "ttl": int(expiry.timestamp()),    # consumed by DynamoDB TTL
    }
    return put_item(item, table_name=MAIN_TABLE)


def is_refresh_token_revoked(jti: str) -> bool:
    """Return True if the JTI has been revoked.

    FAILS CLOSED: if the revocation store cannot be read we must assume the
    token IS revoked. Returning False on a DB error would silently resurrect
    every revoked token for the duration of an outage.
    """
    try:
        item = get_item_strict(f"{_TOKEN_PK_PREFIX}{jti}", _SK, table_name=MAIN_TABLE)
    except ClientError:
        logger.exception("Revocation store unreadable - failing closed for jti=%s", jti)
        return True
    return item is not None
