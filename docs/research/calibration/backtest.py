"""Backtest harness: xP v1 vs xP v2 on 24-25 + 25-26 (B5 review gate).

Reuses the production functions (ratings, fdr, xp — pure paths only). A scratch
in-memory DB is built progressively: at each (season, gw) the prediction uses ONLY
rows with (source, gw) strictly before it, so there is no leakage. Actuals come from
the same per-GW databank rows (total_points).

Coverage: all players of both seasons (scratch players are built from the databank
itself — no dependence on the current-season roster).

Caveats (reported, not hidden):
- v1 uses full-season Understat aggregates (an upper bound for v1 — it 'knows' the
  season; production v1 mid-season sees season-to-date only).
- No promoted-team overrides (the 26-27 map does not apply to 24-25/25-26).
- DGWs collapse into one fdr (team, gw) row (documented production limitation).
- 24-25 has no defensive_contribution column -> v2 xdc = 0 that season.
"""
import csv
import io
import json
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.data.db import connect, init_db          # noqa: E402
from src.data.databank_client import DatabankClient, REQUIRED_COLUMNS  # noqa: E402
from src.data import repository                    # noqa: E402
from src.analytics import ratings, fdr, xp         # noqa: E402

SEASONS = ["2024-25", "2025-26"]
DATABANK_DIR = ROOT / "data" / "databank"
TEAMS_CSV = {s: DATABANK_DIR / f"{s}_teams.csv" for s in SEASONS}
UNDERSTAT_JSON = {"2024-25": DATABANK_DIR / "understat_2024.json",
                  "2025-26": DATABANK_DIR / "understat_2025.json"}


# ---------- local-file source for the production client ----------

class _LocalSession:
    """Serves the production DatabankClient from local CSVs (no network)."""

    def __init__(self):
        self.headers = {}

    def get(self, url, timeout=None):
        # url = https://raw.githubusercontent.com/.../data/{season}/gws/gw{n}.csv
        try:
            season = url.split("/data/")[1].split("/gws/")[0]
            gw = int(url.rsplit("/gw", 1)[1].rstrip(".csv"))
            path = DATABANK_DIR / season / "gws" / f"gw{gw}.csv"
            text = path.read_text()
        except (IndexError, ValueError, OSError) as exc:
            raise RuntimeError(f"local databank read failed for {url}: {exc}")
        return type("R", (), {"status_code": 200, "text": text})()


# ---------- team identity (FPL team ids drift across seasons; canonicalize by name) ----------

def _season_team_maps():
    """{season: ({name: id}, {id: name})} from teams.csv, plus a canonical id per name."""
    by_season = {}
    all_names = set()
    for s in SEASONS:
        name_to_id, id_to_name = {}, {}
        with open(TEAMS_CSV[s]) as f:
            for row in csv.DictReader(f):
                name_to_id[row["name"]] = int(row["id"])
                id_to_name[int(row["id"])] = row["name"]
                all_names.add(row["name"])
        by_season[s] = (name_to_id, id_to_name)
    # canonical: latest season's id wins; unrepeated names get fresh ids
    canonical = {}
    next_free = 100
    for s in SEASONS:
        for name, tid in by_season[s][0].items():
            if name not in canonical:
                canonical[name] = tid
    for name in sorted(all_names - set(canonical)):
        canonical[name] = next_free
        next_free += 1
    return by_season, canonical


def _canonicalize_rows(season, rows, id_to_name, canonical):
    """Remap the season's raw team ids to canonical ids (by name)."""
    out = []
    for r in rows:
        r2 = dict(r)
        opp = r2.get("opponent_team", 0)
        r2["opponent_team"] = canonical.get(id_to_name.get(opp), 0)
        out.append(r2)
    return out


# ---------- scratch DB ----------

@dataclass
class Scratch:
    conn: object
    teams_by_name: dict = field(default_factory=dict)
    id_by_name: dict = field(default_factory=dict)   # normalized name -> element


