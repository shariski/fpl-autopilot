import json

from src.ai import cache
from src.ai.insight.runner import extract_json_object
from src.ai.squad import spikes as spikes_mod
from src.ai.squad.digest import build_squad_digest
from src.ai.squad.spikes import build_spikes_prompt
from src.decisions.squad_builder import build_candidate_pool


def _seed(conn):
    from src.data.db import init_db
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.commit()


def _pool():
    return [
        {"player_id": i, "web_name": f"P{i}", "team_short": f"T{i % 5}",
         "position": pos, "price": price, "status": "a", "xp_next": 5.0,
         "xp_6gw": 30.0 - i, "value": (30.0 - i) / price,
         "ownership_pct": 10.0, "form": 3.0, "transfers_in": 100000 - i,
         "transfers_out": 5000 + i, "net_momentum": 95000 - 2 * i,
         "recent_gws": [6, 7, 8], "fixtures_3": [{"opponent": "HUL", "venue": "H",
         "fdr_attack": 1, "fdr_defense": 1}]}
        for i, (pos, price) in enumerate(
            [("GKP", 5.0)] * 3 + [("DEF", 5.0)] * 7 + [("MID", 7.0)] * 7 + [("FWD", 9.0)] * 5)
    ]


def _signals_json():
    return json.dumps({
        "spikes": [{"player_id": 0, "level": "high", "reason": "100000 transfers in with 8 recent form"},
                   {"player_id": 1, "level": "medium", "reason": "94998 net momentum"}],
        "drops": [{"player_id": 2, "level": "high", "reason": "net momentum 94996 fading"}],
        "market_read": "Midfield-heavy slate.",
    })


class _Seq:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, prompt, **kw):
        self.calls.append(prompt)
        return self._responses.pop(0)


def _digest():
    return {"next_gw": 1, "budget": 100, "players": _pool()}


def test_spikes_happy_path_caches(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest", lambda c, pool=None, next_gw=None: _digest())
    prov = _Seq([_signals_json()])
    out = spikes_mod.generate_spike_signals(db, provider=prov, model_id="m")
    assert out is not None
    assert out["spikes"][0]["player_id"] == 0 and out["spikes"][0]["level"] == "high"
    # cached
    prov2 = _Seq([])
    out2 = spikes_mod.generate_spike_signals(db, provider=prov2, model_id="m")
    assert out2 == out and prov2.calls == []


def test_spikes_rejects_unknown_id_and_retries(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: _digest())
    bad = json.dumps({"spikes": [{"player_id": 9999, "level": "high", "reason": "x"}],
                      "drops": [], "market_read": "m"})
    good = _signals_json()
    prov = _Seq([bad, bad, good])
    out = spikes_mod.generate_spike_signals(db, provider=prov, model_id="m")
    assert out is not None and out["spikes"][0]["player_id"] == 0
    assert len(prov.calls) == 3


def test_spikes_accepts_mixed_edge_and_stat_reason(db, monkeypatch):
    """A reason citing edge evidence AND projection stats is fine (edge present)."""
    _seed(db)
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest", lambda c, pool=None, next_gw=None: _digest())
    payload = json.loads(_signals_json())
    payload["spikes"][0]["reason"] = "100000 transfers in and 30.0 projected points"
    assert spikes_mod.validate_signals(payload, _pool(), _digest()) == []


def test_spikes_rejects_stat_only_restatement(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest", lambda c, pool=None, next_gw=None: _digest())
    payload = json.loads(_signals_json())
    payload["spikes"][0]["reason"] = "best at 30.0 projected points"
    problems = spikes_mod.validate_signals(payload, _pool(), _digest())
    assert any("restatement" in p for p in problems)


def test_spikes_returns_none_when_exhausted(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: _digest())
    bad = json.dumps({"spikes": [{"player_id": 9999, "level": "high", "reason": "x"}],
                      "drops": [], "market_read": "m"})
    prov = _Seq([bad, bad, bad, bad])
    assert spikes_mod.generate_spike_signals(db, provider=prov, model_id="m") is None
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='squad'"
                     " ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None and "spikes_failed" in row["inputs_json"]


def test_spikes_provider_failure_returns_none(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(spikes_mod, "build_candidate_pool", lambda c, next_gw=None: _pool())
    monkeypatch.setattr(spikes_mod, "build_squad_digest",
                        lambda c, pool=None, next_gw=None: _digest())

    class Boom:
        def generate(self, prompt, **kw):
            raise RuntimeError("down")

    assert spikes_mod.generate_spike_signals(db, provider=Boom(), model_id="m") is None
