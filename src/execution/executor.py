from dataclasses import dataclass

MY_TEAM_URL = "https://fantasy.premierleague.com/api/my-team/{entry}/"
TIMEOUT = 10


class ExecutorError(Exception):
    """Invalid lineup payload or a failed team read."""


@dataclass
class ExecResult:
    dry_run: bool
    request: dict       # {"method", "url", "body"} — the exact (would-be) request
    status: int | None  # HTTP status for live; None for dry-run
    ok: bool


def build_lineup_payload(current_picks, captain_id, vice_id):
    if captain_id == vice_id:
        raise ExecutorError("captain and vice must be different players")
    elements = {p["element"] for p in current_picks}
    if captain_id not in elements:
        raise ExecutorError(f"captain {captain_id} not in current squad")
    if vice_id not in elements:
        raise ExecutorError(f"vice {vice_id} not in current squad")
    picks = [
        {"element": p["element"], "position": p["position"],
         "is_captain": p["element"] == captain_id,
         "is_vice_captain": p["element"] == vice_id}
        for p in current_picks
    ]
    return {"chip": None, "picks": picks}
