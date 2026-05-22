import re
import unicodedata
from dataclasses import dataclass

# Understat single-team names that don't normalize-match FPL team names (2025/26 PL).
# Season-specific (D5): revisit on rollover. Maps Understat team -> FPL team name.
UNDERSTAT_TEAM_OVERRIDES = {
    "Manchester City": "Man City",
    "Manchester United": "Man Utd",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Tottenham": "Spurs",
    "Wolverhampton Wanderers": "Wolves",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class ResolutionResult:
    matched: dict          # understat_id -> fpl_player_id
    unmatched: list        # UnderstatPlayer objects with no confident match
    unmapped_teams: list   # understat team tokens that mapped to no FPL team


def _team_lookup(fpl_teams):
    lookup = {}
    for t in fpl_teams:
        lookup[_norm(t["name"])] = t["id"]
        lookup[_norm(t["short_name"])] = t["id"]
    return lookup


def _resolve_team_title(team_title, team_lookup):
    # team_title may be comma-separated for mid-season transfers.
    ids, unmapped = [], []
    for token in team_title.split(","):
        token = token.strip()
        tid = team_lookup.get(_norm(UNDERSTAT_TEAM_OVERRIDES.get(token, token)))
        if tid is None:
            unmapped.append(token)
        else:
            ids.append(tid)
    return ids, unmapped


def resolve_players(fpl_players, fpl_teams, understat_players, overrides=None):
    overrides = overrides or {}
    team_lookup = _team_lookup(fpl_teams)
    by_team = {}
    for p in fpl_players:
        by_team.setdefault(p["team_id"], []).append(
            (p["id"], set(_norm(p["name"]).split()), set(_norm(p["web_name"]).split()))
        )

    matched, unmatched, unmapped_teams = {}, [], set()
    for up in understat_players:
        if up.id in overrides:
            matched[up.id] = overrides[up.id]
            continue
        team_ids, unmapped = _resolve_team_title(up.team_title, team_lookup)
        unmapped_teams.update(unmapped)
        u = set(_norm(up.player_name).split())
        # Candidates are SCOPED TO THE PLAYER'S TEAM(S). This team-scoping is what makes
        # the name heuristics below safe — broadening `cands` past the team would break
        # the conservative guarantee (especially tier 2).
        cands = [c for tid in team_ids for c in by_team.get(tid, [])]
        # Tier 1: full-name token subset, either direction. `full <= u` is safe only
        # because FPL `name` (first + second) is effectively always multi-token, so a
        # common first name never forms a single-token `full` that would match every
        # Understat name containing it. Ambiguity (len != 1) -> left unmatched.
        tier1 = {fid for fid, full, web in cands if u <= full or full <= u}
        if len(tier1) == 1:
            matched[up.id] = next(iter(tier1))
            continue
        # Tier 2 (only if tier 1 found nothing): web_name (usually surname) tokens inside
        # the Understat name. Reliable ONLY because `cands` is team-scoped above — a bare
        # surname is often ambiguous globally but usually unique within one team.
        if not tier1:
            tier2 = {fid for fid, full, web in cands if web and web <= u}
            if len(tier2) == 1:
                matched[up.id] = next(iter(tier2))
                continue
        unmatched.append(up)
    return ResolutionResult(matched, unmatched, sorted(unmapped_teams))
