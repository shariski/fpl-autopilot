from src import config
from src.auth import session as auth_session
from src.decisions import transfers
from src.execution import executor
from src.data import repository


def run_transfer(conn, key, *, rank=1, live=False, confirm_fn=None, session=None, suggester=None,
                 allow_hit=False):
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    sugg = (suggester or transfers.get_transfer_suggestions)(conn)
    suggestions = sugg["suggestions"]
    if not suggestions:
        raise executor.ExecutorError(sugg.get("empty_reason") or "no transfer suggestion available")
    if not (1 <= rank <= len(suggestions)):
        raise executor.ExecutorError(f"rank {rank} out of range (1..{len(suggestions)})")
    chosen = suggestions[rank - 1]
    free_transfers = sugg.get("free_transfers")
    element_out = chosen["out"]["player_id"]
    element_in = chosen["in"]["player_id"]
    purchase_price = round(chosen["in"]["price"] * 10)
    current = executor.fetch_current_picks(session, entry)
    selling_price = next((p["selling_price"] for p in current if p["element"] == element_out), None)
    if selling_price is None:
        raise executor.ExecutorError(f"player {element_out} not in current squad")
    event = transfers._next_gw(conn)
    payload = executor.build_transfer_payload(entry=entry, event=event, element_out=element_out,
                                              element_in=element_in, selling_price=selling_price,
                                              purchase_price=purchase_price)
    diff = (f"OUT {chosen['out']['web_name']} -> IN {chosen['in']['web_name']} "
            f"(EP +{chosen['ep_delta_5gw']})")
    inputs = {"chosen": chosen,
              "alternatives": [s for i, s in enumerate(suggestions) if i != rank - 1],
              "free_transfers": free_transfers}
    url = executor.TRANSFERS_URL.format(entry=entry)

    # Preflight: refuse live -4 hits unless explicitly opted in. Dry-run is observational, never blocked.
    if live and free_transfers == 0 and not allow_hit:
        repository.log_activity(conn, decision_type="transfer", mode="manual",
                                action_taken="refused: would cost -4 hit (free_transfers=0)",
                                inputs=inputs, executed=False,
                                exec_outcome={"diff": diff, "free_transfers": 0})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload,
                                            "note": "refused: would cost -4 hit"},
                                   status=None, ok=False)

    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="transfer", mode="manual",
                                action_taken="aborted", inputs=inputs, executed=False,
                                exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True,
                                   request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)

    result = executor.apply_transfers(session, entry, payload, dry_run=not live)
    action = f"OUT {element_out} IN {element_in}" if live else "dry-run"
    repository.log_activity(conn, decision_type="transfer", mode="manual", action_taken=action,
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result


def run_undo_transfer(conn, key, *, out_id, in_id, live=False, confirm_fn=None, session=None):
    """Reverse a transfer: sell in_id (bought earlier), buy back out_id. Free pre-deadline (FPL nets it)."""
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    current = executor.fetch_current_picks(session, entry)
    selling_price = next((p["selling_price"] for p in current if p["element"] == in_id), None)
    if selling_price is None:
        raise executor.ExecutorError(f"player {in_id} not in current squad — cannot undo")
    row = conn.execute("SELECT price FROM players WHERE id=?", (out_id,)).fetchone()
    if row is None:
        raise executor.ExecutorError(f"player {out_id} not found — cannot undo")
    purchase_price = round(row["price"] * 10)
    event = transfers._next_gw(conn)
    payload = executor.build_transfer_payload(entry=entry, event=event, element_out=in_id, element_in=out_id,
                                              selling_price=selling_price, purchase_price=purchase_price)
    diff = f"UNDO: OUT {in_id} -> IN {out_id}"
    inputs = {"out_id": out_id, "in_id": in_id, "selling_price": selling_price, "purchase_price": purchase_price}
    url = executor.TRANSFERS_URL.format(entry=entry)
    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="transfer", mode="manual", action_taken="undo aborted",
                                inputs=inputs, executed=False, exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True, request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)
    result = executor.apply_transfers(session, entry, payload, dry_run=not live)
    repository.log_activity(conn, decision_type="transfer", mode="manual",
                            action_taken=(f"undo: OUT {in_id} IN {out_id}" if live else "undo dry-run"),
                            inputs=inputs, executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
