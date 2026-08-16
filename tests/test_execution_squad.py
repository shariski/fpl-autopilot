import pytest

from src.data.db import connect, init_db
from src.execution import squad


def _seed_conn():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'NEW', 'NEW')")
    for pid, name in [(10, "Old1"), (11, "Old2"), (12, "Keep")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, 'DEF', 5.0, 'a', 10.0, 3.0)",
                     (pid, name))
    conn.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) VALUES (1, ?, "
                 "'2026-08-22T08:00:00Z')",
                 ('[{"element": 10, "position": 1, "is_captain": false, "is_vice_captain": false, "multiplier": 1},'
                  ' {"element": 11, "position": 2, "is_captain": false, "is_vice_captain": false, "multiplier": 1},'
                  ' {"element": 12, "position": 3, "is_captain": false, "is_vice_captain": false, "multiplier": 1}]',))
    conn.commit()
    return conn


def test_plan_builds_out_in_pairs():
    conn = _seed_conn()
    for pid, name in [(20, "New1"), (21, "New2")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, 'DEF', 5.0, 'a', 10.0, 3.0)",
                     (pid, name))
    target = [{"player_id": 12}, {"player_id": 20}, {"player_id": 21}]
    plan = squad.plan_squad_transfers(conn, target)
    outs = {p["element_out"] for p in plan}
    assert outs == {10, 11}
    assert {p["element_in"] for p in plan} == {20, 21}
    conn.close()


def test_plan_empty_when_identical():
    conn = _seed_conn()
    target = [{"player_id": 10}, {"player_id": 11}, {"player_id": 12}]
    assert squad.plan_squad_transfers(conn, target) == []
    conn.close()


def _seed_mixed_squad_conn():
    """Realistic squad: GK on the bench (pick 12), DEFs first. Target has 2 GKs first."""
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks (id, deadline_utc, finished, is_current, is_next) "
                 "VALUES (1, '2026-08-21T17:30:00Z', 0, 0, 1)")
    conn.execute("INSERT INTO teams (id, name, short_name) VALUES (1, 'NEW', 'NEW')")
    for pid, name, pos in [(1, "Martinez", "GKP"), (2, "Guéhi", "DEF"), (3, "Fernandes", "MID"),
                           (4, "Watkins", "FWD"), (12, "Kinsky", "GKP")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, ?, 5.0, 'a', 10.0, 3.0)",
                     (pid, name, pos))
    conn.execute("INSERT INTO my_team (gw, picks_json, snapshot_at) VALUES (1, ?, "
                 "'2026-08-22T08:00:00Z')",
                 ('[{"element": 1, "position": 1}, {"element": 2, "position": 2},'
                  ' {"element": 3, "position": 3}, {"element": 4, "position": 4},'
                  ' {"element": 12, "position": 12}]',))
    conn.commit()
    return conn


def test_plan_pairs_by_position_not_by_order():
    """FPL requires same-type swaps — pairing positionally by pick order broke when
    the current squad's second GK sat on the bench (2026-08-16: Guéhi -> Donnarumma)."""
    conn = _seed_mixed_squad_conn()
    for pid, name, pos in [(101, "Roefs", "GKP"), (102, "Donnarumma", "GKP"),
                           (103, "Hall", "DEF"), (104, "O'Reilly", "DEF"),
                           (105, "Smith", "MID"), (106, "Gyökeres", "FWD")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, ?, 5.0, 'a', 10.0, 3.0)",
                     (pid, name, pos))
    target = [{"player_id": 101}, {"player_id": 102}, {"player_id": 103},
              {"player_id": 104}, {"player_id": 105}, {"player_id": 106}]
    plan = squad.plan_squad_transfers(conn, target)
    by_out = {p["element_out"]: p["element_in"] for p in plan}
    # GKs pair with GKs regardless of pick order
    assert by_out[1] == 101          # Martinez -> Roefs
    assert by_out[12] == 102         # Kinsky -> Donnarumma (not Guéhi!)
    assert by_out[2] == 103          # Guéhi -> Hall
    assert by_out[3] == 105          # Fernandes -> Smith
    assert by_out[4] == 106          # Watkins -> Gyökeres
    conn.close()


