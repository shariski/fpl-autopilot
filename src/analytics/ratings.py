"""Team and player rates from the Vaastav databank (v0.12/v0.13).

Ratings feed FDR v2 (per-fixture multipliers) and xP v2 (per-player rates). Windows span
seasons: the last N distinct (season, gw) pairs present in player_stats, ordered by
season then gw. Pre-season (no current-season rows yet) the window naturally spans the
last complete season.

All constants pinned in docs/decision-engine.md (v0.12/v0.13) — do not change without a
B4 activity-log entry.
"""
from dataclasses import dataclass

LF_GW_COUNT = 38
SF_GW_COUNT = 6
LF_WEIGHT = 0.8
SF_WEIGHT = 0.2
DAMP_THRESHOLD = 1.55
DAMP_FACTOR = 0.4
MIN_GWS_FOR_RATING = 5

# Promoted teams have no databank history at season start (26-27; Between The Lines
# study: -33.5% xG / +46.5% xGA vs Championship). Applied until MIN_GWS_FOR_RATING GWs.
PROMOTED_XG90 = {"COV": 1.9, "IPS": 1.72, "HUL": 1.3}
PROMOTED_XGC90 = 1.55


def damp(x):
    """Damp extreme multipliers: |x| <= 1.55 passes through, excess decays at 40%."""
    return (min(abs(x), DAMP_THRESHOLD) + max(abs(x) - DAMP_THRESHOLD, 0.0) * DAMP_FACTOR) \
        * (1 if x >= 0 else -1)


@dataclass
class TeamRating:
    team_id: int
    xg90: float
    xgc90: float
    dc90: float
    gw_count: int


@dataclass
class LeagueAverage:
    xg90: float
    xgc90: float
    dc90: float


def _databank_rows(conn):
    return conn.execute(
        """SELECT ps.source, ps.gw, ps.minutes, ps.xg, ps.xgc, ps.dc, p.team_id
           FROM player_stats ps JOIN players p ON p.id = ps.player_id
           WHERE ps.source LIKE 'fpl_databank:%'""").fetchall()


def _window_keys(rows, gw_count):
    """The last `gw_count` distinct (source, gw) pairs across all rows, season-ordered."""
    keys = sorted({(r["source"], r["gw"]) for r in rows})
    return set(keys[-gw_count:]) if gw_count > 0 else set()


def _aggregate(rows, keys):
    """Per-team raw sums over the window: [mins, xg, xgc, dc, nm].

    xGC is team-shared per match (every player row carries the same match xGC),
    so minutes-normalization cancels the squad-size factor. xG and DC are
    per-player stats that sum to the team match total ONCE — dividing by ALL
    players' minutes deflates them by the squad-size factor (~13x real-world),
    which broke the league average (la.xg90 ~0.13 vs true ~1.35) and made
    promoted-override multipliers explode (observed 2026-08-20: damp(1.3/0.132)
    = 4.86 -> Lammens 14.43 xP). They are normalized per MATCH instead (nm,
    one match per team per GW).
    """
    teams = {}
    for r in rows:
        if (r["source"], r["gw"]) not in keys:
            continue
        t = teams.setdefault(r["team_id"], [0.0, 0.0, 0.0, 0.0, set()])
        t[0] += r["minutes"]
        t[1] += r["xg"]
        t[2] += r["xgc"]
        t[3] += r["dc"]
        t[4].add((r["source"], r["gw"]))
    out = {}
    for tid, (mins, xg, xgc, dc, matches) in teams.items():
        if mins <= 0:
            continue
        out[tid] = (mins, xg, xgc, dc, len(matches))
    return out


def _team_rates(agg):
    """{team_id: (xg90, xgc90, dc90)} from raw sums: xG/DC per match (90 min),
    xGC per player-minute (cancels the squad-size factor)."""
    out = {}
    for tid, (mins, xg, xgc, dc, nm) in agg.items():
        if mins <= 0 or nm <= 0:
            continue
        out[tid] = (xg / nm, xgc / mins * 90, dc / nm)
    return out


