# Deadguard Undo (Transfer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-tap revert of the single free transfer deadguard made, before the deadline — a reverse transfer (sell the bought player, buy back the sold one), triggered by a Telegram ↩️ Undo button or a CLI command, refusing safely if it's too late or the squad changed.

**Architecture:** Deadguard records `{out_id, in_id}` on the gameweek when its transfer succeeds. `run_undo_transfer` (execution layer) builds the reverse transfer via the existing executor. `run_undo` (deadguard/jobs) guards (recorded? not undone? before deadline?) then reverses, marks undone, transitions to USER_ACTED, and notifies. A `z:{gw}` Telegram callback and a `undo-transfer` CLI command both call it. Not freeze-gated — undo is a deliberate user action.

**Tech Stack:** Python 3.11+, sqlite3, pytest (fixtures-only — never live), APScheduler. The `db` fixture is in-memory sqlite + `init_db`.

**Spec:** `docs/superpowers/specs/2026-05-24-deadguard-undo-transfer-design.md`

**Conventions (follow exactly):**
- Tests use the `db` fixture; assert via SQL or module calls. **NEVER `git add -A`** — stage explicit paths.
- Run with `.venv/bin/pytest`. Baseline at the start of this plan: **377 passed**.
- B-rules: B8 (single free reverse transfer, never a new transfer/hit), B6 (reverse via the API; no native reset), B7 (no secret logged; SessionExpired → maybe_auto_freeze), B9 (notify every outcome), B10 (log). No `decision-engine.md` change (execution action, not a decision).

---

### Task 1: Storage — `gameweeks` undo columns + repo helpers

**Files:**
- Modify: `src/data/schema.sql` (gameweeks), `src/data/db.py` (`_migrate_gameweeks`), `src/data/repository.py`
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deadguard.py` (has `_seed_gw(db, gw=30)`, imports `repository`):

```python
def test_deadguard_transfer_record_round_trip(db):
    _seed_gw(db)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(gameweeks)")}
    assert "deadguard_transfer_json" in cols and "deadguard_transfer_undone_at" in cols
    assert repository.get_deadguard_transfer(db, 30) is None
    repository.set_deadguard_transfer(db, 30, 7, 99)
    assert repository.get_deadguard_transfer(db, 30) == {"out_id": 7, "in_id": 99}
    repository.mark_deadguard_transfer_undone(db, 30)
    assert db.execute(
        "SELECT deadguard_transfer_undone_at FROM gameweeks WHERE id=30").fetchone()["deadguard_transfer_undone_at"] is not None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/pytest tests/test_deadguard.py -k transfer_record_round_trip -v`
Expected: FAIL (helpers/columns missing).

- [ ] **Step 3: Implement**

In `src/data/schema.sql`, in the `gameweeks` table, the last columns are currently `deadguard_warned_at TIMESTAMP,` and `deadguard_reeval_alerted_at TIMESTAMP`. Add two more (ensure commas are correct, `);` stays):

```sql
  deadguard_reeval_alerted_at TIMESTAMP,
  deadguard_transfer_json TEXT,
  deadguard_transfer_undone_at TIMESTAMP
);
```

In `src/data/db.py` `_migrate_gameweeks`, add two idempotent column-adds (after the existing ones):

```python
    if "deadguard_transfer_json" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_transfer_json TEXT")
    if "deadguard_transfer_undone_at" not in cols:
        conn.execute("ALTER TABLE gameweeks ADD COLUMN deadguard_transfer_undone_at TIMESTAMP")
```

In `src/data/repository.py` (it already imports `json` and has `_now`), add near the other `mark_deadguard_*` helpers:

```python
def set_deadguard_transfer(conn, gw, out_id, in_id):
    conn.execute("UPDATE gameweeks SET deadguard_transfer_json=? WHERE id=?",
                 (json.dumps({"out_id": out_id, "in_id": in_id}), gw))
    conn.commit()