def _norm(s):
    import unicodedata
    import re
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def build_scratch(canonical):
    """Scratch DB with teams keyed by canonical id (stable across seasons)."""
    conn = connect(":memory:")
    init_db(conn)
    for name, tid in canonical.items():
        conn.execute(
            "INSERT INTO teams (id, name, short_name) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
            (tid, name, name[:3].upper()))
    conn.commit()
    return Scratch(conn=conn, teams_by_name=canonical)


def upsert_rows(sc, season, gw, rows):
    """Insert databank rows into the scratch DB, building players on first sight."""
    for r in rows:
        pid = r["element"]
        tid = sc.teams_by_name.get(r["team"])
        if tid is None:
            continue  # team not in FPL teams (shouldn't happen; defensive)
        pos = {"GK": "GKP", "AM": "MID"}.get(r["position"], r["position"])
        sc.conn.execute(
            "INSERT INTO players (id, name, web_name, team_id, position, price, status, updated_at) "
            "VALUES (?,?,?,?,?,?,?,'t') ON CONFLICT(id) DO NOTHING",
            (pid, r["name"], r["name"], tid, pos, 5.0, "a"))
    sc.conn.commit()
    repository.upsert_databank_stats(sc.conn, season, gw, rows)


# ---------- fixtures for one GW from its rows ----------

def fixtures_for_gw(sc, rows):
    """Derive (home, away) per fixture; cross-check both derivations. Returns (fixtures, collisions)."""
    by_name = {}
    for r in rows:
        tid = sc.teams_by_name.get(r["team"])
        if tid is None:
            continue
        by_name.setdefault(tid, {"name": r["team"], "opp": set(), "home": False, "away": False})
        e = by_name[tid]
        e["opp"].add(int(r["opponent_team"]))
        if r["was_home"]:
            e["home"] = True
        else:
            e["away"] = True
    fixtures = []
    collisions = 0
    seen = set()
    for tid, e in by_name.items():
        for opp in e["opp"]:
            pair = tuple(sorted((tid, opp)))
            if pair in seen:
                continue
            seen.add(pair)
            if e["home"] and by_name.get(opp, {}).get("away") and opp in by_name:
                fixtures.append({"gw": 0, "home_team_id": tid, "away_team_id": opp})
            elif e["away"] and by_name.get(opp, {}).get("home"):
                fixtures.append({"gw": 0, "home_team_id": opp, "away_team_id": tid})
            else:
                collisions += 1
    return fixtures, collisions


# ---------- v1 inputs ----------

def _understat_map(season):
    """element -> {xg90, xa90, minutes, games} via name match within the season's databank."""
    norm_names = {}
    for gw in range(1, 39):
        path = DATABANK_DIR / season / "gws" / f"gw{gw}.csv"
        if not path.exists():
            break
        with open(path) as f:
            for r in csv.DictReader(f):
                norm_names.setdefault(_norm(r["name"]), int(r["element"]))
    with open(UNDERSTAT_JSON[season]) as f:
        data = json.load(f)
    out = {}
    for p in data["players"]:
        pid = norm_names.get(_norm(p["player_name"]))
        if pid is None:
            continue
        time_min = int(p["time"])
        out[pid] = {"xg90": float(p["xG"]) / (time_min / 90) if time_min else 0.0,
                    "xa90": float(p["xA"]) / (time_min / 90) if time_min else 0.0,
                    "minutes": time_min, "games": int(p["games"])}
    return out


def _fdr_v1_teams(season):
    with open(TEAMS_CSV[season]) as f:
        return [{"id": int(r["id"]),
                 "strength_attack_home": int(r["strength_attack_home"]),
                 "strength_attack_away": int(r["strength_attack_away"]),
                 "strength_defence_home": int(r["strength_defence_home"]),
                 "strength_defence_away": int(r["strength_defence_away"])}
                for r in csv.DictReader(f)]


