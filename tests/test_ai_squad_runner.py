import json

from src.ai import cache
from src.ai.squad import runner
from src.data.db import connect, init_db


def _pool():
    return [
        {"player_id": i, "web_name": f"P{i}", "team_short": f"T{i % 5}",
         "position": pos, "price": price, "status": "a", "xp_next": 5.0,
         "xp_6gw": 30.0 - i, "value": (30.0 - i) / price,
         "ownership_pct": 10.0, "form": 3.0}
        for i, (pos, price) in enumerate(
            [("GKP", 5.0)] * 3 + [("DEF", 5.0)] * 7 + [("MID", 7.0)] * 7 + [("FWD", 9.0)] * 5)
    ]


def _seed(conn):
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.commit()


def _signals():
    return {"spikes": [{"player_id": 0, "level": "high", "reason": "clean 30.0 xP"},
                       {"player_id": 1, "level": "medium", "reason": "steady 29.0 xP"}],
            "drops": [{"player_id": 2, "level": "high", "reason": "fades 28.0 xP"}],
            "market_read": "Midfield slate."}


def _setup(monkeypatch):
    from src.ai.squad import spikes as spikes_mod
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})


def test_squad_with_signals_attaches_speculation(db, monkeypatch):
    _seed(db)
    _setup(monkeypatch)
    from src.ai.squad import spikes as spikes_mod
    monkeypatch.setattr(spikes_mod, "generate_spike_signals", lambda *a, **kw: _signals())
    out = runner.generate_squad(db, provider=object(), model_id="m")
    assert out is not None and out["source"] == "ai"
    assert len(out["picks"]) == 15
    assert out["speculation"]["spikes"][0]["player_id"] == 0
    assert "spike calls" in out["template_rationale"]
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='squad'"
                     " ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None and '"speculation"' in row["inputs_json"]


def test_squad_deterministic_when_signals_fail(db, monkeypatch):
    _seed(db)
    _setup(monkeypatch)
    from src.ai.squad import spikes as spikes_mod
    monkeypatch.setattr(spikes_mod, "generate_spike_signals", lambda *a, **kw: None)
    out = runner.generate_squad(db, provider=object(), model_id="m")
    assert out is not None and out["source"] == "deterministic"
    assert out["speculation"] is None
    assert "no AI speculation" in out["template_rationale"].lower() or \
        "speculation unavailable" in out["template_rationale"].lower()


def test_squad_cached_skips_signals(db, monkeypatch):
    _seed(db)
    _setup(monkeypatch)
    from src.ai.squad import spikes as spikes_mod
    monkeypatch.setattr(spikes_mod, "generate_spike_signals", lambda *a, **kw: _signals())
    runner.generate_squad(db, provider=object(), model_id="m")
    calls = {"n": 0}
    monkeypatch.setattr(spikes_mod, "generate_spike_signals",
                        lambda *a, **kw: calls.__setitem__("n", calls["n"] + 1) or None)
    out = runner.generate_squad(db, provider=object(), model_id="m")
    assert out is not None and calls["n"] == 0


def test_bonus_map_applies_constants():
    bonus = runner._bonus_map(_signals())
    assert bonus[0] == 1.5 and bonus[1] == 0.75 and bonus[2] == -1.5
    assert runner._bonus_map(None) == {}
