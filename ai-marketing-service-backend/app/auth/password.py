"""Password hashing context (Argon2)."""

import logging

from passlib.context import CryptContext

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored hash.

    Returns False (never raises) when the stored value is missing or is not a
    recognisable password hash - passlib raises UnknownHashError (a ValueError)
    in that case, which would otherwise surface as a 500 instead of a 401.
    """
    if not plain or not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        logger.warning("Password verification failed: stored hash is missing or malformed")
        return False
