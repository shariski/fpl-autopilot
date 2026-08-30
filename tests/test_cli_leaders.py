"""v0.27: leaders CLI — read + --refresh."""
import json
from argparse import Namespace

from src import cli
from src.data import repository


def _seed(db):
    repository.upsert_leader_entry(db, 1, "P1", "E1", past_rank=100, past_pts=2000,
                                   first_gw=1, rank=1, total=227)
    repository.upsert_leader_snapshot(db, 1, 1, 91, 227, 10937, 0, 1000, 0, 0, "3xc")


def test_leaders_read_json(db, capsys):
    _seed(db)
    cli._cmd_leaders_cli(Namespace(refresh=False, json=True), conn=db)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["data"]["cohort"][0]["entry_id"] == 1
    assert out["data"]["patterns"]["chip_timing"]["rows"][0]["count"] == 1


def test_leaders_refresh_calls_fetch(db, capsys, monkeypatch):
    from src.data import leaders as leaders_data
    seen = {}

    def fake_fetch(conn, client, pages=2, league_id=314):
        seen["called"] = True
        return (3, 3)

    monkeypatch.setattr(leaders_data, "fetch_leader_snapshot", fake_fetch)
    cli._cmd_leaders_cli(Namespace(refresh=True, json=True), conn=db)
    assert seen["called"] is True
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
