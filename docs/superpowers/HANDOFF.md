# FPL Autopilot — Session Handoff (2026-08-14)

Resume point for the next session. Everything below is in git (pushed to
`github.com:shariski/fpl-autopilot`, auto-deployed to the VPS `jumbo` by CI on
push to main). This handoff supersedes the 2026-05-24 one.

## Where we are

- **Phase 1 + Phase 2: complete and live.** Phase 3 (AI layer) is partially built.
- **Season 26/27 rollover: done.** Pre-season GW1 (deadline 2026-08-21), team 3122849
  registered but **no squad picked yet** (user must save their GW1 squad; hourly refresh
  will then snapshot it automatically).
- **Deployed:** container on `jumbo` runs `fpl-autopilot serve` (FastAPI + SPA + scheduler),
  healthy, `DEEPSEEK_API_KEY` set in `.env` on the host. Dashboard: `https://server1.taila3964c.ts.net`
  (tailnet only). CI: push to main → GHCR build → auto pull+restart on jumbo.
- **Tests:** 693 pytest + 71 vitest green. Full suite: `.venv/bin/pytest -q`, `cd frontend && npm test`.

## What was built today (2026-08-14)

1. **Season rollover fixes** (live-tested): pre-season GW resolution (`is_next=1`), picks-404
   skip, prior-season understat **rematch** to new player ids (348 rows), stale
   `player_gw_stats`/`my_team` purge, stale xp-row purge. All in `src/cli.py` refresh path.
2. **DeepSeek provider** (`src/ai/provider.py`): `DeepSeekProvider` via OpenAI SDK,
   `build_provider(cfg, conn)` factory, `DEEPSEEK_API_KEY` env var, `ai.provider: deepseek`
   default. ClaudeProvider dropped. Resolves risks.md D2.
3. **Player insight deep-dive** (`src/ai/insight/`): digest → analysis prompt → grounded
   JSON → cache. Endpoint `GET /api/players/{id}/insight`, page `/players/[id]`.
4. **AI squad builder + apply** (`src/ai/squad/`, `src/decisions/squad_validator.py`,
   `src/execution/squad.py`): deterministic candidate pool → **AI speculation layer**
   (spike/drop signals, edge-data gate) → budget-aware optimizer (bonus constants
   `SPIKE_BONUS`/`DROP_BONUS` in decision-engine.md §S-B) → `apply-squad` CLI (dry-run
   default, `--live` typed confirm). Page `/squad-builder`.
5. **SPA fallback** (200.html) for client-side routes; empty-squad state; dark-theme fixes.
6. **Ops:** kerf's stale GHCR credential removed from jumbo (anonymous pulls work);
   insight + speculation verified live on jumbo.

## The new direction (user's vision — brainstorm + spec in the next session)

**CLI-first, agent-operable contract.** The project's big concept: the CLI is the
agent interface — a well-defined, documented surface (`fpl-autopilot <cmd> --json`)
that any AI agent can operate, so the user can swap agents freely (Claude/DeepSeek/future)
and let the agent orchestrate: refresh → read outputs → judge → `apply-squad --live`
(human confirms). In-app AI (insight/speculation) becomes optional display; the
decision-grade reasoning should be able to live agent-side.

User approved options **1 + 2**: CLI-first data surface **and** keep the in-app AI.

**Concrete gaps to close (brainstorm these):**
- Data-side commands with `--json`: `squad --candidates --json` (pool + projections),
  `squad --json` (built squad incl. speculation), `insight <player> --json`,
  `speculate --json` (spike/drop signals), `captain --json`, `transfers --json`.
- An **agent contract doc** (`docs/agent-contract.md`): every command, JSON shape,
  exit codes, dry-run semantics, R3 rules (agent never runs `--live`; user drives).
- Possibly an MCP server (kerf's "BYO-LLM via MCP" tier) later — defer.
- In-app AI stays (display), flagged as optional.

**Out of scope until discussed:** auto-execution beyond current rules (B3/B8), Telegram
one-tap apply-confirm, wildcard-aware rebuild, speculation quality feedback loop.

## Working conventions (binding)

- **B-rules in CLAUDE.md** (B4 decision-engine sacred — doc before changing thresholds;
  B7 no secrets logged; B8 deadguard scope; B11 dry-run first-class; B13 docs = truth).
- Superpowers flow: brainstorm → spec (`docs/superpowers/specs/`) → plan
  (`docs/superpowers/plans/`) → TDD tasks, commit each → merge to main when green.
- **Never commit without running the full suite first** (this was violated twice today —
  enforce it).
- **Never `git add -A`** (worktree gitlinks). Stage explicit paths.
- The agent (this project's assistant) never runs `--live` FPL writes; the user does.
- Dry-run is the default everywhere.

## Pending user-side items (before GW1)

1. Save the GW1 squad on fantasy.premierleague.com (tool then snapshots it hourly).
2. Optional: review `/squad-builder`, run `apply-squad --live` on jumbo to auto-apply
   the AI squad (pre-season = unlimited transfers).
3. FPL team strengths are 0 pre-season → FDR is flat (documented caveat, decision-engine.md);
   self-corrects near GW1.
