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

DeepSeek API (`deepseek-chat`) via the OpenAI SDK, chosen 2026-08-14
(`docs/superpowers/specs/2026-08-14-deepseek-provider-design.md`). Base URL +
model are configurable, so switching to another OpenAI-compatible provider is
config-only. Local LLMs (Ollama) remain a config-selectable fallback but are
not deployed.

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

## R8 — Vaastav databank: element ids are reused across seasons ✅ FIXED 2026-08-20

FPL reissues element ids every season / on every roster re-sync. A 25-26 databank
CSV's `element` column is usually a DIFFERENT player today. `_remap_databank_elements`
trusted `element in current roster` as a passthrough, so historical rows were
mis-attributed by id (e.g. 25-26 "Cole Palmer" element 235 landed on today's id 235
= Aznou; Saka→Madueke, Haaland→Mount, Bruno→Hall).

**Impact observed (GW1 26/27):** ~194 of 587 players — nearly every premium — got
zero/minimal projected minutes, xP v2 rated them at ~0, and the AI squad builder
bought "all cheap players" (84.5m of 100m, zero premiums). The xP surface, FDR v2
team ratings, transfer engine, captain ranker and chip DGW-xP were all fed poisoned
rates.

**Fix (2026-08-20):** rows are now matched by NAME (team-agnostic full-name tokens,
so club-movers match); the element id is accepted only when the current holder's
web_name + team corroborate the CSV name; otherwise the row is dropped, never
mis-assigned (B6). Re-ingested 25-26 from the local full CSVs via
`docs/research/calibration/reingest_databank.py`.

**Residual risk:** new signings / players not in last season's PL databank have no
rates and no xP v2 (e.g. foreign-league imports) — they are simply absent from the
pool, which can shrink the candidate pool. Monitor pool size each refresh.

**Second fix (2026-08-20, same incident):** team/league xG·90 and DC·90 were
normalized per player-minute, deflating them ~13× (la.xg90 0.132 vs true ~1.35)
because player xG sums to the team match total once while minutes count every
player. Non-promoted FDR ratios self-cancelled, but absolute-value promoted
overrides exploded (damp(1.3/0.132) = 4.86 → Lammens 14.43 xP vs a promoted
side). Normalized per match instead; backtest re-run unchanged (v2 still wins).

---

## R9 — Vaastav GitHub CSVs are revision-unstable (truncation observed 2026-08-20)

The live `vaastav/Fantasy-Premier-League` master CSVs currently contain ~31 rows/GW
for 2025-26 (truncated vs the full ~692-row sets fetched 2026-08-16). The 6h
databank cooldown means the DB keeps its last full fetch, but a re-fetch today
would silently overwrite 31 rows/GW with the truncated content — rates would be
computed from a near-empty season.

**Mitigation:** `docs/research/calibration/reingest_databank.py` reads the last
known-good CSVs from disk instead of GitHub; treat live-GitHub databank fetches as
suspect until the upstream files recover. Never delete the local `data/databank/`
snapshot (gitignored — it is the only full copy).

**Partial materialisation 2026-08-25:** the hourly job re-fetched 25-26 from the
live GitHub (which now carries a different partial revision — 17,016 rows vs the
19,074-row trusted snapshot) and silently shifted some xP values. **Resolution:**
`config.yaml` `databank.seasons` is now `["2026-27"]` only — past seasons are
frozen in the DB; live fetching is restricted to the current season. Re-ingest
restores the trusted 25-26 rows.

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