# ---------- metrics ----------

@dataclass
class GWResult:
    season: str
    gw: int
    n: int
    mae_v2: float
    mae_v1: float
    bias_v2: float
    bias_v1: float
    cap_v2: float     # actual points of the top-xP-v2 player that GW
    cap_v1: float
    cap_win_v2: "bool | None"  # v2 top pick outscored v1 top pick


def _mae(pred, act):
    return sum(abs(a - p) for p, a in zip(pred, act)) / len(pred) if act else 0.0


def main():
    client = DatabankClient(session=_LocalSession())
    season_maps, canonical = _season_team_maps()
    sc = build_scratch(canonical)
    understat = {s: _understat_map(s) for s in SEASONS}
    fdr1_teams = {s: _fdr_v1_teams(s) for s in SEASONS}

    results = []
    all_v2 = {"pred": [], "act": []}
    all_v1 = {"pred": [], "act": []}

    for season in SEASONS:
        _, id_to_name = season_maps[season]
        for gw in range(1, 39):
            rows = _canonicalize_rows(season, client.fetch_gw(season, gw), id_to_name, canonical)
            # ---- predict BEFORE inserting this GW's rows (no leakage) ----
            team_ratings, la = ratings.compute_team_ratings(sc.conn)
            fixtures, collisions = fixtures_for_gw(sc, rows)
            mults = {x["team_id"]: x for x in fdr.compute_fdr_v2(team_ratings, la, fixtures, {})}
            player_rates = ratings.compute_player_rates(sc.conn)

            fdr1_rows = {x["team_id"]: x for x in fdr.compute_fdr(fdr1_teams[season], fixtures)}
            us = understat[season]

            pred_v2, pred_v1, act = [], [], []
            for r in rows:
                tid = sc.teams_by_name.get(r["team"])
                if tid is None:
                    continue
                venue = "H" if r["was_home"] else "A"
                pr = player_rates.get(r["element"])
                if pr is None or tid not in mults:
                    continue
                opp_id = None
                for fx in fixtures:
                    if fx["home_team_id"] == tid:
                        opp_id = fx["away_team_id"]
                    elif fx["away_team_id"] == tid:
                        opp_id = fx["home_team_id"]
                if opp_id is None:
                    continue
                opp_r = team_ratings.get(opp_id)
                dc_ratio = ratings.damp(opp_r.dc90 / la.dc90) if opp_r and la.dc90 else 1.0
                team_xgc90 = team_ratings.get(tid).xgc90 if tid in team_ratings else la.xgc90
                res = xp.compute_player_xp_v2(
                    pr.position, "a", 1.0, pr.starts, pr.squads_made,
                    pr.xg_per_start, pr.xa_per_start, pr.dc_hit_rate,
                    pr.saves_per_90, pr.yc_per_90, pr.rc_per_90, pr.p60,
                    team_xgc90, xg_ratio=mults[tid]["fdr_defense_mult"],
                    xgc_ratio=mults[tid]["fdr_attack_mult"],
                    dc_ratio=dc_ratio, venue=venue)
                pred_v2.append((r["element"], res["xp"]))
                act.append((r["element"], int(r["total_points"])))
                # ---- v1 (production formula, season-aggregate inputs) ----
                u = us.get(r["element"])
                f1 = fdr1_rows.get(tid)
                if u is not None and f1 is not None:
                    v1 = xp.compute_player_xp(pr.position, "a", u["xg90"], u["xa90"],
                                              u["minutes"], u["games"],
                                              f1["fdr_attack"], f1["fdr_defense"])
                    pred_v1.append((r["element"], v1["xp"]))

            # actuals + metrics for this GW
            act_map = dict(act)
            p2 = [p for e, p in pred_v2 if e in act_map]
            a2 = [act_map[e] for e, _ in pred_v2 if e in act_map]
            p1 = [p for e, p in pred_v1 if e in act_map]
            a1 = [act_map[e] for e, _ in pred_v1 if e in act_map]
            all_v2["pred"] += p2
            all_v2["act"] += a2
            all_v1["pred"] += p1
            all_v1["act"] += a1

            cap_v2 = cap_v1 = 0.0
            cap_win = None
            if pred_v2 and pred_v1:
                top_v2 = max(pred_v2, key=lambda x: x[1])[0]
                top_v1 = max(pred_v1, key=lambda x: x[1])[0]
                cap_v2 = act_map.get(top_v2, 0)
                cap_v1 = act_map.get(top_v1, 0)
                cap_win = cap_v2 > cap_v1

            results.append(GWResult(
                season=season, gw=gw, n=len(a2), mae_v2=_mae(p2, a2), mae_v1=_mae(p1, a1),
                bias_v2=(sum(p2) - sum(a2)) / len(a2) if a2 else 0.0,
                bias_v1=(sum(p1) - sum(a1)) / len(a1) if a1 else 0.0,
                cap_v2=cap_v2, cap_v1=cap_v1, cap_win_v2=cap_win))

            # ---- then insert this GW's rows (window advances) ----
            upsert_rows(sc, season, gw, rows)

    _report(results, all_v2, all_v1)


