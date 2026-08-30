"""v0.26: note add/list/rm CLI (handler-level, conn injected per the CLI test convention)."""
import json
from argparse import Namespace

import pytest

from src import cli


def _seed(db):
    db.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE')")
    db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
               "VALUES (7,'Morgan Rogers','Rogers',1,'MID','a')")
    db.commit()


def test_note_add_resolves_team_and_player(db, capsys):
    _seed(db)
    args = Namespace(note_command="add", note="rogers takes long shots",
                     team="CHE", player="Rogers", json=True)
    cli._cmd_note_cli(args, conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    note = out["data"]["note"]
    assert note["team_short"] == "CHE" and note["player_name"] == "Rogers"


def test_note_add_unknown_team_errors(db, capsys):
    with pytest.raises(SystemExit):
        cli._cmd_note_cli(Namespace(note_command="add", note="x", team="ZZZ",
                                    player=None, json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NOT_FOUND"


def test_note_add_unknown_player_errors(db, capsys):
    _seed(db)
    with pytest.raises(SystemExit):
        cli._cmd_note_cli(Namespace(note_command="add", note="x", team="CHE",
                                    player="Nobody", json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NOT_FOUND"


def test_note_list_and_rm(db, capsys):
    from src.data import repository
    nid = repository.add_speculation_note(db, "newcastle incredibly good")
    cli._cmd_note_cli(Namespace(note_command="list", json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert [n["id"] for n in out["data"]["notes"]] == [nid]
    cli._cmd_note_cli(Namespace(note_command="rm", id=nid, json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["removed"] is True
    with pytest.raises(SystemExit):
        cli._cmd_note_cli(Namespace(note_command="rm", id=9999, json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NOT_FOUND"
