"""Symmetric encryption for OAuth tokens at rest.

Connected ad/social accounts store long-lived access & refresh tokens. We never
keep those in plaintext in DynamoDB. Encryption uses Fernet (AES-128-CBC + HMAC)
keyed by TOKEN_ENCRYPTION_KEY.

If TOKEN_ENCRYPTION_KEY is unset we fall back to a reversible passthrough so local
dev works without setup — a warning is logged once. Production MUST set the key.
"""

import logging

from app.config import IS_PRODUCTION, TOKEN_ENCRYPTION_KEY

logger = logging.getLogger(__name__)

_PLAINTEXT_PREFIX = "plain:"
_FERNET_PREFIX = "enc:"

_fernet = None
_warned = False


def _get_fernet():
    global _fernet, _warned
    if _fernet is not None:
        return _fernet
    if not TOKEN_ENCRYPTION_KEY:
        if not _warned:
            level = logger.error if IS_PRODUCTION else logger.warning
            level("TOKEN_ENCRYPTION_KEY is not set — OAuth tokens stored WITHOUT encryption.")
            _warned = True
        return None
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(TOKEN_ENCRYPTION_KEY.encode())
        return _fernet
    except Exception:
        logger.exception("Invalid TOKEN_ENCRYPTION_KEY — falling back to plaintext storage.")
        return None


def encrypt(value: str | None) -> str | None:
    """Encrypt a token for storage. Returns a prefixed string the decrypt() below understands."""
    if value is None:
        return None
    f = _get_fernet()
    if f is None:
        return _PLAINTEXT_PREFIX + value
    return _FERNET_PREFIX + f.encrypt(value.encode()).decode()


def decrypt(stored: str | None) -> str | None:
    """Reverse encrypt(). Tolerates legacy unprefixed values (treated as plaintext)."""
    if stored is None:
        return None
    if stored.startswith(_PLAINTEXT_PREFIX):
        return stored[len(_PLAINTEXT_PREFIX):]
    if stored.startswith(_FERNET_PREFIX):
        f = _get_fernet()
        if f is None:
            logger.error("Encrypted token present but no key available to decrypt it.")
            return None
        try:
            return f.decrypt(stored[len(_FERNET_PREFIX):].encode()).decode()
        except Exception:
            logger.exception("Failed to decrypt token.")
            return None
    # Legacy / externally written value with no prefix.
    return stored