def _blend(lf, sf):
    return {tid: (LF_WEIGHT * lf[tid][0] + SF_WEIGHT * sf[tid][0],
                  LF_WEIGHT * lf[tid][1] + SF_WEIGHT * sf[tid][1],
                  LF_WEIGHT * lf[tid][2] + SF_WEIGHT * sf[tid][2])
            for tid in lf}


def compute_team_ratings(conn, lf_gw_count=LF_GW_COUNT, sf_gw_count=SF_GW_COUNT):
    """Per-team blended LF/SF rates + league averages from databank rows.

    Returns (ratings: dict[team_id -> TeamRating], la: LeagueAverage).
    Teams with no databank rows are absent (promoted-team overrides handle them).
    """
    rows = _databank_rows(conn)
    lf_keys = _window_keys(rows, lf_gw_count)
    sf_keys = _window_keys(rows, sf_gw_count)
    lf = _team_rates(_aggregate(rows, lf_keys))
    sf = _team_rates(_aggregate(rows, sf_keys))
    blend = _blend(lf, sf)
    ratings = {tid: TeamRating(team_id=tid, xg90=round(v[0], 4), xgc90=round(v[1], 4),
                               dc90=round(v[2], 4), gw_count=len({(r["source"], r["gw"])
                                                                  for r in rows if r["team_id"] == tid}))
               for tid, v in blend.items()}

    def _league(agg):
        mins = xg = xgc = dc = nm = 0.0
        for _mins, _xg, _xgc, _dc, _nm in agg.values():
            mins += _mins
            xg += _xg
            xgc += _xgc
            dc += _dc
            nm += _nm
        return mins, xg, xgc, dc, nm

    lf_m, lf_xg, lf_xgc, lf_dc, lf_nm = _league(_aggregate(rows, lf_keys))
    sf_m, sf_xg, sf_xgc, sf_dc, sf_nm = _league(_aggregate(rows, sf_keys))

    def _per90(x, m):
        return (x / m * 90) if m else 0.0

    def _per_match(x, nm):
        return (x / nm) if nm else 0.0

    la = LeagueAverage(
        xg90=(LF_WEIGHT * _per_match(lf_xg, lf_nm) + SF_WEIGHT * _per_match(sf_xg, sf_nm)),
        xgc90=(LF_WEIGHT * _per90(lf_xgc, lf_m) + SF_WEIGHT * _per90(sf_xgc, sf_m)),
        dc90=(LF_WEIGHT * _per_match(lf_dc, lf_nm) + SF_WEIGHT * _per_match(sf_dc, sf_nm)),
    )
    return ratings, la


def promoted_overrides(conn):
    """{team_id: (xg90, xgc90)} for promoted teams with no databank history."""
    out = {}
    for r in conn.execute("SELECT id, short_name FROM teams"):
        if r["short_name"] in PROMOTED_XG90:
            out[r["id"]] = (PROMOTED_XG90[r["short_name"]], PROMOTED_XGC90)
    return out


def opponent_rating(ratings, overrides, team_id):
    """Rating for `team_id`, honoring promoted-team overrides until 5 GWs are in."""
    r = ratings.get(team_id)
    if r is not None and r.gw_count >= MIN_GWS_FOR_RATING:
        return r
    if overrides and team_id in overrides:
        xg90, xgc90 = overrides[team_id]
        return TeamRating(team_id=team_id, xg90=xg90, xgc90=xgc90, dc90=0.0, gw_count=0)
    return r


# ---------- Player rates (xP v2) ----------

DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}


@dataclass
class PlayerRates:
    player_id: int
    team_id: int
    position: str
    starts: int            # LF window
    squads_made: int       # LF window (team matches played)
    xg_per_start: float    # LF/SF blended
    xa_per_start: float
    dc_hit_rate: float     # P(DC >= threshold | start), blended
    saves_per_90: float
    yc_per_90: float
    rc_per_90: float
    p60: float             # P(minutes >= 60 | minutes > 0), LF window


