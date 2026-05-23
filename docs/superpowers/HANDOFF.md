# FPL Autopilot — Session Handoff (2026-05-23)

Resume point for continuing on another machine. Everything below is in git (pushed to
`github.com:shariski/fpl-autopilot`); your local auto-memory and secrets do **not** transfer.

## Where we are

- **Phase 1 (Insight Engine) — COMPLETE.** Data layer (FPL + Understat clients, cache, sqlite),
  Analytics (FDR, xP v1, DGW), Decisions (captain, transfers, chips), Interface (FastAPI + SvelteKit
  PWA), scheduler.
- **Phase 2 (Decision Automation) — auth + execution + routing DONE, all merged & pushed:**
  - 2.1 Auth — **token-capture with OAuth refresh** (see "Auth reality"). `init-master-password`,
    `init-fpl` (paste refresh token), `auth-status`. `src/auth/{crypto,master,session}.py`.
  - 2.2 Action Executor — `src/execution/{executor,lineup,transfer}.py`; CLIs `execute-lineup`,
    `execute-transfer` (dry-run default, `--live` + typed confirm).
  - 2.3 Mode Router — `src/decisions/confidence.py`, `src/execution/router.py`; CLI `route-gameweek`;
    unattended scheduling in `src/scheduler.py` (`auto_execute_job`, `_maybe_load_key`).
- **Test suite: 212 passing.** `main` is pushed to origin.

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

## NEXT TASK: 2.4 Telegram (brainstorm in progress — resume here)

We were in the **brainstorming** skill for 2.4 (B9: "notifications are the product"). Scope decision
reached: **decompose into 2.4a + 2.4b**, brainstorm **2.4a first**:
- **2.4a — Outbound notifier:** a Telegram send client (`sendMessage` + inline keyboard via
  `api.telegram.org`) + the **mandatory post-execution notifications** (when the router/executor
  acts) + informational "pending decision" messages + **failure-to-send logging** (B9). Outbound
  only; mockable; no inbound callbacks yet.
- **2.4b — Interactive confirm:** inline confirm/reject/modify buttons + inbound callback handling
  (long-poll `getUpdates`) + confirm→execute wiring (the async one-tap loop; B9 primary interface).

**Open questions for 2.4a** (ask one at a time, then spec→plan→subagent):
1. **Token/chat storage** — recommend **env vars** (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), NOT
   encrypted-at-rest: notifications (esp. failure alerts) must work without the master key loaded, so
   keep them decoupled from it.
2. Which notifications in 2.4a (recommend: post-exec + pending-info + failure logging).
3. Wiring: a `src/interface/telegram.py` `notify(...)` that is a **no-op when `TELEGRAM_BOT_TOKEN`
   is unset** (so existing router tests + dry-run don't try to send); `route_gameweek` calls it
   best-effort after execute/notify routes. Failure-to-send → logged (B9), never raised.

To resume: re-enter the brainstorming skill for 2.4a, confirm the split above, ask the token-storage
question, then proceed spec → plan → subagent-driven, exactly as the prior slices.

## Remaining Phase 2 after 2.4
- **2.5 Deadguard** — conservative fallback for Manual users who go silent: the full `gameweeks.state`
  machine (PENDING/USER_ACTED/DEADGUARD_ACTIVE), warning windows, narrow scope (`docs/deadguard.md`).
- **2.7 Emergency Override** — kill switch / freeze auto-execution.
- (2.6 Dry-Run is effectively satisfied — every executor + the router is dry-run-first.)

## Machine setup (Mac mini)
```bash
git clone git@github.com:shariski/fpl-autopilot.git    # or git pull
cd fpl-autopilot
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q          # expect 212 passed
```
Local-only (re-create if you want live runs): `data/.salt` + `data/.verify` (run
`init-master-password` then `init-fpl`), and the `~/.claude` auto-memory (this file replaces it for
continuity). `config.yaml` is in git (team_id 3122849, mode: manual, unattended.enabled: false).
