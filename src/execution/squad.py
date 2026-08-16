"""Apply the AI-built squad to FPL (pre-season unlimited-transfer window).

Dry-run by default; --live requires the master key + typed confirm (R3: the
user drives live). Builds out/in pairs from the current snapshot and submits
them sequentially via the shared executor; any API refusal aborts the rest and
reports what applied. Deviation from the design spec: we call
executor.apply_transfers directly per pair instead of threading a rebuild mode
through run_transfer — same API behavior, no coupling to the suggestion engine.
"""
import json

from src import config
from src.auth import session as auth_session
from src.execution import executor
from src.data import repository


def plan_squad_transfers(conn, target_picks):
    """Pure: current my_team snapshot vs target player ids -> out/in pairs.

    FPL requires same-type swaps, so outs and ins are paired WITHIN position
    (GK/DEF/MID/FWD) — positional zipping broke when the current squad's pick
    order differed from the target's (2026-08-16: Guéhi -> Donnarumma refused
    with transfer_element_type_mismatch). Pairs are capped at the smaller side
    per position; an unbalanced position silently drops the extras (a legal
    2-5-5-3 validator makes this impossible in practice).
    """
    snap = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    current = [pk["element"] for pk in json.loads(snap["picks_json"])] if snap else []
    target = [pk["player_id"] for pk in target_picks]
    ids = current + target
    pos = {r["id"]: r["position"] for r in conn.execute(
        "SELECT id, position FROM players WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    names = {r["id"]: r["web_name"] for r in conn.execute(
        "SELECT id, web_name FROM players WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    keep = set(target) & set(current)

    def _by_position(elements):
        groups = {}
        for pid in elements:
            groups.setdefault(pos.get(pid), []).append(pid)
        return groups

    outs = _by_position([pid for pid in current if pid not in keep])
    ins = _by_position([pid for pid in target if pid not in keep])
    pairs = []
    for position in ("GKP", "DEF", "MID", "FWD"):
        out_list, in_list = outs.get(position, []), ins.get(position, [])
        for i, out_id in enumerate(out_list):
            if i >= len(in_list):
                break
            pairs.append({"element_out": out_id, "element_in": in_list[i],
                          "out_name": names.get(out_id), "in_name": names.get(in_list[i])})
    return pairs


def _next_gw(conn):
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return nxt["gw"] if nxt else None


def _purchase_price(conn, player_id):
    row = conn.execute("SELECT price FROM players WHERE id=?", (player_id,)).fetchone()
    return round(row["price"] * 10) if row else None


def apply_squad(conn, key, *, live=False, confirm_fn=None, session=None, provider=None,
                model_id="deepseek-chat"):
    from src.ai.squad import runner

    # Pre-season the snapshot is usually absent (my_team 404) — no current squad to
    # diff against; "no changes to apply" would be misleading. Fail actionably.
    snap = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
    if snap is None:
        repository.log_activity(conn, decision_type="squad", mode="manual",
                                action_taken="refused: no current squad snapshot",
                                inputs={"live": live}, executed=False)
        return {"applied": [], "failed": ["no current squad snapshot — "
                                          "save a squad on FPL first (pre-season)"],
                "dry_run": not live, "pairs": []}

    result = runner.generate_squad(conn, provider=provider, model_id=model_id)
    if result is None:
        return {"applied": [], "failed": ["squad builder produced no result"],
                "dry_run": not live, "pairs": []}
    pairs = plan_squad_transfers(conn, result["picks"])
    if not pairs:
        repository.log_activity(conn, decision_type="squad", mode="manual",
                                action_taken="refused: squad already matches",
                                inputs={"picks": [p["player_id"] for p in result["picks"]]},
                                executed=False)
        return {"applied": [], "failed": ["no changes to apply"], "dry_run": not live,
                "pairs": []}
    session = session or auth_session.ensure_session(conn, key)
    entry = config.team_id()
    # FPL checks per-swap affordability (bank + selling >= purchase). With total funds
    # sufficient, ordering swaps by descending (selling - purchase) keeps the running
    # balance >= 0 — bank-building swaps first (2026-08-16: Kinsky -> Donnarumma was
    # refused with insufficient_balance, while a later swap would have funded it).
    sell = {p["element"]: p["selling_price"]
            for p in executor.fetch_current_picks(session, entry)}
    pairs = sorted(pairs, key=lambda pair: (
        sell.get(pair["element_out"], 0) - (_purchase_price(conn, pair["element_in"]) or 0)),
        reverse=True)
    if live and (confirm_fn is None or not confirm_fn(f"{len(pairs)} transfers to apply")):
        return {"applied": [], "failed": ["aborted by user"], "dry_run": False,
                "pairs": pairs}
    event = _next_gw(conn)
    applied, failed = [], []
    for pair in pairs:
        try:
            current = executor.fetch_current_picks(session, entry)
            selling_price = next((p["selling_price"] for p in current
                                  if p["element"] == pair["element_out"]), None)
            if selling_price is None:
                raise executor.ExecutorError(f"{pair['out_name']} not in current squad")
            purchase_price = _purchase_price(conn, pair["element_in"])
            payload = executor.build_transfer_payload(
                entry=entry, event=event,
                element_out=pair["element_out"], element_in=pair["element_in"],
                selling_price=selling_price, purchase_price=purchase_price)
            res = executor.apply_transfers(session, entry, payload, dry_run=not live)
            if res is None or getattr(res, "ok", True) is False:
                status = getattr(res, "status", None)
                body = getattr(res, "error", None)
                raise executor.ExecutorError(
                    f"transfer refused by API (HTTP {status}: {body})")
            applied.append(pair)
        except Exception as exc:
            failed.append(f"{pair['out_name']} -> {pair['in_name']}: {exc}")
            break
        repository.log_activity(conn, decision_type="squad", mode="manual",
                                action_taken=f"apply {pair['out_name']} IN {pair['in_name']}",
                                inputs={"pair": pair, "live": live}, executed=live)
    return {"applied": applied, "failed": failed, "dry_run": not live, "pairs": pairs}
