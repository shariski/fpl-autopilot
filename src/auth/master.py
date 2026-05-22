import os
import getpass
from cryptography.fernet import InvalidToken
from src import config
from .crypto import derive_key, encrypt, decrypt, SALT_BYTES

_DATA = config.ROOT / "data"
DEFAULT_SALT = _DATA / ".salt"
DEFAULT_VERIFY = _DATA / ".verify"
_VERIFY_PLAINTEXT = "fpl-autopilot-ok"


class MasterPasswordError(Exception):
    """Raised for a missing or wrong master password. Never carries the password value."""


def is_initialized(salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    return salt_path.exists() and verify_path.exists()


def init_master_password(password, salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_BYTES)
    salt_path.write_bytes(salt)
    key = derive_key(password, salt)
    verify_path.write_bytes(encrypt(key, _VERIFY_PLAINTEXT))
    return key


def load_key(password, salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    if not is_initialized(salt_path, verify_path):
        raise MasterPasswordError("master password not initialized; run `fpl-autopilot init-master-password`")
    key = derive_key(password, salt_path.read_bytes())
    try:
        decrypt(key, verify_path.read_bytes())
    except InvalidToken:
        raise MasterPasswordError("wrong master password")
    return key


def get_master_key(salt_path=DEFAULT_SALT, verify_path=DEFAULT_VERIFY):
    password = os.getenv("MASTER_PASSWORD") or getpass.getpass("Master password: ")
    return load_key(password, salt_path, verify_path)
