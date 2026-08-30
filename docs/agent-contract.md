# FPL Autopilot — Agent Contract (v1)

The documented surface any AI agent can operate. The agent never touches the host, the
DB, or the container — it speaks only `fpl-autopilot <cmd> --json`. Human users keep the
text commands, the dashboard, and Telegram.

**Transport:** on jumbo, run one-shot commands in the deployed container:

    ssh jumbo 'docker compose --project-directory /opt/fpl-autopilot run --rm -T app <cmd> --json'

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
                              "squad", "insight", "speculate", "refresh", "note", "leaders",
                              "freeze-status", "auth-status", "review"],
      "human_only_commands": ["execute-lineup", "execute-transfer", "apply-squad",
                              "route-gameweek", "undo-transfer", "refresh-my-team",
                              "init-master-password", "init-fpl", "freeze", "unfreeze",
                              "mode"]
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

Each transfer suggestion may carry a `caveat` (v0.24, present only while fewer than 3
live GWs are settled): a data-basis note that start probabilities lean on 25-26 data,
plus — when the buy has live GWs but zero starts — "started 0 of N live GWs". Confidence
carries an early-season penalty (−15) over the same gate; treat sub-70 confidence as
"verify before acting".
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
    fpl-autopilot refresh --full-cycle --json

Plain `refresh` pulls FPL/Understat and reports what changed (suppresses progress prints in `--json`):

    data: {"fpl": {"bootstrap_static": {"players": 587, "teams": 20} | null,
                   "fixtures": 380 | null, "my_team": {"gw": 1, "picks": 15} | null,
                   "my_team_skipped": 1 | null},
           "understat": {"total": 500, "matched": 480, "unmatched": 20,
                         "unmapped_teams": 2} | null,
           "rematch": 0, "cleanup": {"gw_stats": 0, "my_team": 0},
           "warnings": [...], "data_basis": {...}}

`--full-cycle` (v0.25) runs the complete scheduler tick as a one-shot — fetch + FDR v1/v2
recompute + xP v1/v2 recompute + settlement/backfill — and reports the recompute counts:

    data: {<same fetch report>, "recompute": {"fdr_v1": 20, "fdr_v2": 20,
           "xp_v1": 90, "xp_v2": 90}, "settlement_written": 610, "data_basis": {...}}

The authed my-team snapshot runs only when the daemon's master key is available
(env `MASTER_PASSWORD`); otherwise a warning is printed and the public path completes.
Use this after a deploy instead of waiting for the hourly job.

### note (speculation insights, v0.26)

    fpl-autopilot note add "newcastle are incredibly good at playing and scoring goals" [--team NEW] [--player Wissa] [--json]
    fpl-autopilot note list [--json]
    fpl-autopilot note rm <id> [--json]

User-curated qualitative knowledge for the AI speculation layer (manager effects,
transfer cohesion, player traits). Agent-safe: writes the local DB only, never FPL.
`--team`/`--player` resolve by short_name/web_name from the DB (club data is never
assumed). `speculate --json` cross-checks each active note against the system's
stats in a `theses` section: {"theses": [{note_id, note, team_short, player_name,
verdict: "matches"|"contradicts"|"neutral", checks: {...}}]} — verdicts are
deterministic code, never the AI.

### leaders (top-100 cohort analytics)

    fpl-autopilot leaders [--refresh] [--json]

Tracks the global top-100 managers' per-GW behavior: chip timing, transfers/hits,
bank & value, rank momentum (deterministic statistics, no AI). `--refresh` pulls a
fresh snapshot (standings + histories, ~102 requests once per settled GW); without
it, reads the stored data. Agent-safe: `--refresh` writes the local DB only, never FPL.
Shape: {"cohort": [...], "patterns": {"chip_timing", "transfers", "bank_value", "momentum"}} —
same shape as GET /api/leaders.

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
