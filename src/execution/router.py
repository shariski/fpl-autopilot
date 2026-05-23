from src import config
from src.decisions import captain, transfers
from src.execution import lineup, transfer as transfer_exec
from src.data import repository

HYBRID_TRANSFER_EP_FLOOR = 4.0


def _auto_approve(diff=None):
    return True


def route(mode, decision_type, *, confidence, ep_delta=None, is_hit=False, floor=70):
    if mode == "manual":
        return "notify"
    if mode == "auto":
        eligible = True
    elif mode == "hybrid":
        if decision_type == "captain":
            eligible = True
        elif decision_type == "transfer":
            eligible = (not is_hit) and ((ep_delta or 0) >= HYBRID_TRANSFER_EP_FLOOR)
        else:
            eligible = False
    else:
        eligible = False
    if not eligible:
        return "notify"
    if confidence is None or confidence < floor:
        return "notify"
    return "execute"


def route_gameweek(conn, key, *, live=False, mode=None, session=None, ranker=None, suggester=None):
    mode = mode or config.mode()
    floor = config.confidence_floor()
    caps = (ranker or captain.get_captain_picks)(conn)
    plan = []
    if caps["picks"]:
        r = route(mode, "captain", confidence=caps["confidence"], floor=floor)
        cap_name = caps["picks"][0]["web_name"]
        verb = "Captain" if r == "execute" else "Captain pending"
        plan.append({"decision": "captain", "route": r, "confidence": caps["confidence"],
                     "summary": f"{verb}: {cap_name} (confidence {caps['confidence']})",
                     "executed": r == "execute",
                     "identity": {"captain_id": caps["picks"][0]["player_id"],
                                  "vice_id": caps["vice_player_id"]}})
        if r == "execute":
            lineup.run_lineup(conn, key, live=live, confirm_fn=_auto_approve,
                              session=session, ranker=ranker)
        else:
            repository.log_activity(conn, decision_type="lineup", mode=mode,
                                    action_taken=f"pending: captain {caps['picks'][0]['web_name']}",
                                    inputs={"confidence": caps["confidence"], "pick": caps["picks"][0]},
                                    executed=False)
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    if sugg["suggestions"]:
        top = sugg["suggestions"][0]
        r = route(mode, "transfer", confidence=top["confidence"], ep_delta=top["ep_delta_5gw"],
                  is_hit=top["hit_cost"] < 0, floor=floor)
        verb = "Transfer" if r == "execute" else "Transfer pending"
        plan.append({"decision": "transfer", "route": r, "confidence": top["confidence"],
                     "summary": (f"{verb}: OUT {top['out']['web_name']} IN {top['in']['web_name']} "
                                 f"(+{top['ep_delta_5gw']} xP/5GW, conf {top['confidence']})"),
                     "executed": r == "execute",
                     "identity": {"out_id": top["out"]["player_id"],
                                  "in_id": top["in"]["player_id"]}})
        if r == "execute":
            transfer_exec.run_transfer(conn, key, rank=1, live=live, confirm_fn=_auto_approve,
                                       session=session, suggester=suggester)
        else:
            repository.log_activity(conn, decision_type="transfer", mode=mode,
                                    action_taken=f"pending: OUT {top['out']['web_name']} IN {top['in']['web_name']}",
                                    inputs={"confidence": top["confidence"], "suggestion": top},
                                    executed=False)
    return plan
