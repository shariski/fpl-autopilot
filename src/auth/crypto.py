import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

SALT_BYTES = 16
# Pinned Argon2id parameters (documented in the spec): 32-byte key, 3 iterations,
# 4 lanes, 64 MiB memory_cost (memory_cost is in KiB).


def derive_key(password, salt):
    """Argon2id(password, salt) -> a Fernet key (url-safe base64 of 32 raw bytes)."""
    kdf = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536)
    raw = kdf.derive(password.encode())
    return base64.urlsafe_b64encode(raw)


def encrypt(key, plaintext):
    return Fernet(key).encrypt(plaintext.encode())


def decrypt(key, token):
    return Fernet(key).decrypt(token).decode()
