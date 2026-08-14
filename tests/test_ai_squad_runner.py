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


def _legal_picks_json():
    slots = ["GKP1", "GKP2", "DEF1", "DEF2", "DEF3", "DEF4", "DEF5",
             "MID1", "MID2", "MID3", "MID4", "MID5", "FWD1", "FWD2", "FWD3"]
    # pool positions: 0-2 GKP, 3-9 DEF, 10-16 MID, 17-21 FWD
    ids = [0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19]
    return json.dumps({
        "picks": [{"player_id": pid, "slot": slots[i], "reason": "solid pick " + chr(97 + i)}
                  for i, pid in enumerate(ids)],
        "template_rationale": "Balanced template.",
        "risks": ["Fixture rotation risk."],
    })


class _Seq:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, prompt, **kw):
        self.calls.append(prompt)
        return self._responses.pop(0)


def test_runner_ai_pick_caches(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())
    prov = _Seq([_legal_picks_json()])
    out = runner.generate_squad(db, provider=prov, model_id="m")
    assert out is not None and out["source"] == "ai"
    assert len(out["picks"]) == 15
    # cached
    prov2 = _Seq([])
    out2 = runner.generate_squad(db, provider=prov2, model_id="m")
    assert out2 == out and prov2.calls == []
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='squad'").fetchone()
    assert row is not None


def test_runner_retries_then_falls_back(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": _pool()})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())
    bad = json.dumps({"picks": [{"player_id": 1, "slot": "GKP1"}],
                      "template_rationale": "x", "risks": []})
    prov = _Seq([bad, bad, bad, bad])
    out = runner.generate_squad(db, provider=prov, model_id="m")
    assert out is not None and out["source"] == "optimizer"
    assert len(prov.calls) == 3
    assert "validator" in prov.calls[1].lower() or "legal" in prov.calls[1].lower()


def test_runner_returns_none_on_total_failure(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: _pool())

    class Boom:
        def generate(self, prompt, **kw):
            raise RuntimeError("provider down")

    assert runner.generate_squad(db, provider=Boom(), model_id="m") is None
    digest = {"next_gw": 1, "budget": 100, "players": _pool()}
    assert cache.get(db, 1, "squad", cache.recommendation_hash(digest)) is None


def test_runner_budget_repair_on_over_budget_proposal(db, monkeypatch):
    """AI proposes a legal-shape squad that is over budget -> deterministic repair."""
    _seed(db)
    pool = _pool()
    # GKP1 (player 0) is priced +12 over -> any squad containing it is over budget
    pool = [dict(p, price=p["price"] + 12 if p["player_id"] == 0 else p["price"])
            for p in pool]
    pool.append({"player_id": 50, "web_name": "CheapGK", "team_short": "T9",
                 "position": "GKP", "price": 4.0, "status": "a", "xp_next": 1.0,
                 "xp_6gw": 6.0, "value": 1.5})
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": pool})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: pool)
    prov = _Seq([_legal_picks_json()] * 3)  # over budget, repeated on retries
    out = runner.generate_squad(db, provider=prov, model_id="m")
    assert out is not None and out["source"] == "ai"
    assert runner.validate_squad(out["picks"], pool) == []
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='squad'"
                     " ORDER BY rowid DESC LIMIT 1").fetchone()
    import json as _json
    assert _json.loads(row["inputs_json"])["result"] == "budget_repaired"


def test_runner_rejects_reason_with_foreign_number(db, monkeypatch):
    """A pick whose reason cites another player's number (misattribution) is
    rejected — retried with feedback, then repaired/fallback if unrecoverable."""
    _seed(db)
    pool = _pool()
    monkeypatch.setattr(runner, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: {"next_gw": 1, "budget": 100,
                                                            "players": pool})
    monkeypatch.setattr(runner, "build_candidate_pool", lambda c, next_gw=None: pool)
    # GKP1's reason cites 30.0 — which is player 0's OWN xp_6gw... use a foreign one:
    # player 0 (GKP1) reason cites "28.0" (player 3's xp_6gw) -> must be rejected.
    import json as _json
    slots = ["GKP1", "GKP2", "DEF1", "DEF2", "DEF3", "DEF4", "DEF5",
             "MID1", "MID2", "MID3", "MID4", "MID5", "FWD1", "FWD2", "FWD3"]
    ids = [0, 1, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19]
    bad = _json.dumps({
        "picks": [{"player_id": pid, "slot": slots[i],
                   "reason": "best value at 28.0 xP" if pid == 0 else f"r{i}"}
                  for i, pid in enumerate(ids)],
        "template_rationale": "T", "risks": []})
    good = _legal_picks_json()
    prov = _Seq([bad, bad, bad, good])
    out = runner.generate_squad(db, provider=prov, model_id="m")
    assert out is not None and out["source"] == "ai"
    assert "cites numbers" in prov.calls[1].lower() or "not in their data" in prov.calls[1].lower()
