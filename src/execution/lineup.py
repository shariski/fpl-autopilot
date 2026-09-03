from src import config
from src.auth import session as auth_session
from src.decisions import captain as captain_mod
from src.decisions import bench as bench_mod
from src.decisions import formation_rebalancer as form_mod
from src.decisions import optimal_xi as opt_mod
from src.execution import executor
from src.data import repository


def _format_diff(current, captain_id, vice_id):
    cur_c = next((p["element"] for p in current if p.get("is_captain")), None)
    cur_v = next((p["element"] for p in current if p.get("is_vice_captain")), None)
    return f"captain {cur_c}->{captain_id}, vice {cur_v}->{vice_id}"


def _apply_optimal_xi(current, opt):
    """Re-shape the squad into picks ordered by squad slot (1-15). Each
    pick's `position` is the slot the optimizer assigned — slots 1-11
    are starters (per FPL's convention), slots 12-15 are the bench in
    the order chosen by the optimizer (GK first, then xP desc)."""
    starter_slots = opt["starter_slots"]
    bench_slots = opt["bench_slots"]
    captain_id = opt["captain_id"]
    vice_id = opt["vice_id"]
    slot_to_pick = {}
    for p in current:
        eid = p["element"]
        if eid in starter_slots:
            slot_to_pick[starter_slots[eid]] = {
                "element": eid, "position": starter_slots[eid],
                "is_captain": eid == captain_id,
                "is_vice_captain": eid == vice_id,
            }
        elif eid in bench_slots:
            slot_to_pick[bench_slots[eid]] = {
                "element": eid, "position": bench_slots[eid],
                "is_captain": False, "is_vice_captain": False,
            }
    return [slot_to_pick[slot] for slot in sorted(slot_to_pick)], captain_id, vice_id


def run_lineup(conn, key, *, live=False, confirm_fn=None, session=None, ranker=None,
               optimize_bench=False, rebalance_formation=True, optimal_xi=True):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    current = executor.fetch_current_picks(session, entry)

    # v0.26: optimal-XI selection. If it succeeds, it determines the
    # starting 10 + bench 5 + captain/vice. Otherwise fall through to the
    # ranker for captain/vice + bench_mod for bench order.
    opt = opt_mod.select(conn, current) if optimal_xi else None
    if opt is not None:
        picks, captain_id, vice_id = _apply_optimal_xi(current, opt)
        bench_order = None  # bench is set by the optimizer already
        # Synthesize a minimal captain dict for the activity log. The
        # ranker's full output (with web_name/xp/alternatives) is replaced
        # by the optimizer's choice; we only need player_id for diff/log.
        caps = {"picks": [{"player_id": captain_id}],
                "vice_player_id": vice_id}
    else:
        caps = (ranker or captain_mod.get_captain_picks)(conn)
        if not caps["picks"]:
            raise executor.ExecutorError("no captain pick available (no data?)")
        captain_id = caps["picks"][0]["player_id"]
        vice_id = caps["vice_player_id"]
        bench_order = bench_mod.rank_bench(conn, current) if optimize_bench else None
        picks = current

    xi_swap = (form_mod.rebalance(conn, picks, captain_id=captain_id, vice_id=vice_id)
               if rebalance_formation else None)
    payload = executor.build_lineup_payload(picks, captain_id, vice_id,
                                            bench_order=bench_order, xi_swap=xi_swap)
    diff = _format_diff(picks, captain_id, vice_id)
    if xi_swap:
        diff += f", xi_swap {xi_swap}"
    inputs = {"captain": caps["picks"][0], "vice_player_id": vice_id,
              "alternatives": caps["picks"][1:]}
    if bench_order is not None:
        inputs["bench_order"] = bench_order
    if opt is not None:
        inputs["optimal_xi"] = {
            "formation": opt["formation"], "total_xp": opt["total_xp"],
            "captain_id": opt["captain_id"], "vice_id": opt["vice_id"],
            "starter_slots": opt["starter_slots"],
            "bench_slots": opt["bench_slots"],
        }
    if xi_swap:
        info = form_mod.formation_info(conn, picks)
        inputs["formation_rebalance"] = {**info, "swap": xi_swap}
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
    outcome = {"status": result.status, "request": result.request}
    if result.error:
        outcome["error"] = result.error
    repository.log_activity(conn, decision_type="lineup", mode="manual", action_taken=action,
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome=outcome)
    return result
