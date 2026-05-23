# FPL Autopilot — Session Handoff (2026-05-23)

Resume point for continuing on another machine. Everything below is in git (pushed to
`github.com:shariski/fpl-autopilot`); your local auto-memory and secrets do **not** transfer.

## Where we are

- **Phase 1 (Insight Engine) — COMPLETE.** Data layer (FPL + Understat clients, cache, sqlite),
  Analytics (FDR, xP v1, DGW), Decisions (captain, transfers, chips), Interface (FastAPI + SvelteKit
  PWA), scheduler.
- **Phase 2 (Decision Automation) — auth + execution + routing + Telegram (out + interactive) DONE:**
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
- **Test suite: 272 passing.** `main` has 2.1–2.3 pushed; **2.4a + 2.4b are merged to main locally but
  NOT pushed** (25 commits ahead of origin/main — push when ready).

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
- Decision logic lives in `docs/decision-engine.md` (changelog up to v0.8).
- A `code-review-graph` MCP is available (use it before Grep per the user's global CLAUDE.md).

## 2.4 Telegram — DONE (a + b; merged to main locally, not pushed)

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

## NEXT TASK: 2.5 Deadguard

Conservative fallback for Manual users who go silent before a deadline. See `docs/deadguard.md` for the
full state machine and edge cases. Scope (CLAUDE.md B8): the full per-GW `gameweeks.state` machine
(PENDING / USER_ACTED / DEADGUARD_ACTIVE — the `state`, `last_user_action_at`, `deadguard_triggered_at`
columns already exist), warning windows, USER_ACTED detection, and the narrow deadguard action set
(captain/vice + bench order + auto-sub definite-non-players always; a single free transfer if
configured and EP delta is high; **never** hits, chips, multi, or wildcard-level rebuilds). Config
already present in `config.yaml` under `deadguard:`. To resume: re-enter brainstorming for 2.5, then
spec → plan → subagent-driven, exactly as the prior slices.

Useful seams already built: the router emits a `plan` with `route`/`summary`/`executed`/`identity`;
`pending_decisions` + the Telegram interactive loop exist (deadguard warnings could reuse them);
`auto_execute_job` is the deadline-window daemon entry point.

## Remaining Phase 2 after 2.5
- **2.7 Emergency Override** — kill switch / freeze auto-execution (also where B7's "freeze after
  repeated re-login failure" belongs; 2.4a only *alerts* at the existing `SessionExpired` point).
- (2.6 Dry-Run is effectively satisfied — every executor + the router is dry-run-first.)

## Machine setup (Mac mini)
```bash
git clone git@github.com:shariski/fpl-autopilot.git    # or git pull
cd fpl-autopilot
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # python3.14 also works (this machine)
.venv/bin/pytest -q          # expect 272 passed
```
Local-only (re-create if you want live runs): `data/.salt` + `data/.verify` (run
`init-master-password` then `init-fpl`), and the `~/.claude` auto-memory (this file replaces it for
continuity). `config.yaml` is in git (team_id 3122849, mode: manual, unattended.enabled: false).
