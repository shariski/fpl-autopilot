# Telegram Outbound Notifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an outbound Telegram notifier (Phase 2.4a) that tells the user what the autopilot did, what's pending, and when auth failed — wired into the unattended scheduler path, no-op when unconfigured.

**Architecture:** A self-contained `src/interface/telegram.py` (transport + formatting + `notify`/`notify_plan`) is consumed *only* by Interface-layer callers (the scheduler), keeping the router pure (CLAUDE.md B2). Config is env-vars (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`), decoupled from the master key; the channel is a silent no-op when unset, so the existing 212 tests stay network-silent. Failure-to-send is logged via the existing `repository.log_activity` (B9/B10), never raised.

**Tech Stack:** Python 3.11, `requests` (already used in `src/auth/session.py`, `src/scheduler.py`), `pytest` + `monkeypatch`, sqlite (`activity_log` table), APScheduler (existing).

**Branch:** `feat/telegram-notifier` (already created; the spec is committed there).

**Spec:** `docs/superpowers/specs/2026-05-23-telegram-notifier-design.md`

**Conventions to honor (from CLAUDE.md / HANDOFF):**
- TDD: test first, watch it fail, minimal impl, watch it pass, commit.
- **Never `git add -A`** — stage explicit paths only.
- The agent never sends live / sets the real env vars (R3); all tests use `monkeypatch` + fake sessions.
- No `decision-engine.md` change (this slice touches no decision logic).

---

### Task 1: `telegram.is_configured()` + module skeleton

**Files:**
- Create: `src/interface/telegram.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_telegram.py`:

```python
from src.interface import telegram


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.is_configured() is False


def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "tok")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "123")
    assert telegram.is_configured() is True


def test_is_configured_false_when_only_token(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "tok")
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.is_configured() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.interface.telegram'`

- [ ] **Step 3: Write minimal implementation**

Create `src/interface/telegram.py`:

```python
import os

BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
API_BASE = "https://api.telegram.org"
TIMEOUT = 10


def is_configured():
    """True only when both the bot token and chat id env vars are set and non-empty."""
    return bool(os.getenv(BOT_TOKEN_ENV)) and bool(os.getenv(CHAT_ID_ENV))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "feat: telegram.is_configured + module skeleton (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `telegram.send_message()` transport

**Files:**
- Modify: `src/interface/telegram.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def json(self):
        return self._payload


class _FakeSession:
    """Mimics requests.Session.post (json=, timeout=)."""
    def __init__(self, resp=None, boom=False):
        self._resp = resp or _Resp()
        self._boom = boom
        self.posted = None

    def post(self, url, json=None, timeout=None):
        if self._boom:
            import requests
            raise requests.RequestException("boom")
        self.posted = {"url": url, "json": json, "timeout": timeout}
        return self._resp


def _configure(monkeypatch, token="TOK", chat="42"):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, token)
    monkeypatch.setenv(telegram.CHAT_ID_ENV, chat)


def test_send_message_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    sess = _FakeSession()
    assert telegram.send_message("hi", session=sess) is False
    assert sess.posted is None


def test_send_message_posts_and_returns_true(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    assert telegram.send_message("hello", session=sess) is True
    assert sess.posted["url"] == "https://api.telegram.org/botTOK/sendMessage"
    assert sess.posted["json"] == {"chat_id": "42", "text": "hello"}


def test_send_message_includes_buttons(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    btns = [[{"text": "Yes", "callback_data": "y"}]]
    telegram.send_message("q", buttons=btns, session=sess)
    assert sess.posted["json"]["reply_markup"] == {"inline_keyboard": btns}


def test_send_message_false_on_non_200(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(500, {}))) is False


def test_send_message_false_on_ok_false(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(200, {"ok": False}))) is False


def test_send_message_false_on_network_error(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(boom=True)) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: FAIL — `AttributeError: module 'src.interface.telegram' has no attribute 'send_message'`

- [ ] **Step 3: Write minimal implementation**

In `src/interface/telegram.py`, add `import requests` under `import os`, then append:

