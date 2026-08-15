# Agent-Operable CLI Contract + Context Planting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FPL Autopilot agent-operable: a documented CLI contract (`fpl-autopilot <cmd> --json`) any AI agent can drive over SSH, with `status`/`resume`/`log` for one-shot boot + session continuity, R3 hardening (`--live` refuses non-TTY), and repo-root `AGENTS.md` + a superpowers skill so any agent family auto-loads the B-rules and operating notes.

**Architecture:** All changes live in `src/cli.py` (envelope helpers, new subcommands, `--json` wrappers, TTY gate) plus docs. Read commands reuse the existing read-model (`src/interface/queries.py`, decision engines, AI runners — cache-first, no new layers). `refresh` gains a `report=True` return path. Transport stays as-is: `docker compose run --rm -T app <cmd> --json` on jumbo.

**Tech Stack:** Python 3.11+, argparse, sqlite3, pytest. Venv: `.venv/bin/` (`.venv/bin/pytest -q`, `.venv/bin/fpl-autopilot`); frontend unchanged (`cd frontend && npm test`).

## Global Constraints

- **Doc-first (B13):** `docs/agent-contract.md` written in Task 1 defines the exact JSON shapes; Tasks 2–7 implement to match. If code and doc disagree, the doc is truth until explicitly changed.
- **R3:** agent sessions never run `--live`; the agent contract is read + refresh only. `--live` refuses non-TTY stdin.
- **B7:** no credential material (email, tokens, cookies, master key, refresh/access tokens) ever appears in any `--json` output. `auth` blocks report state + expiry timestamps only.
- **B11:** every `--json` command has a deterministic test with frozen inputs; tests never make network calls (monkeypatch providers/runners; use `tests/fixtures/*.json`).
- **Baseline:** full suite `693 passed` (`.venv/bin/pytest -q`) + frontend `71 passed` (`cd frontend && npm test`). **Never commit without the full suite green.**
- **Commits:** conventional style (`feat(cli):`, `docs(agent):`, `test(cli):`), explicit paths — **never `git add -A`**.
- **Branch:** work on `feat/agent-contract`; merge to main locally at the end (Task 9). CI auto-deploys to jumbo on push to main.
- Envelope contract (defined here, doc-first):
  - Success: `{"ok": true, "contract_version": "1", "command": "<cmd>", "generated_at_utc": "<iso>", "data": {...}}`
  - Error: `{"ok": false, "contract_version": "1", "command": "<cmd>", "generated_at_utc": "<iso>", "error": {"code": "E_*", "message": "...", "hint": "..."}}`
  - Exit codes: 0 ok · 1 runtime/data error (`E_RUNTIME`, `E_NO_DATA`) · 2 usage error (`E_USAGE`).
  - `data_basis` block in every decision payload: `{"as_of_utc": "<max cache_meta ts or null>", "xp_model_version": "<config xp_model.version, default v1>"}`.

---

### Task 1: Contract docs — agent-contract.md, AGENTS.md, CLAUDE.md pointer, runbook section

**Files:**
- Create: `docs/agent-contract.md`
- Create: `AGENTS.md` (repo root)
- Modify: `CLAUDE.md` (replace Part B with pointer)
- Modify: `docs/runbook.md` (append "Agent operating notes" section)

**Interfaces:**
- Consumes: nothing.
- Produces: the contract truth that Tasks 2–7 implement — envelope, exit codes, `data_basis`, command shapes below.

- [ ] **Step 1: Create `docs/agent-contract.md`**

```markdown
# FPL Autopilot — Agent Contract (v1)

The documented surface any AI agent can operate. The agent never touches the host, the
DB, or the container — it speaks only `fpl-autopilot <cmd> --json`. Human users keep the
text commands, the dashboard, and Telegram.

**Transport:** on jumbo, run one-shot commands in the deployed container:

    ssh jumbo 'docker compose run --rm -T app <cmd> --json'

(`-T` disables pty allocation so read commands are deterministic over non-interactive
SSH. Locally, run `.venv/bin/fpl-autopilot <cmd> --json` against the dev DB.)

## Contract rules (binding, enforced in code)

1. **R3 — the agent never passes `--live`.** The contract covers read commands + `refresh`
   only. All FPL writes (`execute-*`, `apply-squad`, `route-gameweek`, `undo-transfer`,
   `freeze`, `unfreeze`, `refresh-my-team`, `init-*`) are human-only. `--live` additionally
   refuses to run when stdin is not a TTY, so a non-interactive agent session physically
   cannot pass it.
2. **Dry-run is the default.** Every contract command is read-only or writes only to the
   local DB (`refresh`). Nothing in this contract writes to FPL.
3. **No secrets.** No contract output ever contains credentials, cookies, or tokens.
   `auth` reports state and expiry timestamps only.
4. **One JSON object per invocation on stdout.** Nothing else on stdout (progress lines,
   warnings, and log output are suppressed in `--json` mode).

## Envelope

Success:

    {"ok": true, "contract_version": "1", "command": "captain",
     "generated_at_utc": "2026-08-15T12:00:00Z",
     "data": { ... }}

Error:

    {"ok": false, "contract_version": "1", "command": "squad",
     "generated_at_utc": "2026-08-15T12:00:00Z",
     "error": {"code": "E_NO_DATA", "message": "no upcoming gameweek with xP data",
               "hint": "run refresh --json first"}}

Exit codes (CLI): `0` ok · `1` runtime/data error · `2` usage error.

Error codes: `E_USAGE` (bad arguments) · `E_RUNTIME` (provider/gate/internal failure) ·
`E_NO_DATA` (required data absent).

## data_basis

Every decision payload carries `data_basis` so the agent can judge freshness (B5):

    "data_basis": {"as_of_utc": "2026-08-15T11:00:00Z", "xp_model_version": "v1"}

## Commands

### status

One-shot bootstrap: mode, frozen?, auth state, data freshness, next GW + deadline,
pending decisions, last system actions, health.

    fpl-autopilot status --json

Data shape:

    {
      "mode": "manual",
      "frozen": {"is_frozen": false} | {"is_frozen": true, "since": "...", "source": "user", "reason": "..."},
      "auth": null | {"state": "active"|"expired", "access_token_expires_at": "...",
                      "session_last_refreshed": "...", "relogin_failures": 0},
      "data_freshness": {"bootstrap-static": "...", "fixtures": "...",
                         "my_team": "...", "understat": "..."},
      "current_gameweek": null | {"id": 1, "deadline_utc": "..."},
      "next_gameweek": null | {"id": 2, "deadline_utc": "...", "state": "PENDING",
                               "hours_until_deadline": 96.5},
      "pending_decisions": [{"decision_type": "captain", "summary": "...", "created_at": "..."}],
      "last_system_actions": [{"ts_utc": "...", "gw": 1, "mode": "manual",
                               "decision_type": "squad", "action_taken": "...", "executed": true}],
      "health": {"db_ok": true, "players": 587, "teams": 20},
      "data_basis": { ... }
    }

### resume

Session continuity: everything in `status` plus the activity tail and the operating rules
the agent must obey. **The first call of every agent session.**

    fpl-autopilot resume --json [--tail N]

Adds to the status shape:

    "activity": {"entries": [{"ts_utc": "...", "gw": 1, "mode": "manual",
                  "decision_type": "transfer", "action_taken": "...",
                  "executed": true, "outcome": null | { ... }}]},
    "operating_rules": {
      "agent_never_live": "Agent sessions must never pass --live. All FPL writes are human-only (R3); --live refuses non-TTY stdin.",
      "dry_run_default": "Every contract command is read-only or local-DB-only; nothing writes to FPL.",
      "boot_ritual": ["resume --json — boot context",
                      "refresh --json — pull latest data when stale",
                      "captain/transfers/chips/squad --json — decision inputs",
                      "insight <player_id> --json / speculate --json — player analysis",
                      "propose a plan; the human executes writes (--live) via the CLI"],
      "agent_safe_commands": ["status", "resume", "log", "captain", "transfers", "chips",
                              "squad", "insight", "speculate", "refresh",
                              "freeze-status", "auth-status", "review"],
      "human_only_commands": ["execute-lineup", "execute-transfer", "apply-squad",
                              "route-gameweek", "undo-transfer", "refresh-my-team",
                              "init-master-password", "init-fpl", "freeze", "unfreeze"]
    }

### log

Filterable activity tail (runbook appendix already documents these flags):

    fpl-autopilot log --json [--tail N] [--gw N] [--mode M] [--decision-type T]

Data shape:

    {"entries": [{"ts_utc": "...", "gw": 1, "mode": "manual", "decision_type": "transfer",
                  "action_taken": "...", "executed": true,
                  "outcome": null | { ... }}]}

### captain / transfers / chips

Decision-grade outputs, shapes documented in `docs/decision-engine.md`, each enriched with
`reasoning`/`reasoning_source` on the top pick (same read-model as the dashboard):

    fpl-autopilot captain --json        data: {"picks": [...], "vice_player_id": ..., "confidence": ..., "data_basis": {...}}
    fpl-autopilot transfers --json      data: {"suggestions": [...], "empty_reason": ..., "free_transfers": ..., "data_basis": {...}}
    fpl-autopilot chips --json          data: {"recommendation": {...} | null, "data_basis": {...}}

### squad / squad --candidates

    fpl-autopilot squad --json [--candidates]

- Without `--candidates`: the AI-built squad (cache-first; generates on miss — can take
  ~30s on first run). Data shape mirrors `GET /api/squad/builder`:
  `{"status": "cached"|"generated", "gw": N, "source": "ai"|"optimizer",
    "picks": [{"player_id", "web_name", "team", "position", "price", "xp_6gw", "slot", "reason"}],
    "template_rationale": "...", "risks": [...], "budget_used": 99.9,
    "speculation": {"spikes": [...], "drops": [...], "differentials": [...]} | null,
    "model_id": "...", "generated_at": "...", "data_basis": {...}}`
- With `--candidates`: the deterministic candidate pool:
  `{"gw": N, "count": 96, "pool": [candidate dicts], "data_basis": {...}}` (candidate
  fields: player_id, web_name, team_short, position, price, status, xp_next, xp_6gw,
  value, ownership_pct, form, transfers_in, transfers_out, net_momentum).

### insight

    fpl-autopilot insight <player_id> --json

Cache-first player deep-dive; generates on miss. Data shape mirrors
`GET /api/players/{id}/insight`: `{"status": "cached"|"generated", "player_id": N, "gw": N,
  "player": {"name", "web_name", "position", "team", "price"} | null,
  "insights": [...], "summary": "...", "data_limits": [...],
  "model_id": "...", "generated_at": "...", "data_basis": {...}}`
Unknown player → `E_NO_DATA`.

### speculate

    fpl-autopilot speculate --json

The AI speculation layer alone (spike/drop signals; cache-first, generates on miss).
`differentials` = spike players not already in the saved squad (my_team snapshot):

    data: {"gw": N, "signals": {"spikes": [...], "drops": [...], "market_read": "..."},
           "differentials": [...], "data_basis": {...}}

Failure (provider error or gate rejected) → `E_RUNTIME`.

### refresh

    fpl-autopilot refresh --json [--full] [--source fpl|understat]

Pulls FPL/Understat and reports what changed (suppresses progress prints in `--json`):

    data: {"fpl": {"bootstrap_static": {"players": 587, "teams": 20} | null,
                   "fixtures": 380 | null, "my_team": {"gw": 1, "picks": 15} | null,
                   "my_team_skipped": 1 | null},
           "understat": {"total": 500, "matched": 480, "unmatched": 20,
                         "unmapped_teams": 2} | null,
           "rematch": 0, "cleanup": {"gw_stats": 0, "my_team": 0},
           "warnings": [...], "data_basis": {...}}

### freeze-status / auth-status

    fpl-autopilot freeze-status --json
    data: {"frozen": {"is_frozen": false} | {"is_frozen": true, "since", "source", "reason"}}

    fpl-autopilot auth-status --json
    data: {"auth": null | {"state", "access_token_expires_at",
                           "session_last_refreshed", "relogin_failures"}}

## Operating loop (the agent's canonical flow)

1. `resume --json` — boot context (state, freshness, deadlines, pending decisions, rules).
2. `refresh --json` — pull latest data when `data_basis.as_of_utc` is stale.
3. `captain/transfers/chips/squad --json` — decision-grade inputs.
4. `insight <id> --json`, `speculate --json` — player-level analysis.
5. Reason (agent's own tools: web search, etc.) and propose a plan to the human.
6. The **human** executes writes via the CLI (`--live`) or the dashboard. The agent never
   runs `--live` (R3).
7. `log --json` — post-execution audit trail (B10).

## MCP server — deferred (decision 2026-08-15)

Not built. The CLI+SSH contract already gives any shell-capable agent full read access;
a REST/MCP layer would add tokens, a server process, and maintenance for no contract gain.
Revisit when: (a) a client needs tool-call access and cannot shell/SSH; (b) tool-call
ergonomics become painful; (c) a dashboard-side consumer needs the same surface.

## Human-only commands (never part of the agent contract)

`execute-lineup`, `execute-transfer`, `apply-squad`, `route-gameweek`, `undo-transfer`,
`refresh-my-team`, `init-master-password`, `init-fpl`, `freeze`, `unfreeze`.
```

