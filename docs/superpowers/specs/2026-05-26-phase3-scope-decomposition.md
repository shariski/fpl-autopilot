# Phase 3 (AI Layer) — Scope Decomposition Memo

**Status:** approved 2026-05-26
**Purpose:** decide the order in which Phase 3's seven placeholder bullets in `docs/plan.md` are
shipped. This is a one-shot reference, not a living doc — once the first slice ships, future-slice
ordering can be revisited but the rationale below stands as captured.
**Companion docs:** [`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md)
(cross-cutting architecture, reused by every slice) and
[`2026-05-26-phase3-llm-captain-reasoning-design.md`](./2026-05-26-phase3-llm-captain-reasoning-design.md)
(first-slice spec for S-A.1).
**Source of truth:** `docs/plan.md` §"Phase 3 — AI Layer" and `docs/product-spec.md`
§"Phase 3 — AI Layer". The plan intentionally defers detail until the activity log has accumulated
real data — this memo is the first time that detail lands, scoped to "what can ship now."

## The seven Phase-3 bullets

From `docs/plan.md` §"Placeholder tasks":

1. Integrate LLM (Claude API or local) into reasoning layer.
2. Replace template-string reasoning with LLM-generated explanations grounded in actual numbers.
3. Add conversational query interface: "why are you recommending X?"
4. Pull mini-league standing data and incorporate into recommendations (template vs differential
   strategy).
5. Build user-history analyzer: identify risk preference, captain preference patterns.
6. Add scenario simulator: "what if I wildcard now vs in 3 weeks?"
7. Cache LLM reasoning per GW to control cost.

**Bullets #1 and #7 are not slices** — they are cross-cutting infrastructure that every slice
shares (provider plumbing, cache layer). They are captured in the AI-architecture spec, not as
standalone bullets.

## Decomposition into independent slices

| ID | Slice | Touches B4? | Data dependency | Indep. value | Risk |
|---|---|---|---|---|---|
| **S-A** | **LLM-rendered reasoning** for existing captain / transfer / chip / deadguard-summary outputs (replaces the template strings the engine already emits). Includes per-GW cache. | **No** — describe-only. | None. Uses already-computed `xP`, `fdr`, alternatives. | High — every dashboard pane gains real prose. | Low. |
| S-B | **Conversational "why X?" query** — a chat box on the dashboard + Telegram `/why` command, answering questions grounded in `activity_log` + current GW snapshot. | No — describe-only. | Small (current GW is enough; `activity_log` enriches but isn't required). | Medium — one feature, two surfaces. | Medium — open-ended user prompts widen the surface area. |
| S-C | **Mini-league context** — pull league standings, compute template-vs-differential pressure, surface as a *modifier on the recommendation* (initially describe-only: "Salah is template — captaining him locks in parity with rank 12"). | **No if describe-only; YES if it changes the pick.** Default first ship: describe-only. | Needs league ID (Q2 in `docs/risks.md`) and a public league endpoint. | Medium — interesting but only matters in season run-ins. | Medium — new external endpoint, new schema, mini-league privacy. |
| S-D | **User-behavior personalization** — learn the user's risk preference / captain stickiness / hit tolerance from `activity_log`, bias prose (and eventually thresholds). | Describe-only first; threshold-bias would be B4. | **Hard-blocked: ~1 GW of `activity_log` data exists today.** Needs ≥ 8–10 GWs minimum to draw any signal. | Low until data accumulates. | Low (no live writes), but no useful output until the log is fat enough. |
| S-E | **Scenario simulator** — "wildcard now vs in 3 weeks", "what if I take this −4?". Deterministic counterfactual planner + LLM narration. | **YES** — outputs are recommendations, not explanations. | None for the planner; prose layer needs S-A. | High — fills a real Phase-3 gap. | High — multi-step counterfactual logic, heavy testing surface. |
| S-F | **Player-news ingestion + LLM digest** — fold press conferences / injury news / lineup leaks into the H-2 re-eval. | **YES** — changes *inputs* to the decision engine. | New scraper / news source. | High value at H-2. | High — new scraping target (cf. R2), new failure surface, B4 territory. |

## Dependency ordering

```
S-A  (foundation: provider + cache + prompt + grounding + first pane)
 │
 ├─ S-A.2/3/4  (other panes; reuse all S-A plumbing)
 │
 ├─ S-B  (conversational; reuses provider + cache + grounding)
 │
 ├─ S-C  (mini-league; reuses prose plumbing; adds new data source)
 │
 ├─ S-D  (personalization; reuses prose plumbing; gated on activity_log)
 │
 ├─ S-E  (scenario sim; reuses prose plumbing; B4 doc-first required)
 │
 └─ S-F  (player news; reuses prose plumbing; B4 doc-first required)
```

- **S-A is the foundation.** Every other slice that produces text reuses its provider plumbing,
  prompt-context builder, cache layer, grounding check, and fallback ladder. Building S-B or S-C
  before S-A duplicates that scaffolding and risks divergent prose styles.
- **S-D is empirically blocked** until the activity log has 8–10 GWs. With ~1 GW, any "user
  preference" inferred is a single data point and would mislead. Per `docs/plan.md` Phase 3 preamble:
  "Detail this phase after Phase 2 is operational and the system has accumulated real activity-log
  data." This is the slice that preamble was written for.
- **S-E and S-F are B4 territory.** They produce or change *what gets recommended*. Both require a
  `docs/decision-engine.md` entry before code per B4 ("sacred"). They are Phase-3 endgame, not the
  first slice.
- **S-B and S-C are credible second waves** (after S-A.1 ships and the architecture is proven on
  real GW data). Their relative order is not material — pick by user demand at that time.

## Recommendation: first slice is **S-A.1 (captain reasoning)**

S-A is selected as the foundation slice (locked in brainstorming 2026-05-26). Within S-A, the
first ship is **S-A.1: captain reasoning only**. Follow-ups S-A.2 (transfer), S-A.3 (chip), S-A.4
(deadguard summary) repeat the established pattern (one prompt template, one few-shot exemplar,
one scheduler call, one dashboard binding, tests).

Why captain-only first ship rather than all four panes:
1. **Read the prose on real GW data before committing to wider rollout.** Quality from a 7B
   quantized local model under "don't invent numbers" instructions is genuinely uncertain. The
   captain pane is the single most-read line in the dashboard — getting feedback there is highest
   leverage.
2. **Smallest reversible bet.** If `qwen2.5:7b-instruct-q4_K_M` produces bad prose, the slice can
   be flipped off via `ai.enabled: false` config and the system returns to Phase-2 behaviour with
   zero schema migration. Adding three more panes in the same merge would mean a wider rollback.
3. **Matches the project's TDD / subagent / per-task-commit rhythm.** A narrow first ship lets the
   architecture be built, tested, and reviewed (per-task + final opus per the established Phase-2
   pattern) without simultaneously debating four different prose styles.

## Why S-A satisfies the B-rule boundaries cleanly

- **B4 (decision-engine.md is sacred):** S-A is purely *descriptive*. The deterministic engine
  still picks the captain. The LLM only paraphrases inputs the engine already produced. No
  decision-engine.md change. No new model version. No new threshold.
- **B7 (no creds/cookies in any side channel):** The prompt builder is the only path that touches
  the LLM, and it accepts typed inputs from the decision-output layer. Credentials, cookies, and
  raw `/my-team` responses have no path into a prompt.
- **B8 (no auto chips/hits/multi-transfer/formation):** S-A does not execute anything. It produces
  text. The deadguard/executor surfaces are untouched.
- **R3 (the agent never executes live FPL writes):** The LLM has no tools, no write access, never
  makes an HTTP call to FPL. Output is a string.

## Out of scope for S-A.1 (deferred to later slices)

- Any prose for transfer / chip / deadguard summary panes (→ S-A.2 / S-A.3 / S-A.4).
- Any conversational interface (→ S-B).
- Any mini-league pull (→ S-C).
- Any personalization from `activity_log` (→ S-D, blocked on data accumulation).
- Any scenario simulation (→ S-E, B4 doc-first).
- Any player-news ingestion (→ S-F, B4 doc-first).
- Any LLM-driven change to the captain pick itself (this would be a different slice with a B4
  entry, not S-A).
- Any Claude-API or hosted-provider integration — local Ollama first, swap later if needed (see
  the architecture spec).

## Next step

Read [`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md)
for the cross-cutting architecture every slice above reuses, then
[`2026-05-26-phase3-llm-captain-reasoning-design.md`](./2026-05-26-phase3-llm-captain-reasoning-design.md)
for the first-slice spec. A `writing-plans` implementation plan for S-A.1 belongs in the next
session, after the user approves these three specs.
