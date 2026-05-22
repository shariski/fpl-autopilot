import json
from src import cli
from src.auth import master, crypto, fpl_login
from src.data import repository


def test_init_fpl_cli_stores_encrypted(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    monkeypatch.setenv("FPL_EMAIL", "me@example.com")
    monkeypatch.setenv("FPL_PASSWORD", "throwaway-fpl-pw")

    fake = fpl_login.LoginResult(cookies={"pl_profile": "abc"}, csrf="tok", entry_id=3122849)
    cli._init_fpl_cli(conn=db, login_fn=lambda *a, **k: fake, salt_path=s, verify_path=v)

    key = master.load_key("throwaway-master-12", s, v)
    assert crypto.decrypt(key, repository.get_encrypted(db, "fpl_email_encrypted")) == "me@example.com"
    assert crypto.decrypt(key, repository.get_encrypted(db, "fpl_password_encrypted")) == "throwaway-fpl-pw"
    cookies = json.loads(crypto.decrypt(key, repository.get_encrypted(db, "session_cookie_encrypted")))
    assert cookies["pl_profile"] == "abc"
    assert crypto.decrypt(key, repository.get_encrypted(db, "csrf_token_encrypted")) == "tok"
    row = db.execute("SELECT session_last_refreshed FROM credentials WHERE id=1").fetchone()
    assert row["session_last_refreshed"] is not None

    out = capsys.readouterr().out
    assert "3122849" in out
    assert "throwaway-fpl-pw" not in out  # password never echoed


def test_init_fpl_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # intentionally not created
    called = []
    cli._init_fpl_cli(conn=db, login_fn=lambda *a, **k: called.append(1),
                      salt_path=s, verify_path=v)
    assert not called  # login never attempted
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"] == 0


def test_init_fpl_clears_freeze(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    monkeypatch.setenv("FPL_EMAIL", "me@example.com")
    monkeypatch.setenv("FPL_PASSWORD", "throwaway-fpl-pw")
    repository.set_auth_state(db, "frozen")  # pretend we were frozen

    fake = fpl_login.LoginResult(cookies={"pl_profile": "abc"}, csrf="tok", entry_id=3122849)
    cli._init_fpl_cli(conn=db, login_fn=lambda *a, **k: fake, salt_path=s, verify_path=v)

    assert repository.get_auth_state(db) == "active"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 0


def test_auth_status_cli(db, capsys):
    repository.set_auth_state(db, "active")
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "active" in out
    assert "relogin_failures" in out