- [ ] **Step 2: Create `AGENTS.md` (repo root)**

```markdown
# FPL Autopilot — Agent Operating Contract

Single-user Fantasy Premier League assistant: data layer → analytics → decision engine →
interface. Phase 1+2 complete and live; the AI layer (Phase 3) is partially built.
This file auto-loads for every agent family (Claude/Codex/Copilot/Gemini). It is the
working contract — the B-rules below are binding, the operating notes tell you how to
drive the tool.

**Agent contract (commands, JSON shapes, R3): `docs/agent-contract.md`.**
**Everything else: `docs/` (architecture.md, decision-engine.md, runbook.md, risks.md).**

## B-rules (binding)

Copy the following sections **verbatim** from `CLAUDE.md`, demoting every heading by one
level (`##` → `###`): from the line `# PART B — FPL Autopilot Project Rules` through the
end of the file (the `## Working pattern per task` section included). Then delete that
span from `CLAUDE.md` and replace it with the pointer below (Step 3). Also update the two
intro bullets in `CLAUDE.md` that say "This file merges two layers: Part A … Part B …"
so the Part B bullet reads "**Part B** — the project rules live in `AGENTS.md` (moved
2026-08-15 for single-source auto-load); read it."
```

- [ ] **Step 3: Modify `CLAUDE.md`**

Replace everything from `# PART B — FPL Autopilot Project Rules` to the end of the file
with:

```markdown
## PART B — FPL Autopilot Project Rules

Moved to `AGENTS.md` (repo root) on 2026-08-15 — single source of truth so every agent
family auto-loads the B-rules (B13). Read `AGENTS.md`; it is binding.
```

- [ ] **Step 4: Append the runbook section**

Append to `docs/runbook.md` (after the Tailscale appendix):

```markdown
## Agent operating notes

The tool is agent-operable via `fpl-autopilot <cmd> --json` (contract: `docs/agent-contract.md`).

**Boot a session:** `docker compose run --rm -T app resume --json` — state, freshness,
next GW + deadline, pending decisions, and the operating rules in one call.

**Read-safe (agents may run these):** `status`, `resume`, `log`, `captain`, `transfers`,
`chips`, `squad`, `squad --candidates`, `insight <player_id>`, `speculate`, `refresh`,
`freeze-status`, `auth-status`, `review` — always with `--json` where offered.

**Human-only (writes or secrets):** `execute-lineup`, `execute-transfer`, `apply-squad`,
`route-gameweek`, `undo-transfer`, `refresh-my-team`, `init-master-password`, `init-fpl`,
`freeze`, `unfreeze`. `--live` refuses non-TTY stdin, so an agent session can never pass it.

**Stale data:** run `docker compose run --rm -T app refresh --json` before judging;
`data_basis.as_of_utc` in every decision payload tells you how stale things are.

**Useful one-liners on jumbo:**

```bash
docker compose run --rm -T app status --json
docker compose run --rm -T app resume --json
docker compose run --rm -T app refresh --json
docker compose run --rm -T app captain --json
docker compose run --rm -T app transfers --json
docker compose run --rm -T app squad --json
docker compose run --rm -T app squad --candidates --json
docker compose run --rm -T app insight 234 --json
docker compose run --rm -T app speculate --json
docker compose run --rm -T app log --json --tail 20
```
```

- [ ] **Step 5: Verify the docs**

Review `docs/agent-contract.md` against the shapes defined in the Global Constraints
(envelope, exit codes, `data_basis`) — they must match exactly, since the code tasks
implement against this document.

- [ ] **Step 6: Commit**

```bash
git add docs/agent-contract.md AGENTS.md CLAUDE.md docs/runbook.md
git commit -m "docs(agent): agent contract, AGENTS.md (B-rules single source), runbook operating notes"
```

---

### Task 2: Envelope helpers + status / resume / log

**Files:**
- Modify: `src/cli.py` (imports, helpers after `NAME_RESOLUTION_PATH`, new `_cmd_*` functions before `main`, parsers + dispatch in `main`)
- Test: `tests/test_cli_agent.py` (create)

**Interfaces:**
- Consumes: `src.execution.override.status`, `src.data.repository.get_auth_state/get_access_expiry/get_relogin_failures`, `load_config`, `connect`, `init_db`.
- Produces: `_json_ok(command, data)`, `_json_err(command, code, message, hint=None, exit_code=1)` (raises `SystemExit`), `_data_basis(conn, cfg) -> dict`, `_status_data(conn, cfg, limit_actions=5) -> dict`, `_operating_rules() -> dict`, `_activity_entries(conn, limit, *, gw=None, mode=None, decision_type=None) -> list[dict]`, `_cmd_status_cli(conn=None, cfg=None, json_out=False)`, `_cmd_resume_cli(conn=None, cfg=None, tail=10, json_out=False)`, `_cmd_log_cli(conn=None, cfg=None, tail=10, gw=None, mode=None, decision_type=None, json_out=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_agent.py`:

