"""Unit tests for password hashing and verification."""

import pytest

from app.auth.password import hash_password, verify_password


def test_hash_password_returns_string():
    h = hash_password("mypassword123")
    assert isinstance(h, str)
    assert len(h) > 20


def test_hash_password_not_plaintext():
    plain = "mypassword123"
    h = hash_password(plain)
    assert plain not in h


def test_hash_password_different_salts():
    """Same password hashed twice should produce different hashes (bcrypt/argon2 salts)."""
    h1 = hash_password("password")
    h2 = hash_password("password")
    assert h1 != h2


def test_verify_password_correct():
    plain = "correct-horse-battery-staple"
    h = hash_password(plain)
    assert verify_password(plain, h) is True


def test_verify_password_wrong():
    h = hash_password("correct_pass")
    assert verify_password("wrong_pass", h) is False


def test_verify_password_empty():
    h = hash_password("somepassword")
    assert verify_password("", h) is False


def test_verify_password_garbage_hash_returns_false():
    assert verify_password("anything", "not-a-valid-hash") is False