def _player_agg(rows, keys):
    """Per-player aggregates over the window: [mins, starts, xg, xa, dc_hits, saves, yc, rc, p60c, played]."""
    out = {}
    for r in rows:
        if (r["source"], r["gw"]) not in keys:
            continue
        a = out.setdefault(r["player_id"], [0.0] * 10)
        a[0] += r["minutes"]
        a[1] += r["starts"]
        a[2] += r["xg"]
        a[3] += r["xa"]
        if r["starts"] and r["dc"] >= DC_THRESHOLD.get(r["position"], 9999):
            a[4] += 1
        a[5] += r["saves"]
        a[6] += r["yellow_cards"]
        a[7] += r["red_cards"]
        if r["minutes"] > 0:
            a[8] += 1 if r["minutes"] >= 60 else 0
            a[9] += 1
    return out


def compute_player_rates(conn, lf_gw_count=LF_GW_COUNT, sf_gw_count=SF_GW_COUNT):
    """Per-player rates for xP v2 from databank rows. Returns {player_id: PlayerRates}.

    DC hits are counted per start against the position threshold (DEF 10, MID/FWD 12).
    p60 uses the LF window only; all other rates blend LF 0.8 / SF 0.2.
    """
    rows = conn.execute(
        """SELECT ps.source, ps.gw, ps.minutes, ps.xg, ps.xa, ps.dc, ps.saves,
                  ps.starts, ps.yellow_cards, ps.red_cards,
                  p.id AS player_id, p.team_id, p.position
           FROM player_stats ps JOIN players p ON p.id = ps.player_id
           WHERE ps.source LIKE 'fpl_databank:%'""").fetchall()
    lf_keys = _window_keys(rows, lf_gw_count)
    sf_keys = _window_keys(rows, sf_gw_count)
    lf = _player_agg(rows, lf_keys)
    sf = _player_agg(rows, sf_keys)
    # squads made per team in the LF window (one match per team per GW)
    teams_squads = {}
    for r in rows:
        if (r["source"], r["gw"]) in lf_keys:
            teams_squads.setdefault(r["team_id"], set()).add((r["source"], r["gw"]))

    out = {}
    info = {r["player_id"]: r for r in rows}
    for pid in lf:
        l = lf[pid]
        s = sf.get(pid, [0.0] * 10)
        squads = teams_squads.get(info[pid]["team_id"], set())
        out[pid] = PlayerRates(
            player_id=pid,
            team_id=info[pid]["team_id"],
            position=info[pid]["position"],
            starts=int(l[1]),
            squads_made=len(squads),
            xg_per_start=round(_blend_rates(l[2], s[2], l[1], s[1]), 4),
            xa_per_start=round(_blend_rates(l[3], s[3], l[1], s[1]), 4),
            dc_hit_rate=round(_blend_rates(l[4], s[4], l[1], s[1]), 4),
            saves_per_90=round(_blend_rates(l[5], s[5], l[0], s[0], per90=True), 4),
            yc_per_90=round(_blend_rates(l[6], s[6], l[0], s[0], per90=True), 4),
            rc_per_90=round(_blend_rates(l[7], s[7], l[0], s[0], per90=True), 4),
            p60=round(l[8] / l[9], 4) if l[9] else 0.0,
        )
    return out


def _blend_rates(lf_val, sf_val, lf_den, sf_den, per90=False):
    """Blend LF/SF per-unit rates (0.8/0.2), guarding zero denominators."""
    def _rate(v, d):
        if per90:
            return (v / d * 90) if d else 0.0
        return (v / d) if d else 0.0
    return LF_WEIGHT * _rate(lf_val, lf_den) + SF_WEIGHT * _rate(sf_val, sf_den)