```python
import json

import pytest

from src import cli
from src.data.db import connect, init_db


def _seed_status_data(db):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_current, is_next, state, finished) "
               "VALUES (1, '2026-08-21T17:30:00Z', 0, 1, 'PENDING', 0), "
               "(38, '2027-05-30T13:30:00Z', 1, 0, 'PENDING', 0)")
    db.execute("INSERT INTO cache_meta (resource, last_fetched_utc) VALUES "
               "('bootstrap-static', '2026-08-15T10:00:00Z'), "
               "('fixtures', '2026-08-15T10:00:00Z'), "
               "('my_team', '2026-08-15T10:00:00Z'), "
               "('understat', '2026-08-14T09:00:00Z')")
    db.execute("INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken, executed) "
               "VALUES ('2026-08-15T09:00:00Z', 1, 'manual', 'squad', 'built squad', 0), "
               "('2026-08-14T18:00:00Z', 1, 'manual', 'override', 'unfrozen (user)', 1)")
    db.execute("INSERT INTO pending_decisions (gw, decision_type, identity_json, summary, status, created_at) "
               "VALUES (1, 'captain', '{}', 'Captain pick needed', 'pending', '2026-08-15T08:00:00Z')")
    db.execute("INSERT INTO system_state (key, value) VALUES "
               "('freeze', '{\"since\": \"2026-08-15T07:00:00Z\", \"source\": \"user\", \"reason\": \"travel\"}')")
    db.execute("INSERT INTO credentials (id, auth_state, relogin_failures) VALUES (1, 'active', 0)")
    db.commit()


def _cfg():
    return {"mode": {"current": "manual"}, "xp_model": {"version": "v1"}}
```

- [ ] **Step 2: Run tests to verify they fail**

Write the test functions below, run `.venv/bin/pytest tests/test_cli_agent.py -q`, and confirm
failures are `AttributeError: module 'src.cli' has no attribute '_json_ok'` etc.

```python
def test_status_json_envelope(db, capsys):
    _seed_status_data(db)
    cli._cmd_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["contract_version"] == "1" and out["command"] == "status"
    data = out["data"]
    assert data["mode"] == "manual"
    assert data["frozen"] == {"is_frozen": True, "since": "2026-08-15T07:00:00Z",
                              "source": "user", "reason": "travel"}
    assert data["auth"]["state"] == "active" and data["auth"]["relogin_failures"] == 0
    assert data["data_freshness"]["bootstrap-static"] == "2026-08-15T10:00:00Z"
    assert data["data_freshness"]["understat"] == "2026-08-14T09:00:00Z"
    assert data["next_gameweek"]["id"] == 1
    assert data["next_gameweek"]["state"] == "PENDING"
    assert data["current_gameweek"]["id"] == 38
    assert len(data["pending_decisions"]) == 1
    assert len(data["last_system_actions"]) == 2
    assert data["health"] == {"db_ok": True, "players": 0, "teams": 0}
    assert data["data_basis"] == {"as_of_utc": "2026-08-15T10:00:00Z", "xp_model_version": "v1"}


def test_status_empty_db(db, capsys):
    cli._cmd_status_cli(conn=db, cfg=_cfg(), json_out=True)
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["frozen"] == {"is_frozen": False}
    assert data["auth"] is None
    assert data["next_gameweek"] is None
    assert data["pending_decisions"] == []
    assert data["data_freshness"] == {"bootstrap-static": None, "fixtures": None,
                                      "my_team": None, "understat": None}


def test_resume_includes_activity_and_rules(db, capsys):
    _seed_status_data(db)
    cli._cmd_resume_cli(conn=db, cfg=_cfg(), tail=10, json_out=True)
    data = json.loads(capsys.readouterr().out)["data"]
    assert [e["decision_type"] for e in data["activity"]["entries"]] == ["override", "squad"]
    assert data["activity"]["entries"][1]["executed"] is False
    rules = data["operating_rules"]
    assert "captain" in rules["agent_safe_commands"]
    assert "apply-squad" in rules["human_only_commands"]
    assert rules["boot_ritual"][0].startswith("resume")


def test_log_filters(db, capsys):
    db.execute("INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken, executed) VALUES "
               "('2026-08-15T09:00:00Z', 1, 'manual', 'squad', 'built squad', 0), "
               "('2026-08-14T18:00:00Z', 1, 'deadguard', 'transfer', 'auto sub', 1), "
               "('2026-08-14T08:00:00Z', 37, 'manual', 'transfer', 'free transfer', 1)")
    db.commit()
    cli._cmd_log_cli(conn=db, cfg=_cfg(), tail=10, gw=1, json_out=True)
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    assert len(entries) == 2
    cli._cmd_log_cli(conn=db, cfg=_cfg(), tail=10, mode="deadguard", json_out=True)
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    assert [e["decision_type"] for e in entries] == ["transfer"]
    cli._cmd_log_cli(conn=db, cfg=_cfg(), tail=10, decision_type="transfer", gw=37, json_out=True)
    entries = json.loads(capsys.readouterr().out)["data"]["entries"]
    assert len(entries) == 1 and entries[0]["action_taken"] == "free transfer"


def test_json_err_exit_code_and_shape(capsys):
    with pytest.raises(SystemExit) as exc:
        cli._json_err("squad", "E_NO_DATA", "no upcoming gameweek", "run refresh first")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["command"] == "squad"
    assert out["error"] == {"code": "E_NO_DATA", "message": "no upcoming gameweek",
                            "hint": "run refresh first"}


def test_status_text_mode(db, capsys):
    _seed_status_data(db)
    cli._cmd_status_cli(conn=db, cfg=_cfg(), json_out=False)
    text = capsys.readouterr().out
    assert "mode: manual" in text and "next GW: 1" in text and "frozen" in text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_agent.py -q`
Expected: FAIL — missing functions on `src.cli`.

- [ ] **Step 4: Implement the helpers and commands**

Add to `src/cli.py` imports: `import sys` and `from datetime import datetime, timezone`.

Insert after `NAME_RESOLUTION_PATH = ...`:

```python
def _print_json(payload):
    print(json.dumps(payload, default=str))


def _json_ok(command, data):
    _print_json({"ok": True, "contract_version": "1", "command": command,
                 "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                 "data": data})


def _json_err(command, code, message, hint=None, exit_code=1):
    error = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    _print_json({"ok": False, "contract_version": "1", "command": command,
                 "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                 "error": error})
    raise SystemExit(exit_code)


def _data_basis(conn, cfg):
    fresh = conn.execute("SELECT MAX(last_fetched_utc) AS m FROM cache_meta").fetchone()
    return {"as_of_utc": fresh["m"] if fresh else None,
            "xp_model_version": cfg.get("xp_model", {}).get("version", "v1")}
```

Insert the status/resume/log query functions after `_current_gw_from_db`:

```python
def _status_data(conn, cfg, limit_actions=5):
    from .data import repository
    from .execution import override
    cur = conn.execute("SELECT id, deadline_utc FROM gameweeks WHERE is_current=1").fetchone()
    nxt = conn.execute("SELECT id, deadline_utc, state FROM gameweeks WHERE is_next=1").fetchone()
    resources = ["bootstrap-static", "fixtures", "my_team", "understat"]
    freshness = {r: None for r in resources}
    for row in conn.execute(
            "SELECT resource, last_fetched_utc FROM cache_meta "
            "WHERE resource IN (%s)" % ",".join("?" * len(resources)), resources).fetchall():
        freshness[row["resource"]] = row["last_fetched_utc"]
    frozen = override.status(conn)
    auth_state = repository.get_auth_state(conn)
    now = datetime.now(timezone.utc)
    nxt_gw = None
    if nxt is not None:
        hours = None
        if nxt["deadline_utc"]:
            hours = round((datetime.fromisoformat(nxt["deadline_utc"]) - now).total_seconds() / 3600, 1)
        nxt_gw = {"id": nxt["id"], "deadline_utc": nxt["deadline_utc"],
                  "state": nxt["state"], "hours_until_deadline": hours}
    pending = [dict(r) for r in conn.execute(
        "SELECT decision_type, summary, created_at FROM pending_decisions "
        "WHERE status='pending' ORDER BY created_at")]
    actions = [dict(r) for r in conn.execute(
        "SELECT ts_utc, gw, mode, decision_type, action_taken, executed "
        "FROM activity_log ORDER BY id DESC LIMIT ?", (limit_actions,))]
    auth = None
    if auth_state is not None:
        row = conn.execute("SELECT session_last_refreshed FROM credentials WHERE id=1").fetchone()
        auth = {"state": auth_state,
                "access_token_expires_at": repository.get_access_expiry(conn),
                "session_last_refreshed": row["session_last_refreshed"] if row else None,
                "relogin_failures": repository.get_relogin_failures(conn)}
    n_players = conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    n_teams = conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
    return {
        "mode": cfg.get("mode", {}).get("current", "manual"),
        "frozen": ({"is_frozen": True, **frozen}) if frozen else {"is_frozen": False},
        "auth": auth,
        "data_freshness": freshness,
        "current_gameweek": {"id": cur["id"], "deadline_utc": cur["deadline_utc"]} if cur else None,
        "next_gameweek": nxt_gw,
        "pending_decisions": pending,
        "last_system_actions": actions,
        "health": {"db_ok": True, "players": n_players, "teams": n_teams},
        "data_basis": _data_basis(conn, cfg),
    }


def _operating_rules():
    return {
        "agent_never_live": "Agent sessions must never pass --live. All FPL writes are "
                            "human-only (R3); --live refuses non-TTY stdin.",
        "dry_run_default": "Every contract command is read-only or local-DB-only; "
                           "nothing writes to FPL.",
        "boot_ritual": ["resume --json — boot context",
                        "refresh --json — pull latest data when stale",
                        "captain/transfers/chips/squad --json — decision inputs",
                        "insight <player_id> --json / speculate --json — player analysis",
                        "propose a plan; the human executes writes (--live) via the CLI"],
        "agent_safe_commands": ["status", "resume", "log", "captain", "transfers", "chips",
                                "squad", "insight", "speculate", "refresh",
                                "freeze-status", "auth-status", "review"],
        "human_only_commands": ["execute-lineup", "execute-transfer", "apply-squad",
                                "route-gameweek", "undo-transfer", "refresh-my-team",
                                "init-master-password", "init-fpl", "freeze", "unfreeze"],
    }


def _activity_entries(conn, limit, *, gw=None, mode=None, decision_type=None):
    sql = ("SELECT ts_utc, gw, mode, decision_type, action_taken, executed, "
           "exec_outcome_json FROM activity_log")
    clauses, params = [], []
    if gw is not None:
        clauses.append("gw = ?")
        params.append(gw)
    if mode is not None:
        clauses.append("mode = ?")
        params.append(mode)
    if decision_type is not None:
        clauses.append("decision_type = ?")
        params.append(decision_type)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    entries = []
    for r in conn.execute(sql, params).fetchall():
        e = {"ts_utc": r["ts_utc"], "gw": r["gw"], "mode": r["mode"],
             "decision_type": r["decision_type"], "action_taken": r["action_taken"],
             "executed": bool(r["executed"])}
        outcome = r["exec_outcome_json"]
        if outcome is not None:
            try:
                e["outcome"] = json.loads(outcome)
            except ValueError:
                e["outcome"] = {"raw": outcome}
        entries.append(e)
    return entries
```

