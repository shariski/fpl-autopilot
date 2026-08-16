"""Calibrate Benchwarmers-model constants against the Vaastav databank (24-25 + 25-26)."""
import csv, json, unicodedata
import pandas as pd
import numpy as np

SEASONS = ["2024-25", "2025-26"]
COLS = ["minutes", "expected_goals", "expected_assists", "expected_goals_conceded", "bonus",
        "bps", "total_points", "saves", "starts", "defensive_contribution", "goals_scored",
        "assists", "clean_sheets", "goals_conceded", "yellow_cards", "red_cards"]


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum())


def build(season):
    """One combined per-GW frame. Vaastav gws CSVs are already per-GW (not cumulative)."""
    frames = []
    for gw in range(1, 39):
        with open(f"data/databank/{season}/gws/gw{gw}.csv") as f:
            cur = pd.DataFrame(list(csv.DictReader(f)))
        cur = cur.drop_duplicates(subset=["element"], keep="last")
        cur["gw"] = gw
        cur["was_home"] = cur["was_home"].astype(str).str.lower() == "true"
        for c in COLS:
            if c in cur.columns:
                cur[c] = pd.to_numeric(cur[c], errors="coerce").fillna(0.0)
        rows = cur[["element", "name", "team", "position", "gw", "was_home"]].copy()
        for c in COLS:
            if c in cur.columns:
                rows[c] = cur[c].values
        frames.append(rows)
    return pd.concat(frames, ignore_index=True)


DB = {s: build(s) for s in SEASONS}

US = {}
for season, us_season in [("2024-25", "2024"), ("2025-26", "2025")]:
    with open(f"data/databank/understat_{us_season}.json") as f:
        data = json.load(f)
    US[season] = pd.DataFrame([{"norm": norm(p["player_name"]),
                                "team": norm(p["team_title"]),
                                "us_xg": float(p["xG"]), "us_xa": float(p["xA"]),
                                "time": int(p["time"]), "games": int(p["games"])}
                               for p in data["players"]])

print("=== B: FA BOOST — assists vs xA ===")
for s in SEASONS:
    t = DB[s][DB[s]["minutes"] > 0].groupby("element").agg(
        xa=("expected_assists", "sum"), a=("assists", "sum"), mins=("minutes", "sum"))
    t = t[(t["mins"] >= 600) & (t["xa"] > 0)]
    r = t["a"] / t["xa"]
    weighted = t["a"].sum() / t["xa"].sum()
    print(f"{s}: assists/xA per-player mean={r.mean():.3f} median={r.median():.3f} n={len(t)}; "
          f"weighted (league-wide)={weighted:.3f}")
print()

print("=== A: FPL xG/xA (databank, season totals) vs Understat ===")
for s in SEASONS:
    t = DB[s].groupby("element").agg(fpl_xg=("expected_goals", "sum"),
                                     fpl_xa=("expected_assists", "sum"),
                                     mins=("minutes", "sum"), name=("name", "first"))
    t["norm"] = t["name"].map(norm)
    m = t.merge(US[s], left_on="norm", right_on="norm")
    m = m[(m["mins"] >= 600) & (m["fpl_xg"] > 1) & (m["fpl_xa"] > 0.5)]
    g = m[m["fpl_xg"] > 2]
    r = g["fpl_xg"] / g["us_xg"]
    corr = np.corrcoef(g["fpl_xg"], g["us_xg"])[0, 1]
    ra = m["fpl_xa"] / m["us_xa"]
    print(f"{s}: matched={len(m)}; xG FPL/US mean={r.mean():.3f} med={r.median():.3f} corr={corr:.3f}; "
          f"xA FPL/US mean={ra.mean():.3f} med={ra.median():.3f}; "
          f"weighted xG={g['fpl_xg'].sum() / g['us_xg'].sum():.3f} xA={m['fpl_xa'].sum() / m['us_xa'].sum():.3f}")
print()

print("=== C: HOME/AWAY multipliers (per-90 or per-start, team means) ===")
for s in SEASONS:
    print(f"-- {s} --")
    d = DB[s]
    for col, rt in [("expected_goals", 90), ("expected_goals_conceded", 90), ("bonus", 90),
                    ("bps", 90), ("saves", 90), ("starts", 90), ("assists", 90),
                    ("goals_scored", 90)]:
        if col not in d.columns:
            continue
        h = d[d["was_home"]][["team", col, "minutes"]].groupby("team")[[col, "minutes"]].sum()
        a = d[~d["was_home"]][["team", col, "minutes"]].groupby("team")[[col, "minutes"]].sum()
        h_rate = (h[col] / h["minutes"]).clip(lower=0)
        a_rate = (a[col] / a["minutes"]).clip(lower=0)
        mult = h_rate / a_rate.replace(0, np.nan)
        print(f"  {col:28s} H={h[col].sum()/h['minutes'].sum()*90:6.2f} A={a[col].sum()/a['minutes'].sum()*90:6.2f} "
              f"mult mean={mult.mean():.3f} med={mult.median():.3f}")
    if "defensive_contribution" in d.columns:
        h = d[d["was_home"]][["team", "defensive_contribution", "minutes"]].groupby("team")[
            ["defensive_contribution", "minutes"]].sum()
        a = d[~d["was_home"]][["team", "defensive_contribution", "minutes"]].groupby("team")[
            ["defensive_contribution", "minutes"]].sum()
        mult = (h["defensive_contribution"] / h["minutes"]).clip(lower=0) / \
               (a["defensive_contribution"] / a["minutes"]).replace(0, np.nan)
        print("  per-team DC H/A multipliers:")
        for team, m in mult.sort_values().items():
            print(f"    {team:28s} {m:.3f}")
