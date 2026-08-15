# Agent-Operable CLI Contract + Context Planting — design

The tool becomes **agent-operable**: a documented CLI contract (`fpl-autopilot <cmd> --json`)
that any AI agent (Claude/Codex/Copilot/Gemini/future) can operate, so agents are swappable
and the agent orchestrates the weekly loop: **refresh → read → judge → human runs `--live`**.
In-app AI (insight/speculation) stays as optional display.

**Status:** approved 2026-08-15 (brainstorming).
**Resolves:** the 2026-08-14 handoff's "CLI-first, agent-operable contract" direction —
concrete command surface, agent contract doc, session continuity, and context planting for
any agent family. B13: docs are written first and are the truth.
**Builds on:** `docs/runbook.md` (appendices already document `status`/`log` — currently
aspirational; this spec makes them real), `docs/api-contract.md` (dashboard API, reused via
`src/interface/queries.py`), `docs/decision-engine.md` (decision outputs must be inspectable,
B4), Phase-2 executor patterns (`execute-transfer`/`apply-squad` typed-confirm flow).

## Why

The user's vision: agents are swappable orchestrators with reasoning + tool call + web search.
The human talks to the agent from anywhere (road included); the agent reads decision-grade
data from the tool and proposes; the human drives every FPL write. For that to work across
agent families, the surface an agent drives must be: one documented contract, zero secrets,
dry-run by default, and no way for an agent to pass `--live`.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Transport | **As-is.** No wrapper, no Makefile, no host install, no REST/MCP layer. The agent drives the CLI as it exists today: on jumbo via `ssh jumbo 'docker compose run --rm app <cmd> --json'`, locally via the venv for dev. The runbook documents the exact one-liners. |
| MCP server | **Deferred, documented** in `docs/agent-contract.md` with revisit criteria (a client that cannot shell/SSH; tool-call ergonomics pain; a dashboard-side consumer needing the same surface). |
| Contract surface | CLI subcommands with `--json`: `status`, `resume`, `log` (new) + `--json` on `captain`, `transfers`, `chips`, `squad` (with `--candidates`), `insight <id>`, `speculate`, `refresh`, `freeze-status`, `auth-status` (thin wrappers over existing query/decision/AI modules). |
| Envelope | Every `--json` response is one JSON object on stdout: `{ok, contract_version, command, generated_at_utc, data}` or `{ok: false, command, error: {code, message, hint}}`. Nothing else on stdout; no logs. |
| Exit codes | 0 ok · 1 runtime/data error · 2 usage error. Errors are JSON on stdout. |
| Dry-run | All contract commands are read-only or `refresh` (FPL reads + local DB writes). Decision payloads carry a `data_basis` block (as-of freshness, xp model version — B5), never a hard freshness gate. Writes are human-only, never in the contract. |
| R3 hardening | `--live` refuses when stdin is not a TTY (`execute-lineup`, `execute-transfer`, `apply-squad`, `route-gameweek`, `undo-transfer`). Non-interactive SSH / agent sessions physically cannot pass it. Typed confirm unchanged. |
| status/resume/log | Pure DB reads over `activity_log`, `pending_decisions`, `system_state`, `gameweeks`, `cache_meta`, `credentials` (auth state only — never secrets). `resume` = `status` + activity tail + pending decisions + a self-contained operating-rules block (R3, read-safe vs human-only lists, boot ritual) so an SSH-only agent never needs to fetch docs. |
| AGENTS.md | New repo-root `AGENTS.md` = universal auto-load for any agent family: B-rules (moved from CLAUDE.md, single source per B13), architecture map, where things run, how to drive the CLI, test commands, working conventions. |
| CLAUDE.md | Keeps Part A (Claude-family behavioral principles) + a pointer to AGENTS.md. Part B (B-rules) and the working pattern move to AGENTS.md. |
| Skill | `skills/fpl-agent/SKILL.md` (repo, superpowers-style): boot ritual (read AGENTS.md → run `resume` → note mode/frozen/freshness/deadline) + operating rules. Install = one documented symlink per agent dir (`~/.claude/skills`, `~/.config/opencode/skills`). |
| Out of scope | Auto-execution beyond current rules (B3/B8), Telegram one-tap apply-confirm, wildcard-aware rebuild, speculation quality feedback loop, MCP server, in-app AI changes. |

## Operating model (the loop, documented in agent-contract.md)

```
agent session start
   │
   ▼
resume --json          boot: state, freshness, next GW + deadline, rules
   │
   ▼
refresh --json         pull FPL/Understat (agent-safe: reads + local DB writes)
   │
   ▼
captain/transfers/squad/candidates/insight/speculate --json
                       decision-grade inputs (data_basis included)
   │
   ▼
agent reasons (own tools: web search, etc.) → proposes plan
   │
   ▼
human runs the write   apply-squad --live / execute-captain --live / ...
   │                   (CLI, TTY-gated + typed confirm; never the agent)
   ▼
log --json             post-execution audit trail (B10)
```