Insert the command handlers after `_auth_status_cli`:

```python
def _print_status_text(data):
    nxt = data["next_gameweek"]
    print(f"mode: {data['mode']} | frozen: {data['frozen']['is_frozen']}")
    if nxt:
        print(f"next GW: {nxt['id']} (deadline {nxt['deadline_utc']}, "
              f"{nxt['hours_until_deadline']}h)")
    print(f"data fresh as of: {data['data_basis']['as_of_utc']}")
    print(f"pending decisions: {len(data['pending_decisions'])}")


def _cmd_status_cli(conn=None, cfg=None, json_out=False):
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = _status_data(conn, cfg)
        if json_out:
            _json_ok("status", data)
        else:
            _print_status_text(data)
    finally:
        if owns:
            conn.close()


def _cmd_resume_cli(conn=None, cfg=None, tail=10, json_out=False):
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = _status_data(conn, cfg)
        data["activity"] = {"entries": _activity_entries(conn, tail)}
        data["operating_rules"] = _operating_rules()
        if json_out:
            _json_ok("resume", data)
        else:
            _print_status_text(data)
            for e in data["activity"]["entries"]:
                print(f"  {e['ts_utc']} GW{e['gw']} [{e['mode']}] {e['decision_type']}: "
                      f"{e['action_taken']} ({'done' if e['executed'] else 'skip'})")
    finally:
        if owns:
            conn.close()


def _cmd_log_cli(conn=None, cfg=None, tail=10, gw=None, mode=None, decision_type=None,
                 json_out=False):
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        entries = _activity_entries(conn, tail, gw=gw, mode=mode,
                                    decision_type=decision_type)
        if json_out:
            _json_ok("log", {"entries": entries})
        else:
            for e in entries:
                print(f"{e['ts_utc']} GW{e['gw']} [{e['mode']}] {e['decision_type']}: "
                      f"{e['action_taken']} ({'done' if e['executed'] else 'skip'})")
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 5: Wire the parsers + dispatch in `main()`**

Add parsers after the `freeze-status` parser:

```python
    p_status = sub.add_parser("status", help="one-shot state: mode, frozen, freshness, next GW, pending decisions")
    p_status.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")
    p_resume = sub.add_parser("resume", help="session continuity: status + activity tail + operating rules")
    p_resume.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")
    p_resume.add_argument("--tail", type=int, default=10, help="activity entries to include (default 10)")
    p_log = sub.add_parser("log", help="filterable activity tail")
    p_log.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")
    p_log.add_argument("--tail", type=int, default=10, help="max entries (default 10)")
    p_log.add_argument("--gw", type=int, default=None, help="filter by gameweek")
    p_log.add_argument("--mode", default=None, help="filter by mode (e.g. manual, deadguard, auto)")
    p_log.add_argument("--decision-type", dest="decision_type", default=None,
                       help="filter by decision type (e.g. transfer, captain)")
```

Add dispatch branches after the `auth-status` branch:

```python
    elif args.command == "status":
        _cmd_status_cli(json_out=args.json)
    elif args.command == "resume":
        _cmd_resume_cli(tail=args.tail, json_out=args.json)
    elif args.command == "log":
        _cmd_log_cli(tail=args.tail, gw=args.gw, mode=args.mode,
                     decision_type=args.decision_type, json_out=args.json)
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_agent.py -q`
Expected: all PASS.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: 693 + new tests, all PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add src/cli.py tests/test_cli_agent.py
git commit -m "feat(cli): status/resume/log + JSON envelope helpers (agent contract)"
```

---

### Task 3: refresh --json

**Files:**
- Modify: `src/cli.py` (`_refresh_fpl`, `_refresh_understat`, `refresh`, `main`)
- Test: `tests/test_cli_agent.py` (append)

**Interfaces:**
- Consumes: existing `refresh` internals.
- Produces: `refresh(full=False, cfg=None, conn=None, client=None, understat_client=None, sources=None, report=False) -> dict | None` — with `report=True` returns the report dict and prints nothing; `report=False` keeps today's exact prints and returns `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_agent.py`:

```python
from src.data.models import BootstrapStatic, EntryPicks, Fixture
import requests


class _FakeClient:
    def __init__(self, bs, fx, picks):
        self._bs, self._fx, self._picks = bs, fx, picks

    def bootstrap_static(self):
        return self._bs

    def fixtures(self, event=None):
        return self._fx

    def picks(self, team_id, gw):
        return self._picks


class _NoSquadClient(_FakeClient):
    def picks(self, team_id, gw):
        resp = requests.Response()
        resp.status_code = 404
        resp.url = "https://fantasy.premierleague.com/api/entry/1/event/1/picks/"
        raise requests.exceptions.HTTPError("404 Client Error", response=resp)


def _refresh_cfg():
    return {"fpl": {"team_id": 3122849}, "storage": {"db_path": ":memory:"},
            "mode": {"current": "manual"}, "xp_model": {"version": "v1"},
            "understat": {"season": "2026"}}


def test_refresh_report_json_shape(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    picks = EntryPicks.model_validate(load("picks.json"))
    # First run populates from the fixture (fixture events set GW38 is_next).
    cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                client=_FakeClient(bs, fx, picks), sources=("fpl",), report=True)
    capsys.readouterr()  # report mode prints nothing; drain defensively
    # Pre-season reality: GW1 is the upcoming gameweek — pin it, then re-run.
    conn.execute("UPDATE gameweeks SET is_next=0, is_current=0")
    conn.execute("UPDATE gameweeks SET is_next=1 WHERE id=1")
    conn.commit()
    report = cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                         client=_FakeClient(bs, fx, picks), sources=("fpl",), report=True)
    assert report["fpl"]["bootstrap_static"]["players"] == len(bs.elements)
    assert report["fpl"]["fixtures"] == len(fx)
    assert report["fpl"]["my_team"]["gw"] == 1
    assert report["fpl"]["my_team_skipped"] is None
    assert capsys.readouterr().out == ""
    conn.close()


def test_refresh_report_skipped_squad_no_stdout(load, capsys):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]
    cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                client=_FakeClient(bs, fx, None), sources=("fpl",), report=True)
    conn.execute("UPDATE gameweeks SET is_next=0, is_current=0")
    conn.execute("UPDATE gameweeks SET is_next=1 WHERE id=1")
    conn.commit()
    report = cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                         client=_NoSquadClient(bs, fx, None), sources=("fpl",), report=True)
    assert report["fpl"]["my_team_skipped"] == 1
    assert report["fpl"]["my_team"] is None
    assert capsys.readouterr().out == ""


def test_refresh_report_collects_warnings(load, capsys, monkeypatch):
    conn = connect(":memory:")
    init_db(conn)
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    fx = [Fixture.model_validate(f) for f in load("fixtures.json")]

    def boom(*a, **k):
        raise RuntimeError("rematch exploded")
    monkeypatch.setattr(cli, "_rematch_prior_understat", boom)
    report = cli.refresh(full=True, cfg=_refresh_cfg(), conn=conn,
                         client=_FakeClient(bs, fx, None), sources=("fpl",), report=True)
    assert report["warnings"] == ["understat prior rematch failed (rematch exploded)"]
    assert capsys.readouterr().out == ""
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_agent.py::test_refresh_report_json_shape -q`
Expected: FAIL — `report` keyword not accepted by `refresh`.

- [ ] **Step 3: Refactor `_refresh_fpl` to return a report**

Replace `_refresh_fpl` (lines ~39–63) with:

