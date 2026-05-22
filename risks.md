# Risks & Open Questions

Known unknowns, unverified assumptions, and deferred decisions. These exist as a register so they don't get lost.

Format:
- **R**isks — things that could break the system or assumptions that may be wrong.
- **D**eferred — decisions intentionally not made yet.
- **Q**uestions — clarifications needed before specific tasks proceed.

---

## R1 — FPL API schema stability across the season

The FPL API is unofficial. Endpoints occasionally change between seasons and rarely mid-season.

**Mitigation in current plan:** schema-assertion tests on `bootstrap-static` (in `plan.md` Phase 1.1). Tests run on every refresh and fail loudly.

**What could still go wrong:** mid-season subtle additions (new fields) that the system ignores correctly, vs subtle changes (field renamed, type changed) that break silently. Schema tests catch the latter only if the assertions are tight enough.

**Action:** when writing schema tests, be specific about field types and presence, not just "field exists."

---

## R2 — Understat / FBref scraping reliability

xG and xA come from Understat (or FBref as backup). Both are scraped, not API'd. Both can break without notice.

**Mitigation:** fail gracefully when scraping fails. Use last known data with a staleness flag. xP confidence drops accordingly.

**What could still go wrong:** silent partial failure — e.g., scraper returns data for 80% of players, leaving 20% with stale stats. xP for those 20% will be wrong but the system won't know.

**Action:** add a per-player freshness check. If a player's stats haven't updated in 14 days but they've played in the last 14 days, log a warning and downgrade confidence for any decision involving that player.

---

## R3 — Auto-execution legality / account flag risk

The FPL Terms of Service do not explicitly forbid automated play, but they also don't sanction it. Holding a session cookie and POSTing transfers programmatically is in a gray zone.

**Worst case:** account temporarily restricted or banned mid-season. Probability is low, but not zero.

**What is in the user's control:**
- Keep request volume modest. Human-like cadence, no bursts.
- Use a realistic User-Agent.
- Don't share the session cookie across IPs.

**What is not:** if FPL decides to crack down, no amount of mitigation helps.

**Decision needed before Phase 2.2:** is the user willing to accept this risk? If not, Phase 2 should stop at "draft transfer + push notification with one-tap confirm" rather than fully auto-executing. The user has not yet confirmed risk tolerance here.

---

## R4 — xP model v1 accuracy

The Phase 1 xP model is the simplest version that captures the main signals. It is not validated.

Known limitations:
- No xBonus modeling.
- Set-piece taker assumptions absent (a player on set pieces gets significantly more xG).
- Penalty taker not modeled.
- No team-form context.
- No "in form" / "out of form" team-level adjustment.

**Action:** after one full month of live data, compare xP_v1 predictions against actual points. If RMSE is high, define xP_v2 with adjustments.

---

## R5 — Session cookie longevity

Working assumption: FPL session cookies last weeks. Source: anecdotal community knowledge, not measured.

**What happens if shorter:** every couple days a re-login is needed. The system handles this automatically, but increases the surface area for "login is rate-limited / blocked" failures.

**Action:** in Phase 2.1, instrument session age. Log how long cookies actually last across multiple refresh cycles. Adjust expectations.

---

## R6 — Deadguard edge cases

The deadguard layer has several edge cases (`docs/deadguard.md` enumerates them). Even with the enumeration, real-world conditions can produce combinations not anticipated.

Highest concern:
- Late-news race condition: lineup leak arrives between deadguard execution and deadline.
- Partial failure: captain set, transfer fails. State is now neither clean DEADGUARD_EXECUTED nor PENDING.
- Multiple devices: user thinks they acted on phone but the network call failed; backend state still PENDING.

**Mitigation:** structured logging, backend-as-source-of-truth, exhaustive tests for the state machine before live use.

**What could still go wrong:** an edge case discovered only by running deadguard for several real gameweeks. This is why dry-run mode for 3 GWs is part of the Phase 2 done criteria.

---

## R7 — Notification reliability

Telegram is the primary channel. Telegram has had outages. The user could lose phone signal at H-2 hours.

**Mitigation already in plan:** fall back to email if Telegram fails.

**What is not yet decided:** how aggressive should the failover be? If Telegram is slow but not down (notif takes 30 min to arrive), should the system also email? Or wait?

**Action:** for Phase 2.4, define a timeout (e.g., 5 minutes) after which a Telegram notif is considered "failed" and a fallback fires.

---

## D1 — Frontend framework ✅ RESOLVED

**Resolved:** SvelteKit. Reason: terse syntax produces cleaner output when building with AI assistance, and PWA support is straightforward.

The decision is captured here and reflected in `architecture.md`. Phase 1.4 task "Choose frontend framework" is closed.

---

## D2 — LLM choice for Phase 3

Claude API vs local LLM (e.g., Llama 3.1, Qwen 2.5). Earlier project considerations (separate context) leaned toward local for personal projects, but the user has not yet stated a preference here.

**Decision needed by:** Phase 3 start. Not relevant until Phase 2 is operational.

---

## D3 — Hosting

Three options on the table:

- **VPS (~$5/mo):** predictable uptime, accessible from anywhere, requires SSH discipline.
- **Home server:** free, full control, requires reliable home network for deadline-critical jobs.
- **Hybrid:** home as primary with VPS as failover. More complex to operate.

**Deferred to:** before Phase 1 deployment. Not blocking Phase 1 development — the codebase runs identically on any of the three.

---

## D4 — Wildcard auto-rebuild support

Out of scope per `CLAUDE.md` B3 and `docs/deadguard.md`. May reconsider after Phase 3.

**Note:** the user has said chips always require confirmation. This includes Wildcard. But "use Wildcard" and "Wildcard rebuild" are two decisions — the activation, and the 11-transfer rebuild. Even if Wildcard activation requires user input, the system could prepare a recommended squad. This is deferred.

---

## D5 — Multi-season support

Currently designed for a single season. Season rollover (squad reset, prices reset, new player IDs) is not specifically handled.

**Deferred to:** end of first full season usage. By then, the system has been through one rollover and the edge cases are known.

---

## Q1 — User risk tolerance for auto-execution ✅ RESOLVED

See R3. The user has confirmed they accept the risk of fully auto-executing transfers via session cookie. Phase 2.2 implements the full Action Executor as originally planned.

**Resolved:** user accepts R3 risk. Phase 2.2 proceeds with full auto-execution. R3 remains an open risk to monitor, but is not a blocker.

---

## Q2 — Mini-league context (Phase 3)

If mini-league strategy is added in Phase 3, the user's mini-league has not yet been specified. League ID is needed at that point.

**Needs resolution before:** Phase 3.

---

## Q3 — Telegram chat ID

The Telegram bot needs the user's chat ID to send notifications. Setup process is:

1. User creates bot via BotFather.
2. User sends `/start` to bot.
3. System captures and stores chat ID.

This is a one-time setup. Document in deployment instructions, not in code.

**Needs resolution before:** Phase 2.4 testing.

---

## How this file is used

- Every new risk goes here, with an ID and a date.
- Every resolved risk is annotated with the resolution and date, kept for history.
- Reviewed at the start of each phase.
- Items marked "needs resolution before X" block work on X until resolved.
