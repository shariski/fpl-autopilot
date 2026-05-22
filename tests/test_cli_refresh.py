from src import cli
from src.data.db import connect, init_db
from src.data.models import BootstrapStatic, EntryPicks, Fixture


class FakeClient:
    def __init__(self, bs, fx, picks):
        self._bs, self._fx, self._picks = bs, fx, picks

    def bootstrap_static(self):
        return self._bs

    def fixtures(self, event=None):
        return self._fx

    def picks(self, team_id, gw):
        return self._picks


def test_refresh_populates_db(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    client = FakeClient(bs, fx, picks)
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"}}

    cli.refresh(full=True, cfg=cfg, conn=conn, client=client, sources=("fpl",))  # FPL-only: no live Understat call

    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"] == len(bs.teams)
    assert conn.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"] == len(fx)
    assert conn.execute("SELECT COUNT(*) c FROM my_team").fetchone()["c"] == 1
    conn.close()


class FakeUnderstatClient:
    def __init__(self, resp):
        self._resp = resp
        self.called = False

    def players_stats(self, season="2025"):
        self.called = True
        return self._resp


class BoomUnderstatClient:
    def players_stats(self, season="2025"):
        raise RuntimeError("understat down")


def _understat_resp(load):
    from src.data.models import UnderstatPlayersResponse
    return UnderstatPlayersResponse.model_validate(load("understat-players.json"))


def test_refresh_populates_understat(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(
        full=True, cfg=cfg, conn=conn,
        client=FakeClient(bs, fx, picks),
        understat_client=FakeUnderstatClient(_understat_resp(load)),
    )
    n = conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"]
    assert n == len(_understat_resp(load).players)
    matched = conn.execute(
        "SELECT COUNT(*) c FROM understat_players WHERE fpl_player_id IS NOT NULL"
    ).fetchone()["c"]
    assert matched >= int(0.95 * n)
    conn.close()


def test_refresh_understat_failure_degrades_gracefully(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(
        full=True, cfg=cfg, conn=conn,
        client=FakeClient(bs, fx, picks),
        understat_client=BoomUnderstatClient(),
    )
    assert conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"] == len(bs.elements)
    assert conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"] == 0
    assert "WARNING" in capsys.readouterr().out
    conn.close()


def test_refresh_source_filter_fpl_only_skips_understat(load):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    uc = FakeUnderstatClient(_understat_resp(load))
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
           "understat": {"season": "2025"}}
    cli.refresh(full=True, cfg=cfg, conn=conn, client=FakeClient(bs, fx, picks),
                understat_client=uc, sources=("fpl",))
    assert uc.called is False
    assert conn.execute("SELECT COUNT(*) c FROM understat_players").fetchone()["c"] == 0
    conn.close()
