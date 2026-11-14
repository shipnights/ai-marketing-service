"""Unit tests for password hashing and verification."""

import pytest

from app.auth.password import hash_password, verify_password


def test_hash_password_returns_string():
    h = hash_password("mypassword123")
    assert isinstance(h, str)
    # Must be an argon2id PHC-format hash, not just "some long string".
    assert h.startswith("$argon2id$")


def test_hash_password_not_plaintext():
    plain = "mypassword123"
    h = hash_password(plain)
    assert plain not in h
    # And the context can identify its own output (guards against scheme
    # misconfiguration silently producing something unverifiable).
    from app.auth.password import pwd_context
    assert pwd_context.identify(h) == "argon2"


def test_hash_password_different_salts():
    """Same password hashed twice should produce different hashes (bcrypt/argon2 salts)."""
    h1 = hash_password("password")
    h2 = hash_password("password")
    assert h1 != h2
    # Different salts must not break verification of either hash.
    assert verify_password("password", h1) is True
    assert verify_password("password", h2) is True


@pytest.mark.parametrize(
    "plain",
    [
        "correct-horse-battery-staple",
        "p\u00e4ssw\u00f6rd-\u00fcnic\u00f8de-\U0001f512",  # unicode survives hashing
        "x" * 100,  # longer than bcrypt's 72-byte limit; argon2 must handle it
    ],
)
def test_verify_password_correct(plain):
    h = hash_password(plain)
    assert verify_password(plain, h) is True


def test_verify_password_wrong():
    h = hash_password("correct_pass")
    assert verify_password("wrong_pass", h) is False
    # Near-misses are still wrong: case and surrounding whitespace matter.
    assert verify_password("Correct_pass", h) is False
    assert verify_password(" correct_pass", h) is False
    assert verify_password("correct_pass ", h) is False


def test_verify_password_empty():
    h = hash_password("somepassword")
    assert verify_password("", h) is False
    # Both-empty must also be False - "no password" never authenticates.
    assert verify_password("", "") is False


@pytest.mark.parametrize(
    "bad_hash",
    [
        "not-a-valid-hash",
        "",                                # missing hash (e.g. OAuth-only account)
        "$argon2id$v=19$m=65536,t=3,p=4$fake$hash"[:20],  # truncated argon2
        "$2b$12$saltsaltsaltsaltsalts.hashhashhashhashhashhashhashhashhas",  # bcrypt format (not configured)
        "plaintext-password-stored-by-mistake",
    ],
)
def test_verify_password_garbage_hash_returns_false(bad_hash):
    """Malformed/unknown stored hashes must yield False, never an exception -
    an exception here turns a login attempt into a 500."""
    assert verify_password("anything", bad_hash) is False
