# FPL Autopilot — Session Handoff (2026-05-24)

Resume point for continuing on another machine. Everything below is in git (pushed to
`github.com:shariski/fpl-autopilot`); your local auto-memory and secrets do **not** transfer.

## Where we are

- **Phase 1 (Insight Engine) — COMPLETE.** Data layer (FPL + Understat clients, cache, sqlite),
  Analytics (FDR, xP v1, DGW), Decisions (captain, transfers, chips), Interface (FastAPI + SvelteKit
  PWA), scheduler.
- **Phase 2 (Decision Automation) — auth + execution + routing + Telegram + deadguard (captain/vice) DONE:**
  - 2.1 Auth — **token-capture with OAuth refresh** (see "Auth reality"). `init-master-password`,
    `init-fpl` (paste refresh token), `auth-status`. `src/auth/{crypto,master,session}.py`.
  - 2.2 Action Executor — `src/execution/{executor,lineup,transfer}.py`; CLIs `execute-lineup`,
    `execute-transfer` (dry-run default, `--live` + typed confirm).
  - 2.3 Mode Router — `src/decisions/confidence.py`, `src/execution/router.py`; CLI `route-gameweek`;
    unattended scheduling in `src/scheduler.py` (`auto_execute_job`, `_maybe_load_key`).
  - 2.4a Telegram outbound notifier — `src/interface/telegram.py` (`is_configured`, `send_message`,
    `notify`, `notify_plan`); wired into `auto_execute_job` (post-exec + pending-info + auth alert).
    Env vars `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (silent no-op when unset). Outbound only.
  - 2.4b Telegram interactive confirm — `src/interface/telegram_interactive.py` (`send_pending`,
    `notify_plan`, `poll_once`, `handle_callback`); `pending_decisions` + `telegram_state` tables;
    `get_updates`/`answer_callback_query` transport. One-tap Confirm/Reject via getUpdates poll in the
    daemon; re-run+verify (re-notify if changed); chat whitelist + status-gate + durable offset +
    deadline guard. Opt-in `telegram.interactive` (loads master key + registers `telegram_poll` job).
  - 2.5a Deadguard state machine + captain/vice safety net — `src/interface/deadguard.py` (pure
    `evaluate` + `user_acted`/`send_warning`/`handle_keep`/`run_deadguard_job`); `gameweeks.state`
    machine (PENDING/USER_ACTED/SYSTEM_ACTED/DEADGUARD_ACTIVE/EXECUTED/SKIPPED) + `deadguard_warned_at`
    column. H-120 warning (Keep-as-is button, routed via `poll_once`), H-30 trigger → captain/vice via
    `run_lineup`. USER_ACTED from Keep tap / 2.4b confirm-reject / manual CLI execute. Opt-in
    `deadguard.enabled` (default true) → `deadguard_job`. Captain/vice ONLY (B8).
  - 2.5b Deadguard bench-order + transfer-if-flagged — `decisions/bench.py` (`rank_bench` 13/14/15 by
    xP), `run_lineup(optimize_bench=True)` (captain/vice + bench in one write), `deadguard._pick_flagged_transfer`
    (targeted, free, ep≥3.0, conf≥75, ≤1) wired into `_run_trigger` (lineup → lock → best-effort transfer).
    `deadguard.scope` config accessors. Bench reorder → FPL native auto-sub. B8 (no chips/hits/multi/formation).
  - 2.7 Emergency Override — **persisted freeze / kill-switch + B7 auto-freeze DONE.** New `system_state`
    table; `src/execution/override.py` gate; `auto_execute_job` + `run_deadguard_job` short-circuit when
    frozen (explicit Confirm NOT gated); `ensure_session` counts refresh failures → freeze at 2; Telegram
    🛑/▶️ + CLI `freeze`/`unfreeze`/`freeze-status`. (See "2.7" section below.)
  - 2.5c-1 Deadguard late-news re-evaluation — after DEADGUARD_EXECUTED, `evaluate` returns `reeval`/`lockout`;
    `_run_reevaluate` force-refreshes FPL + re-checks the lineup, auto-applying a material captain/bench change
    (>15 min out) or alert-only in the ≤15-min lockout. Lineup-only (B8). decision-engine.md v0.11. (See "2.5c-1".)
  - 2.5c-2 Deadguard undo (transfer) — deadguard records its transfer; one-tap ↩️ Undo (Telegram `z:` + CLI
    `undo-transfer`) reverses it before the deadline → USER_ACTED; refuses safely if too late / squad changed.
  - 2.5c-3 Dashboard deadguard/freeze banner + controls — `get_status` returns `frozen` + populated `banners`
    (`Header.svelte` renders them + a Freeze/Unfreeze toggle + a warning-window Keep button); POST `/api/freeze`,
    `/api/unfreeze`, `/api/deadguard/keep` (DB-state only, no key); `serve` binds 127.0.0.1 by default; dashboard
    polls `/api/status` (~30s + focus) → multi-device. No dashboard live FPL write (Undo stays Telegram/CLI).
- **PHASE 2 (Decision Automation) — COMPLETE.** **Test suite: 404 pytest + 50 vitest passing**, all merged to
  `main` and pushed to origin.

## Auth reality (don't re-derive this — it cost a lot to find)

Programmatic email+password login is **dead**: `users.premierleague.com` is decommissioned; PL uses
**PingOne (DaVinci) behind Cloudflare**. The working mechanism is **token-capture with refresh**:
- The FPL API authenticates with `X-Api-Authorization: Bearer <access_token>` (RS256 JWT, ~8h TTL).
- Refresh: `POST https://account.premierleague.com/as/token`, form body
  `grant_type=refresh_token&refresh_token=<rt>&client_id=bfcbaf69-aade-4c1b-8f00-c1cb8a193030`
  (client_id ends **a193030**). Refresh token lasts ~180 days; rotates on use. Verified live.
