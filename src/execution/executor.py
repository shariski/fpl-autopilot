from dataclasses import dataclass

MY_TEAM_URL = "https://fantasy.premierleague.com/api/my-team/{entry}/"
# The transfer-SUBMIT endpoint; the entry id goes in the POST body (see build_transfer_payload),
# not the URL. (`/api/entry/{entry}/transfers/` is the read-only transfer HISTORY — POST → 405.)
# `.format(entry=...)` is a harmless no-op here (no placeholder); callers pass entry anyway.
TRANSFERS_URL = "https://fantasy.premierleague.com/api/transfers/"
TIMEOUT = 10


class ExecutorError(Exception):
    """Invalid lineup payload or a failed team read."""


@dataclass
class ExecResult:
    dry_run: bool
    request: dict       # {"method", "url", "body"} — the exact (would-be) request
    status: int | None  # HTTP status for live; None for dry-run
    ok: bool


def build_lineup_payload(current_picks, captain_id, vice_id, bench_order=None):
    if captain_id == vice_id:
        raise ExecutorError("captain and vice must be different players")
    elements = {p["element"] for p in current_picks}
    if captain_id not in elements:
        raise ExecutorError(f"captain {captain_id} not in current squad")
    if vice_id not in elements:
        raise ExecutorError(f"vice {vice_id} not in current squad")
    pos_override = {}
    if bench_order is not None:
        current_bench = {p["element"] for p in current_picks if p["position"] in (13, 14, 15)}
        if set(bench_order) != current_bench:
            raise ExecutorError("bench_order must be exactly the current bench (positions 13-15)")
        pos_override = {element: 13 + i for i, element in enumerate(bench_order)}
    picks = [
        {"element": p["element"], "position": pos_override.get(p["element"], p["position"]),
         "is_captain": p["element"] == captain_id,
         "is_vice_captain": p["element"] == vice_id}
        for p in current_picks
    ]
    return {"chip": None, "picks": picks}


def fetch_current_picks(session, entry_id):
    resp = session.get(MY_TEAM_URL.format(entry=entry_id), timeout=TIMEOUT)
    if resp.status_code != 200:
        raise ExecutorError(f"could not read current team (HTTP {resp.status_code})")
    return resp.json().get("picks", [])


def _post_json(session, url, payload, *, dry_run):
    request = {"method": "POST", "url": url, "body": payload}
    if dry_run:
        return ExecResult(dry_run=True, request=request, status=None, ok=True)
    resp = session.post(url, json=payload, timeout=TIMEOUT)
    return ExecResult(dry_run=False, request=request, status=resp.status_code, ok=resp.status_code == 200)


def apply_lineup(session, entry_id, payload, *, dry_run):
    return _post_json(session, MY_TEAM_URL.format(entry=entry_id), payload, dry_run=dry_run)


def apply_transfers(session, entry_id, payload, *, dry_run):
    return _post_json(session, TRANSFERS_URL.format(entry=entry_id), payload, dry_run=dry_run)


def build_transfer_payload(*, entry, event, element_out, element_in, selling_price, purchase_price):
    return {"chip": None, "entry": entry, "event": event,
            "transfers": [{"element_in": element_in, "element_out": element_out,
                           "purchase_price": purchase_price, "selling_price": selling_price}]}
