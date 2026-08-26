"""xP v2 — 11-component model (v0.13). Pure function + compute_and_store_v2."""
import pytest
from src.analytics import xp
from src.data import repository


def _v2(position="FWD", status="a", chance_of_playing=1.0, starts=30, squads_made=38,
        xg_per_start=0.5, xa_per_start=0.2, dc_hit_rate=0.1, saves_per_90=0.0,
        yc_per_90=0.2, rc_per_90=0.01, p60=0.9, team_xgc90=1.4,
        xg_ratio=1.0, xgc_ratio=1.0, dc_ratio=1.0, venue="H",
        recent_starts=0, recent_squads=0):
    return xp.compute_player_xp_v2(
        position, status, chance_of_playing, starts, squads_made,
        xg_per_start, xa_per_start, dc_hit_rate, saves_per_90,
        yc_per_90, rc_per_90, p60, team_xgc90,
        xg_ratio=xg_ratio, xgc_ratio=xgc_ratio, dc_ratio=dc_ratio, venue=venue,
        recent_starts=recent_starts, recent_squads=recent_squads)


def test_v2_home_advantage_attack():
    home = _v2(venue="H")
    away = _v2(venue="A")
    assert home["xp"] > away["xp"]


def test_v2_start_probability_scales_rates():
    sure = _v2(chance_of_playing=1.0, starts=38, squads_made=38)
    doubt = _v2(chance_of_playing=0.5, starts=38, squads_made=38)
    assert sure["p_start"] == pytest.approx(1.0)
    assert doubt["p_start"] == pytest.approx(0.5)
    assert doubt["xp"] < sure["xp"]


def test_v2_p_start_capped_at_one():
    r = _v2(chance_of_playing=1.0, starts=38, squads_made=30)
    assert r["p_start"] == pytest.approx(1.0)


def test_v2_poisson_clean_sheet_scales_with_opponent_attack():
    easy = _v2(position="GKP", xg_ratio=0.8)
    hard = _v2(position="GKP", xg_ratio=1.3)
    assert easy["xcs"] > hard["xcs"]
    assert easy["xcs_lambda"] < hard["xcs_lambda"]
    assert easy["xcs_lambda"] == pytest.approx(1.4 * 0.8)


def test_v2_twogc_penalty_gk_def_only():
    # the 2+ GC penalty hits GK/DEF only (negative component), never MID/FWD
    from src.analytics.xp import _twogc
    assert _twogc("GKP", 1.4 * 1.4) < 0
    assert _twogc("DEF", 1.4 * 1.4) < 0
    assert _twogc("MID", 1.4 * 1.4) == 0
    assert _twogc("FWD", 1.4 * 1.4) == 0
    # a harder opponent (higher xg_ratio) costs the GK more
    hard = _v2(position="GKP", xg_ratio=1.4, team_xgc90=1.4, starts=38, squads_made=38)
    easy = _v2(position="GKP", xg_ratio=0.7, team_xgc90=1.4, starts=38, squads_made=38)
    assert hard["xcs"] < easy["xcs"]


def test_v2_gk_saves_component():
    gk = _v2(position="GKP", saves_per_90=3.0, xg_ratio=1.2)
    gk0 = _v2(position="GKP", saves_per_90=0.0, xg_ratio=1.2)
    assert gk["xp"] > gk0["xp"]
    mid = _v2(position="MID", saves_per_90=3.0)
    mid0 = _v2(position="MID", saves_per_90=0.0)
    assert mid["xp"] == mid0["xp"]  # saves only for GK


def test_v2_dc_component_uses_hit_rate():
    r = _v2(position="DEF", dc_hit_rate=0.5, dc_ratio=1.0, starts=38, squads_made=38)
    assert r["xdc"] == pytest.approx(0.5 * 2)
    strong = _v2(position="DEF", dc_hit_rate=0.5, dc_ratio=1.5, starts=38, squads_made=38)
    assert strong["xdc"] > r["xdc"]


def test_v2_bonus_scales_with_opponent_mult_by_position():
    mid_hard_opp_def = _v2(position="MID", xgc_ratio=1.4)
    mid_easy = _v2(position="MID", xgc_ratio=0.8)
    assert mid_hard_opp_def["xbonus"] > mid_easy["xbonus"]
    gk_strong_opp_att = _v2(position="GKP", xg_ratio=1.4)
    gk_weak = _v2(position="GKP", xg_ratio=0.8)
    assert gk_strong_opp_att["xbonus"] > gk_weak["xbonus"]


def test_v2_assist_uses_fa_boost():
    r = _v2(xa_per_start=1.0, xgc_ratio=1.0, starts=38, squads_made=38)
    from src.analytics.xp import FA_BOOST, VENUE_ATTACK
    assert r["xassists"] == pytest.approx(1.0 * FA_BOOST * 3 * VENUE_ATTACK["H"])


def test_v2_injured_status_zero():
    r = _v2(status="i")
    assert r["p_start"] == 0.0
    assert r["xp"] == 0.0


def test_v2_components_are_inspectable():
    r = _v2()
    for key in ("p_start", "xgoals", "xassists", "xcs", "xbonus", "xdc", "xcs_lambda", "xp"):
        assert key in r


# ---------- compute_and_store_v2 (integration) ----------

from src.data.models import BootstrapStatic, UnderstatPlayersResponse
from src.data import name_resolver
from src.analytics import fdr


