"""Unit tests for app/utils/crypto.py - OAuth token encryption at rest."""

import logging

import pytest
from cryptography.fernet import Fernet

import app.utils.crypto as crypto


@pytest.fixture
def no_key(monkeypatch):
    """No TOKEN_ENCRYPTION_KEY: plaintext passthrough mode (dev)."""
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(crypto, "_fernet", None)
    monkeypatch.setattr(crypto, "_warned", False)


@pytest.fixture
def with_key(monkeypatch):
    """A valid Fernet key is configured; the cached instance is reset."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setattr(crypto, "_fernet", None)
    monkeypatch.setattr(crypto, "_warned", False)
    return key


# -- encrypt / decrypt with a key ---------------------------------------------

def test_encrypt_with_key_produces_fernet_ciphertext(with_key):
    stored = crypto.encrypt("secret-access-token")
    assert stored.startswith("enc:")
    assert "secret-access-token" not in stored          # never stored in clear
    assert crypto.decrypt(stored) == "secret-access-token"


def test_encrypt_is_non_deterministic(with_key):
    """Fernet uses a random IV: equal plaintexts must not yield equal ciphertexts
    (otherwise equal tokens are linkable in the database)."""
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_ciphertext_decrypts_with_the_same_key_only(with_key, monkeypatch, caplog):
    stored = crypto.encrypt("tok")
    # Rotate to a different key: old ciphertext must fail closed (None), not crash.
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "_fernet", None)
    with caplog.at_level(logging.ERROR, logger="app.utils.crypto"):
        assert crypto.decrypt(stored) is None
    assert "Failed to decrypt token" in caplog.text


def test_tampered_ciphertext_returns_none(with_key):
    stored = crypto.encrypt("tok")
    tampered = stored[:-4] + ("AAAA" if not stored.endswith("AAAA") else "BBBB")
    assert crypto.decrypt(tampered) is None              # HMAC check rejects it


# -- passthrough mode (no key) ------------------------------------------------

def test_no_key_uses_reversible_plain_prefix(no_key):
    stored = crypto.encrypt("tok")
    assert stored == "plain:tok"
    assert crypto.decrypt(stored) == "tok"


def test_no_key_warns_exactly_once(no_key, caplog):
    with caplog.at_level(logging.WARNING, logger="app.utils.crypto"):
        crypto.encrypt("a")
        crypto.encrypt("b")
    warnings = [r for r in caplog.records if "WITHOUT encryption" in r.getMessage()]
    assert len(warnings) == 1


def test_no_key_in_production_logs_error_not_warning(no_key, monkeypatch, caplog):
    monkeypatch.setattr(crypto, "IS_PRODUCTION", True)
    with caplog.at_level(logging.WARNING, logger="app.utils.crypto"):
        crypto.encrypt("a")
    rec = [r for r in caplog.records if "WITHOUT encryption" in r.getMessage()][0]
    assert rec.levelno == logging.ERROR


def test_encrypted_value_without_key_returns_none(with_key, monkeypatch, caplog):
    """Key removed after data was encrypted: decrypt must fail closed (None)."""
    stored = crypto.encrypt("tok")
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(crypto, "_fernet", None)
    with caplog.at_level(logging.ERROR, logger="app.utils.crypto"):
        assert crypto.decrypt(stored) is None
    assert "no key available" in caplog.text


def test_invalid_key_falls_back_to_plaintext_and_logs(monkeypatch, caplog):
    """Pins CURRENT behaviour: a malformed key silently downgrades to plaintext
    storage (logged at ERROR). See the report - production should fail loudly."""
    monkeypatch.setattr(crypto, "TOKEN_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    monkeypatch.setattr(crypto, "_fernet", None)
    monkeypatch.setattr(crypto, "_warned", False)
    with caplog.at_level(logging.ERROR, logger="app.utils.crypto"):
        stored = crypto.encrypt("tok")
    assert stored == "plain:tok"
    assert "Invalid TOKEN_ENCRYPTION_KEY" in caplog.text


# -- edge inputs --------------------------------------------------------------

@pytest.mark.usefixtures("with_key")
def test_none_passes_through_both_ways():
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None


@pytest.mark.usefixtures("with_key")
def test_legacy_unprefixed_value_returned_as_is():
    assert crypto.decrypt("legacy-raw-token") == "legacy-raw-token"


@pytest.mark.usefixtures("with_key")
@pytest.mark.parametrize("value", ["", "x" * 5000, "t\u00f6k\u00e9n-\U0001f511"])
def test_roundtrip_edge_values(value):
    assert crypto.decrypt(crypto.encrypt(value)) == value


def test_fernet_instance_is_cached(with_key):
    first = crypto._get_fernet()
    assert first is not None
    assert crypto._get_fernet() is first