def get_deadguard_transfer(conn, gw):
    row = conn.execute("SELECT deadguard_transfer_json FROM gameweeks WHERE id=?", (gw,)).fetchone()
    return json.loads(row["deadguard_transfer_json"]) if row and row["deadguard_transfer_json"] else None


def mark_deadguard_transfer_undone(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_transfer_undone_at=? WHERE id=?", (_now(), gw))
    conn.commit()
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `.venv/bin/pytest tests/test_deadguard.py -k transfer_record_round_trip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/data/schema.sql src/data/db.py src/data/repository.py tests/test_deadguard.py
git commit -m "feat: gameweeks undo-transfer columns + repo helpers (2.5c-2)"
```

---

### Task 2: Reverse-transfer executor — `run_undo_transfer`

**Files:**
- Modify: `src/execution/transfer.py` (add `run_undo_transfer`)
- Test: `tests/test_transfer.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer.py`:

```python
class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _UndoSession:
    def __init__(self, picks, post_status=200):
        self._picks = picks
        self._post_status = post_status
        self.posted = None
        self.headers = {}

    def get(self, url, timeout=None):
        return _Resp(200, {"picks": self._picks})

    def post(self, url, json=None, timeout=None):
        self.posted = json
        return _Resp(self._post_status, {})


def _seed_next_gw_and_player(db, out_id=7, out_price=5.4):
    db.execute("INSERT INTO gameweeks (id, is_next, finished) VALUES (30, 1, 0)")
    db.execute("INSERT INTO players (id, web_name, price) VALUES (?, 'Out', ?)", (out_id, out_price))
    db.commit()


def test_run_undo_transfer_builds_reverse_payload(db):
    from src.execution import transfer as transfer_mod
    _seed_next_gw_and_player(db, out_id=7, out_price=5.4)
    sess = _UndoSession([{"element": 99, "selling_price": 60}])    # the bought player still in squad
    res = transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=True,
                                         confirm_fn=lambda d: True, session=sess)
    assert res.ok
    t = sess.posted["transfers"][0]
    assert t["element_out"] == 99 and t["element_in"] == 7          # reverse direction
    assert t["selling_price"] == 60                                 # in_id selling price (from picks)
    assert t["purchase_price"] == 54                                # out_id price*10 (from players)


def test_run_undo_transfer_dry_run_does_not_post(db):
    from src.execution import transfer as transfer_mod
    _seed_next_gw_and_player(db)
    sess = _UndoSession([{"element": 99, "selling_price": 60}])
    res = transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=False, session=sess)
    assert res.dry_run is True and sess.posted is None


def test_run_undo_transfer_in_player_gone_raises(db):
    import pytest
    from src.execution import transfer as transfer_mod
    from src.execution import executor as executor_mod
    _seed_next_gw_and_player(db)
    sess = _UndoSession([{"element": 11, "selling_price": 50}])     # 99 no longer in squad
    with pytest.raises(executor_mod.ExecutorError):
        transfer_mod.run_undo_transfer(db, b"key", out_id=7, in_id=99, live=True,
                                       confirm_fn=lambda d: True, session=sess)
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_transfer.py -k undo -v`
Expected: FAIL (`run_undo_transfer` missing).

- [ ] **Step 3: Implement**

In `src/execution/transfer.py`, add (the module already imports `config`, `executor`, `repository`, `transfers`, and `auth_session`; reuse them — match `run_transfer`'s imports):

```python
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
    url = executor.TRANSFERS_URL.format(entry=entry)
    if live and (confirm_fn is None or not confirm_fn(diff)):
        repository.log_activity(conn, decision_type="transfer", mode="manual", action_taken="undo aborted",
                                executed=False, exec_outcome={"diff": diff})
        return executor.ExecResult(dry_run=True, request={"method": "POST", "url": url, "body": payload},
                                   status=None, ok=False)
    result = executor.apply_transfers(session, entry, payload, dry_run=not live)
    repository.log_activity(conn, decision_type="transfer", mode="manual",
                            action_taken=(f"undo: OUT {in_id} IN {out_id}" if live else "undo dry-run"),
                            executed=(result.ok and not result.dry_run),
                            exec_outcome={"status": result.status, "request": result.request})
    return result
```

(If `transfer.py`'s existing import alias differs, match it — e.g. `from src.auth import session as auth_session`. Use whatever `run_transfer` already uses for `ensure_session`.)

- [ ] **Step 4: Run them to confirm they pass**

Run: `.venv/bin/pytest tests/test_transfer.py -v`
Expected: PASS (3 new + all existing transfer tests).

- [ ] **Step 5: Commit**

```bash
git add src/execution/transfer.py tests/test_transfer.py
git commit -m "feat: run_undo_transfer — reverse a transfer (free pre-deadline) (2.5c-2)"
```

---

### Task 3: Orchestration — `deadguard.run_undo`

**Files:**
- Modify: `src/interface/deadguard.py` (add `run_undo`)
- Test: `tests/test_deadguard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deadguard.py` (has `_seed_gw_dl`, `_NOW`, `_configure_tg`, `_CFG`, `timedelta`, `types`, imports `deadguard`, `telegram`, `repository`):

```python
def test_run_undo_nothing_to_undo(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=60), state="DEADGUARD_EXECUTED")   # no transfer recorded
    notes, ran = [], []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    monkeypatch.setattr(deadguard.transfer_exec, "run_undo_transfer", lambda *a, **k: ran.append(1))
    deadguard.run_undo(db, b"key", 30, now=_NOW)
    assert ran == [] and "info" in notes


def test_run_undo_already_undone(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=60), state="DEADGUARD_EXECUTED")
    repository.set_deadguard_transfer(db, 30, 7, 99)
    repository.mark_deadguard_transfer_undone(db, 30)
    ran = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.transfer_exec, "run_undo_transfer", lambda *a, **k: ran.append(1))
    deadguard.run_undo(db, b"key", 30, now=_NOW)
    assert ran == []


def test_run_undo_past_deadline(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW - timedelta(minutes=1), state="DEADGUARD_EXECUTED")     # deadline passed
    repository.set_deadguard_transfer(db, 30, 7, 99)
    ran = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: None)
    monkeypatch.setattr(deadguard.transfer_exec, "run_undo_transfer", lambda *a, **k: ran.append(1))
    deadguard.run_undo(db, b"key", 30, now=_NOW)
    assert ran == []


def test_run_undo_success_marks_and_user_acted(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=60), state="DEADGUARD_EXECUTED")
    repository.set_deadguard_transfer(db, 30, 7, 99)
    notes = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    monkeypatch.setattr(deadguard.transfer_exec, "run_undo_transfer",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    deadguard.run_undo(db, b"key", 30, now=_NOW)
    row = db.execute("SELECT state, deadguard_transfer_undone_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "USER_ACTED" and row["deadguard_transfer_undone_at"] is not None
    assert "executed" in notes


def test_run_undo_not_ok_alerts(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=60), state="DEADGUARD_EXECUTED")
    repository.set_deadguard_transfer(db, 30, 7, 99)
    notes = []
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    monkeypatch.setattr(deadguard.transfer_exec, "run_undo_transfer",
                        lambda conn, key, **k: types.SimpleNamespace(ok=False, dry_run=False, status=500))
    deadguard.run_undo(db, b"key", 30, now=_NOW)
    assert "alert" in notes
    assert db.execute(
        "SELECT deadguard_transfer_undone_at FROM gameweeks WHERE id=30").fetchone()["deadguard_transfer_undone_at"] is None
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py -k run_undo -v`
Expected: FAIL (`deadguard.run_undo` missing).

- [ ] **Step 3: Implement**

In `src/interface/deadguard.py`, add `run_undo` (after `run_undo`'s dependencies — place it near `run_deadguard_job`). It uses existing module imports (`repository`, `transfer_exec`, `override`, `_notify`, `SessionExpired`, `datetime`, `timezone`, `log`):

```python
def run_undo(conn, key, gw, *, live=True, confirm_fn=None, now=None):
    target = repository.get_deadguard_transfer(conn, gw)
    if target is None:
        _notify(conn, "info", "Nothing to undo — deadguard made no transfer this gameweek.")
        return None
    row = conn.execute("SELECT deadline_utc, deadguard_transfer_undone_at FROM gameweeks WHERE id=?",
                       (gw,)).fetchone()
    if row["deadguard_transfer_undone_at"]:
        _notify(conn, "info", "Already undone.")
        return None
    now = now or datetime.now(timezone.utc)
    if row["deadline_utc"] and now >= datetime.fromisoformat(row["deadline_utc"]):
        _notify(conn, "info", "Too late to undo — the deadline has passed.")
        return None
    try:
        result = transfer_exec.run_undo_transfer(conn, key, out_id=target["out_id"], in_id=target["in_id"],
                                                 live=live, confirm_fn=confirm_fn)
    except SessionExpired:
        froze = override.maybe_auto_freeze(conn)
        _notify(conn, "alert", "Undo: FPL session expired — re-run init-fpl.")
        if froze:
            _notify(conn, "alert", "Auto-execution FROZEN — 2 consecutive auth failures. "
                                   "Re-run init-fpl, then unfreeze.")
        return None
    except Exception as e:
        log.exception("deadguard undo failed")
        _notify(conn, "alert", f"Undo failed: {type(e).__name__} — the squad may have changed.")
        return None
    if getattr(result, "ok", False) and not getattr(result, "dry_run", False):
        repository.mark_deadguard_transfer_undone(conn, gw)
        repository.touch_user_action(conn, gw)
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken=f"undo transfer: restored {target['out_id']}, removed {target['in_id']}",
                                inputs=target, executed=True)
        _notify(conn, "executed", "Reverted deadguard's transfer — sold player restored, free transfer back.")
    elif not getattr(result, "dry_run", False):
        _notify(conn, "alert", "Undo did not complete — the squad may have changed.")
    return result
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `.venv/bin/pytest tests/test_deadguard.py -k run_undo -v`
Expected: PASS (5 new). Then run the full file: `.venv/bin/pytest tests/test_deadguard.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard.run_undo — guarded reverse + USER_ACTED + notify (2.5c-2)"
```

---

### Task 4: `_run_trigger` records the undo target + sends the ↩️ Undo button

**Files:**
- Modify: `src/interface/deadguard.py` (`_run_trigger` transfer block)
- Test: `tests/test_deadguard.py` (UPDATE one existing test + append one)

- [ ] **Step 1: Update the existing trigger test and write the new one**

In `tests/test_deadguard.py`, the existing `test_trigger_executes_flagged_transfer` fakes `run_transfer` returning a `SimpleNamespace` WITHOUT a `request`. Update that fake to include a `request` body (since real `run_transfer` always returns one, and `_run_trigger` now reads it):

Replace its `run_transfer` monkeypatch line:
```python
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer",
                        lambda conn, key, **k: xfers.append(k.get("rank")) or types.SimpleNamespace(ok=True, dry_run=False, status=200))
```
with:
```python
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer",
                        lambda conn, key, **k: xfers.append(k.get("rank")) or types.SimpleNamespace(
                            ok=True, dry_run=False, status=200,
                            request={"method": "POST", "url": "u", "body": {"transfers": [{"element_out": 7, "element_in": 99}]}}))
```

Append a new test:
```python
def test_trigger_records_undo_target_and_sends_button(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="PENDING")
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: None)
    sent = []
    monkeypatch.setattr(deadguard.telegram, "send_message", lambda text, **k: sent.append(k.get("buttons")) or True)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: 1)
    monkeypatch.setattr(deadguard.transfer_exec, "run_transfer",
                        lambda conn, key, **k: types.SimpleNamespace(
                            ok=True, dry_run=False, status=200,
                            request={"method": "POST", "url": "u", "body": {"transfers": [{"element_out": 7, "element_in": 99}]}}))
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert repository.get_deadguard_transfer(db, 30) == {"out_id": 7, "in_id": 99}
    assert [[{"text": "↩️ Undo", "callback_data": "z:30"}]] in sent