```python
def send_message(text, *, buttons=None, session=None):
    """Pure transport. No-op (return False) if the channel is unconfigured.
    Returns True only on HTTP 200 + JSON {"ok": true}. Catches all network/HTTP
    errors and returns False. Never raises. Never logs the token/chat/URL (B7)."""
    if not is_configured():
        return False
    token = os.getenv(BOT_TOKEN_ENV)
    chat_id = os.getenv(CHAT_ID_ENV)
    payload = {"chat_id": chat_id, "text": text}
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    session = session or requests.Session()
    try:
        resp = session.post(f"{API_BASE}/bot{token}/sendMessage", json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    try:
        return bool((resp.json() or {}).get("ok"))
    except ValueError:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "feat: telegram.send_message transport (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `telegram._format()` + `telegram.notify()`

**Files:**
- Modify: `src/interface/telegram.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_format_executed():
    assert telegram._format("executed", "Captain: X") == "✅ Executed\nCaptain: X"


def test_format_info_has_review_suffix():
    out = telegram._format("info", "Captain pending: X")
    assert out == "📊 Decision pending\nCaptain pending: X\nReview before the deadline."


def test_format_alert():
    assert telegram._format("alert", "session expired").startswith("❌ Autopilot blocked")


def test_notify_noop_unconfigured_no_log(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(1) or True)
    assert telegram.notify(db, kind="info", decision_type="captain", mode="manual", summary="s") is False
    assert sent == []
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_notify_success_no_failure_log(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    assert telegram.notify(db, kind="executed", decision_type="captain", mode="auto",
                           summary="Captain: X") is True
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_notify_failure_logs_one_row_without_token(db, monkeypatch):
    _configure(monkeypatch, token="SECRET_TOKEN")
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: False)
    assert telegram.notify(db, kind="info", decision_type="transfer", mode="hybrid",
                           summary="OUT A IN B") is False
    rows = db.execute(
        "SELECT decision_type, action_taken, inputs_json, executed FROM activity_log").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["decision_type"] == "notification"
    assert r["executed"] == 0
    assert "SECRET_TOKEN" not in (r["action_taken"] + (r["inputs_json"] or ""))
```

Note: `db` is the in-memory fixture from `tests/conftest.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: FAIL — `AttributeError: module 'src.interface.telegram' has no attribute '_format'`

- [ ] **Step 3: Write minimal implementation**

In `src/interface/telegram.py`, add `from src.data import repository` to the imports (below `import requests`), then append:

```python
_ICONS = {
    "executed": "✅ Executed",
    "info": "📊 Decision pending",
    "alert": "❌ Autopilot blocked",
}


def _format(kind, summary):
    """B9 copy: functional icon + header + caller-built summary (action/reason/impact)."""
    header = _ICONS.get(kind, _ICONS["info"])
    if kind == "info":
        return f"{header}\n{summary}\nReview before the deadline."
    return f"{header}\n{summary}"


def notify(conn, *, kind, decision_type, mode, summary, session=None):
    """Send one B9 notification. Silent no-op (no send, no log) when unconfigured.
    On a send failure while configured, log ONE activity row (B9/B10) and return
    False. Never raises."""
    if not is_configured():
        return False
    ok = send_message(_format(kind, summary), session=session)
    if not ok:
        repository.log_activity(
            conn, decision_type="notification", mode=mode,
            action_taken=f"telegram send failed ({decision_type}/{kind})",
            inputs={"kind": kind, "summary": summary, "decision_type": decision_type},
            executed=False)
    return ok
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: PASS (15 passed)

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "feat: telegram._format + notify with failure logging (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `telegram.notify_plan()`

**Files:**
- Modify: `src/interface/telegram.py`
- Test: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_notify_plan_noop_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    calls = []
    monkeypatch.setattr(telegram, "notify", lambda *a, **k: calls.append(k))
    telegram.notify_plan(None, [{"decision": "captain", "executed": True, "summary": "x"}], mode="auto")
    assert calls == []


def test_notify_plan_maps_kinds(monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: calls.append(k))
    plan = [{"decision": "captain", "executed": True, "summary": "Cap: X"},
            {"decision": "transfer", "executed": False, "summary": "OUT A IN B"}]
    telegram.notify_plan("CONN", plan, mode="hybrid")
    assert [c["kind"] for c in calls] == ["executed", "info"]
    assert [c["decision_type"] for c in calls] == ["captain", "transfer"]
    assert [c["summary"] for c in calls] == ["Cap: X", "OUT A IN B"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: FAIL — `AttributeError: module 'src.interface.telegram' has no attribute 'notify_plan'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/interface/telegram.py`:

```python
def notify_plan(conn, plan, *, mode, session=None):
    """Best-effort: notify per plan entry (executed -> confirmation, else pending info).
    Early-returns when unconfigured so callers with minimal plan dicts never touch
    summary/executed keys (keeps the existing scheduler/router tests untouched)."""
    if not is_configured():
        return
    for entry in plan:
        kind = "executed" if entry["executed"] else "info"
        notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
               summary=entry["summary"], session=session)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_telegram.py -q`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "feat: telegram.notify_plan (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Router `plan` enrichment (`summary` + `executed`)

**Files:**
- Modify: `src/execution/router.py` (the `route_gameweek` function, lines 33-63)
- Test: `tests/test_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_router.py` (reuses the existing `_FakeSession`, `_current`, `_ranker`, `_suggester`, `db`):

```python
def test_route_gameweek_plan_has_summary_and_executed(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="auto",
                                 session=sess, ranker=_ranker(82), suggester=_suggester(80, 5.0))
    by = {p["decision"]: p for p in plan}
    assert by["captain"]["executed"] is True
    assert "Captain: Cap" in by["captain"]["summary"]
    assert by["transfer"]["executed"] is True
    assert "OUT O" in by["transfer"]["summary"] and "IN I" in by["transfer"]["summary"]


