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
