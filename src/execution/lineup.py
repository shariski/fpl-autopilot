from src import config
from src.auth import session as auth_session
from src.decisions import captain as captain_mod
from src.execution import executor
from src.data import repository


def _format_diff(current, captain_id, vice_id):
    cur_c = next((p["element"] for p in current if p.get("is_captain")), None)
    cur_v = next((p["element"] for p in current if p.get("is_vice_captain")), None)
    return f"captain {cur_c}->{captain_id}, vice {cur_v}->{vice_id}"


def run_lineup(conn, key, *, live=False, confirm_fn=None, session=None, ranker=None):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    current = executor.fetch_current_picks(session, entry)
    caps = (ranker or captain_mod.get_captain_picks)(conn)
    if not caps["picks"]:
        raise executor.ExecutorError("no captain pick available (no data?)")
    captain_id = caps["picks"][0]["player_id"]
    vice_id = caps["vice_player_id"]
    payload = executor.build_lineup_payload(current, captain_id, vice_id)
    diff = _format_diff(current, captain_id, vice_id)
    inputs = {"captain": caps["picks"][0], "vice_player_id": vice_id,
              "alternatives": caps["picks"][1:]}
    url = executor.MY_TEAM_URL.format(entry=entry)

    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="lineup", mode="manual",
                                action_taken="aborted", inputs=inputs, executed=False,
                                exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)

    result = executor.apply_lineup(session, entry, payload, dry_run=not live)
    action = f"captain={captain_id}, vice={vice_id}" if live else "dry-run"
    repository.log_activity(conn, decision_type="lineup", mode="manual", action_taken=action,
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
