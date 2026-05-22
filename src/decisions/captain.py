"""Captain ranker (Decision Layer). Reads Analytics output (xp) + squad/fixtures and
ranks the 15-man squad for captaincy. Suggest-only — no execution/persistence (CLAUDE.md B2)."""
import json

MODEL_VERSION = "v1"


def rank_captains(candidates):
    """Pure: rank captain candidates, build reasoning, return the top-5 picks.

    Each candidate: {player_id, web_name, position, xp, xminutes, fdr_attack, fixture}.
    Order: xp desc, tiebreak xminutes desc, then fdr_attack asc.
    Returns up to 5 picks: {player_id, web_name, xp, fixture, reason}."""
    ranked = sorted(candidates, key=lambda c: (-c["xp"], -c["xminutes"], c["fdr_attack"]))[:5]
    picks = []
    for i, c in enumerate(ranked):
        if i == 0 and len(ranked) > 1:
            s = ranked[1]
            reason = (f"Highest xP ({c['xp']}) {c['fixture']}. "
                      f"Next best {s['web_name']} {s['xp']} — gap {round(c['xp'] - s['xp'], 1)}.")
        elif i == 0:
            reason = f"Highest xP ({c['xp']}) {c['fixture']}."
        else:
            reason = f"xP {c['xp']} {c['fixture']}."
        picks.append({"player_id": c["player_id"], "web_name": c["web_name"],
                      "xp": c["xp"], "fixture": c["fixture"], "reason": reason})
    return picks
