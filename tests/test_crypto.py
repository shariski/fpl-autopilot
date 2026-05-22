import os
import pytest
from cryptography.fernet import InvalidToken
from src.auth import crypto


def test_derive_key_deterministic():
    salt = b"0123456789abcdef"
    k1 = crypto.derive_key("hunter2-throwaway", salt)
    k2 = crypto.derive_key("hunter2-throwaway", salt)
    assert k1 == k2
    assert crypto.derive_key("hunter2-throwaway", os.urandom(16)) != k1  # different salt -> different key


def test_encrypt_decrypt_roundtrip():
    key = crypto.derive_key("throwaway-pw", b"0123456789abcdef")
    assert crypto.decrypt(key, crypto.encrypt(key, "s3cr3t-value")) == "s3cr3t-value"


def test_wrong_key_fails_loudly():
    salt = b"0123456789abcdef"
    good = crypto.derive_key("right-pw", salt)
    bad = crypto.derive_key("wrong-pw", salt)
    token = crypto.encrypt(good, "value")
    with pytest.raises(InvalidToken):
        crypto.decrypt(bad, token)
