import pytest
from src.auth import master


def _paths(tmp_path):
    return tmp_path / ".salt", tmp_path / ".verify"


def test_is_initialized(tmp_path):
    s, v = _paths(tmp_path)
    assert master.is_initialized(s, v) is False
    master.init_master_password("throwaway-pw-123", s, v)
    assert master.is_initialized(s, v) is True


def test_init_then_load_simulating_restart(tmp_path):
    s, v = _paths(tmp_path)
    key1 = master.init_master_password("throwaway-pw-123", s, v)
    key2 = master.load_key("throwaway-pw-123", s, v)
    from src.auth import crypto
    assert crypto.decrypt(key2, crypto.encrypt(key1, "x")) == "x"


def test_load_wrong_password_raises(tmp_path):
    s, v = _paths(tmp_path)
    master.init_master_password("right-pw-123456", s, v)
    with pytest.raises(master.MasterPasswordError):
        master.load_key("wrong-pw-123456", s, v)


def test_load_not_initialized_raises(tmp_path):
    s, v = _paths(tmp_path)
    with pytest.raises(master.MasterPasswordError):
        master.load_key("whatever-123456", s, v)


def test_get_master_key_env(tmp_path, monkeypatch):
    s, v = _paths(tmp_path)
    master.init_master_password("env-pw-12345678", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "env-pw-12345678")
    key = master.get_master_key(s, v)
    from src.auth import crypto
    assert crypto.decrypt(key, crypto.encrypt(key, "y")) == "y"


def test_secrets_not_logged(tmp_path, caplog):
    s, v = _paths(tmp_path)
    with caplog.at_level("DEBUG"):
        master.init_master_password("nolog-pw-123456", s, v)
        master.load_key("nolog-pw-123456", s, v)
    assert "nolog-pw-123456" not in caplog.text


def test_init_master_password_cli(tmp_path, monkeypatch, capsys):
    from src import cli
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    pws = iter(["throwaway-pw-123", "throwaway-pw-123"])  # entry + confirm
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(pws))
    cli._init_master_password_cli(salt_path=s, verify_path=v)
    assert s.exists() and v.exists()
    out = capsys.readouterr().out
    assert "UNRECOVERABLE" in out
    assert "throwaway-pw-123" not in out  # never echoes the password


def test_init_master_password_cli_mismatch(tmp_path, monkeypatch, capsys):
    from src import cli
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    pws = iter(["throwaway-pw-123", "different-pw-456"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(pws))
    cli._init_master_password_cli(salt_path=s, verify_path=v)
    assert not s.exists()  # aborted on mismatch
    assert "do not match" in capsys.readouterr().out
