# Telegram Interactive Confirm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Manual/Hybrid user gets a "decision pending" ping with Confirm/Reject buttons and acts with one tap from their phone; the daemon polls Telegram getUpdates and, on Confirm, re-runs+verifies the decision and executes it (or re-notifies if it changed).

**Architecture:** A dedicated `pending_decisions` table holds decision identity + status; a durable `telegram_state` offset makes the poller idempotent. `telegram.py` gains two transport primitives (`get_updates`, `answer_callback_query`); a new `telegram_interactive.py` (Interface layer) creates buttoned pings, polls, and handles callbacks — re-running the existing ranker/suggester to verify, then calling the existing bounded executors. The router stays pure (only gains an additive `identity` field); decision logic is unchanged.

**Tech Stack:** Python 3.11+, `requests`, APScheduler, sqlite, `pytest` + `monkeypatch`. Run tests with `.venv/bin/pytest`.

**Branch:** `feat/telegram-interactive` (already created; the spec is committed there).

**Spec:** `docs/superpowers/specs/2026-05-23-telegram-interactive-design.md`

**Conventions (binding):**
- TDD: test first, watch it fail, minimal impl, watch it pass, commit. Baseline before this plan: **238 passing**.
- **Never `git add -A`** — stage explicit paths only.
- The agent never runs the live poller/daemon or live execution (R3); tests use fakes + `monkeypatch`, no network.
- No `docs/decision-engine.md` change (no decision logic touched).
- `db` is an in-memory sqlite fixture in `tests/conftest.py`; `init_db` runs `schema.sql`, so new tables exist in tests automatically.

---

### Task 1: Schema + repository helpers (`pending_decisions`, `telegram_state`)

**Files:**
- Modify: `src/data/schema.sql`
- Modify: `src/data/repository.py`
- Test: `tests/test_pending_decisions.py` (create)

- [ ] **Step 1: Write the failing tests.** Create `tests/test_pending_decisions.py`:

