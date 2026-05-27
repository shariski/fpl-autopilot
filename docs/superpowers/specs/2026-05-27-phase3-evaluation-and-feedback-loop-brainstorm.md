# Phase 3 (AI Layer) — Evaluation & Feedback-Loop Brainstorm

**Status:** brainstorm 2026-05-27 — captures a multi-question design conversation about
extending the system with strategic features (wildcard drafting, post-hoc evaluation, closed-loop
parameter tuning). Not a spec or plan. Will be promoted to one or more specs as slices are
committed to.

**Purpose:** preserve the architectural reasoning from this session so a future session (possibly
post context-compression or in a fresh window) can pick up exactly where we left off. The
conversation reached real conclusions that should not need to be re-derived.

**Companion docs:**
- [`2026-05-26-phase3-scope-decomposition.md`](./2026-05-26-phase3-scope-decomposition.md) —
  parent memo. Defined S-A through S-F. This brainstorm proposes adding S-G and S-H to that
  family, with notes on how S-E (originally "scenario simulator") should be re-shaped.
- [`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md) —
  cross-cutting AI architecture every slice reuses (provider + cache + prompt + grounding).

**Source of truth:** `docs/plan.md` Phase 3 placeholder tasks #5 (user-history analyzer), #6
(scenario simulator), and #7 (cache LLM reasoning). This memo proposes a concrete decomposition
that respects the project's B-rule discipline.

---

## The starting questions

The user asked, over the course of one session:

1. "Can we add strategic features (player drafting, wildcard recommendations) and let an LLM reason
   through them, with the system feeding necessary data?"
2. "What if we use a stronger model like Sonnet 4.6 instead of qwen2.5:7b local?"
3. "Can the system reason about past decisions — why we picked player X in GW3, why the expected
   output didn't match actual, how to learn from that?"
4. "If a retrospective audit produces insights, how does the system consume them and adjust its
   parameters/values/calculations for next decisions (wildcard, transfer, lineup, etc.)?"

These questions converge on a single architectural theme: **closed-loop learning** — auditing past
decisions and feeding insights back into future ones, possibly automated, possibly not.

---

## Core architectural conclusions

### 1. LLM-as-planner (Shape A) is the wrong architecture for strategic features

Putting an LLM in the planner seat — "here's data, produce a 15-player wildcard draft" — fails on
hard combinatorial problems regardless of model strength:

| Concern | qwen2.5:7b local | Sonnet 4.6 |
|---|---|---|
| Budget constraint (£100m) | Fails ~30% on novel combos | Fails ~2-5% on edge cases — still nonzero |
| 3-per-club rule | Forgets mid-draft | Mostly remembered, drifts on novel constraints |
| Position quotas (2/5/5/3) | Drifts | Usually correct |
| Hallucinated player names | High | Low but nonzero ("Saka" vs "Bukayo Saka" breaks ID lookup) |
| Combinatorial coverage | Proposes 1-2 squads | Proposes 3-5 — still not exhaustive |
| Strategic FPL knowledge | Limited | Substantial |
| Reproducibility | Different each call | Same |

Even with Sonnet, an LLM cannot reliably do **exhaustive search under hard constraints**. That's
what deterministic code is for.

### 2. Deterministic planner + LLM narrator (Shape B) is the right shape

```
Data → Deterministic planner ──enumerates→ Top N candidate options (e.g. 5 wildcard squads)
                                              │
                                              ↓
                                            LLM narrates: tradeoffs, framing, comparison
                                              │
                                              ↓
                                            User picks one
```

- **Planner** handles constraint satisfaction + exhaustive search (where deterministic code excels)
- **LLM** handles judgment, framing, comparison narrative (where Sonnet 4.6 excels)
- **User** retains decision authority on high-stakes irreversible actions (preserves B4 / B8)

This is the same pattern the S-A slice formalized at the per-pane level. Extending it to strategic
features (wildcards, drafting) is consistent, not novel.

### 3. Retrospective decision audit (S-G) is a prerequisite, not a luxury

You cannot trust a multi-GW projection (needed for wildcard scoring) without first knowing whether
the underlying xP model is calibrated. The scope-decomp memo missed this dependency. **The honest
slice order is S-A → S-G (audit) → S-D/E (personalization, strategic), not S-A → S-D → S-E.**

The audit is also the cheapest, safest use of Sonnet 4.6 — the job is "narrate structured
residuals with nuance," which is pure LLM sweet-spot work, no constraint satisfaction involved.

### 4. The parameter-update partitioning

Different parameters have different acceptable update mechanisms. This partitioning is the spine
of the entire feedback-loop architecture:

| Parameter class | Examples | Update mechanism | Blast radius |
|---|---|---|---|
| **Hard constraints** | 3-per-club, budget cap, 2/5/5/3 quotas | Never change | n/a (FPL rules, not policy) |
| **Safety thresholds** | Deadguard `min_ep_delta_for_transfer`, `max_hit_per_gw_auto`, auth-freeze threshold | Human-only | Bounds worst-case downside of system action |
| **Soft decision thresholds** | EP delta for transfer, confidence floor, hit penalty calibration, chip flag thresholds | **Auto-tunable within bounded ranges (S-H)** | Calibration; mis-calibration costs points, doesn't break |
| **xP formula weights** | `FDR_ATTACK_MULT`, `CS_PROB`, position pts, xMinutes formula | Versioned via B5 (parallel-run) | Model drift; can silently mis-calibrate everything for a season |
| **Model architecture** | Replacing v1 with Poisson GLM, adding form/xBonus terms | Versioned + doc-first (B4 + B5) | Same |
| **Algorithm** | Captain ranker logic, transfer engine search | Doc-first, human-implemented | Pure B4 territory |

Critically: **most "interesting" decisions (which player to captain, which transfer to recommend)
have no directly auto-tunable parameter** — they're outputs of the xP model. The auto-tuning loop
mostly affects *thresholds that decide when to act vs. hold*, not the *content* of the
recommendation. This is by design: thresholds are reversible, model changes aren't.

### 5. Three categories of feedback flow

| Category | Mechanism | Project's existing position |
|---|---|---|
| **(A) Human-mediated** | Audit report → human edits config/code | Current default. CLAUDE.md B4 implicitly requires this for all changes today. |
| **(B) Bounded auto-tuning** | System changes its own soft thresholds within explicitly-listed bounded ranges, with cooldowns + logging + revert command | **Genuinely new B-rule territory.** Requires CLAUDE.md amendment. |
| **(C) Online model retraining** | xP weights re-fit on new data each cycle | Forbidden by B5 without parallel-run validation. |

Category (B) is the interesting new capability. It needs a new B-rule (proposed "B15") describing
the contract: which params are auto-tunable, what ranges, what statistical significance test,
what cooldown, how logged, how reverted.

### 6. Sonnet 4.6 use is feature-specific, not blanket

The scope-decomp memo said "local Ollama first, swap later if needed." That stands for the prose
narration layer (S-A). For S-G (retrospective audit narration), Sonnet 4.6 is genuinely better
fit because:
- The job is sophisticated post-hoc reasoning, not per-pane prose
- No constraint satisfaction involved (so hallucination cost is low)
- Lower call frequency (audit runs every 4 GWs or on-demand, not per GW per pane)
- Cost is bounded (~$0.15-0.50 per audit call; annual maybe $5-20)

For S-H (auto-tuning), no LLM needed — pure statistical analysis on residuals.

For S-C (mini-league context, deferred), Sonnet introduces a real privacy concern (sends other
managers' standings to Anthropic). That's a per-feature opt-in decision, not blanket.

---

## Proposed new/revised slices

Adding to the S-A through S-F set defined in the original scope-decomp memo:

| ID | Slice | Status before | Status now | Notes |
|---|---|---|---|---|
| S-G | **Retrospective decision audit** | not in memo | **NEW — recommended next** | Deterministic reader + Sonnet narrator. Reports findings, proposes changes. Does not apply them. Inside current B-rules. |
| S-G.5 | **Activity log outcome backfill** | not in memo | **NEW — verification needed** | Ensure Monday settlement job actually fills `outcome` on every decision row. May be partial today. |
| S-H | **Bounded auto-tuning loop** | not in memo | **NEW — requires B15 amendment** | Reads S-G audit output, applies bounded changes to soft thresholds, logs + notifies + revertable. |
| S-E | **Wildcard / scenario simulator** | in memo, deferred ("B4 territory") | **Re-shape to Shape B explicitly** | Deterministic enumerator + multi-objective Pareto frontier + Sonnet narration. Build after S-G validates xP model. |
| xP v2 | **Model revision** | implicit in B5 | Promote as a named slice | Triggered when S-G reveals specific xP systematic biases. B5 parallel-run process. |

S-A (done), S-B (conversational why?), S-C (mini-league), S-D (personalization), S-F (player
news) remain as defined in the original memo. S-D is essentially the *user-facing surfacing* of
S-G's insights — they're closely related.

---

## Dependency ordering

```
S-A (done — captain / transfer / chip / deadguard-summary prose)
 │
 ├─ S-G  (audit; reads activity_log + outcomes; Sonnet narrator)
 │   │
 │   ├─ Run for 4-6 GWs to accumulate audit data + develop intuition
 │   │   for what should be auto-tunable
 │   │
 │   ├─ B15 amendment to CLAUDE.md (doc-first per B13)
 │   │   │
 │   │   └─ S-H  (bounded auto-tuning; applies soft threshold changes)
 │   │
 │   ├─ S-D  (personalization — surfaces S-G insights to user)
 │   │
 │   ├─ xP v2 (when S-G reveals specific model gaps; B5 process)
 │   │
 │   └─ S-E  (wildcard / drafting via Shape B; requires validated xP)
 │
 └─ S-B / S-C / S-F (parallel tracks, independent of feedback loop)
```

---

## The S-G slice — sketch

This is the next concrete step. Full spec to be written in a follow-up.

**Inputs:** `activity_log` rows for past GW(s), `player_gw_points` for the same GW(s), the
recorded `xp` and `fdr` snapshots, the `mode` at time of decision.

**Deterministic layer:**
- Compute residual = actual_points - expected_xp for every decision's primary subject
  (captained player, transferred-in player, etc.)
- Cluster misses by likely cause:
  - "injury we caught late" (status changed close to deadline)
  - "rotation we didn't predict" (xMinutes ratio was high, actual minutes low)
  - "xP model miss" (status fine, played, underperformed)
  - "lucky outperform" (status fine, exceeded xP)
  - "FDR miss" (FDR was low/easy, actual was hard)
- Aggregate trends over N GWs per (decision_type, cluster):
  - mean residual, std dev, sample size
  - statistical significance vs zero (one-sample t-test or Bayesian credible interval)
- Propose threshold adjustments where data supports it (does **not** apply them in S-G)

**Sonnet 4.6 layer:**
- Input: structured findings from the deterministic layer
- Output: prose audit report with sections:
  - "Last N GWs at a glance"
  - "Captain picks: what went well, what missed"
  - "Transfers: residual analysis + ROI"
  - "Systematic biases detected"
  - "Recommended adjustments" (human-readable, deferred to user / S-H)

**Surfaces:**
- New CLI command: `fpl-autopilot review --gw N` (single-GW) or `fpl-autopilot review --last 4`
- New dashboard view: "Audit" tab showing the latest report
- Activity log: every audit run is itself a logged event

**Out of scope for S-G specifically:**
- Applying any auto-changes (that's S-H)
- Changing the xP formula (that's xP v2 via B5)
- User-history personalization across modes (that's S-D)
- Mini-league context comparison (that's S-C)

---

## Open questions for future sessions

1. **What's the audit cadence?** Per-GW? Every 4 GWs? On-demand only? Likely on-demand first,
   scheduled later.
2. **How is statistical significance enforced before S-H acts?** N ≥ 20? Bayesian CI? Cooldown
   period?
3. **Where does the user accept/reject S-H auto-changes?** Dashboard banner + Telegram + CLI
   revert? Or single surface?
4. **Should S-G use Sonnet 4.6 or stay on Ollama?** Brainstorm leans Sonnet; user has not
   confirmed willingness to introduce the API dependency.
5. **What's the format of B15 (the new B-rule covering auto-tuning)?** Needs careful drafting —
   it's the contract that opens up category (B) behavior.
6. **Outcome backfill state (S-G.5):** what's actually wired up today vs. what needs to be built?
   Verification step before S-G implementation.

---

## Why this fits within the project's discipline

- **B4 (decision engine is sacred):** S-G is purely descriptive (audit). S-H acts on soft
  thresholds only, in bounded ranges, logged for audit. Algorithms and model architecture remain
  untouched.
- **B5 (xP model is iterative + versioned):** xP v2 (when triggered by S-G findings) follows the
  existing parallel-run protocol. No silent model changes.
- **B7 (no creds in side channels):** S-G reads from `activity_log` (no FPL writes). Sonnet
  receives only structured residuals, no cookies/creds.
- **B8 (deadguard scope):** untouched. Deadguard safety thresholds are in the "human-only" row of
  the parameter partitioning table.
- **B11 (decision testing):** S-G itself is deterministic with frozen-input tests. S-H requires
  property tests for "auto-changes only happen within bounded ranges + with N ≥ threshold."
- **B13 (doc-first):** this brainstorm is itself a step toward doc-first. S-G spec lands before
  S-G code. B15 amendment lands before S-H code.

---

## Recommended next step

Write `docs/superpowers/specs/2026-05-27-phase3-s-g-decision-audit-design.md` (full spec for the
retrospective audit slice). This is the concrete artifact the next implementation session works
from. Implementation plan + TDD comes after spec is approved.

S-H, B15, xP v2, and revised S-E follow once S-G has produced real audit data — likely 4-6 GWs
of system operation after S-G ships.

---

## Pointer back to memory

A summary of this conversation's *principles* (not the contents) should land in user memory once
S-G commits — specifically the "LLM-as-planner vs LLM-as-narrator" framing and the parameter
partitioning table. Both are likely to be re-derived in unrelated future contexts and are worth
crystallizing into a memory file. (Memory dir:
`/Users/shariski/.claude/projects/-Users-shariski-Work-fpl-autopilot-phase3/memory/`)

This brainstorm doc itself is the primary handoff artifact — if the current session compacts or a
new session begins, reading this doc + the parent scope-decomp memo should restore full context
in one read.
