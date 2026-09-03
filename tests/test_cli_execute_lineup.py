from src import cli
from src.auth import master


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, current, post_status=200):
        self._current = current
        self._post_status = post_status
        self.posted = None

    def get(self, url, timeout=None):
        return _Resp(200, {"picks": self._current})

    def post(self, url, json=None, timeout=None):
        self.posted = {"url": url, "json": json}
        return _Resp(self._post_status, {})


def _current():
    return [{"element": e, "position": e, "is_captain": e == 1, "is_vice_captain": e == 2}
            for e in range(1, 16)]


def _ranker(conn):
    return {"picks": [{"player_id": 5, "web_name": "Cap", "xp": 8.0},
                      {"player_id": 6, "web_name": "Vice", "xp": 6.0}],
            "vice_player_id": 6}


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def test_execute_lineup_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current())
    cli._execute_lineup_cli(conn=db, salt_path=s, verify_path=v, live=False,
                            session=sess, ranker=_ranker)
    assert sess.posted is None
    assert "DRY-RUN" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 0


def test_execute_lineup_live_confirmed(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    sess = _FakeSession(_current(), post_status=200)
    cli._execute_lineup_cli(conn=db, salt_path=s, verify_path=v, live=True,
                            session=sess, ranker=_ranker, confirm_fn=lambda d: True)
    assert sess.posted is not None
    assert "Submitted" in capsys.readouterr().out
    assert db.execute("SELECT executed FROM activity_log").fetchone()["executed"] == 1


def test_execute_lineup_requires_master_password(tmp_path, monkeypatch, db, capsys):
    s, v = tmp_path / ".salt", tmp_path / ".verify"  # not created
    cli._execute_lineup_cli(conn=db, salt_path=s, verify_path=v, live=False,
                            session=_FakeSession(_current()), ranker=_ranker)
    assert "init-master-password" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_dry_run_prints_pretty_formation(db, capsys):
    """The dry-run output should render an ASCII pitch + bench with C/V markers."""
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?, ?, ?)",
                   [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE"),
                    (3, "Liverpool", "LIV"), (4, "Man City", "MCI")])
    players = [
        # GK
        (101, "Raya", "GKP", 1),
        # DEF (3)
        (102, "Saliba", "DEF", 1),
        (103, "Silva", "DEF", 4),
        (104, "James", "DEF", 2),
        # MID (4)
        (105, "Saka", "MID", 1),
        (106, "Palmer", "MID", 2),
        (107, "Caicedo", "MID", 2),
        (108, "Foden", "MID", 4),
        # FWD (3)
        (109, "Haaland", "FWD", 4),
        (110, "Watkins", "FWD", 3),
        (111, "Pedro", "FWD", 3),
        # Bench
        (112, "BenchGK", "GKP", 1),
        (113, "BenchDef", "DEF", 1),
        (114, "BenchMid", "MID", 2),
        (115, "BenchFwd", "FWD", 3),
    ]
    db.executemany("INSERT INTO players (id, web_name, position, team_id) VALUES (?,?,?,?)",
                   players)
    db.commit()

    picks = [
        {"element": 101, "position": 1, "is_captain": False, "is_vice_captain": False},
        {"element": 102, "position": 2, "is_captain": False, "is_vice_captain": False},
        {"element": 103, "position": 3, "is_captain": False, "is_vice_captain": False},
        {"element": 104, "position": 4, "is_captain": False, "is_vice_captain": False},
        {"element": 105, "position": 5, "is_captain": False, "is_vice_captain": False},
        {"element": 106, "position": 6, "is_captain": True,  "is_vice_captain": False},
        {"element": 107, "position": 7, "is_captain": False, "is_vice_captain": False},
        {"element": 108, "position": 8, "is_captain": False, "is_vice_captain": False},
        {"element": 109, "position": 9, "is_captain": False, "is_vice_captain": False},
        {"element": 110, "position": 10, "is_captain": False, "is_vice_captain": True},
        {"element": 111, "position": 11, "is_captain": False, "is_vice_captain": False},
        {"element": 112, "position": 12, "is_captain": False, "is_vice_captain": False},
        {"element": 113, "position": 13, "is_captain": False, "is_vice_captain": False},
        {"element": 114, "position": 14, "is_captain": False, "is_vice_captain": False},
        {"element": 115, "position": 15, "is_captain": False, "is_vice_captain": False},
    ]
    cli._print_formation_preview(db, {"picks": picks})
    out = capsys.readouterr().out
    assert "Formation: 1-3-4-3" in out
    assert "Raya (ARS)" in out
    assert "Saliba" in out and "Silva" in out and "James" in out
    assert "Haaland (MCI)" in out
    assert "Palmer (C) (CHE)" in out
    assert "Watkins (V) (LIV)" in out
    assert "Bench:" in out
    assert "BenchGK" in out and "BenchFwd" in out
    assert "{" not in out   # raw dict must not leak