```python
from src.data import repository


def test_create_and_get_pending_decision(db):
    pid = repository.create_pending_decision(
        db, gw=30, decision_type="transfer",
        identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    row = repository.get_pending_decision(db, pid)
    assert row["gw"] == 30
    assert row["decision_type"] == "transfer"
    assert row["status"] == "pending"
    assert row["summary"] == "Transfer pending: OUT O IN I"
    import json
    assert json.loads(row["identity_json"]) == {"out_id": 7, "in_id": 99}
    assert row["created_at"] is not None and row["resolved_at"] is None


def test_get_pending_decision_missing_returns_none(db):
    assert repository.get_pending_decision(db, 999) is None


def test_set_pending_status_sets_status_and_resolved_at(db):
    pid = repository.create_pending_decision(
        db, gw=30, decision_type="lineup", identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    repository.set_pending_status(db, pid, "confirmed")
    row = repository.get_pending_decision(db, pid)
    assert row["status"] == "confirmed"
    assert row["resolved_at"] is not None


def test_telegram_state_round_trip_and_default(db):
    assert repository.get_telegram_state(db, "update_offset") is None
    repository.set_telegram_state(db, "update_offset", "42")
    assert repository.get_telegram_state(db, "update_offset") == "42"
    repository.set_telegram_state(db, "update_offset", "43")   # upsert
    assert repository.get_telegram_state(db, "update_offset") == "43"
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_pending_decisions.py -q` → `AttributeError: module 'src.data.repository' has no attribute 'create_pending_decision'` (and the tables don't exist yet).

- [ ] **Step 3a: Add the tables to `src/data/schema.sql`** (append at end):

```sql
CREATE TABLE IF NOT EXISTS pending_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gw INTEGER,
  decision_type TEXT NOT NULL,
  identity_json TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP,
  resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_state (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

- [ ] **Step 3b: Add the helpers to `src/data/repository.py`** (append at end; the file already imports `json` and defines `_now()` returning an ISO-UTC string):

```python
def create_pending_decision(conn, *, gw, decision_type, identity, summary):
    cur = conn.execute(
        "INSERT INTO pending_decisions (gw, decision_type, identity_json, summary, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (gw, decision_type, json.dumps(identity), summary, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_decision(conn, pid):
    return conn.execute("SELECT * FROM pending_decisions WHERE id=?", (pid,)).fetchone()


def set_pending_status(conn, pid, status):
    conn.execute("UPDATE pending_decisions SET status=?, resolved_at=? WHERE id=?",
                 (status, _now(), pid))
    conn.commit()


def get_telegram_state(conn, key):
    row = conn.execute("SELECT value FROM telegram_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_telegram_state(conn, key, value):
    conn.execute(
        "INSERT INTO telegram_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_pending_decisions.py -q` → 4 passed.

- [ ] **Step 5: Commit.**
```bash
git add src/data/schema.sql src/data/repository.py tests/test_pending_decisions.py
git commit -m "feat: pending_decisions + telegram_state tables and repo helpers (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Router `plan` enrichment — add `identity`

**Files:**
- Modify: `src/execution/router.py` (the `route_gameweek` function)
- Test: `tests/test_router.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_router.py` (reuses existing `_FakeSession`, `_current`, `_ranker`, `_suggester`, `db`):

```python
def test_route_gameweek_entries_carry_identity(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="manual",
                                 session=sess, ranker=_ranker(90), suggester=_suggester(90, 9.0))
    by = {p["decision"]: p for p in plan}
    assert by["captain"]["identity"] == {"captain_id": 5, "vice_id": 6}
    assert by["transfer"]["identity"] == {"out_id": 7, "in_id": 99}
```

(Identity values come from the existing `_ranker`/`_suggester` fakes: captain pick `player_id` 5, vice 6; transfer out `player_id` 7, in 99.)

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_router.py::test_route_gameweek_entries_carry_identity -q` → `KeyError: 'identity'`.

- [ ] **Step 3: Implement.** In `src/execution/router.py`, add an `"identity"` key to BOTH `plan.append(...)` dicts (everything else in `route_gameweek` stays identical). The captain append becomes:

```python
        plan.append({"decision": "captain", "route": r, "confidence": caps["confidence"],
                     "summary": f"{verb}: {cap_name} (confidence {caps['confidence']})",
                     "executed": r == "execute",
                     "identity": {"captain_id": caps["picks"][0]["player_id"],
                                  "vice_id": caps["vice_player_id"]}})
```

and the transfer append becomes:

```python
        plan.append({"decision": "transfer", "route": r, "confidence": top["confidence"],
                     "summary": (f"{verb}: OUT {top['out']['web_name']} IN {top['in']['web_name']} "
                                 f"(+{top['ep_delta_5gw']} xP/5GW, conf {top['confidence']})"),
                     "executed": r == "execute",
                     "identity": {"out_id": top["out"]["player_id"],
                                  "in_id": top["in"]["player_id"]}})
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_router.py -q` → all pass (existing tests only assert decision/route/confidence/summary/executed; the new key is additive).

- [ ] **Step 5: Commit.**
```bash
git add src/execution/router.py tests/test_router.py
git commit -m "feat: add identity to route_gameweek plan entries (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Config accessor `telegram_interactive_enabled`

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_config.py`:

```python
def test_telegram_interactive_enabled_from_config():
    assert config.telegram_interactive_enabled({"telegram": {"interactive": True}}) is True
    assert config.telegram_interactive_enabled({"telegram": {}}) is False
    assert config.telegram_interactive_enabled({}) is False  # default off
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_config.py::test_telegram_interactive_enabled_from_config -q` → `AttributeError`.

- [ ] **Step 3: Implement.** Append to `src/config.py` (mirrors the existing `unattended_enabled`):

```python
def telegram_interactive_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("telegram", {}).get("interactive", False))
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_config.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/config.py tests/test_config.py
git commit -m "feat: config.telegram_interactive_enabled accessor (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Telegram transport — `get_updates` + `answer_callback_query`

**Files:**
- Modify: `src/interface/telegram.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_telegram.py` (reuses `_Resp`, `_FakeSession`, `_configure` defined earlier in that file; `_FakeSession.post` records `{"url","json","timeout"}`):

```python
def test_get_updates_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.get_updates(None, session=_FakeSession()) == []


def test_get_updates_returns_result_and_passes_offset(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True, "result": [{"update_id": 5}]}))
    out = telegram.get_updates(7, session=sess)
    assert out == [{"update_id": 5}]
    assert sess.posted["url"].endswith("/getUpdates")
    assert sess.posted["json"] == {"offset": 7, "timeout": 0}


def test_get_updates_empty_on_error(monkeypatch):
    _configure(monkeypatch)
    assert telegram.get_updates(None, session=_FakeSession(boom=True)) == []
    assert telegram.get_updates(None, session=_FakeSession(_Resp(500, {}))) == []


def test_answer_callback_query_posts_when_configured(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    assert telegram.answer_callback_query("cbid", text="ok", session=sess) is True
    assert sess.posted["url"].endswith("/answerCallbackQuery")
    assert sess.posted["json"]["callback_query_id"] == "cbid"
    assert sess.posted["json"]["text"] == "ok"


def test_answer_callback_query_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.answer_callback_query("cbid", session=_FakeSession()) is False
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_telegram.py -q` → `AttributeError: ... has no attribute 'get_updates'`.

- [ ] **Step 3: Implement.** Append to `src/interface/telegram.py`:

```python
def get_updates(offset, *, session=None):
    """Telegram getUpdates. Returns the 'result' list, or [] when unconfigured or on any
    error (never raises, never logs the token). offset (int|None) acks prior updates."""
    if not is_configured():
        return []
    token = os.getenv(BOT_TOKEN_ENV)
    session = session or requests.Session()
    try:
        resp = session.post(f"{API_BASE}/bot{token}/getUpdates",
                            json={"offset": offset, "timeout": 0}, timeout=TIMEOUT)
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    try:
        body = resp.json()
    except ValueError:
        return []
    if not isinstance(body, dict) or not body.get("ok"):
        return []
    result = body.get("result")
    return result if isinstance(result, list) else []


def answer_callback_query(callback_query_id, *, text=None, session=None):
    """Ack a callback so the client stops spinning. Returns False when unconfigured/on error."""
    if not is_configured():
        return False
    token = os.getenv(BOT_TOKEN_ENV)
    payload = {"callback_query_id": callback_query_id}
    if text is not None:
        payload["text"] = text
    session = session or requests.Session()
    try:
        resp = session.post(f"{API_BASE}/bot{token}/answerCallbackQuery", json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        return False
    return resp.status_code == 200
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_telegram.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "feat: telegram get_updates + answer_callback_query transport (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `telegram_interactive` — `is_enabled`, `send_pending`, `notify_plan`

**Files:**
- Create: `src/interface/telegram_interactive.py`
- Test: `tests/test_telegram_interactive.py` (create)

- [ ] **Step 1: Write the failing tests.** Create `tests/test_telegram_interactive.py`:

```python
import json
from src.interface import telegram, telegram_interactive as ti


def _configure(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "T")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "42")


def test_is_enabled_requires_config_and_flag(monkeypatch):
    _configure(monkeypatch)
    assert ti.is_enabled({"telegram": {"interactive": True}}) is True
    assert ti.is_enabled({"telegram": {"interactive": False}}) is False
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    assert ti.is_enabled({"telegram": {"interactive": True}}) is False  # unconfigured


def test_send_pending_creates_row_and_buttons(db, monkeypatch):
    _configure(monkeypatch)
    sent = {}
    monkeypatch.setattr(telegram, "send_message",
                        lambda text, **k: sent.update(text=text, buttons=k.get("buttons")) or True)
    entry = {"decision": "transfer", "summary": "Transfer pending: OUT O IN I",
             "identity": {"out_id": 7, "in_id": 99}}
    ti.send_pending(db, entry, gw=30, mode="manual")
    rows = db.execute("SELECT id, decision_type, status, summary FROM pending_decisions").fetchall()
    assert len(rows) == 1
    pid = rows[0]["id"]
    assert rows[0]["decision_type"] == "transfer" and rows[0]["status"] == "pending"
    assert "Transfer pending: OUT O IN I" in sent["text"]
    assert sent["buttons"] == [[{"text": "✅ Confirm", "callback_data": f"c:{pid}"},
                                {"text": "❌ Reject", "callback_data": f"r:{pid}"}]]


def test_send_pending_noop_unconfigured(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    ti.send_pending(db, {"decision": "captain", "summary": "x", "identity": {"captain_id": 1, "vice_id": 2}},
                    gw=1, mode="manual")
    assert db.execute("SELECT COUNT(*) c FROM pending_decisions").fetchone()["c"] == 0


def test_notify_plan_routes_executed_and_pending(db, monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: calls.append(("notify", k["kind"])))
    monkeypatch.setattr(ti, "send_pending", lambda conn, entry, **k: calls.append(("pending", entry["decision"])))
    plan = [{"decision": "captain", "executed": True, "summary": "Captain: X",
             "identity": {"captain_id": 5, "vice_id": 6}},
            {"decision": "transfer", "executed": False, "summary": "Transfer pending: OUT O IN I",
             "identity": {"out_id": 7, "in_id": 99}}]
    ti.notify_plan(db, plan, gw=30, mode="hybrid")
    assert calls == [("notify", "executed"), ("pending", "transfer")]
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → `ModuleNotFoundError: No module named 'src.interface.telegram_interactive'`.

- [ ] **Step 3: Implement.** Create `src/interface/telegram_interactive.py`:

```python
import json
import os
from datetime import datetime, timezone

from src import config
from src.config import db_path
from src.data import repository
from src.data.db import connect, init_db
from src.decisions import captain, transfers
from src.execution import lineup, transfer as transfer_exec
from src.auth.session import SessionExpired
from src.interface import telegram


def is_enabled(cfg=None):
    return telegram.is_configured() and config.telegram_interactive_enabled(cfg)


def _dtype(decision):
    return "lineup" if decision == "captain" else "transfer"


def send_pending(conn, entry, *, gw, mode):
    """Create a pending_decisions row, then send the buttoned ping. No-op if unconfigured."""
    if not telegram.is_configured():
        return
    pid = repository.create_pending_decision(
        conn, gw=gw, decision_type=_dtype(entry["decision"]),
        identity=entry["identity"], summary=entry["summary"])
    buttons = [[{"text": "✅ Confirm", "callback_data": f"c:{pid}"},
                {"text": "❌ Reject", "callback_data": f"r:{pid}"}]]
    text = f"📊 Decision pending\n{entry['summary']}\nConfirm or reject below."
    telegram.send_message(text, buttons=buttons)


def notify_plan(conn, plan, *, gw, mode):
    """Interactive variant of telegram.notify_plan: executed -> ✅ confirmation; pending -> buttoned ping."""
    for entry in plan:
        if entry["executed"]:
            telegram.notify(conn, kind="executed", decision_type=entry["decision"],
                            mode=mode, summary=entry["summary"])
        else:
            send_pending(conn, entry, gw=gw, mode=mode)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → 4 passed.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/telegram_interactive.py tests/test_telegram_interactive.py
git commit -m "feat: telegram_interactive is_enabled/send_pending/notify_plan (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `telegram_interactive.handle_callback`

**Files:**
- Modify: `src/interface/telegram_interactive.py`
- Test: `tests/test_telegram_interactive.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_telegram_interactive.py`:

```python
import types
from datetime import datetime, timezone, timedelta

_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _seed_gw(db, gw=30, deadline=None):
    deadline = deadline or (_NOW + timedelta(hours=1))
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next) VALUES (?, ?, 1)",
               (gw, deadline.isoformat()))
    db.commit()


def _cq(data, chat_id="42", cb_id="cb1"):
    return {"id": cb_id, "data": data, "message": {"chat": {"id": chat_id}}}


def _ranker_caps(captain_id=5, vice_id=6, name="Cap"):
    def f(conn):
        return {"picks": [{"player_id": captain_id, "web_name": name, "xp": 8.0}],
                "vice_player_id": vice_id, "confidence": 80}
    return f


def _suggester_top(out_id=7, in_id=99):
    def f(conn):
        return {"suggestions": [{"out": {"player_id": out_id, "web_name": "O", "price": 5.0},
                                 "in": {"player_id": in_id, "web_name": "I", "price": 6.0},
                                 "ep_delta_5gw": 5.0, "hit_cost": 0, "confidence": 80}],
                "empty_reason": None}
    return f


def _ok_result(*a, **k):
    return types.SimpleNamespace(ok=True, dry_run=False, status=200)


def test_handle_callback_wrong_chat_ignored(db, monkeypatch):
    _configure(monkeypatch)  # CHAT_ID=42
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"c:{pid}", chat_id="999"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "pending"


def test_handle_callback_reject(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    monkeypatch.setattr(telegram, "notify", lambda *a, **k: None)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"r:{pid}"), now=_NOW,
                       transfer_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "rejected"


def test_handle_callback_already_resolved_ignored(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="x")
    repository.set_pending_status(db, pid, "confirmed")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []  # idempotent: not re-executed


def test_handle_callback_confirm_match_executes(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    notes = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: notes.append(k["kind"]))
    executed = []

    def fake_transfer(conn, key, **k):
        executed.append((k.get("live"), k.get("rank")))
        return _ok_result()

    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       suggester=_suggester_top(7, 99), transfer_fn=fake_transfer)
    assert executed == [(True, 1)]
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "confirmed"
    assert "executed" in notes


def test_handle_callback_confirm_changed_supersedes(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="transfer",
                                             identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    executed = []
    # suggester now returns a DIFFERENT in player (88 != 99)
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       suggester=_suggester_top(7, 88), transfer_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "superseded"
    # a NEW pending row was created for the changed recommendation
    assert db.execute("SELECT COUNT(*) c FROM pending_decisions WHERE status='pending'").fetchone()["c"] == 1


def test_handle_callback_confirm_past_deadline_expires(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db, deadline=_NOW - timedelta(hours=1))  # already passed
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="x")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    executed = []
    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=lambda *a, **k: executed.append(1))
    assert executed == []
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "expired"


def test_handle_callback_confirm_execution_failure_marks_failed(db, monkeypatch):
    _configure(monkeypatch)
    _seed_gw(db)
    pid = repository.create_pending_decision(db, gw=30, decision_type="lineup",
                                             identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    monkeypatch.setattr(telegram, "answer_callback_query", lambda cid, **k: True)
    alerts = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: alerts.append(k["kind"]))

    def boom(conn, key, **k):
        raise SessionExpired("expired")

    ti.handle_callback(db, b"key", _cq(f"c:{pid}"), now=_NOW,
                       ranker=_ranker_caps(), lineup_fn=boom)
    assert db.execute("SELECT status FROM pending_decisions WHERE id=?", (pid,)).fetchone()["status"] == "failed"
    assert "alert" in alerts
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → `AttributeError: ... has no attribute 'handle_callback'`.

- [ ] **Step 3: Implement.** Append to `src/interface/telegram_interactive.py`:

```python
def _recompute_entry(conn, decision_type, *, ranker, suggester):
    """Recompute the current top decision. Returns (entry, available). entry has
    decision/summary/identity (router shape) plus confirmed_summary (neutral wording)."""
    if decision_type == "lineup":
        caps = ranker(conn)
        if not caps["picks"]:
            return None, False
        name = caps["picks"][0]["web_name"]
        return ({"decision": "captain",
                 "summary": f"Captain pending: {name} (confidence {caps['confidence']})",
                 "confirmed_summary": f"Captain: {name}",
                 "identity": {"captain_id": caps["picks"][0]["player_id"],
                              "vice_id": caps["vice_player_id"]}}, True)
    sugg = suggester(conn)
    if not sugg["suggestions"]:
        return None, False
    top = sugg["suggestions"][0]
    out_n, in_n = top["out"]["web_name"], top["in"]["web_name"]
    return ({"decision": "transfer",
             "summary": (f"Transfer pending: OUT {out_n} IN {in_n} "
                         f"(+{top['ep_delta_5gw']} xP/5GW, conf {top['confidence']})"),
             "confirmed_summary": f"OUT {out_n} IN {in_n}",
             "identity": {"out_id": top["out"]["player_id"], "in_id": top["in"]["player_id"]}}, True)


def handle_callback(conn, key, cq, *, session=None, now=None,
                    ranker=None, suggester=None, lineup_fn=None, transfer_fn=None):
    ranker = ranker or captain.get_captain_picks
    suggester = suggester or transfers.get_transfer_suggestions
    lineup_fn = lineup_fn or lineup.run_lineup
    transfer_fn = transfer_fn or transfer_exec.run_transfer
    mode = config.mode()

    # 1. chat whitelist
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id"))
    if chat_id != os.getenv(telegram.CHAT_ID_ENV):
        telegram.answer_callback_query(cq["id"], text="Not authorized", session=session)
        return

    # 2. parse + idempotency
    action, _, pid_s = cq.get("data", "").partition(":")
    row = repository.get_pending_decision(conn, int(pid_s)) if pid_s.isdigit() else None
    if row is None or row["status"] != "pending":
        telegram.answer_callback_query(cq["id"], text="Already handled", session=session)
        return
    pid = row["id"]
    dtype = row["decision_type"]

    # 3. reject
    if action == "r":
        repository.set_pending_status(conn, pid, "rejected")
        repository.log_activity(conn, decision_type=dtype, mode=mode,
                                action_taken="rejected via telegram", executed=False)
        telegram.notify(conn, kind="info", decision_type=dtype, mode=mode,
                        summary="Rejected — no change made.")
        telegram.answer_callback_query(cq["id"], text="Rejected", session=session)
        return

    # 4. confirm: deadline guard
    now = now or datetime.now(timezone.utc)
    gw_row = conn.execute("SELECT deadline_utc FROM gameweeks WHERE id=?", (row["gw"],)).fetchone()
    if gw_row and gw_row["deadline_utc"] and now > datetime.fromisoformat(gw_row["deadline_utc"]):
        repository.set_pending_status(conn, pid, "expired")
        telegram.answer_callback_query(cq["id"], text="Deadline passed", session=session)
        return

    # 5. re-run + verify
    entry, available = _recompute_entry(conn, dtype, ranker=ranker, suggester=suggester)
    if not available:
        repository.set_pending_status(conn, pid, "superseded")
        telegram.answer_callback_query(cq["id"], text="No current recommendation", session=session)
        return
    if entry["identity"] != json.loads(row["identity_json"]):
        repository.set_pending_status(conn, pid, "superseded")
        send_pending(conn, entry, gw=row["gw"], mode=mode)
        telegram.answer_callback_query(cq["id"], text="Recommendation changed — see new message", session=session)
        return

    # 6. match -> execute via the existing bounded executor
    try:
        if dtype == "lineup":
            result = lineup_fn(conn, key, live=True, confirm_fn=lambda d: True, session=session)
        else:
            result = transfer_fn(conn, key, rank=1, live=True, confirm_fn=lambda d: True, session=session)
    except SessionExpired:
        repository.set_pending_status(conn, pid, "failed")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary="FPL session expired — re-run init-fpl. No changes were made.")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    except Exception as e:  # executor error — never crash the poller
        repository.set_pending_status(conn, pid, "failed")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary=f"Execution failed: {e}")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    if not getattr(result, "ok", False):
        repository.set_pending_status(conn, pid, "failed")
        telegram.notify(conn, kind="alert", decision_type=dtype, mode=mode,
                        summary="Execution did not complete.")
        telegram.answer_callback_query(cq["id"], text="Execution failed", session=session)
        return
    repository.set_pending_status(conn, pid, "confirmed")
    telegram.notify(conn, kind="executed", decision_type=dtype, mode=mode,
                    summary=entry["confirmed_summary"])
    telegram.answer_callback_query(cq["id"], text="Confirmed", session=session)
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/telegram_interactive.py tests/test_telegram_interactive.py
git commit -m "feat: telegram_interactive.handle_callback (verify/execute/supersede) (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `telegram_interactive.poll_once`

**Files:**
- Modify: `src/interface/telegram_interactive.py`
- Test: `tests/test_telegram_interactive.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_telegram_interactive.py`:

```python
def test_poll_once_noop_when_disabled(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    called = []
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: called.append(1) or [])
    ti.poll_once(b"key", conn=db)
    assert called == []


def test_poll_once_dispatches_and_advances_offset(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    updates = [{"update_id": 10, "callback_query": {"id": "a", "data": "r:1"}},
               {"update_id": 11, "callback_query": {"id": "b", "data": "r:2"}}]
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: updates)
    seen = []
    monkeypatch.setattr(ti, "handle_callback", lambda conn, key, cq, **k: seen.append(cq["id"]))
    ti.poll_once(b"key", conn=db)
    assert seen == ["a", "b"]
    assert repository.get_telegram_state(db, "update_offset") == "12"  # last update_id + 1


def test_poll_once_passes_stored_offset(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    repository.set_telegram_state(db, "update_offset", "5")
    seen_offset = []
    monkeypatch.setattr(telegram, "get_updates", lambda offset, **k: seen_offset.append(offset) or [])
    ti.poll_once(b"key", conn=db)
    assert seen_offset == [5]
```

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → `AttributeError: ... has no attribute 'poll_once'`.

- [ ] **Step 3: Implement.** Append to `src/interface/telegram_interactive.py`:

```python
def poll_once(key, *, conn=None, session=None):
    if not is_enabled():
        return
    owns = conn is None
    conn = conn or connect(db_path())
    init_db(conn)
    try:
        offset = repository.get_telegram_state(conn, "update_offset")
        offset = int(offset) if offset is not None else None
        for u in telegram.get_updates(offset, session=session):
            cq = u.get("callback_query")
            if cq:
                handle_callback(conn, key, cq, session=session)
            repository.set_telegram_state(conn, "update_offset", str(u["update_id"] + 1))
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_telegram_interactive.py -q` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add src/interface/telegram_interactive.py tests/test_telegram_interactive.py
git commit -m "feat: telegram_interactive.poll_once (offset-tracked getUpdates) (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Scheduler + config wiring

**Files:**
- Modify: `src/scheduler.py` (`_maybe_load_key`, `build_scheduler`, `auto_execute_job`)
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_scheduler.py`:

```python
def test_maybe_load_key_loads_when_interactive(monkeypatch):
    monkeypatch.setattr(scheduler.config, "unattended_enabled", lambda *a, **k: False)
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: True)
    import src.auth.master as master
    monkeypatch.setattr(master, "get_master_key", lambda: b"k")
    assert scheduler._maybe_load_key() == b"k"


def test_build_scheduler_registers_telegram_poll_when_interactive(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: True)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "telegram_poll" in {j.id for j in sched.get_jobs()}


def test_build_scheduler_no_telegram_poll_when_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    monkeypatch.setattr(scheduler.config, "telegram_interactive_enabled", lambda *a, **k: False)
    sched = scheduler.build_scheduler(BackgroundScheduler(timezone="UTC"), key=b"x")
    assert "telegram_poll" not in {j.id for j in sched.get_jobs()}


def test_auto_execute_uses_interactive_notify_when_enabled(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    from src.interface import telegram_interactive as ti
    monkeypatch.setattr(ti, "is_enabled", lambda cfg=None: True)
    got = {}
    monkeypatch.setattr(ti, "notify_plan", lambda conn, plan, **k: got.update(k, n=len(plan)))
    plan = [{"decision": "captain", "route": "notify", "confidence": 50,
             "summary": "Captain pending: X", "executed": False,
             "identity": {"captain_id": 5, "vice_id": 6}}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=_CFG)
    assert got["n"] == 1 and got["gw"] == 1 and got["mode"] == "manual"
```

(`_CFG`/`_NOW`/`_seed_gw` already exist in `tests/test_scheduler.py` from 2.3c. `_CFG` has no `mode` key, so `config.mode(_CFG)` returns `"manual"`. The `_seed_gw` there inserts a `gameweeks` row with `id=1`, so `got["gw"]==1`.)

- [ ] **Step 2: Run, expect FAIL.** `.venv/bin/pytest tests/test_scheduler.py -q` → the four new tests fail (`_maybe_load_key` doesn't consult interactive yet; `telegram_poll` not registered; `auto_execute_job` doesn't branch to the interactive notifier).

- [ ] **Step 3: Implement.** In `src/scheduler.py`:

(a) Replace `_maybe_load_key`:
```python
def _maybe_load_key():
    if not (config.unattended_enabled() or config.telegram_interactive_enabled()):
        return None
    from .auth import master
    return master.get_master_key()
```

(b) In `build_scheduler`, after the existing `auto_execute` block (just before `return scheduler`), add:
```python
    if key is not None and config.telegram_interactive_enabled():
        from .interface import telegram_interactive
        scheduler.add_job(lambda: telegram_interactive.poll_once(key),
                          CronTrigger(second="*/20"), id="telegram_poll", replace_existing=True)
```

(c) In `auto_execute_job`, replace the post-execution notify block:
```python
        try:
            telegram.notify_plan(conn, plan, mode=mode)
        except Exception:
            log.exception("telegram notify_plan failed after execution")
```
with:
```python
        try:
            from .interface import telegram_interactive
            if telegram_interactive.is_enabled(cfg):
                telegram_interactive.notify_plan(conn, plan, gw=row["id"], mode=mode)
            else:
                telegram.notify_plan(conn, plan, mode=mode)
        except Exception:
            log.exception("telegram notify_plan failed after execution")
```

- [ ] **Step 4: Run, expect PASS.** `.venv/bin/pytest tests/test_scheduler.py -q` → all pass (the existing 2.4a scheduler tests still pass: with interactive disabled by default they take the `telegram.notify_plan` branch unchanged).

- [ ] **Step 5: Commit.**
```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: wire telegram interactive poll job + notify routing into scheduler (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Config default + README + full-suite verification

**Files:**
- Modify: `config.yaml`
- Modify: `README.md`

- [ ] **Step 1: Add the config default.** In `config.yaml`, add a top-level block (alongside the existing `unattended:` block):
```yaml
telegram:
  interactive: false
```

- [ ] **Step 2: Document in README.** In `README.md`, under the existing "Telegram notifications" section added in 2.4a, append:
```markdown
**Interactive confirm (optional, Phase 2.4b):** set `telegram.interactive: true` in `config.yaml`
to receive Confirm/Reject buttons on pending decisions and act with one tap. This requires running
the daemon (`serve`/scheduler) with the master password loaded (the daemon executes the confirmed
decision). Off by default.
```

- [ ] **Step 3: Full-suite verification.** Run `.venv/bin/pytest -q`. Expected: all prior 238 tests plus the new 2.4b tests pass (~262 total), zero failures, no network access (no live execution; interactive off by default in the test env).

- [ ] **Step 4: Secret-leak check.** Run `grep -rn "log\|print" src/interface/telegram_interactive.py` and confirm there are NO logging/print calls that include the token, chat id, or a token-bearing URL (the module reaches the network only via `telegram.*`, which is already B7-clean).

- [ ] **Step 5: Commit.**
```bash
git add config.yaml README.md
git commit -m "docs: config default + README for Telegram interactive confirm (2.4b)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done (CLAUDE.md B14)

- [ ] `pending_decisions` + `telegram_state` tables and repo helpers exist and round-trip.
- [ ] Router plan entries carry `identity`; existing router tests still green.
- [ ] `telegram.get_updates`/`answer_callback_query` are B7-clean transport (no-op when unconfigured, never raise).
- [ ] `telegram_interactive` sends buttoned pings (`c:<id>`/`r:<id>`), polls with a durable offset, and `handle_callback` covers: wrong-chat ignore, idempotent already-resolved, reject, confirm+match→execute+confirmed, confirm+changed→supersede+re-notify, past-deadline→expired, execution failure→failed+alert.
- [ ] Scheduler registers `telegram_poll` only when key present + interactive enabled; `_maybe_load_key` loads on interactive; `auto_execute_job` routes pending entries through the interactive notifier when enabled (and the unchanged 2.4a path otherwise).
- [ ] `config.yaml`/README updated; full `pytest -q` green; no token/chat ever logged; no `decision-engine.md` change; the agent never ran the live poller or live execution.
- [ ] Manual smoke check (out of band, by the user): enable interactive, run `serve`, trigger a pending decision in-window, tap Confirm on the phone, verify execution + the `pending_decisions`/`activity_log` rows.