```python
def _refresh_fpl(conn, client, tid, full):
    out = {"bootstrap_static": None, "fixtures": None,
           "my_team": None, "my_team_skipped": None}
    if full or cache.is_stale(conn, "bootstrap-static"):
        bs = client.bootstrap_static()
        repository.upsert_teams(conn, bs.teams)
        repository.upsert_players(conn, bs.elements, bs.element_types)
        repository.upsert_gameweeks(conn, bs.events)
        cache.mark_fetched(conn, "bootstrap-static")
        out["bootstrap_static"] = {"players": len(bs.elements), "teams": len(bs.teams)}
    if full or cache.is_stale(conn, "fixtures"):
        fx = client.fixtures()
        repository.upsert_fixtures(conn, fx)
        cache.mark_fetched(conn, "fixtures")
        out["fixtures"] = len(fx)
    gw = _current_gw_from_db(conn)
    if gw is not None and (full or cache.is_stale(conn, "my_team")):
        try:
            picks = client.picks(tid, gw)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                out["my_team_skipped"] = gw
                return out
            raise
        repository.snapshot_my_team(conn, gw, picks)
        cache.mark_fetched(conn, "my_team")
        out["my_team"] = {"gw": gw, "picks": len(picks.picks)}
    return out
```

- [ ] **Step 4: Refactor `_refresh_understat` to return a report**

Replace `_refresh_understat` (lines ~66–81) with:

```python
def _refresh_understat(conn, understat_client, cfg, full, report=False):
    # Supplementary data: a failure must NOT break the FPL refresh (R2).
    try:
        if not (full or cache.is_stale(conn, "understat")):
            return None
        season = cfg.get("understat", {}).get("season", "2025")
        resp = understat_client.players_stats(season)
        fpl_players = [dict(r) for r in conn.execute("SELECT id, name, web_name, team_id FROM players")]
        fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams")]
        res = name_resolver.resolve_players(fpl_players, fpl_teams, resp.players, _load_name_overrides())
        repository.upsert_understat_players(conn, resp.players, res, season)
        cache.mark_fetched(conn, "understat")
        return {"total": len(resp.players), "matched": len(res.matched),
                "unmatched": len(res.unmatched), "unmapped_teams": len(res.unmapped_teams)}
    except Exception as exc:  # noqa: BLE001 - supplementary source degrades gracefully
        if report:
            return {"warning": str(exc)}
        print(f"WARNING: understat refresh failed ({exc}); keeping last data")
        return None
```

- [ ] **Step 5: Refactor `refresh` to emit prints or return the report**

Replace `refresh` (lines ~142–173) with:

```python
def refresh(full=False, cfg=None, conn=None, client=None, understat_client=None,
            sources=None, report=False):
    cfg = cfg or load_config()
    if sources is None:  # explicit: an empty tuple means "no sources", not "both"
        sources = ("fpl", "understat")
    owns_conn = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)

    fpl_out = {}
    if "fpl" in sources:
        fpl_out = _refresh_fpl(conn, client or FPLClient(), cfg_team_id(cfg), full)
        if not report:
            bs = fpl_out["bootstrap_static"]
            if bs:
                print(f"bootstrap-static OK ({bs['players']} players, {bs['teams']} teams)")
            if fpl_out["fixtures"] is not None:
                print(f"fixtures OK ({fpl_out['fixtures']} fixtures)")
            if fpl_out["my_team"] is not None:
                print(f"my_team OK (GW{fpl_out['my_team']['gw']}, "
                      f"{fpl_out['my_team']['picks']} picks)")
            elif fpl_out["my_team_skipped"] is not None:
                print(f"my_team skipped: no squad saved yet for "
                      f"GW{fpl_out['my_team_skipped']} (404)")
    understat_out = None
    if "understat" in sources:
        understat_out = _refresh_understat(conn, understat_client or UnderstatClient(),
                                           cfg, full, report=report)
        if not report and understat_out is not None and "warning" not in understat_out:
            print(f"understat OK (matched {understat_out['matched']}/{understat_out['total']}, "
                  f"{understat_out['unmatched']} unmatched, "
                  f"{understat_out['unmapped_teams']} unmapped teams)")

    # Season rollover: re-link prior-season understat rows to the current players
    # table (player ids change every season; stale pointers silently feed the wrong
    # player's stats into xP and insights). Always runs — also on fpl-only refreshes.
    rematch = 0
    cleanup = {"gw_stats": 0, "my_team": 0}
    warnings = []
    try:
        current_season = cfg.get("understat", {}).get("season", "2025")
        rematch = _rematch_prior_understat(conn, current_season)
        if rematch and not report:
            print(f"understat prior rematch: {rematch} rows re-linked to current player ids")
    except Exception as exc:  # noqa: BLE001 - data hygiene must not break refresh
        if report:
            warnings.append(f"understat prior rematch failed ({exc})")
        else:
            print(f"WARNING: understat prior rematch failed ({exc})")
    try:
        n_gw, n_team = _clear_stale_season_rows(conn)
        cleanup = {"gw_stats": n_gw, "my_team": n_team}
        if (n_gw or n_team) and not report:
            print(f"season rollover cleanup: {n_gw} gw_stats rows, "
                  f"{n_team} my_team rows cleared")
    except Exception as exc:  # noqa: BLE001 - data hygiene must not break refresh
        if report:
            warnings.append(f"season rollover cleanup failed ({exc})")
        else:
            print(f"WARNING: season rollover cleanup failed ({exc})")

    if owns_conn:
        conn.close()
    if report:
        return {"fpl": fpl_out, "understat": understat_out, "rematch": rematch,
                "cleanup": cleanup, "warnings": warnings}
```

- [ ] **Step 6: Wire `refresh --json` in `main()`**

Add to the refresh parser: `p_refresh.add_argument("--json", action="store_true", help="output the JSON envelope (agent contract)")`.

Replace the refresh dispatch branch:

```python
    if args.command == "refresh":
        sources = (args.source,) if args.source else ("fpl", "understat")
        if args.json:
            report = refresh(full=args.full, sources=sources, report=True)
            _json_ok("refresh", report)
        else:
            refresh(full=args.full, sources=sources)
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_agent.py tests/test_cli_refresh.py -q`
Expected: all PASS — including the pre-existing `test_cli_refresh.py` (prints unchanged in non-report mode).

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/cli.py tests/test_cli_agent.py
git commit -m "feat(cli): refresh --json report path (suppresses stdout, returns counts)"
```

---

### Task 4: captain / transfers / chips / freeze-status / auth-status --json

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_agent.py` (append)

**Interfaces:**
- Consumes: `src.interface.queries.get_captain_picks/get_transfer_suggestions/get_chip_recommendation`, `src.execution.override.status`, `src.data.repository.get_auth_state/get_access_expiry/get_relogin_failures`, `_data_basis`.
- Produces: `_cmd_captain_cli(conn=None, cfg=None)`, `_cmd_transfers_cli(conn=None, cfg=None)`, `_cmd_chips_cli(conn=None, cfg=None)`, `_cmd_freeze_status_cli(conn=None, cfg=None)`, `_cmd_auth_status_cli(conn=None, cfg=None)` — all JSON-always.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_agent.py`:

```python
def _seed_decision_data(db, load):
    """Full deterministic seed: teams/players/gameweeks/fixtures/understat/fdr/xp."""
    from src.analytics import fdr, xp
    from src.data import name_resolver, repository
    from src.data.models import BootstrapStatic, Fixture, UnderstatPlayersResponse

    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    repository.upsert_gameweeks(db, bs.events)
    db.execute("UPDATE gameweeks SET is_next=0, is_current=0, finished=0 WHERE 1")
    db.execute("UPDATE gameweeks SET is_next=1 WHERE id=1")
    repository.upsert_fixtures(db, [Fixture.model_validate(f) for f in load("fixtures.json")])
    us = UnderstatPlayersResponse.model_validate(
        load("understat-players.json")).players
    fpl_players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    fpl_teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    res = name_resolver.resolve_players(fpl_players, fpl_teams, us)
    repository.upsert_understat_players(db, us, res, "2026")
    fdr.compute_and_store(db)
    xp.compute_and_store(db)
    db.commit()


def test_captain_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_captain_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "captain"
    assert out["data"]["picks"] and "data_basis" in out["data"]
    assert out["data"]["data_basis"]["xp_model_version"] == "v1"


def test_transfers_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_transfers_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "transfers"
    assert set(out["data"]) == {"suggestions", "empty_reason", "free_transfers", "data_basis"}
    for s in out["data"]["suggestions"]:
        assert set(s["out"]) == {"player_id", "web_name", "price"}
        assert set(s["in"]) == {"player_id", "web_name", "price"}


def test_chips_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_chips_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "chips"
    assert "recommendation" in out["data"] and "data_basis" in out["data"]


def test_freeze_status_json(db, capsys):
    db.execute("INSERT INTO system_state (key, value) VALUES "
               "('freeze', '{\"since\": \"2026-08-15T07:00:00Z\", \"source\": \"user\", \"reason\": \"travel\"}')")
    db.commit()
    cli._cmd_freeze_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["frozen"] == {"is_frozen": True, "since": "2026-08-15T07:00:00Z",
                                     "source": "user", "reason": "travel"}
    db.execute("DELETE FROM system_state")
    db.commit()
    cli._cmd_freeze_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["frozen"] == {"is_frozen": False}


