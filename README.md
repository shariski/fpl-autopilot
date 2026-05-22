# FPL Autopilot

**Personal Fantasy Premier League assistant for someone who keeps quitting mid-season.**

A self-hosted tool that handles the boring parts of FPL (captain, bench, transfers, deadline reminders) so the user doesn't have to. Combines an analytics engine, a decision automation layer with switchable Auto / Manual / Hybrid modes, and a deadguard fallback that takes over if the user goes silent before a deadline.

## The problem

The user has followed FPL for two seasons. The pattern is the same each year: strong start, then attention drifts mid-season, gameweeks get skipped, and the team is abandoned by GW20-something. The bottleneck is not strategy. It is friction and forgetfulness. The tool exists to lower friction enough that the user stays engaged through GW38.

## What it does

Three layers, built in three phases.

**Phase 1 — Insight Engine (read-only).** Pulls data from the FPL API plus supplementary xG / xA data, computes a custom Fixture Difficulty Rating, runs an Expected Points (xP) model, and surfaces captain picks, transfer suggestions, fixture swings, and chip opportunities through a dashboard.

**Phase 2 — Decision Automation.** Adds authenticated session handling so the system can execute changes on the user's behalf. Three modes (Auto / Manual / Hybrid), one-tap confirmation via Telegram, a deadguard layer that kicks in if no user action has occurred in a configurable pre-deadline window, and a dry-run mode for trust-building before going live.

**Phase 3 — AI Layer.** Adds LLM-based reasoning for natural-language explanations, mini-league context awareness (differential vs template strategy), personalization based on user history, and a conversational interface.

## What it deliberately does not do

- No social features, league chat, or compare-with-friends.
- No native mobile app. PWA only.
- No multi-user support. This is a personal tool, single-tenant.
- No live in-match tracking. FPL official already does this.
- No auto-execution of chips. Chips always require user confirmation, in all modes including deadguard.

These are scope decisions, not "coming later."

## Stack

- **Backend:** Python (FastAPI), single process
- **Storage:** SQLite, single file
- **Frontend:** SvelteKit PWA
- **Notifications:** Telegram bot (inline-button confirmations)
- **Scheduler:** APScheduler with persistent job store
- **Hosting:** TBD (VPS / home server / hybrid) — decision deferred
- **Supplementary data:** Understat / FBref for xG / xA

The full reasoning behind these choices lives in `docs/architecture.md`.

## Status

Pre-build. See [`docs/plan.md`](docs/plan.md) for the task list and [`docs/risks.md`](docs/risks.md) for known unknowns.

## License

Personal project. No license granted.