- `init-fpl` pastes the **refresh token** (from DevTools → the `/as/token` response or localStorage).
- **The agent never runs live login/execution (R3).** The user runs `init-*` and any `--live`/daemon.

## Working conventions (followed all session)

- **Superpowers flow per slice:** brainstorming → spec (`docs/superpowers/specs/`) → writing-plans
  (`docs/superpowers/plans/`) → subagent-driven-development (fresh subagent per task, TDD, commit
  each) → final review (opus for security/exec slices) → finishing-a-development-branch.
- **Per-slice branch** `feat/<slice>`; merge to `main` locally; push only when asked.
- **Dry-run first; the agent never executes live writes (R3).** All tests are fixtures-only (fake
  session/ranker/suggester/route_fn; no network/getpass/live).
- **NEVER `git add -A`** here — it sweeps in `.claude/worktrees/` gitlinks. Stage explicit paths.
- **B-rules in `CLAUDE.md` are binding** (B4 decision-engine is "sacred" — document changes there;
  B7 secrets encrypted/never-logged; B8 no auto chips/hits/multi; B11 dry-run first-class).
- Decision logic lives in `docs/decision-engine.md` (changelog up to v0.11).
- A `code-review-graph` MCP is available (use it before Grep per the user's global CLAUDE.md).

## 2.4 Telegram — DONE (a + b; pushed)

Specs/plans under `docs/superpowers/{specs,plans}/2026-05-23-telegram-*`. **2.4a** (outbound): env-var
storage decoupled from the master key, caller-driven from `auto_execute_job` (router stays pure, B2),
post-exec ✅ / pending-info 📊 / auth-failure ❌ + failure-to-send logging. **2.4b** (interactive):
`pending_decisions` + `telegram_state` tables; `telegram_interactive.{send_pending,notify_plan,
poll_once,handle_callback}`; one-tap Confirm/Reject via a `telegram_poll` getUpdates job in the daemon;
**re-run + verify** on confirm (execute only if the recompute still matches what was shown, else
re-notify); chat-id whitelist + status-gate idempotency + durable update offset + deadline guard; B8
keeps it to a single captain/transfer via the existing executors. Opt-in `telegram.interactive`
(default false) → `_maybe_load_key` loads the key + `build_scheduler` registers the poll job. No
`decision-engine.md` change. Both slices reviewed (per-task spec ✅/quality + final opus; review fixes
applied: send_message json-hardening, scheduler notify exception-safety, poll_once poison-loop guard,
no telegram session into FPL executors). 272 tests green.
- **Deferred → 2.4c (future):** the "Modify" button (cycle transfer rank / pick vice) + its stateful
  multi-message flow.

## 2.5 Deadguard — DONE (a + b; 2.5a pushed, 2.5b merged to main locally, not pushed)

Specs/plans under `docs/superpowers/{specs,plans}/2026-05-23-deadguard-*`. **2.5a** (state machine):
pure `evaluate` (system_acted/user_acted/warn/trigger/noop), `user_acted` (Keep tap / 2.4b confirm-reject
/ manual CLI execute), H-120 `send_warning` (Keep button via `poll_once` `k:`), H-30 `_run_trigger` →
captain/vice via `run_lineup` (checks `result.ok`, EXECUTED/SKIPPED/retryable, always notifies);
`deadguard_job` (every 5 min when key + `deadguard.enabled`); `gameweeks.deadguard_warned_at`.
**2.5b** (bench + transfer): `decisions/bench.py` `rank_bench` (13/14/15 by xP → FPL native auto-sub),
`run_lineup(optimize_bench=True)` (captain/vice + bench, one write), `_pick_flagged_transfer` (targeted
at the flagged player, free only, ep≥3.0, conf≥75, ≤1) → best-effort in `_run_trigger` after the lineup
lock. `deadguard.scope` config accessors. decision-engine.md v0.9 + v0.10. Both reviewed (per-task +
final; B8 holds — no chips/hits/multi/formation; bench reorder only touches 13/14/15). 329 tests green.
- **Deferred (deadguard):** `transfer_if_underperform` (sell a healthy underperformer; default off);
  explicit pre-deadline formation-valid starter→bench swaps (rely on FPL native auto-sub instead).

## 2.7 Emergency Override — DONE (merged to main, pushed)

Specs/plans under `docs/superpowers/{specs,plans}/2026-05-23-emergency-override*`. A persisted freeze
(new `system_state` key/value table; row present = frozen, holds `{since, reason, source}`) halts
autonomous FPL writes: `auto_execute_job` (auto mode) and the ENTIRE `run_deadguard_job` short-circuit
when frozen (deadguard **fully dormant** — no H-120 warning, no H-30 trigger, no state change). The user's
explicit Telegram **Confirm** (`handle_callback`) is intentionally NOT gated (autonomous-only). The gate is
`src/execution/override.py` (`is_frozen`/`status`/`freeze`/`unfreeze`/`maybe_auto_freeze`, Data-Layer-only
per B2 — no Telegram import; callers send the copy). **B7 wired:** `ensure_session` increments
`credentials.relogin_failures` on a `TokenRefreshError` (network blips don't count); the orchestrators call
`override.maybe_auto_freeze` → freeze (`source="auto"`) at 2 consecutive failures + alert once; success
resets the counter via `mark_session_ok`. Telegram `f:`/`u:` callbacks: 🛑 Freeze on the deadguard warning
+ auto-mode notice, ▶️ Unfreeze on the freeze confirmation (chat-whitelisted). CLI `freeze`/`unfreeze`/
`freeze-status` (no master password — freeze is plaintext operational state) + `frozen:`/`relogin_failures:`
lines in `auth-status`. No `decision-engine.md` change (execution gate, not decision logic, so B4 untouched);
`deadguard.md` + `runbook.md` updated. Built via 10 TDD subagent tasks + two-stage reviews + a final opus
review (clean: B2 intact, no secrets logged, every autonomous write path gated, confirm-while-frozen
regression-tested). 361 tests green.
- **Deferred (noted in review, non-blocking):** the `_run_trigger` transfer step's `SessionExpired` is
  caught by the generic handler (unreachable for auth failures — the lineup write fails first); a stale
  `DEADGUARD_ACTIVE` can linger after a SessionExpired-aborted trigger (pre-existing from 2.5a, benign —
  not a RESOLVED state, so it re-runs cleanly).

## 2.5c-1 / 2.5c-2 — DONE (merged to main, pushed)

2.5c was decomposed into three sub-slices (late-news re-eval / undo / dashboard). Two are done:
- **2.5c-1 late-news re-evaluation** — specs/plans `docs/superpowers/{specs,plans}/2026-05-23-deadguard-late-news-reeval*`.
  `evaluate` gains `reeval`/`lockout` directives for DEADGUARD_EXECUTED (gated by `reeval_if_late_news`, default on;
  `reeval_lockout_minutes` 15). `_run_reevaluate` force-refreshes FPL bootstrap (cache-bypassed) + recomputes the
  lineup; on a material captain/vice/bench change it auto-applies via `run_lineup` when >15 min out, else alert-only
  (once, via `gameweeks.deadguard_reeval_alerted_at`). Lineup-only (B8); frozen → dormant (2.7). decision-engine.md v0.11.
- **2.5c-2 undo (transfer)** — specs/plans `.../2026-05-24-deadguard-undo-transfer*`. `_run_trigger` records the
  transfer (`gameweeks.deadguard_transfer_json`) + sends a ↩️ Undo button; `transfer.run_undo_transfer` reverses it
  (sell bought, buy back sold; free pre-deadline); `deadguard.run_undo` guards (recorded / not-undone / before-deadline)
  → reverse → `mark_deadguard_transfer_undone` + USER_ACTED + notify; Telegram `z:` handler + CLI `undo-transfer`
  (dry-run/`--live`). Not freeze-gated (user action). No decision-engine.md change. Both reviewed (per-task + final opus).

## 2.5c-3 — DONE (merged to main, pushed)

Specs/plans `docs/superpowers/{specs,plans}/2026-05-24-dashboard-deadguard-controls*`. `get_status` →
`frozen` + `banners` (with optional `action`); POST `/api/freeze`/`/api/unfreeze`/`/api/deadguard/keep`
(DB-state only, no master key, no FPL call); `serve` binds 127.0.0.1 by default (the API now mutates state —
use `--host 0.0.0.0` for LAN). Frontend: `Status.frozen`/`Banner.action?` types, `client.postAction`/`fetchStatus`,
presentational `Header` (`onaction` callback: Freeze/Unfreeze toggle + banner Keep button), `+page.svelte` owns
postAction + ~30s status polling (multi-device). 8 TDD tasks (pytest + vitest), final opus review clean (web
layer holds no key, makes no FPL call). api-contract.md/deadguard.md/runbook.md updated.

## NEXT: live end-to-end test against the real account (then Phase 3)

Phase 2 is feature-complete but has only ever run on fixtures (R3 — the agent never ran live). Before Phase 3,
the USER drives a real-account smoke test (the agent prepares the runbook + watches output, never runs live login/
execution): `init-master-password` → `init-fpl` (paste refresh token) → `auth-status`; then dry-run a
`route-gameweek` / `execute-lineup` / `execute-transfer`, then `--live`; bring up `serve` + the dashboard; exercise
freeze/unfreeze + Keep; (optionally) the daemon's deadguard/telegram loop. `docs/runbook.md` is the operational
guide. Capture any real-API schema drift (B6 schema assertions) or auth-flow surprises.

After the e2e test → **Phase 3 (AI Layer)** — LLM reasoning, mini-league context, personalization, conversational interface.

## Phase 2 status — COMPLETE
- **DONE:** 2.1 auth · 2.2 executor · 2.3 router · 2.4a/b Telegram · 2.5a/b deadguard · 2.7 override · 2.5c-1/2.5c-2/2.5c-3.
- 2.6 Dry-Run — satisfied (every executor + the router is dry-run-first).

## Tech debt / cleanup (small, non-blocking — flagged in 2.5 reviews)
- `src/decisions/bench.py` imports the private `transfers._next_gw` (captain.py does the same) — extract
  to a public Data-Layer helper (e.g. `repository.next_gw`) and update both call sites.
- The executors (`run_lineup`, `run_transfer`) hardcode `mode="manual"` in their internal `log_activity`,
  so a deadguard/auto executor-level log row is mislabeled (the *decision* IS logged correctly with
  `mode="deadguard"`/router mode by the orchestrator's own summary entry). Thread a `mode` param through both.

## Machine setup (Mac mini)
```bash
git clone git@github.com:shariski/fpl-autopilot.git    # or git pull
cd fpl-autopilot
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # python3.14 also works (this machine)
.venv/bin/pytest -q          # expect 404 passed
cd frontend && npm install && npm test   # frontend (vitest): expect 50 passed  (npm install needed once)
```
Local-only (re-create if you want live runs): `data/.salt` + `data/.verify` (run
`init-master-password` then `init-fpl`), and the `~/.claude` auto-memory (this file replaces it for
continuity). `config.yaml` is in git (team_id 3122849, mode: manual, unattended.enabled: false).