def test_auth_status_json(db, capsys):
    cli._cmd_auth_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["auth"] is None
    db.execute("INSERT INTO credentials (id, auth_state, relogin_failures) VALUES (1, 'active', 0)")
    db.commit()
    cli._cmd_auth_status_cli(conn=db, cfg=_cfg(), json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["auth"]["state"] == "active"
    assert out["data"]["auth"]["relogin_failures"] == 0
    assert all(k not in out["data"]["auth"] for k in ("password", "token", "cookie"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_agent.py -q`
Expected: FAIL — `_cmd_captain_cli` etc. missing.

- [ ] **Step 3: Implement the wrappers**

Insert after `_cmd_log_cli`:

```python
def _cmd_captain_cli(conn=None, cfg=None):
    from .interface.queries import get_captain_picks
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = get_captain_picks(conn)
        data["data_basis"] = _data_basis(conn, cfg)
        _json_ok("captain", data)
    finally:
        if owns:
            conn.close()


def _cmd_transfers_cli(conn=None, cfg=None):
    from .interface.queries import get_transfer_suggestions
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = get_transfer_suggestions(conn)
        data["data_basis"] = _data_basis(conn, cfg)
        _json_ok("transfers", data)
    finally:
        if owns:
            conn.close()


def _cmd_chips_cli(conn=None, cfg=None):
    from .interface.queries import get_chip_recommendation
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        data = get_chip_recommendation(conn)
        data["data_basis"] = _data_basis(conn, cfg)
        _json_ok("chips", data)
    finally:
        if owns:
            conn.close()


def _cmd_freeze_status_cli(conn=None, cfg=None, json_out=False):
    from .execution import override
    if not json_out:
        return _freeze_status_cli(conn=conn)
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        frozen = override.status(conn)
        data = {"frozen": ({"is_frozen": True, **frozen}) if frozen else {"is_frozen": False}}
        _json_ok("freeze-status", data)
    finally:
        if owns:
            conn.close()


def _cmd_auth_status_cli(conn=None, cfg=None, json_out=False):
    from .data import repository
    if not json_out:
        return _auth_status_cli(conn=conn)
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        state = repository.get_auth_state(conn)
        auth = None
        if state is not None:
            row = conn.execute("SELECT session_last_refreshed FROM credentials WHERE id=1").fetchone()
            auth = {"state": state,
                    "access_token_expires_at": repository.get_access_expiry(conn),
                    "session_last_refreshed": row["session_last_refreshed"] if row else None,
                    "relogin_failures": repository.get_relogin_failures(conn)}
        _json_ok("auth-status", {"auth": auth})
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 4: Wire parsers + dispatch in `main()`**

Add parsers (after the `log` parser):

```python
    for _name, _help in (("captain", "captain ranker output (JSON)"),
                         ("transfers", "transfer suggestions (JSON)"),
                         ("chips", "chip recommendation (JSON)")):
        p = sub.add_parser(_name, help=_help)
        p.add_argument("--json", action="store_true", required=True,
                       help="output the JSON envelope (agent contract)")
```

Change the two existing parser lines to keep handles and add `--json`:

```python
    p_freeze_status = sub.add_parser("freeze-status", help="show whether autonomous execution is frozen")
    p_freeze_status.add_argument("--json", action="store_true",
                                 help="output the JSON envelope (agent contract)")
    p_auth_status = sub.add_parser("auth-status", help="show stored FPL session state (no secrets)")
    p_auth_status.add_argument("--json", action="store_true",
                               help="output the JSON envelope (agent contract)")
```

Add dispatch branches:

```python
    elif args.command == "captain":
        _cmd_captain_cli()
    elif args.command == "transfers":
        _cmd_transfers_cli()
    elif args.command == "chips":
        _cmd_chips_cli()
    elif args.command == "freeze-status":
        _cmd_freeze_status_cli(json_out=args.json)
    elif args.command == "auth-status":
        _cmd_auth_status_cli(json_out=args.json)
```

Text mode (no `--json`) delegates to the existing `_freeze_status_cli` / `_auth_status_cli`
bodies, so current behavior is unchanged.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_agent.py tests/test_cli_freeze.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_agent.py
git commit -m "feat(cli): captain/transfers/chips/freeze-status/auth-status --json (agent contract)"
```

---

### Task 5: squad, squad --candidates, speculate --json

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_agent.py` (append)

**Interfaces:**
- Consumes: `src.decisions.squad_builder.build_candidate_pool`, `src.ai.squad.runner` (`PANE_TYPE`, `build_squad_digest`, `extract_json_object`, `generate_squad`), `src.ai.squad.spikes.generate_spike_signals`, `src.ai.cache` (`recommendation_hash`, `get`), `src.config.ai_enabled()`, `src.ai.provider.build_provider`, `src.config.ai_deepseek_model()`, `_data_basis`.
- Produces: `_cmd_squad_cli(conn=None, cfg=None, candidates_only=False)`, `_cmd_speculate_cli(conn=None, cfg=None)` — JSON-always; `E_NO_DATA` when the pool is empty, `E_RUNTIME` when AI is disabled or the runner returns `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_agent.py`:

```python
def test_squad_candidates_json(load, db, capsys):
    _seed_decision_data(db, load)
    cli._cmd_squad_cli(conn=db, cfg=_cfg(), candidates_only=True)
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "squad" and out["data"]["gw"] == 1
    assert out["data"]["count"] == len(out["data"]["pool"]) > 0
    p = out["data"]["pool"][0]
    assert {"player_id", "web_name", "team_short", "position", "price", "xp_next",
            "xp_6gw", "value", "ownership_pct", "form", "transfers_in",
            "transfers_out", "net_momentum"} <= set(p)


def test_squad_candidates_no_data(db, capsys):
    cli._cmd_squad_cli(conn=db, cfg=_cfg(), candidates_only=True)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NO_DATA"


def test_squad_json_built(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai import cache as ai_cache
    from src.ai.squad import runner as squad_runner
    from src.decisions.squad_builder import build_candidate_pool

    pid = build_candidate_pool(db)[0]["player_id"]
    result = {"source": "ai", "picks": [{"player_id": pid, "slot": "GKP1", "reason": "good"}],
              "template_rationale": "template", "risks": ["rotation"], "speculation": None}
    monkeypatch.setattr(squad_runner, "generate_squad", lambda conn, **k: result)
    cli._cmd_squad_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "generated"
    assert out["data"]["gw"] == 1 and out["data"]["source"] == "ai"
    assert out["data"]["picks"][0]["player_id"] == pid
    assert out["data"]["picks"][0]["web_name"]  # enriched from the pool
    assert out["data"]["budget_used"] >= 0
    assert out["data"]["data_basis"]["xp_model_version"] == "v1"


def test_squad_json_cached(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    import json as _json
    from src.ai import cache as ai_cache
    from src.ai.squad import runner as squad_runner

    pool = __import__("src.decisions.squad_builder", fromlist=["build_candidate_pool"]).build_candidate_pool(db)
    digest = squad_runner.build_squad_digest(db, pool=pool)
    rec_hash = ai_cache.recommendation_hash(digest)
    payload = _json.dumps({"source": "ai",
                           "picks": [{"player_id": pool[0]["player_id"], "slot": "GKP1",
                                      "reason": "cached"}],
                           "template_rationale": "t", "risks": [], "speculation": None},
                          sort_keys=True)
    db.execute("INSERT INTO ai_reasoning_cache (gw, pane_type, recommendation_hash, prose, "
               "model_id, generated_at) VALUES (?, 'squad', ?, ?, 'deepseek-chat', "
               "'2026-08-15T08:00:00Z')", (1, rec_hash, payload))
    db.commit()
    monkeypatch.setattr(squad_runner, "generate_squad",
                        lambda conn, **k: (_ for _ in ()).throw(AssertionError("must be cache hit")))
    cli._cmd_squad_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "cached"
    assert out["data"]["picks"][0]["reason"] == "cached"


def test_squad_json_ai_disabled(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src import config
    monkeypatch.setattr(config, "ai_enabled", lambda: False)
    cli._cmd_squad_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_RUNTIME"


def test_speculate_json(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai.squad import spikes
    signals = {"spikes": [{"player_id": 234, "level": "high", "reason": "in 48.1"}],
               "drops": [], "market_read": "market quiet"}
    monkeypatch.setattr(spikes, "generate_spike_signals",
                        lambda conn, **k: signals)
    cli._cmd_speculate_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["gw"] == 1
    assert out["data"]["signals"]["spikes"][0]["player_id"] == 234
    # no my_team snapshot -> every spike is a differential
    assert [s["player_id"] for s in out["data"]["differentials"]] == [234]


def test_speculate_json_failure(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai.squad import spikes
    monkeypatch.setattr(spikes, "generate_spike_signals", lambda conn, **k: None)
    cli._cmd_speculate_cli(conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_RUNTIME"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_agent.py -q`
Expected: FAIL — `_cmd_squad_cli`/`_cmd_speculate_cli` missing.

- [ ] **Step 3: Implement `_cmd_squad_cli` and `_cmd_speculate_cli`**

Insert after `_cmd_auth_status_cli`:

```python
def _cmd_squad_cli(conn=None, cfg=None, candidates_only=False):
    from .ai import cache as ai_cache
    from .ai.squad import runner as squad_runner
    from .decisions.squad_builder import build_candidate_pool
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        pool = build_candidate_pool(conn)
        if not pool:
            _json_err("squad", "E_NO_DATA", "no upcoming gameweek with xP data",
                      "run refresh --json first")
        nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        gw = nxt["gw"] if nxt else None
        if candidates_only:
            _json_ok("squad", {"gw": gw, "count": len(pool), "pool": pool,
                               "data_basis": _data_basis(conn, cfg)})
        if not config.ai_enabled():
            _json_err("squad", "E_RUNTIME", "AI disabled (config ai.enabled=false)",
                      "squad --json requires the AI squad builder")
        digest = squad_runner.build_squad_digest(conn, pool=pool)
        rec_hash = ai_cache.recommendation_hash(digest)
        hit = ai_cache.get(conn, gw, squad_runner.PANE_TYPE, rec_hash)
        if hit is not None:
            result = squad_runner.extract_json_object(hit["prose"])
            status = "cached"
        else:
            from .ai.provider import build_provider
            result = squad_runner.generate_squad(
                conn, provider=build_provider(config.load_config()),
                model_id=config.ai_deepseek_model())
            if result is None:
                _json_err("squad", "E_RUNTIME",
                          "squad builder gate rejected or provider failed",
                          "check AI provider config and retry")
            status = "generated"
        by_id = {p["player_id"]: p for p in pool}
        picks = []
        for pk in result["picks"]:
            p = by_id.get(pk["player_id"])
            if p is None:
                continue
            picks.append({"player_id": pk["player_id"], "web_name": p["web_name"],
                          "team": p["team_short"], "position": p["position"],
                          "price": p["price"], "xp_6gw": p["xp_6gw"],
                          "slot": pk["slot"], "reason": pk.get("reason", "")})
        budget_used = round(sum(p["price"] for p in picks), 1)
        spec = result.get("speculation")
        if spec:
            for kind in ("spikes", "drops", "differentials"):
                for s in spec.get(kind, []):
                    p = by_id.get(s["player_id"])
                    s["web_name"] = p["web_name"] if p else f"#{s['player_id']}"
                    s["team"] = p["team_short"] if p else None
        _json_ok("squad", {
            "status": status, "gw": gw, "source": result.get("source", "ai"),
            "picks": picks, "template_rationale": result.get("template_rationale", ""),
            "risks": result.get("risks", []), "budget_used": budget_used,
            "speculation": spec, "model_id": config.ai_deepseek_model(),
            "generated_at": hit["generated_at"] if hit is not None else None,
            "data_basis": _data_basis(conn, cfg),
        })
    finally:
        if owns:
            conn.close()


def _cmd_speculate_cli(conn=None, cfg=None):
    from .ai.squad import spikes
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        from .ai.provider import build_provider
        signals = spikes.generate_spike_signals(
            conn, provider=build_provider(config.load_config()),
            model_id=config.ai_deepseek_model())
        if signals is None:
            _json_err("speculate", "E_RUNTIME",
                      "speculation unavailable (provider error or gate rejected)",
                      "retry later; the squad builder runs without speculation")
        nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        gw = nxt["gw"] if nxt else None
        in_squad = set()
        snap = conn.execute("SELECT picks_json FROM my_team ORDER BY gw DESC LIMIT 1").fetchone()
        if snap is not None:
            in_squad = {pk["element"] for pk in json.loads(snap["picks_json"])}
        differentials = [s for s in signals.get("spikes", [])
                         if s["player_id"] not in in_squad]
        _json_ok("speculate", {"gw": gw, "signals": signals,
                               "differentials": differentials,
                               "data_basis": _data_basis(conn, cfg)})
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 4: Wire parsers + dispatch in `main()`**

Add parsers (after the `chips` parser loop):

```python
    p_squad = sub.add_parser("squad", help="AI-built squad (JSON; --candidates for the pool)")
    p_squad.add_argument("--json", action="store_true", required=True,
                         help="output the JSON envelope (agent contract)")
    p_squad.add_argument("--candidates", action="store_true",
                         help="output the deterministic candidate pool instead of the built squad")
    p_speculate = sub.add_parser("speculate", help="AI spike/drop signals (JSON)")
    p_speculate.add_argument("--json", action="store_true", required=True,
                             help="output the JSON envelope (agent contract)")
```

Add dispatch branches:

```python
    elif args.command == "squad":
        _cmd_squad_cli(candidates_only=args.candidates)
    elif args.command == "speculate":
        _cmd_speculate_cli()
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_agent.py tests/test_api_squad_builder.py -q`
Expected: all PASS (the api squad-builder tests are untouched, confirming the enrichment logic matches the dashboard).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_agent.py
git commit -m "feat(cli): squad / squad --candidates / speculate --json (agent contract)"
```

---

### Task 6: insight <player_id> --json

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_agent.py` (append)

**Interfaces:**
- Consumes: `src.ai.insight.runner` (`build_player_digest`, `PANE_TYPE`, `extract_json_object`, `generate_player_insight`), `src.ai.cache`, `src.config.ai_enabled()`, `src.ai.provider.build_provider`, `src.config.ai_deepseek_model()`, `_data_basis`.
- Produces: `_cmd_insight_cli(player_id, conn=None, cfg=None)` — JSON-always; `E_NO_DATA` for unknown player or missing digest, `E_RUNTIME` when AI disabled or generation fails. `_player_identity(conn, player_id) -> dict | None` (copied from `src/interface/api.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_agent.py`:

```python
def test_insight_json_generated(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    from src.ai.insight import runner as insight_runner
    pid = db.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    payload = {"insights": [{"category": "value_market", "claim": "In 48.1", "evidence_used": ["48.1"],
                             "confidence": "high"}], "summary": "solid", "data_limits": []}
    monkeypatch.setattr(insight_runner, "generate_player_insight",
                        lambda conn, player_id, **k: payload)
    cli._cmd_insight_cli(pid, conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "insight" and out["data"]["status"] == "generated"
    assert out["data"]["player_id"] == pid
    assert out["data"]["insights"][0]["category"] == "value_market"
    assert out["data"]["player"]["web_name"]
    assert out["data"]["data_basis"]["xp_model_version"] == "v1"


def test_insight_json_cached(load, db, capsys, monkeypatch):
    _seed_decision_data(db, load)
    import json as _json
    from src.ai import cache as ai_cache
    from src.ai.insight import runner as insight_runner
    pid = db.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    digest = insight_runner.build_player_digest(db, pid)
    rec_hash = ai_cache.recommendation_hash(digest)
    payload = _json.dumps({"insights": [{"category": "fixture_alignment", "claim": "In 48.1",
                                         "evidence_used": ["48.1"], "confidence": "high"}],
                           "summary": "cached summary", "data_limits": []}, sort_keys=True)
    db.execute("INSERT INTO ai_reasoning_cache (gw, pane_type, recommendation_hash, prose, "
               "model_id, generated_at) VALUES (?, 'insight', ?, ?, 'deepseek-chat', "
               "'2026-08-15T08:00:00Z')", (1, rec_hash, payload))
    db.commit()
    monkeypatch.setattr(insight_runner, "generate_player_insight",
                        lambda conn, player_id, **k: (_ for _ in ()).throw(AssertionError("cache hit expected")))
    cli._cmd_insight_cli(pid, conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "cached"
    assert out["data"]["summary"] == "cached summary"


def test_insight_json_unknown_player(db, capsys):
    cli._cmd_insight_cli(999999, conn=db, cfg=_cfg())
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NO_DATA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_agent.py -q`
Expected: FAIL — `_cmd_insight_cli` missing.

- [ ] **Step 3: Implement `_cmd_insight_cli` + `_player_identity`**

Insert after `_cmd_speculate_cli`:

```python
def _player_identity(conn, player_id):
    row = conn.execute(
        "SELECT p.name, p.web_name, p.position, p.price, t.short_name AS team "
        "FROM players p JOIN teams t ON t.id = p.team_id WHERE p.id=?",
        (player_id,)).fetchone()
    if row is None:
        return None
    return {"name": row["name"], "web_name": row["web_name"],
            "position": row["position"], "team": row["team"], "price": row["price"]}


def _cmd_insight_cli(player_id, conn=None, cfg=None):
    from .ai import cache as ai_cache
    from .ai.insight import runner as insight_runner
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        exists = conn.execute("SELECT id FROM players WHERE id=?", (player_id,)).fetchone()
        if exists is None:
            _json_err("insight", "E_NO_DATA", f"unknown player {player_id}",
                      "look up player ids via squad --candidates --json")
        if not config.ai_enabled():
            _json_err("insight", "E_RUNTIME", "AI disabled (config ai.enabled=false)")
        digest = insight_runner.build_player_digest(conn, player_id)
        if digest is None:
            _json_err("insight", "E_NO_DATA", f"no digest for player {player_id}",
                      "run refresh --json first")
        nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        gw = nxt["gw"]
        rec_hash = ai_cache.recommendation_hash(digest)
        hit = ai_cache.get(conn, gw, insight_runner.PANE_TYPE, rec_hash)
        if hit is not None:
            payload = insight_runner.extract_json_object(hit["prose"])
            status = "cached"
        else:
            from .ai.provider import build_provider
            payload = insight_runner.generate_player_insight(
                conn, player_id, provider=build_provider(config.load_config()),
                model_id=config.ai_deepseek_model())
            if payload is None:
                _json_err("insight", "E_RUNTIME",
                          "provider error or quality gate rejected",
                          "retry later")
            status = "generated"
        data = {
            "status": status, "player_id": player_id, "gw": gw,
            "player": _player_identity(conn, player_id),
            "insights": payload.get("insights", []),
            "summary": payload.get("summary", ""),
            "data_limits": payload.get("data_limits", []),
            "model_id": config.ai_deepseek_model(),
            "generated_at": hit["generated_at"] if hit is not None else None,
            "data_basis": _data_basis(conn, cfg),
        }
        _json_ok("insight", data)
    finally:
        if owns:
            conn.close()
```

- [ ] **Step 4: Wire parser + dispatch in `main()`**

Add parser (after the `speculate` parser):

```python
    p_insight = sub.add_parser("insight", help="per-player AI deep-dive (JSON)")
    p_insight.add_argument("player_id", type=int, help="FPL player id")
    p_insight.add_argument("--json", action="store_true", required=True,
                           help="output the JSON envelope (agent contract)")
```

Add dispatch branch:

```python
    elif args.command == "insight":
        _cmd_insight_cli(args.player_id)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_agent.py tests/test_api_insight.py -q`
Expected: all PASS (api insight tests untouched — the CLI path mirrors the endpoint behavior).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py tests/test_cli_agent.py
git commit -m "feat(cli): insight <player_id> --json (agent contract)"
```

---

### Task 7: --live non-TTY gate (R3 hardening)

**Files:**
- Modify: `src/cli.py` (`main`)
- Test: `tests/test_cli_agent.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `_live_requires_tty(live: bool)` — raises `SystemExit(2)` with a stderr message when `live` and stdin is not a TTY. Called in `main()` before every `--live` dispatch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_agent.py`:

```python
from types import SimpleNamespace


def test_live_refuses_non_tty(monkeypatch, capsys):
    import src.auth.master as master
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(master, "is_initialized", lambda **k: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["execute-lineup", "--live"])
    assert exc.value.code == 2
    assert "--live" in capsys.readouterr().err


def test_live_ok_with_tty(monkeypatch, capsys):
    import src.auth.master as master
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(master, "is_initialized", lambda **k: False)
    cli.main(["execute-lineup", "--live"])
    assert "Master password not set" in capsys.readouterr().out


def test_dry_run_never_needs_tty(monkeypatch, capsys):
    import src.auth.master as master
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(master, "is_initialized", lambda **k: False)
    cli.main(["execute-lineup"])
    assert "Master password not set" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_agent.py -q`
Expected: FAIL — `main(["execute-lineup", "--live"])` with non-TTY currently proceeds (no gate) and exits with a different code or message.

- [ ] **Step 3: Implement the gate**

Insert after `_resolve_audit_provider_choice`:

```python
def _live_requires_tty(live):
    if live and not sys.stdin.isatty():
        print("Error: --live requires an interactive terminal (stdin TTY). "
              "Agent sessions can never pass --live (R3).", file=sys.stderr)
        raise SystemExit(2)
```

In `main()`, before the dispatch `if args.command == ...` chain, add:

```python
    if args.command in ("execute-lineup", "execute-transfer", "apply-squad",
                        "route-gameweek", "undo-transfer"):
        _live_requires_tty(args.live)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_agent.py tests/test_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/test_cli_agent.py
git commit -m "feat(cli): --live refuses non-TTY stdin (R3: agents can never write)"
```

---

### Task 8: skills/fpl-agent/SKILL.md

**Files:**
- Create: `skills/fpl-agent/SKILL.md`

**Interfaces:**
- Consumes: the commands from Tasks 2–7 (the ritual references `resume`, `refresh`, `captain`, `transfers`, `squad`, `insight`, `speculate`, `log`).

- [ ] **Step 1: Create the skill**

```markdown
---
name: fpl-agent
description: Session boot ritual + operating rules for operating FPL Autopilot (repo
  github.com:shariski/fpl-autopilot). Use at the start of ANY session that touches this
  project — drives `fpl-autopilot <cmd> --json` over SSH (jumbo) or locally.
---

# FPL Autopilot — Agent Operating Skill

## When to use
Any session where you will read FPL data, propose transfers/captain/chips/squad decisions,
or run the CLI. Never skip this when the task touches the live system.

## Boot ritual (in order)

1. Read `AGENTS.md` (repo root) — binding B-rules.
2. Read `docs/agent-contract.md` — the JSON contract (shapes, exit codes, R3).
3. Run `resume --json` and note: mode, frozen?, `data_basis.as_of_utc` (freshness),
   `next_gameweek` id + `hours_until_deadline`, `pending_decisions`, and the
   `operating_rules` block. This is the session's context anchor.
   - Locally: `.venv/bin/fpl-autopilot resume --json`
   - On jumbo: `ssh jumbo 'docker compose run --rm -T app resume --json'`
4. If data is stale (`as_of_utc` older than ~1 hour, or pre-season and never refreshed):
   run `refresh --json` (agent-safe — FPL reads + local DB writes only).

## Operating rules (R3 — non-negotiable)

- **Never pass `--live`.** `--live` refuses non-TTY stdin; a non-interactive agent
  session cannot pass it even by accident. Propose the exact command; the HUMAN runs it.
- **Read-only + refresh only.** Agent-safe: status, resume, log, captain, transfers, chips,
  squad, squad --candidates, insight <id>, speculate, refresh, freeze-status, auth-status,
  review.
- **Human-only (never run):** execute-lineup, execute-transfer, apply-squad,
  route-gameweek, undo-transfer, refresh-my-team, init-master-password, init-fpl, freeze,
  unfreeze.
- **No secrets:** never print or log credentials/tokens; contract output already excludes them.
- **Dry-run is the default:** every contract command is read-only or local-DB-only.

## Weekly loop

resume → refresh (if stale) → captain/transfers/chips/squad → insight/speculate for
targets → reason (add web search + judgment) → propose to the user with exact commands →
human runs `--live` → verify via `log --json`.

## Troubleshooting

- `E_NO_DATA` → run `refresh --json` first.
- `E_RUNTIME` on squad/insight/speculate → AI provider issue; retry later; the squad
  builder still runs without speculation.
- Unknown player id → look it up via `squad --candidates --json`.

## Install (one-time, per agent family)

Symlink this repo's `skills/fpl-agent/` into the agent's skills directory:

    ln -s "$PWD/skills/fpl-agent" ~/.claude/skills/fpl-agent
    ln -s "$PWD/skills/fpl-agent" ~/.config/opencode/skills/fpl-agent
```

- [ ] **Step 2: Verify the skill references only commands that exist**

Check every command named in the skill against the Task 2–7 outputs: `status`, `resume`,
`log`, `captain`, `transfers`, `chips`, `squad` (+`--candidates`), `insight`, `speculate`,
`refresh`, `freeze-status`, `auth-status` — all implemented.

- [ ] **Step 3: Commit**

```bash
git add skills/fpl-agent/SKILL.md
git commit -m "docs(skill): fpl-agent session boot ritual + R3 operating rules"
```

---

### Task 9: Full suite + contract cross-check + merge

**Files:**
- Test: full repo suite; manual smoke of the CLI.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS (693 baseline + new tests).

Run: `cd frontend && npm test`
Expected: 71 PASS (untouched, but the house rule is full-suite-green).

- [ ] **Step 2: Smoke the contract locally**

Run against a scratch DB (no live data needed — empty tables must still produce valid envelopes):

```bash
.venv/bin/fpl-autopilot status --json ; echo "exit=$?"
.venv/bin/fpl-autopilot resume --json | head -c 400 ; echo
.venv/bin/fpl-autopilot log --json --tail 3 ; echo "exit=$?"
.venv/bin/fpl-autopilot freeze-status --json ; echo "exit=$?"
.venv/bin/fpl-autopilot auth-status --json ; echo "exit=$?"
```

Expected: each prints exactly one JSON object; `exit=0`; `ok` matches the exit code.
Also: `.venv/bin/fpl-autopilot captain` (no `--json`) → argparse error, exit 2.

- [ ] **Step 3: Cross-check the contract doc against the implementation**

Skim `docs/agent-contract.md` — every shape (envelope, status, resume, log, captain,
transfers, chips, squad, candidates, insight, speculate, refresh, freeze-status,
auth-status) must match the actual output of the corresponding command. Fix the doc if the
code drifted (B13: doc is truth, but drift must be reconciled in the doc or code — pick
the doc's intent, fix the code to match).

- [ ] **Step 4: Run the full suite once more**

Run: `.venv/bin/pytest -q && cd frontend && npm test`
Expected: all PASS.

- [ ] **Step 5: Merge to main**

```bash
git checkout main && git merge feat/agent-contract
git push origin main
```

(CI auto-builds + deploys to jumbo. Verify on jumbo afterward:
`ssh jumbo 'docker compose run --rm -T app resume --json'` prints the envelope.)

- [ ] **Step 6: Verify on jumbo (post-deploy smoke)**

```bash
ssh jumbo 'docker compose run --rm -T app status --json'
ssh jumbo 'docker compose run --rm -T app resume --json | head -c 600'
ssh jumbo 'docker compose run --rm -T app log --json --tail 5'
```

Expected: valid envelopes from the live DB; `data_freshness` reflects the hourly
scheduler's last refresh.