def test_plan_position_counts_mismatch_reports_failure():
    """Target position mix differs from current (shouldn't happen with a legal 2-5-5-3
    validator, but must not silently produce illegal pairs)."""
    conn = _seed_mixed_squad_conn()
    target = [{"player_id": 101}, {"player_id": 103}, {"player_id": 105}]  # only 1 GK in
    for pid, name, pos in [(101, "Roefs", "GKP"), (103, "Hall", "DEF"), (105, "Smith", "MID")]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, ?, 5.0, 'a', 10.0, 3.0)",
                     (pid, name, pos))
    plan = squad.plan_squad_transfers(conn, target)
    # one GK out has no GK in: pair the extra GK out with nothing -> dropped, no illegal swap
    by_out = {p["element_out"]: p["element_in"] for p in plan}
    assert by_out.get(12) not in (103, 105)  # never a cross-position pair
    assert all(not (p["element_in"] in (103, 105) and p["element_out"] in (1, 12))
               for p in plan)
    conn.close()


def test_apply_squad_dry_run_posts_nothing(monkeypatch):
    conn = _seed_conn()
    from types import SimpleNamespace

    monkeypatch.setattr("src.execution.executor.apply_transfers",
                        lambda s, e, payload, dry_run: SimpleNamespace(ok=True))
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 50},
                                      {"element": 11, "selling_price": 50},
                                      {"element": 12, "selling_price": 50}])
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=False, provider=None)
    assert out["dry_run"] is True and len(out["applied"]) == 1
    row = conn.execute("SELECT * FROM activity_log WHERE decision_type='squad'").fetchone()
    assert row is not None
    conn.close()


def test_apply_squad_refuses_when_no_diff(monkeypatch):
    conn = _seed_conn()
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers", lambda c, target: [])
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=True, provider=None)
    assert out["applied"] == [] and out["failed"] == ["no changes to apply"]
    conn.close()


def test_apply_squad_orders_swaps_by_bank_delta(monkeypatch):
    """FPL checks per-swap affordability (bank + selling >= purchase). Swaps that
    build bank must go first, or an affordable squad fails on an early swap
    (2026-08-16: Kinsky -> Donnarumma, insufficient_balance)."""
    conn = _seed_conn()
    for pid, name, price in [(20, "New1", 4.5), (21, "New2", 6.0), (22, "New3", 4.5)]:
        conn.execute("INSERT INTO players (id, web_name, team_id, position, price, status, "
                     "ownership, form) VALUES (?, ?, 1, 'DEF', ?, 'a', 10.0, 3.0)",
                     (pid, name, price))
    # bad order on purpose: bank-losing swap first (sell 40 -> buy 60), then bank-gaining
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [
                            {"element_out": 10, "element_in": 21,
                             "out_name": "Old1", "in_name": "New2"},
                            {"element_out": 11, "element_in": 22,
                             "out_name": "Old2", "in_name": "New3"}])
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 40},
                                      {"element": 11, "selling_price": 90}])
    submitted = []
    monkeypatch.setattr("src.execution.executor.apply_transfers",
                        lambda session, entry, payload, dry_run: (
                            submitted.append(payload["transfers"][0]["element_in"]) or
                            type("R", (), {"ok": True, "status": 200, "error": None})()))
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=True, confirm_fn=lambda d: True,
                            provider=None)
    assert out["applied"] and len(out["applied"]) == 2
    # the bank-gaining swap (sell 90 -> buy 45) must be submitted first
    assert submitted == [22, 21]
    conn.close()


def test_apply_squad_aborts_on_api_refusal(monkeypatch):
    conn = _seed_conn()

    class _Refused(Exception):
        pass

    def _apply(session, entry, payload, dry_run):
        raise _Refused("transfer refused by API")

    monkeypatch.setattr("src.execution.executor.apply_transfers", _apply)
    monkeypatch.setattr("src.execution.executor.fetch_current_picks",
                        lambda s, e: [{"element": 10, "selling_price": 50}])
    monkeypatch.setattr("src.execution.squad.plan_squad_transfers",
                        lambda c, target: [{"element_out": 10, "element_in": 20,
                                            "out_name": "Old1", "in_name": "New1"}])
    monkeypatch.setattr("src.auth.session.ensure_session", lambda conn, key: object())
    monkeypatch.setattr("src.ai.squad.runner.generate_squad",
                        lambda c, *, provider, model_id, **kw: {
                            "picks": [{"player_id": 10}], "source": "ai"})
    out = squad.apply_squad(conn, b"key", live=True, confirm_fn=lambda d: True,
                            provider=None)
    assert out["failed"] and "refused" in out["failed"][0].lower()
    conn.close()
