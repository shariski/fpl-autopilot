"""Top-100 leaders pattern analysis (v0.27). Deterministic statistics — no AI.

Consumes leader_entries + leader_gw_snapshots (written by src/data/leaders.py).
Every function returns plain dicts; empty-DB guards return empty structures.
"""
from statistics import mean, median

SUSTAINED_ELITE_MAX_RANK = 250_000  # top ~5% of 25-26 (12.3M entries)


def chip_timing(conn):
    rows = [dict(r) for r in conn.execute(
        """SELECT chip_played AS chip, gw, COUNT(*) AS count
           FROM leader_gw_snapshots WHERE chip_played IS NOT NULL
           GROUP BY chip_played, gw ORDER BY gw, chip_played""")]
    first_chip = {}
    for r in rows:
        first_chip.setdefault(r["chip"], {"gw": r["gw"], "count": r["count"]})
    return {"rows": rows, "first_chip": first_chip}


def transfer_discipline(conn):
    all_rows = [dict(r) for r in conn.execute(
        "SELECT event_transfers, hit_cost FROM leader_gw_snapshots")]
    if not all_rows:
        return {"mean_per_gw": None, "median_per_gw": None, "hit_freq": None,
                "mean_hit_cost": None, "histogram": []}
    transfers = [r["event_transfers"] for r in all_rows]
    hits = [r["hit_cost"] for r in all_rows if r["hit_cost"]]
    hist = {}
    for t in transfers:
        hist[t] = hist.get(t, 0) + 1
    return {"mean_per_gw": round(mean(transfers), 3),
            "median_per_gw": median(transfers),
            "hit_freq": round(len(hits) / len(all_rows), 3),
            "mean_hit_cost": round(mean(hits), 2) if hits else 0.0,
            "histogram": [{"transfers": t, "count": c}
                          for t, c in sorted(hist.items())]}


def bank_value(conn):
    def _series(col):
        out = []
        for r in conn.execute(
                f"SELECT gw, AVG({col}) AS m FROM leader_gw_snapshots "
                f"GROUP BY gw ORDER BY gw"):
            vals = [x[col] for x in conn.execute(
                f"SELECT {col} FROM leader_gw_snapshots WHERE gw=? ORDER BY {col}",
                (r["gw"],))]
            out.append({"gw": r["gw"], "mean": round(r["m"], 2),
                        "median": median(vals)})
        return out
    return {"bank": _series("bank"), "value": _series("value")}


def rank_momentum(conn):
    rows = [dict(r) for r in conn.execute(
        """SELECT entry_id, gw, overall_rank FROM leader_gw_snapshots
           ORDER BY entry_id, gw""")]
    series = {}
    for r in rows:
        series.setdefault(r["entry_id"], []).append(r)
    movers = []
    for eid, pts in series.items():
        for prev, cur in zip(pts, pts[1:]):
            gain = prev["overall_rank"] - cur["overall_rank"]  # positive = climbed
            movers.append({"entry_id": eid, "from_gw": prev["gw"], "to_gw": cur["gw"],
                           "rank_gain": gain})
    movers.sort(key=lambda m: -m["rank_gain"])
    elite = [r["entry_id"] for r in conn.execute(
        "SELECT entry_id FROM leader_entries WHERE past_season_rank IS NOT NULL "
        "AND past_season_rank <= ?", (SUSTAINED_ELITE_MAX_RANK,))]
    return {"top_movers": movers[:10], "sustained_elite": elite}


def cohort_stats(conn):
    rows = conn.execute(
        """SELECT e.entry_id, e.player_name, e.entry_name, e.last_rank AS rank,
                  e.last_total AS total, e.past_season_rank AS past_rank,
                  s.points AS last_gw_points, s.event_transfers AS transfers,
                  s.hit_cost, s.bank, s.value, s.chip_played
           FROM leader_entries e
           LEFT JOIN leader_gw_snapshots s
             ON s.entry_id = e.entry_id
            AND s.gw = (SELECT MAX(gw) FROM leader_gw_snapshots s2
                        WHERE s2.entry_id = e.entry_id)
           ORDER BY e.last_rank""").fetchall()
    chips = {}
    for r in conn.execute(
            "SELECT entry_id, chip_played FROM leader_gw_snapshots "
            "WHERE chip_played IS NOT NULL"):
        chips.setdefault(r["entry_id"], []).append(r["chip_played"])
    out = []
    for r in rows:
        d = dict(r)
        d["chips_used"] = chips.get(r["entry_id"], [])
        out.append(d)
    return out


def analyze(conn):
    return {"cohort": cohort_stats(conn),
            "patterns": {"chip_timing": chip_timing(conn),
                         "transfers": transfer_discipline(conn),
                         "bank_value": bank_value(conn),
                         "momentum": rank_momentum(conn)}}