print()

print("=== D: Poisson CS validation ===")
for s in SEASONS:
    d = DB[s]
    t = d[d["minutes"] > 0].groupby(["team", "gw"], as_index=False).agg(
        xgc=("expected_goals_conceded", "sum"), mins=("minutes", "sum"),
        cs=("clean_sheets", "max"), gc=("goals_conceded", "max"))
    t["lam"] = t["xgc"] / t["mins"] * 90
    t["p_cs"] = np.exp(-t["lam"])
    t["p_2gc"] = 1 - np.exp(-t["lam"]) * (1 + t["lam"])
    cs_rate = (t["cs"] == 1).mean()
    gc2_rate = (t["gc"] >= 2).mean()
    byp = t.groupby("team")["p_cs"].mean()
    bya = t.groupby("team")["cs"].mean()
    mae = (byp - bya).abs().mean()
    print(f"{s}: actual CS%={cs_rate:.3f} vs Poisson {t['p_cs'].mean():.3f}; "
          f"actual 2+GC%={gc2_rate:.3f} vs Poisson {t['p_2gc'].mean():.3f}; "
          f"team-level CS MAE={mae:.3f}; league xGC/90={t['lam'].mean():.3f}")
print()

print("=== E: DC hit rates by position (25-26) ===")
d = DB["2025-26"]
m = d[d["minutes"] > 0]
agg = m.groupby("position").agg(dc=("defensive_contribution", "sum"), starts=("starts", "sum"))
for pos in ["GK", "DEF", "MID", "FWD"]:
    mm = m[m["position"] == pos]
    thr = 10 if pos == "DEF" else 12
    hits = (mm["defensive_contribution"] >= thr).sum()
    print(f"  {pos}: dc/start={agg.loc[pos, 'dc'] / agg.loc[pos, 'starts']:.2f}  "
          f"P(>= {thr}) per start={hits / mm['starts'].sum():.4f}  starts={mm['starts'].sum():.0f}")
print()

print("=== F: Bonus/BPS league fallbacks ===")
for s in SEASONS:
    m = DB[s][DB[s]["minutes"] > 0]
    print(f"{s}: bonus/start={m['bonus'].sum() / m['starts'].sum():.4f}  "
          f"bps/start={m['bps'].sum() / m['starts'].sum():.2f}  "
          f"bonus/bps={m['bonus'].sum() / m['bps'].sum():.4f}")
    m2 = m[m["bps"] > 0]
    buckets = [(1, 20), (20, 40), (40, 60), (60, 90), (90, 150), (150, 400)]
    print("   mean bonus by bps bucket:",
          {f"{a}-{b}": round(m2[(m2['bps'] >= a) & (m2['bps'] < b)]['bonus'].mean(), 2) for a, b in buckets})
print()

print("=== G: appearance / starts structure ===")
for s in SEASONS:
    m = DB[s]
    team_gws = m[m["minutes"] > 0].groupby(["team", "gw"]).size().reset_index(name="n_players")
    squads = m.groupby(["team", "gw"]).size().reset_index(name="squad_rows")
    starts_total = m.groupby(["team", "gw"])["starts"].sum().reset_index()
    x = starts_total.merge(team_gws, on=["team", "gw"])
    x["squads_made"] = x["n_players"] - x["starts"] * 0 + 11 * (x["starts"] > 0)  # placeholder
    plays = m[m["minutes"] > 0]
    start_rate = m["starts"].sum() / (11 * 38 * m["team"].nunique())
    print(f"{s}: P(any minutes per player-GW)={plays.shape[0] / len(m):.3f}; "
          f"starts/team-match={start_rate:.2f} (league mean, 11 per match=1.0); "
          f"players listed per team-match={team_gws['n_players'].mean():.1f}")
    # P(60+ | plays)
    p60 = ((plays["minutes"] >= 60)).mean()
    print(f"   P(60+ mins | played)={p60:.3f}; mean minutes when played={plays['minutes'].mean():.1f}")
print()

print("=== H: league-average anchors ===")
for s in SEASONS:
    d = DB[s]
    print(f"{s}: LA xG/90={d['expected_goals'].sum()/d['minutes'].sum()*90:.3f}  "
          f"LA xGC/90={d['expected_goals_conceded'].sum()/d['minutes'].sum()*90:.3f}")