def _report(results, v2, v1):
    def season_rows(s):
        return [r for r in results if r.season == s and r.n > 0]

    print("=== xP backtest: v1 vs v2 (databank actuals, strict no-leakage windows) ===")
    for s in SEASONS:
        rs = season_rows(s)
        if not rs:
            continue
        n = sum(r.n for r in rs)
        mae2 = sum(r.mae_v2 * r.n for r in rs) / n
        mae1 = sum(r.mae_v1 * r.n for r in rs) / n
        bias2 = sum(r.bias_v2 * r.n for r in rs) / n
        bias1 = sum(r.bias_v1 * r.n for r in rs) / n
        caps2 = [r.cap_v2 for r in rs if r.cap_win_v2 is not None]
        caps1 = [r.cap_v1 for r in rs if r.cap_win_v2 is not None]
        wins = sum(1 for r in rs if r.cap_win_v2)
        print(f"\n{'-' * 60}\n{s}: {len(rs)} GWs, {n} player-GWs")
        print(f"  MAE      v2={mae2:.3f}  v1={mae1:.3f}   ({'v2 better' if mae2 < mae1 else 'v1 better'})")
        print(f"  bias     v2={bias2:+.3f}  v1={bias1:+.3f}   (pred - actual, per player-GW)")
        if caps1:
            print(f"  captain-proxy: v2 top-pick actual {sum(caps2)/len(caps2):.2f} vs "
                  f"v1 {sum(caps1)/len(caps1):.2f}; v2 wins {wins}/{len(caps1)} GWs")
        print(f"  worst GWs by v2 MAE: " +
              ", ".join(f"GW{r.gw}:{r.mae_v2:.2f}" for r in sorted(rs, key=lambda r: -r.mae_v2)[:3]))
    # pooled stats
    import math
    def corr(p, a):
        n = len(p)
        if n < 3:
            return float("nan")
        mp, ma = sum(p) / n, sum(a) / n
        cov = sum((x - mp) * (y - ma) for x, y in zip(p, a))
        vp = sum((x - mp) ** 2 for x in p) ** 0.5
        va = sum((y - ma) ** 2 for y in a) ** 0.5
        return cov / (vp * va) if vp and va else float("nan")
    print(f"\npooled 25-26 correlation: v2 r={corr(v2['pred'], v2['act']):.3f}  "
          f"v1 r={corr(v1['pred'], v1['act']):.3f}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = ROOT / "data" / "audit" / f"backtest-{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "per_gw": [r.__dict__ for r in results],
        "pooled_25_26": {"v2_mae": None, "v1_mae": None},
    }, indent=1))
    print(f"\nJSON detail: {out}")


if __name__ == "__main__":
    main()
