# CLAUDE.md

Behavioral guidelines for Claude Code when working on **FPL Autopilot**.

This file merges two layers:

- **Part A** — universal LLM coding principles (from Andrej Karpathy's observations, via multica-ai/andrej-karpathy-skills)
- **Part B** — project-specific rules for FPL Autopilot (personal Fantasy Premier League assistant)

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks (typos, obvious one-liners), use judgment — not every change needs the full rigor.

---

# PART A — Universal Principles

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

# PART B — FPL Autopilot Project Rules

## B1. What this project is

A personal Fantasy Premier League assistant. Single-user, self-hosted. Built in three phases:

1. **Insight Engine** — read-only analytics (xP model, FDR, captain & transfer suggestions, fixture planner).
2. **Decision Automation** — authenticated execution with Auto / Manual / Hybrid modes, Telegram one-tap confirmations, and a deadguard fallback.
3. **AI Layer** — LLM reasoning, mini-league context, personalization, conversational interface.

The animating problem is the user's history of abandoning FPL mid-season. Every design decision is judged against: "does this reduce the friction that causes mid-season drop-off?"

## B2. Architecture boundaries

The system is structured as four layers. Code should respect these boundaries:

```
Data Layer       — FPL API client, supplementary data scrapers, cache, DB
   ↓
Analytics Engine — FDR computation, xP model, form metrics
   ↓
Decision Layer   — captain ranker, transfer engine, chip recommender, mode router
   ↓
Interface        — dashboard (PWA), Telegram bot, scheduled jobs
```

Rules:

- Analytics never calls the FPL API directly. It reads from the Data Layer only.
- The Decision Layer never scrapes or queries external sources. It consumes Analytics output.
- The Interface never computes — it only displays, notifies, and accepts user input.
- Auto-execution (Phase 2) flows: Scheduler → Decision Layer → FPL API client → log.

## B3. Scope discipline

Three things are explicitly **out of scope** at every phase unless the doc says otherwise:

- **Social / multi-user.** Single-tenant, single-account.
- **Live in-match tracking.** FPL's own site does this.
- **Auto-execution of chips.** Chips (Wildcard, Free Hit, Bench Boost, Triple Captain) always require user confirmation. This rule holds in Auto mode and in deadguard.

If a feature request touches any of these, stop and confirm with the user before implementing.

## B4. The decision engine is sacred

The decision logic in `docs/decision-engine.md` is the core of the product. It is the only thing that distinguishes this tool from a generic dashboard.

When changing decision logic:

- Document the change in `docs/decision-engine.md` first, then implement.
- Never change thresholds (EP gain, confidence floor, hit calculator cutoffs) without an entry in the activity log explaining why.
- Decision outputs must always be inspectable: every recommendation logs its inputs (xP, FDR, alternatives considered).

## B5. The xP model is iterative

The Expected Points model in Phase 1 starts simple (`xMinutes × baseline + xGoals × position_weight + xAssists × 3 + xCleanSheet × position_weight`). It will be refined.

Rules:

- Never silently change the xP formula. Version it: `xp_v1`, `xp_v2`, etc. Store the version with every recommendation.
- When upgrading, run both versions in parallel for one full gameweek and compare predictions vs actual.
- Bonus points (`xBonus`) are deferred — proxy with BPS history only when the rest of the model is stable.

## B6. FPL API is unofficial; treat it that way

The FPL API has no public documentation and no stability guarantee.

- All API calls go through a single client module with retry, backoff, and schema assertions.
- Schema assertions fail loudly. A silent schema drift is worse than a crash, because it could feed bad data into auto-execution.
- Cache aggressively: `bootstrap-static` updates roughly once per gameweek, fixtures rarely change. Respect this.
- Rate limit: no documented limit, but keep requests ≤ 1 per second.
- User-Agent must be realistic. Default Python `requests` UA is a flag.

## B7. Auth and credentials (Phase 2 onward)

Auto-execution requires holding the user's FPL session.

- Credentials are encrypted at rest with a key derived from a master password (Argon2 or scrypt).
- Session cookies are encrypted the same way.
- Never log credentials. Never log full cookies. Truncate or redact in all log output.
- Session expiry → mark expired, re-login on next scheduled action.
- If re-login fails twice in a row, alert the user and freeze auto-execution.

## B8. Deadguard is a fallback, not a primary mode

The deadguard layer (Phase 2) is the safety net that activates when the user goes silent before a deadline. Its scope is deliberately narrower than full Auto mode.

Deadguard rules:

- **Always allowed:** captain & vice, bench order, auto-substituting players who definitely will not play (flagged out, suspended, removed from squad).
- **Allowed if configured:** a single free transfer if a player is flagged out or there is an obvious upgrade (EP delta above a high threshold, default 3+ over horizon).
- **Never:** taking a hit (-4 or worse), activating any chip, multiple transfers, wildcard-level rebuilds.

The deadguard does not exist to win the gameweek. It exists to keep the user from falling out of the league while not paying attention.

See `docs/deadguard.md` for full state machine and edge cases.

## B9. Notifications are the product

The Telegram bot is not a side channel. For Manual and Hybrid modes, it is the primary user interface during a gameweek.

- Inline buttons must allow one-tap confirm / reject / modify.
- Post-execution notifications are mandatory: the user must always know what changed and why.
- Notification copy is functional, not chatty. State the action, the reason, the impact. No emojis except as functional icons (✅ confirm, ❌ reject, 📊 details).
- Failure to send a notification is itself a logged event.

## B10. Logging discipline

Every decision (auto or manual, executed or skipped) generates a structured log entry containing:

- Timestamp (UTC and local)
- Decision type (captain / transfer / bench / chip / deadguard)
- Mode at the time of decision (auto / manual / hybrid / deadguard)
- Inputs (xP values, FDR, alternatives considered, confidence score)
- Action taken (or skipped, with reason)
- Outcome (filled in after the gameweek settles: actual points vs alternative)

Logs are append-only. The activity log is what allows Phase 3 to personalize, and what allows the user to audit auto decisions.

## B11. Testing rules for decision code

The decision engine touches a real game with a real season. Bugs are expensive.

- Every decision function has a deterministic test with frozen inputs.
- The transfer engine has property tests for the 3-per-club rule, budget constraints, and valid squad structure.
- Dry-run mode is part of the product, not just a test harness. Phase 2 ships with the ability to run the full decision flow without executing.
- Before enabling Auto mode for the first time, run dry-run for at least 3 consecutive gameweeks and compare to the user's manual choices.

## B12. Challenge and clarify

When the user asks for something that conflicts with the rules in this document, or when a request would touch the decision engine without an explicit doc update, stop and ask.

Examples:

- "Add a feature to let the bot send funny commentary after each match" → out of scope per B3, surface this.
- "Change the EP threshold for auto-transfer from 2 to 1" → requires a `decision-engine.md` update first per B4, ask for that.
- "Let deadguard activate a wildcard if the squad is broken" → forbidden by B8, push back hard.

## B13. Documentation lives in `docs/`

The `docs/` folder is the source of truth. README is the public-facing summary. CLAUDE.md is the working contract. Everything else lives in `docs/`.

When implementing a feature, the doc is updated first. When a doc and the code disagree, the doc is treated as correct until explicitly changed.

## B14. Definition of done (per task)

A task is done when:

- The code implements what the doc says.
- Tests pass for the code that was changed.
- The activity log captures whatever the new code does, in the same format as existing log entries.
- Manual smoke check: run the relevant scheduled job or UI flow once and confirm output.

Not "the linter passes." Not "I think it works."

---

## Working pattern per task

1. Read the relevant section in `docs/` first.
2. State the assumption / interpretation explicitly.
3. Implement the minimum that satisfies the doc.
4. Verify against the test or smoke check.
5. If the doc was wrong or incomplete, update the doc as part of the same change.