def _seed_full_v2(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    # databank: 3 GWs of history (25-26) so LF/SF windows are non-empty
    for gw in (1, 2, 3):
        rows = []
        for e in bs.elements:
            team_id = e.team
            rows.append({"element": e.id, "name": e.web_name, "team": "T", "position": "P",
                         "minutes": 90, "expected_goals": 0.5, "expected_assists": 0.2,
                         "expected_goals_conceded": 1.4, "dc": 2, "saves": 1,
                         "starts": 1, "bps": 20, "bonus": 0, "total_points": 5,
                         "yellow_cards": 0, "red_cards": 0,
                         "was_home": True, "value": 5.0})
        repository.upsert_databank_stats(db, "2025-26", gw, rows)
    # upcoming GW 5 (pre-season of a fresh season: 26-27 rows absent)
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (5, 'GW5', 0)")
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (6, 'GW6', 0)")
    team_ids = [r["id"] for r in db.execute("SELECT id FROM teams")]
    db.executemany(
        "INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished) VALUES (?,?,?,?,0)",
        [(i, 5, tid, team_ids[(i + 1) % len(team_ids)],) for i, tid in enumerate(team_ids)])
    db.commit()
    fdr.compute_and_store_v2(db, horizon=1)


def test_v2_compute_and_store_persists(db, load):
    _seed_full_v2(db, load)
    n = xp.compute_and_store_v2(db, horizon=1)
    assert n > 0
    rows = db.execute("SELECT model_version, xp, p_start, xcs_lambda FROM xp").fetchall()
    assert rows and all(r["model_version"] == "v2" for r in rows)
    assert all(r["p_start"] is not None for r in rows)
    assert all(r["xcs_lambda"] is not None for r in rows)
    # v1 rows must NOT be created by the v2 path (B5: versions stay distinct)
    assert db.execute("SELECT COUNT(*) c FROM xp WHERE model_version='v1'").fetchone()["c"] == 0


def test_v2_compute_and_store_idempotent(db, load):
    _seed_full_v2(db, load)
    xp.compute_and_store_v2(db, horizon=1)
    before = db.execute("SELECT COUNT(*) c FROM xp").fetchone()["c"]
    xp.compute_and_store_v2(db, horizon=1)
    after = db.execute("SELECT COUNT(*) c FROM xp").fetchone()["c"]
    assert before == after


def test_v2_compute_and_store_no_upcoming_returns_zero(db, load):
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1, 'GW1', 1)")
    db.commit()
    assert xp.compute_and_store_v2(db) == 0


def test_v2_missing_fdr_falls_back_neutral(db, load):
    _seed_full_v2(db, load)
    db.execute("DELETE FROM fdr")
    db.commit()
    n = xp.compute_and_store_v2(db, horizon=1)
    assert n > 0  # neutral ratios (1.0), not a failure


def test_v2_removes_stale_rows_for_missing_rates(db, load):
    """Players without databank rates must not keep ghost v2 rows (mirrors v1 stale cleanup)."""
    _seed_full_v2(db, load)
    xp.compute_and_store_v2(db, horizon=1)
    db.execute("DELETE FROM player_stats WHERE source LIKE 'fpl_databank:%'")
    db.commit()
    n = xp.compute_and_store_v2(db, horizon=1)
    assert n == 0
    assert db.execute("SELECT COUNT(*) c FROM xp").fetchone()["c"] == 0


# ---------- v0.24: recent-window start% term ----------

def _v24(position="FWD", status="a", chance_of_playing=1.0, starts=38, squads_made=38,
         recent_starts=0, recent_squads=0, **kw):
    return _v2(position=position, status=status, chance_of_playing=chance_of_playing,
               starts=starts, squads_made=squads_made,
               recent_starts=recent_starts, recent_squads=recent_squads, **kw)


def test_v24_no_live_rows_unchanged():
    """Pre-season (recent_squads=0): the v0.22 behavior is byte-identical."""
    old = _v2(chance_of_playing=0.75, starts=30, squads_made=38)
    new = _v24(chance_of_playing=0.75, starts=30, squads_made=38)
    assert new["p_start"] == old["p_start"]          # both rounded identically
    assert new["p_start"] == pytest.approx(round(0.75 * 30 / 38, 4), abs=1e-9)
    assert new["xp"] == old["xp"]


def test_v24_benched_live_gw_halves_the_prior():
    """v0.24: a 37/38 prior-season starter benched in the ONE live GW drops from
    0.97 to ~0.65, not 0.97 (the v0.23 natural-window blind spot)."""
    r = _v24(starts=37, squads_made=38, recent_starts=0, recent_squads=1)
    w = 1 / 3
    expected = (1 - w) * (37 / 38) + w * 0.0
    assert r["p_start"] == pytest.approx(expected, abs=1e-3)
    assert r["p_start"] < 0.70


def test_v24_started_live_gw_raises_the_rate():
    r = _v24(starts=25, squads_made=38, recent_starts=1, recent_squads=1)
    w = 1 / 3
    expected = (1 - w) * (25 / 38) + w * 1.0
    assert r["p_start"] == pytest.approx(expected, abs=1e-3)
    assert r["p_start"] > 25 / 38


def test_v24_full_recent_weight_at_three_live_gws():
    """At >= 3 live GWs start% is the current-season rate alone."""
    r = _v24(starts=37, squads_made=38, recent_starts=3, recent_squads=3)
    assert r["p_start"] == pytest.approx(1.0)
    r2 = _v24(starts=37, squads_made=38, recent_starts=0, recent_squads=3)
    assert r2["p_start"] == pytest.approx(0.0)


def test_v24_cop_applies_after_the_blend():
    """The doubt haircut applies to the blended rate, not just the prior term."""
    base = _v24(starts=37, squads_made=38, recent_starts=0, recent_squads=1)
    doubt = _v24(starts=37, squads_made=38, recent_starts=0, recent_squads=1,
                 chance_of_playing=0.75)
    assert doubt["p_start"] == pytest.approx(base["p_start"] * 0.75, abs=1e-3)
