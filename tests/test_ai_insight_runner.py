import json

from src.ai import cache
from src.ai.insight import runner
from src.data.db import connect, init_db


def _digest():
    return {"player": {"web_name": "Haaland", "price": 15.0},
            "prior_season": {"xg_per90": 0.8, "season": "2025"},
            "current_season_gws": [], "projection": [{"gw": 1, "xp": 7.5}],
            "fixtures": [{"gw": 1, "opponent": "HUL", "venue": "H",
                          "fdr_attack": 1, "fdr_defense": 1}],
            "data_limits": ["no current-season minutes yet (pre-season)"]}


def _seed_player(conn):
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'MCI', 'MCI')")
    conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                 "ownership, form) VALUES (1, 'Haaland', 1, 'FWD', 15.0, 'a', 50.0, 7.0)")
    conn.commit()


def _persist_digest(conn):
    """Write the digest rows the runner's build_player_digest reads."""
    conn.execute("INSERT INTO understat_players (understat_id, fpl_player_id, season, "
                 "player_name, team_title, xg_per_90, xa_per_90, minutes, games, goals, "
                 "assists) VALUES (1, 1, '2025', 'Haaland', 'Man City', 0.8, 0.3, 3000, 36, "
                 "30, 8)")
    conn.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id) "
                 "VALUES (1, 1, 1, 2)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (2, 'HUL', 'HUL')")
    conn.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
                 "VALUES (1, 1, 1, 1, '2026-08-01T00:00:00Z')")
    conn.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
                 "xassists, xcs, computed_at) VALUES (1, 1, 'v1', 7.5, 80, 0.7, 0.2, 0.3, "
                 "'2026-08-01T00:00:00Z')")
    conn.commit()


def _grounded_payload():
    return {
        "insights": [
            {"category": "fixture_alignment",
             "claim": "Three home fixtures against low-FDR defences (fdr_attack 1 twice).",
             "evidence_used": ["1", "1"], "confidence": "high",
             "implication": "His attacking output projects well over the run."},
        ],
        "summary": "Good fixture run ahead for Haaland.",
        "data_limits": ["no current-season minutes yet (pre-season)"],
    }


class _SequenceProvider:
    """Returns responses in order, then raises."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, prompt, **kw):
        self.calls.append(prompt)
        if not self._responses:
            raise RuntimeError("no more responses")
        return self._responses.pop(0)


def test_runner_generates_and_caches(db, monkeypatch):
    _seed_player(db)
    _persist_digest(db)
    monkeypatch.setattr(runner, "build_player_digest", lambda c, pid, next_gw=None: _digest())
    payload = _grounded_payload()
    provider = _SequenceProvider([json.dumps(payload)])
    out = runner.generate_player_insight(db, 1, provider=provider, model_id="m")
    assert out is not None and out["summary"] == payload["summary"]
    # cached for the same digest
    provider2 = _SequenceProvider([])
    out2 = runner.generate_player_insight(db, 1, provider=provider2, model_id="m")
    assert out2 == out and provider2.calls == []
    # activity log entry exists
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='ai.insight'").fetchone()
    assert row is not None and json.loads(row["inputs_json"])["gate_result"] == "passed"


def test_runner_rejects_ungrounded_and_retries(db, monkeypatch):
    _seed_player(db)
    monkeypatch.setattr(runner, "build_player_digest", lambda c, pid, next_gw=None: _digest())
    bad = {"insights": [{"category": "value_market",
                         "claim": "Owned by 50% with 9.5 xP",
                         "evidence_used": ["9.5"], "confidence": "medium",
                         "implication": "n/a"}],
           "summary": "s", "data_limits": []}
    good = _grounded_payload()
    provider = _SequenceProvider([json.dumps(bad), json.dumps(good)])
    out = runner.generate_player_insight(db, 1, provider=provider, model_id="m")
    assert out is not None and out["summary"] == good["summary"]
    assert len(provider.calls) == 2
    assert "9.5" in provider.calls[1]  # retry feedback names the ungrounded number


def test_runner_retries_until_exhausted_then_none(db, monkeypatch):
    _seed_player(db)
    monkeypatch.setattr(runner, "build_player_digest", lambda c, pid, next_gw=None: _digest())
    bad = {"insights": [{"category": "value_market",
                         "claim": "Owned by 50% with 9.5 xP",
                         "evidence_used": ["9.5"], "confidence": "medium",
                         "implication": "n/a"}],
           "summary": "s", "data_limits": []}
    provider = _SequenceProvider([json.dumps(bad)] * 4)
    assert runner.generate_player_insight(db, 1, provider=provider, model_id="m") is None
    assert len(provider.calls) == 3  # 1 initial + 2 retries, capped
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='ai.insight'"
                     " ORDER BY rowid DESC LIMIT 1").fetchone()
    assert json.loads(row["inputs_json"])["gate_result"] == "failed"
    # nothing cached
    rec_hash = cache.recommendation_hash(_digest())
    assert cache.get(db, 1, "insight", rec_hash) is None


def test_runner_returns_none_for_unknown_player(db):
    _seed_player(db)
    provider = _SequenceProvider([])
    assert runner.generate_player_insight(db, 999, provider=provider, model_id="m") is None
    assert provider.calls == []


def test_extract_json_object_handles_fences_and_garbage():
    assert runner.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert runner.extract_json_object('prefix {"a": 1} suffix') == {"a": 1}
    assert runner.extract_json_object("no json here") is None
