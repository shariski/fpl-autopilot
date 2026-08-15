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
   - On jumbo: `ssh jumbo 'docker compose --project-directory /opt/fpl-autopilot run --rm -T app resume --json'`
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