def test_trigger_no_transfer_no_undo_record(db, monkeypatch):
    _configure_tg(monkeypatch)
    _seed_gw_dl(db, _NOW + timedelta(minutes=20), state="PENDING")
    monkeypatch.setattr(deadguard.telegram, "notify", lambda conn, **k: None)
    sent = []
    monkeypatch.setattr(deadguard.telegram, "send_message", lambda text, **k: sent.append(k.get("buttons")) or True)
    monkeypatch.setattr(deadguard.captain, "get_captain_picks",
                        lambda conn: {"picks": [{"player_id": 5, "web_name": "Cap"}], "vice_player_id": 6, "confidence": 80})
    monkeypatch.setattr(deadguard.lineup, "run_lineup",
                        lambda conn, key, **k: types.SimpleNamespace(ok=True, dry_run=False, status=200))
    monkeypatch.setattr(deadguard, "_pick_flagged_transfer", lambda conn, cfg: None)
    deadguard.run_deadguard_job(b"key", conn=db, now=_NOW, cfg=_CFG)
    assert repository.get_deadguard_transfer(db, 30) is None
    assert all(b != [[{"text": "↩️ Undo", "callback_data": "z:30"}]] for b in sent)
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_deadguard.py -k "records_undo_target or no_transfer_no_undo" -v`
Expected: FAIL (no recording, no button).

- [ ] **Step 3: Implement**

In `src/interface/deadguard.py`, in `_run_trigger`, change the transfer block (step 3) to track `transfer_applied`, record the undo target from `tr.request`, and send the Undo button after the executed `_notify`. The block becomes:

```python
    # 3. transfer-if-flagged (best-effort; never undoes the lineup, never retried)
    transfer_note = "no transfer"
    transfer_applied = False
    try:
        rank = _pick_flagged_transfer(conn, cfg)
        if rank is not None:
            tr = transfer_exec.run_transfer(conn, key, rank=rank, live=True, confirm_fn=lambda d: True)
            if getattr(tr, "ok", False):
                transfer_note = "transfer applied"
                transfer_applied = True
                body = tr.request["body"]["transfers"][0]
                repository.set_deadguard_transfer(conn, gw, body["element_out"], body["element_in"])
            else:
                transfer_note = "transfer failed"
                _notify(conn, "alert", "Deadguard: flagged-player transfer did not complete.")
    except Exception as e:
        transfer_note = f"transfer failed ({type(e).__name__})"
        log.exception("deadguard transfer step failed")
        _notify(conn, "alert", f"Deadguard transfer failed: {type(e).__name__}")
    try:
        repository.log_activity(conn, decision_type="deadguard", mode="deadguard",
                                action_taken=f"captain {name}; bench optimized; {transfer_note}",
                                inputs={"pick": caps["picks"][0]}, executed=True)
    except Exception:
        log.exception("deadguard summary log failed (lineup and transfer already applied)")
    _notify(conn, "executed", f"Deadguard: captain {name}, bench optimized, {transfer_note}.")
    if transfer_applied:
        try:
            telegram.send_message("↩️ Undo the transfer? Free before the deadline.",
                                  buttons=[[{"text": "↩️ Undo", "callback_data": f"z:{gw}"}]])
        except Exception:
            log.exception("deadguard undo-button send failed")
