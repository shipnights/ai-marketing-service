"""Connected ad-platform accounts & OAuth connections (ACCOUNTS_TABLE).

Item layout (single-table):
    pk                | sk                              | meaning
    TEAM#{team_id}    | ADCONN#{platform}               | OAuth connection (encrypted tokens)
    TEAM#{team_id}    | ADACCT#{platform}#{account_id}  | one ad account under that connection

`platform` is one of services.ads.schema.AD_PLATFORMS. OAuth tokens are encrypted
at rest via app.utils.crypto. Reads return decrypted TokenBundles only through
`load_token()`; raw records keep the ciphertext.
"""

from datetime import UTC, datetime
from typing import Any

from app.db.main import (
    delete_item,
    get_item,
    put_item,
    query_by_pk_sk_begins,
    update_item,
)
from app.db.tables import ACCOUNTS_TABLE
from app.services.ads.base import TokenBundle
from app.services.ads.schema import AdAccountInfo
from app.utils.crypto import decrypt, encrypt


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _conn_sk(platform: str) -> str:
    return f"ADCONN#{platform}"


def _acct_sk(platform: str, account_id: str) -> str:
    return f"ADACCT#{platform}#{account_id}"


# ── Connections (OAuth) ───────────────────────────────────────────────────────

def save_connection(
    team_id: str,
    platform: str,
    token: TokenBundle,
    *,
    connected_by: str,
    scopes: str | None = None,
) -> bool:
    """Create/replace the OAuth connection for a platform, encrypting tokens."""
    now = _now()
    existing = get_item(f"TEAM#{team_id}", _conn_sk(platform), table_name=ACCOUNTS_TABLE)
    item = {
        "pk": f"TEAM#{team_id}",
        "sk": _conn_sk(platform),
        "platform": platform,
        "team_id": team_id,
        "kind": "connection",
        "status": "active",
        "access_token_enc": encrypt(token.access_token),
        "refresh_token_enc": encrypt(token.refresh_token),
        "expires_at": token.expires_at,
        "scope": scopes or token.scope,
        "connected_by": connected_by,
        "connected_at": (existing or {}).get("connected_at", now),
        "updated_at": now,
        "last_error": None,
    }
    return put_item(item, table_name=ACCOUNTS_TABLE)


def get_connection(team_id: str, platform: str) -> dict[str, Any] | None:
    return get_item(f"TEAM#{team_id}", _conn_sk(platform), table_name=ACCOUNTS_TABLE)


def list_connections(team_id: str) -> list[dict[str, Any]]:
    items, _ = query_by_pk_sk_begins(f"TEAM#{team_id}", "ADCONN#", table_name=ACCOUNTS_TABLE)
    return items


def load_token(team_id: str, platform: str) -> TokenBundle | None:
    """Return a decrypted TokenBundle for the connection, or None if not connected."""
    conn = get_connection(team_id, platform)
    if not conn:
        return None
    access = decrypt(conn.get("access_token_enc"))
    if not access:
        return None
    expires = conn.get("expires_at")
    return TokenBundle(
        access_token=access,
        refresh_token=decrypt(conn.get("refresh_token_enc")),
        expires_at=int(expires) if expires is not None else None,
        scope=conn.get("scope"),
    )


def update_connection(team_id: str, platform: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    return update_item(
        f"TEAM#{team_id}", _conn_sk(platform), {**updates, "updated_at": _now()},
        table_name=ACCOUNTS_TABLE,
    )


def store_refreshed_token(team_id: str, platform: str, token: TokenBundle) -> None:
    """Persist a token refreshed mid-request so we don't re-refresh next time."""
    update_connection(team_id, platform, {
        "access_token_enc": encrypt(token.access_token),
        "refresh_token_enc": encrypt(token.refresh_token),
        "expires_at": token.expires_at,
        "status": "active",
        "last_error": None,
    })


def mark_connection_error(team_id: str, platform: str, message: str) -> None:
    update_connection(team_id, platform, {"status": "error", "last_error": message[:500]})


def disconnect(team_id: str, platform: str) -> bool:
    """Remove the connection and all its ad accounts."""
    for acct in list_ad_accounts(team_id, platform):
        delete_item(f"TEAM#{team_id}", _acct_sk(platform, acct["account_id"]), table_name=ACCOUNTS_TABLE)
    return delete_item(f"TEAM#{team_id}", _conn_sk(platform), table_name=ACCOUNTS_TABLE)


# ── Ad accounts ───────────────────────────────────────────────────────────────

def upsert_ad_account(team_id: str, info: AdAccountInfo, *, enabled: bool = True) -> bool:
    now = _now()
    existing = get_item(
        f"TEAM#{team_id}", _acct_sk(info.platform, info.account_id), table_name=ACCOUNTS_TABLE
    )
    item = {
        "pk": f"TEAM#{team_id}",
        "sk": _acct_sk(info.platform, info.account_id),
        "platform": info.platform,
        "team_id": team_id,
        "kind": "ad_account",
        "account_id": info.account_id,
        "name": info.name,
        "currency": info.currency,
        "account_status": info.status,
        "timezone": info.timezone,
        # preserve operator's enable/disable choice and sync cursor across refreshes
        "enabled": (existing or {}).get("enabled", enabled),
        "last_synced_at": (existing or {}).get("last_synced_at"),
        "updated_at": now,
        "created_at": (existing or {}).get("created_at", now),
    }
    return put_item(item, table_name=ACCOUNTS_TABLE)


def list_ad_accounts(team_id: str, platform: str | None = None) -> list[dict[str, Any]]:
    prefix = f"ADACCT#{platform}#" if platform else "ADACCT#"
    items, _ = query_by_pk_sk_begins(f"TEAM#{team_id}", prefix, table_name=ACCOUNTS_TABLE)
    return items


def get_ad_account(team_id: str, platform: str, account_id: str) -> dict[str, Any] | None:
    return get_item(f"TEAM#{team_id}", _acct_sk(platform, account_id), table_name=ACCOUNTS_TABLE)


def set_account_enabled(team_id: str, platform: str, account_id: str, enabled: bool) -> dict[str, Any] | None:
    return update_item(
        f"TEAM#{team_id}", _acct_sk(platform, account_id),
        {"enabled": enabled, "updated_at": _now()}, table_name=ACCOUNTS_TABLE,
    )


def mark_account_synced(team_id: str, platform: str, account_id: str) -> None:
    update_item(
        f"TEAM#{team_id}", _acct_sk(platform, account_id),
        {"last_synced_at": _now()}, table_name=ACCOUNTS_TABLE,
    )