def test_route_gameweek_notify_entries_executed_false(db):
    sess = _FakeSession(_current())
    plan = router.route_gameweek(db, key=b"u", live=False, mode="manual",
                                 session=sess, ranker=_ranker(90), suggester=_suggester(90, 9.0))
    assert all(p["executed"] is False for p in plan)
    assert all("pending" in p["summary"].lower() for p in plan)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_router.py -q`
Expected: FAIL — `KeyError: 'executed'`

- [ ] **Step 3: Write minimal implementation**

Replace the body of `route_gameweek` in `src/execution/router.py` with (only the two `plan.append` blocks change — they gain `summary` + `executed`; the existing `log_activity` "pending" calls stay):

```python
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
                     "executed": r == "execute"})
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
                     "executed": r == "execute"})
        if r == "execute":
            transfer_exec.run_transfer(conn, key, rank=1, live=live, confirm_fn=_auto_approve,
                                       session=session, suggester=suggester)
        else:
            repository.log_activity(conn, decision_type="transfer", mode=mode,
                                    action_taken=f"pending: OUT {top['out']['web_name']} IN {top['in']['web_name']}",
                                    inputs={"confidence": top["confidence"], "suggestion": top},
                                    executed=False)
    return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_router.py -q`
Expected: PASS (all router tests, including the 2 new ones — the existing tests still pass since they only assert `decision`/`route`/`confidence`).

- [ ] **Step 5: Commit**

```bash
git add src/execution/router.py tests/test_router.py
git commit -m "feat: enrich route_gameweek plan with summary+executed (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Scheduler wiring — `notify_plan` + `SessionExpired` alert

**Files:**
- Modify: `src/scheduler.py` (the `auto_execute_job` function, lines 71-98)
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Add to the top imports of `tests/test_scheduler.py` (alongside the existing imports):

```python
import pytest
from src.interface import telegram as tg
```

Append to `tests/test_scheduler.py`:

```python
def test_auto_execute_notifies_plan(db, monkeypatch):
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(text) or True)
    plan = [{"decision": "captain", "route": "execute", "confidence": 80,
             "summary": "Captain: X", "executed": True},
            {"decision": "transfer", "route": "notify", "confidence": 50,
             "summary": "Transfer pending: OUT A IN B", "executed": False}]
    scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=lambda c, k: plan, cfg=_CFG)
    assert any(t.startswith("✅ Executed") for t in sent)
    assert any(t.startswith("📊 Decision pending") for t in sent)


def test_auto_execute_session_expired_alerts_and_raises(db, monkeypatch):
    from src.auth.session import SessionExpired
    _seed_gw(db, _NOW + timedelta(hours=1))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    sent = []
    monkeypatch.setattr(tg, "send_message", lambda text, **k: sent.append(text) or True)

    def boom(conn, key):
        raise SessionExpired("expired")

    with pytest.raises(SessionExpired):
        scheduler.auto_execute_job(b"key", conn=db, now=_NOW, route_fn=boom, cfg=_CFG)
    assert any(t.startswith("❌ Autopilot blocked") for t in sent)
    assert db.execute(
        "SELECT last_system_action_at FROM gameweeks WHERE id=1").fetchone()["last_system_action_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_scheduler.py -q`