```

(Only the transfer block + the trailing Undo-button send change. The lineup steps 1–2 are unchanged.)

- [ ] **Step 4: Run the full file to confirm it passes**

Run: `.venv/bin/pytest tests/test_deadguard.py -v`
Expected: PASS — the 2 new tests, the updated `test_trigger_executes_flagged_transfer`, and all other deadguard tests.

- [ ] **Step 5: Commit**

```bash
git add src/interface/deadguard.py tests/test_deadguard.py
git commit -m "feat: deadguard records undo target + sends Undo button on transfer (2.5c-2)"
```

---

### Task 5: Telegram `handle_undo` + `poll_once` `z:` routing

**Files:**
- Modify: `src/interface/telegram_interactive.py`
- Test: `tests/test_telegram_interactive.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_interactive.py` (has `_configure`, `_cq(data, chat_id="42")`, imports `telegram`, `ti`):

```python
def test_handle_undo_calls_run_undo(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    from src.interface import deadguard
    calls = []
    monkeypatch.setattr(deadguard, "run_undo", lambda conn, key, gw, **k: calls.append((gw, k.get("live"))))
    ti.handle_undo(db, b"key", _cq("z:30"))
    assert calls == [(30, True)]


def test_handle_undo_wrong_chat_ignored(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    from src.interface import deadguard
    calls = []
    monkeypatch.setattr(deadguard, "run_undo", lambda conn, key, gw, **k: calls.append(1))
    ti.handle_undo(db, b"key", _cq("z:30", chat_id="999"))
    assert calls == []


def test_poll_once_routes_undo(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 50, "callback_query": {"id": "z", "data": "z:30", "message": {"chat": {"id": "42"}}}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    seen = []
    monkeypatch.setattr(ti, "handle_undo", lambda conn, key, cq, **k: seen.append(cq["id"]))
    confirms = []
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: confirms.append(cq["id"]))
    ti.poll_once(b"key", conn=db)
    assert seen == ["z"] and confirms == []
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_telegram_interactive.py -k undo -v`
Expected: FAIL (`ti.handle_undo` missing; `poll_once` routes `z:` to `handle_callback`).

- [ ] **Step 3: Implement**

In `src/interface/telegram_interactive.py`, add `handle_undo` near `handle_freeze`/`handle_unfreeze`:

```python
def handle_undo(conn, key, cq, *, session=None):
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return
    _, _, gw_s = cq.get("data", "").partition(":")
    if not gw_s.isdigit():
        telegram.answer_callback_query(cq["id"], text="Unknown action", session=session)
        return
    from src.interface import deadguard
    deadguard.run_undo(conn, key, int(gw_s), live=True, confirm_fn=lambda d: True)
    telegram.answer_callback_query(cq["id"], text="Undo requested", session=session)
```

In `poll_once`, add the `z:` branch to the dispatch chain (it currently has `k:`/`f:`/`u:`/else). Insert before the `else`:

```python
                    elif data.startswith("z:"):
                        handle_undo(conn, key, cq, session=session)
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `.venv/bin/pytest tests/test_telegram_interactive.py -v`
Expected: PASS — the 3 new tests plus all existing (k:/f:/u:/c:/r: routing unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram_interactive.py tests/test_telegram_interactive.py
git commit -m "feat: telegram z: undo handler + poll_once routing (2.5c-2)"
```

---

### Task 6: CLI `undo-transfer`

**Files:**
- Modify: `src/cli.py` (`_undo_transfer_cli` + subparser + dispatch)
- Test: `tests/test_cli_undo.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_undo.py`:

```python
from src import cli
from src.auth import master
from src.data import repository


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _UndoSession:
    def __init__(self, picks, post_status=200):
        self._picks = picks
        self._post_status = post_status
        self.posted = None
        self.headers = {}

    def get(self, url, timeout=None):
        return _Resp(200, {"picks": self._picks})

    def post(self, url, json=None, timeout=None):
        self.posted = json
        return _Resp(self._post_status, {})


def _master(tmp_path, monkeypatch):
    s, v = tmp_path / ".salt", tmp_path / ".verify"
    master.init_master_password("throwaway-master-12", s, v)
    monkeypatch.setenv("MASTER_PASSWORD", "throwaway-master-12")
    return s, v


def _seed(db):
    from datetime import datetime, timezone, timedelta
    deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.execute("INSERT INTO gameweeks (id, is_next, finished, state, deadline_utc) VALUES (30, 1, 0, 'DEADGUARD_EXECUTED', ?)",
               (deadline,))
    db.execute("INSERT INTO players (id, web_name, price) VALUES (7, 'Out', 5.4)")
    db.commit()
    repository.set_deadguard_transfer(db, 30, 7, 99)


def test_undo_cli_dry_run(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    _seed(db)
    sess = _UndoSession([{"element": 99, "selling_price": 60}])
    cli._undo_transfer_cli(conn=db, salt_path=s, verify_path=v, live=False, session=sess)
    assert sess.posted is None
    assert "DRY-RUN" in capsys.readouterr().out


def test_undo_cli_live(tmp_path, monkeypatch, db, capsys):
    s, v = _master(tmp_path, monkeypatch)
    _seed(db)
    sess = _UndoSession([{"element": 99, "selling_price": 60}], post_status=200)
    cli._undo_transfer_cli(conn=db, salt_path=s, verify_path=v, live=True, session=sess,
                           confirm_fn=lambda d: True)
    assert sess.posted is not None
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "USER_ACTED"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_cli_undo.py -v`
Expected: FAIL (`cli._undo_transfer_cli` missing).

- [ ] **Step 3: Implement**

In `src/cli.py`, add `_undo_transfer_cli` (mirrors `_execute_transfer_cli`'s master-key + dry-run/live + print pattern, but calls `run_undo`, which itself does the guards/marking; thread `session` through to `run_undo` → `run_undo_transfer`):

```python
def _undo_transfer_cli(conn=None, salt_path=None, verify_path=None, live=False,
                       session=None, confirm_fn=None):
    from .auth import master
    from .auth.session import SessionError
    from .execution import executor as executor_mod
    from .interface import deadguard
    from .decisions.transfers import _next_gw
    mkw = {}
    if salt_path is not None:
        mkw["salt_path"] = salt_path
    if verify_path is not None:
        mkw["verify_path"] = verify_path
    if not master.is_initialized(**mkw):
        print("Master password not set — run `fpl-autopilot init-master-password` first.")
        return
    key = master.get_master_key(**mkw)
    if confirm_fn is None:
        def confirm_fn(diff):
            print(f"Planned undo: {diff}")
            return input("Type 'yes' to submit to your live FPL team: ").strip().lower() == "yes"
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path())
    init_db(conn)
    try:
        gw = _next_gw(conn)
        if gw is None:
            print("No upcoming gameweek.")
            return
        try:
            result = deadguard.run_undo(conn, key, gw, live=live, confirm_fn=confirm_fn,
                                        **({"session": session} if session is not None else {}))
        except (executor_mod.ExecutorError, SessionError) as exc:
            print(f"Could not undo: {exc}")
            return
        if result is None:
            print("Nothing to undo (no deadguard transfer, already undone, or deadline passed).")
        elif result.dry_run:
            print("DRY-RUN — would POST:")
            print(f"  {result.request['method']} {result.request['url']}")
            print(f"  body: {result.request['body']}")
        elif result.ok:
            print(f"Undone. HTTP {result.status}.")
        else:
            print(f"Undo failed (HTTP {result.status}); nothing changed.")
    finally:
        if owns_conn:
            conn.close()
```

This requires `run_undo` to accept an optional `session` kwarg passed through to `run_undo_transfer`. Update `run_undo`'s signature to `def run_undo(conn, key, gw, *, live=True, confirm_fn=None, now=None, session=None):` and pass `session=session` into the `transfer_exec.run_undo_transfer(...)` call. (Default `None` → unchanged for the Telegram path.)

In `main`, add the subparser + dispatch:
```python
    p_undo = sub.add_parser("undo-transfer", help="revert deadguard's transfer before the deadline (dry-run unless --live)")
    p_undo.add_argument("--live", action="store_true", help="actually submit the reverse transfer (requires typed confirmation)")
    ...
    elif args.command == "undo-transfer":
        _undo_transfer_cli(live=args.live)
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `.venv/bin/pytest tests/test_cli_undo.py tests/test_deadguard.py -q`
Expected: PASS (the 2 CLI tests + all deadguard tests — confirm the `run_undo` `session` kwarg didn't break the Task-3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli.py src/interface/deadguard.py tests/test_cli_undo.py
git commit -m "feat: CLI undo-transfer (dry-run/--live) + run_undo session passthrough (2.5c-2)"
```

---

### Task 7: `deadguard.md` note + full-suite verification + final review

**Files:**
- Modify: `docs/deadguard.md`

- [ ] **Step 1: Add the undo note**

In `docs/deadguard.md`, near the "User opens the dashboard after deadguard executed" / "Emergency override" sections, add:

```markdown
## Undo (transfer) — Telegram + CLI (Phase 2.5c-2)

When deadguard makes its single free transfer, it records the swap on the gameweek and offers a one-tap
**↩️ Undo** (Telegram button `z:{gw}`, or `fpl-autopilot undo-transfer --live`). Undo submits the reverse
transfer (sell the bought player, buy back the sold one) — free while still pending before the deadline — then
marks the gameweek USER_ACTED (which also stops late-news re-evaluation). It is available only before the
deadline and only once; if the deadline has passed or the bought player is no longer in the squad, undo refuses
with a notice rather than making a new transfer. Undo is a deliberate user action, so the 2.7 emergency freeze
does not block it. Lineup changes (captain/bench) are not undone — they are user-adjustable.
```

- [ ] **Step 2: Run the FULL suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — all green (377 baseline + the ~18 new 2.5c-2 tests ≈ 395). If anything fails, fix before committing.

- [ ] **Step 3: Commit**

```bash
git add docs/deadguard.md
git commit -m "docs: deadguard undo-transfer note (2.5c-2)"
```

---

## Definition of done (CLAUDE.md B14)
- After deadguard makes a transfer, an ↩️ Undo button (and `undo-transfer --live`) reverses it before the deadline — sold player restored, free transfer back, GW → USER_ACTED, user notified. After the deadline or if the squad changed, undo refuses safely (notice, no new transfer). No hit/chip/multi ever.
- All tests pass (`.venv/bin/pytest -q`); every new test is fixtures-only (fake session/picks/apply, in-memory DB, frozen clock) — the agent never runs live or `--live` (R3).
- `activity_log` captures the undo (`mode="deadguard"`) + the reverse transfer row. No secret logged (B7). No `decision-engine.md` change. `deadguard.md` updated.

## Self-review notes (checked against the spec)
- **Spec coverage:** §1 storage → Task 1; §2 executor → Task 2; §3 run_undo → Task 3; §1 recording + button → Task 4; §4 telegram → Task 5; §5 CLI → Task 6; docs → Task 7. All mapped.
- **Type/name consistency:** `run_undo_transfer(conn, key, *, out_id, in_id, live, confirm_fn, session)`, `run_undo(conn, key, gw, *, live, confirm_fn, now, session)`, `handle_undo(conn, key, cq, *, session)`, helpers `set_deadguard_transfer`/`get_deadguard_transfer`/`mark_deadguard_transfer_undone`, columns `deadguard_transfer_json`/`deadguard_transfer_undone_at`, callback prefix `z:` — identical across tasks.
- **Existing test touched:** only `test_trigger_executes_flagged_transfer` (Task 4) gains a `request` body in its fake `run_transfer` (real `run_transfer` always returns one). All other existing tests stay green.
- **session passthrough:** Task 6 adds `session=None` to `run_undo`; the Task-3 tests don't pass it (default None), so they remain valid.
```
