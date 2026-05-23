from src.analytics.xp import MODEL_VERSION
from src.decisions.transfers import _next_gw


def rank_bench(conn, current_picks):
    """Element ids currently at bench positions 13/14/15, ordered by next-GW xP (desc),
    xMinutes as the rotation-risk tiebreaker. Missing xP -> 0 (sorts last). The sub-GK
    (position 12) is fixed and not reordered."""
    gw = _next_gw(conn)
    bench = [p["element"] for p in current_picks if p["position"] in (13, 14, 15)]

    def _key(element):
        if gw is None:
            return (0.0, 0.0)
        row = conn.execute(
            "SELECT xp, xminutes FROM xp WHERE player_id=? AND gw=? AND model_version=?",
            (element, gw, MODEL_VERSION)).fetchone()
        return (row["xp"], row["xminutes"]) if row else (0.0, 0.0)

    return sorted(bench, key=_key, reverse=True)