Expected: FAIL — `test_auto_execute_notifies_plan` fails because no message is sent (`sent` is empty); `test_auto_execute_session_expired_alerts_and_raises` fails because `SessionExpired` propagates with no alert sent.

- [ ] **Step 3: Write minimal implementation**

Replace `auto_execute_job` in `src/scheduler.py` with (adds two local imports, a try/except around the route call, and a `notify_plan` after the mark):

```python
def auto_execute_job(key, *, conn=None, now=None, route_fn=None, cfg=None):
    from datetime import datetime, timezone, timedelta
    from .interface import telegram
    from .auth.session import SessionExpired
    cfg = cfg or load_config()
    if not config.unattended_enabled(cfg):
        return None
    hours = config.unattended_hours_before(cfg)
    owns = conn is None
    conn = conn or connect(db_path(cfg))
    init_db(conn)
    try:
        row = conn.execute(
            "SELECT id, deadline_utc, last_system_action_at FROM gameweeks WHERE is_next=1"
        ).fetchone()
        if not row or not row["deadline_utc"] or row["last_system_action_at"]:
            return None
        deadline = datetime.fromisoformat(row["deadline_utc"])
        now = now or datetime.now(timezone.utc)
        if not (now <= deadline <= now + timedelta(hours=hours)):
            return None
        try:
            plan = (route_fn or _default_route)(conn, key)
        except SessionExpired:
            telegram.notify(conn, kind="alert", decision_type="auth", mode=config.mode(cfg),
                            summary="FPL session expired — re-run init-fpl. No changes were made.")
            raise
        if any(p["route"] == "execute" for p in plan):
            conn.execute("UPDATE gameweeks SET last_system_action_at=? WHERE id=?",
                         (now.isoformat(), row["id"]))
            conn.commit()
        telegram.notify_plan(conn, plan, mode=config.mode(cfg))
        return plan
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scheduler.py -q`
Expected: PASS (all scheduler tests — the existing `test_auto_execute_*` still pass because their env vars are unset, so `notify_plan` early-returns and never touches their minimal plan dicts).

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: wire telegram notify_plan + auth alert into auto_execute_job (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Full-suite verification + README env-var docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — all prior 212 tests plus the new telegram/router/scheduler tests (~233 total), zero failures, no network access (no env vars set in the test environment).

- [ ] **Step 2: Confirm no secret leaks in logs (manual grep)**

Run: `grep -rn "BOT_TOKEN\|bot{token}\|reply_markup" src/interface/telegram.py`
Expected: the token only appears inside the `f"{API_BASE}/bot{token}/sendMessage"` URL construction (never in a `log`/`print`/`log_activity` call). Confirm there is no logging statement in `telegram.py` at all.

- [ ] **Step 3: Document the env vars in README**

In `README.md`, under the existing configuration/credentials section (search for `MASTER_PASSWORD` or `init-fpl` to find it), add:

```markdown
### Telegram notifications (optional, Phase 2.4a)

Set these env vars to receive outbound notifications (post-execution confirmations,
pending-decision pings, and auth-failure alerts) from the unattended scheduler:

- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your chat id

These are intentionally **not** encrypted under the master password so alerts work even
when the key isn't loaded. When unset, notifications are a silent no-op.
```

If no such section exists, add it as a new top-level `## Telegram notifications` section near the configuration docs.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document Telegram notifier env vars (2.4a)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done (CLAUDE.md B14)

- [ ] `src/interface/telegram.py` exists with `is_configured`, `send_message`, `_format`, `notify`, `notify_plan`; never logs the token/chat/URL.
- [ ] `route_gameweek` plan entries carry `summary` + `executed`; existing router tests unchanged and green.
- [ ] `auto_execute_job` sends `✅ Executed` / `📊 Decision pending` per plan entry and `❌ Autopilot blocked` on `SessionExpired` (then re-raises); all best-effort, no-op when unconfigured.
- [ ] Failure-to-send (configured) writes exactly one `notification` row to `activity_log`, never raises.
- [ ] Full `pytest -q` green; no `decision-engine.md` change; the agent never sent live.
- [ ] README documents the two env vars.
- [ ] Manual smoke check (out of band, by the user): export the env vars, trigger an unattended run in-window, confirm the message arrives and `activity_log` records the action.
