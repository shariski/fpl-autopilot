from src import cli
from src.execution import override


def test_freeze_cli_freezes(db):
    cli._freeze_cli(reason="going on holiday", conn=db)
    assert override.is_frozen(db) is True
    assert override.status(db)["reason"] == "going on holiday"


def test_unfreeze_cli_clears(db):
    override.freeze(db, reason="x", source="user")
    cli._unfreeze_cli(conn=db)
    assert override.is_frozen(db) is False


def test_freeze_status_cli_reports(db, capsys):
    cli._freeze_status_cli(conn=db)
    assert "not frozen" in capsys.readouterr().out
    override.freeze(db, reason="boom", source="auto")
    cli._freeze_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "FROZEN" in out and "boom" in out and "auto" in out


def test_auth_status_shows_freeze_and_relogin(db, capsys):
    from src.data import repository
    override.freeze(db, reason="auth gone", source="auto")
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)
    cli._auth_status_cli(conn=db)
    out = capsys.readouterr().out
    assert "frozen: yes" in out and "relogin_failures: 2" in out