Agent-safe (read + refresh): `status, resume, log, captain, transfers, chips, squad,
squad --candidates, insight, speculate, refresh, freeze-status, auth-status, review`.
Human-only (writes or secrets): `execute-lineup, execute-transfer, apply-squad,
route-gameweek, undo-transfer, refresh-my-team, init-master-password, init-fpl, freeze,
unfreeze, serve, scheduler`.

## Architecture

```
fpl-autopilot <cmd> --json        (src/cli.py — argparse)
   │
   ├─ status / resume / log       new queries over activity_log, pending_decisions,
   │                              system_state, gameweeks, cache_meta, credentials
   ├─ captain / transfers / chips  reuse src/interface/queries.py getters (same shapes
   │                              as the dashboard API — one read-model, two surfaces)
   ├─ squad / squad --candidates   reuse src/ai/squad runner + build_candidate_pool (same
   │                              cache-first behavior as /api/squad/builder)
   ├─ insight                     reuse src/ai/insight runner (same cache-first
   │                              behavior as /api/players/{id}/insight)
   ├─ speculate                   reuse src/ai/squad/spikes.generate_spike_signals
   │                              + differentials derivation (same cache-first/gated
   │                              behavior as /api/squad/builder's speculation section)
   └─ refresh --json              reuse existing refresh; emit counts + freshness
```

No new modules or layers. `--json` serializers live beside the existing commands; the
envelope helper (`emit_json(ok=..., data=...)`) is one shared function.

### status — one-call bootstrap
mode (config) · frozen? · FPL auth state (active/expired — state only, B7) · data freshness
per resource (cache_meta: bootstrap-static, fixtures, understat, my_team) · next GW id +
deadline + hours-until · pending decisions count + summaries · last system actions ·
health flags (DB readable, schema sane).

### resume — session continuity
Everything in `status` + activity-log tail (last 10, default) + pending decisions +
operating-rules block. First call of any agent session.

### log — filterable audit tail
`--tail N`, `--gw N`, `--mode <mode>`, `--decision-type <type>` (runbook appendix already
documents these flags). Entries: ts_utc, gw, mode, decision_type, action_taken, executed,
outcome.

## Deliverables

**Docs (doc-first, B13):**
1. `docs/agent-contract.md` — the contract: command list, JSON shapes per command, envelope,
   exit codes, error codes (E_USAGE / E_RUNTIME / E_NO_DATA), dry-run semantics, R3 rules,
   data_basis, read-safe vs human-only lists, operating loop, the jumbo one-liner
   (`ssh jumbo 'docker compose run --rm app <cmd> --json'`), MCP deferral + revisit criteria.
2. `AGENTS.md` (repo root) — B-rules moved from CLAUDE.md + architecture + where things run
   + how to drive the CLI + test commands + working conventions.
3. `CLAUDE.md` — Part A stays; Part B replaced by a pointer to AGENTS.md.
4. `docs/runbook.md` — new "Agent operating notes" section: read-safe vs human-only, R3,
   boot ritual, the exact jumbo one-liners (already-working commands today), stale-data
   handling (run refresh).
5. `skills/fpl-agent/SKILL.md` — boot ritual + operating rules + install instructions.

**Code:**
6. `status`, `resume`, `log` subcommands (pure DB reads) + tests.
7. `--json` on `captain`, `transfers`, `chips`, `squad` (+`--candidates`), `insight <id>`,
   `speculate`, `refresh`, `freeze-status`, `auth-status` + tests.
8. `--live` non-TTY gate (shared helper) + tests.

## Testing (B11 + house rule)

- Deterministic frozen-input test per `--json` command: JSON shape + exit code.
- R3 gate test: `--live` with non-TTY stdin refuses (exit 2, JSON error), TTY passes.
- B7 test: no credential material (email, tokens, cookies, master key) in any contract output.
- status/resume/log query tests (freshness, pending decisions, activity tail, filters).
- Envelope invariants: stdout is exactly one JSON object; `ok` matches exit code.
- **Never commit without the full suite green** (693 pytest + 71 vitest).

## Commit order

1. Docs: `agent-contract.md`, `AGENTS.md`, `CLAUDE.md` pointer, runbook "Agent operating
   notes" (doc-first; the contract doc is the truth the code implements).
2. `status` / `resume` / `log` + tests.
3. `--json` wrappers + tests.
4. `--live` TTY gate + tests; `skills/fpl-agent/SKILL.md` (references the now-real commands).
5. Full suite green → merge to main (CI auto-deploys to jumbo).
