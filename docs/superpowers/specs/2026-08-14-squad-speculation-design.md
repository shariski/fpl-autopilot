# AI Speculation Layer for Squad Builder — design

Replace the AI-as-squad-picker with **AI-as-speculative-operator**: the LLM emits structured
spike/drop signals over the candidate pool; the deterministic optimizer consumes them as one
logged input and still owns legality and the final decision.

**Status:** approved 2026-08-14 (brainstorming).
**Amends:** `docs/superpowers/specs/2026-08-14-squad-builder-design.md` (the AI-picks path is
retired; the speculative layer replaces it). `docs/decision-engine.md` §"Squad builder (S-B)"
gains the bonus constants and the new input signal (B4 entry update).
**Why:** the LLM cannot do multi-constraint arithmetic — it invented slots, mismatched
positions, and overshot the budget by 10-14m repeatedly, even with retries and normalization.
Its actual strength is pattern recognition: predicting which players spike. That is now its
only job. (kerf's exact split: LLM judgment → deterministic gate.)

## Architecture

```
GET /api/squad/builder
   │
   ▼
runner.generate_squad
   │  build digest (unchanged) ──► cache check (pane "squad_spikes")
   │                                   ▼ miss
   │                              generate_spike_signals(provider)
   │                                   │ {spikes[], drops[], market_read}
   │                                   │ gate: ids ∈ digest, levels ∈
   │                                   │ {high, medium}, reasons grounded
   │                                   │ per-player, retry ≤3, fail → None
   ▼
optimize_squad(pool, bonus_map)      score = xp_6gw + spike_bonus(level)
   │  SPIKE_BONUS = {"high": 1.5, "medium": 0.75}   (decision-engine.md)
   │  budget/slots/clubs deterministic (unchanged)
   ▼
final squad + attached signals → API → page
```

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| AI output | `{spikes: [{player_id, level: "high"\|"medium", reason}], drops: [...same shape], market_read: str}` — ≤10 each. No slots, no prices, no sums. |
| Prompt persona | "Elite FPL speculator" — which players spike (fixture alignment, form, xG divergence, ownership) and which fall flat. Reasons 1 sentence, grounded in the cited player's digest numbers. |
| Bonus | `SPIKE_BONUS = {"high": 1.5, "medium": 0.75}` added to `xp_6gw` inside the optimizer's sort key. Constants live in `src/decisions/squad_validator.py`, documented in `decision-engine.md` (B4). |
| Drops | `drops` players get `-0.75` (medium) / `-1.5` (high) — a drop signal discounts an otherwise high-xp pick. |
| Failure mode | Signals fail (provider/gate) → `bonus_map = {}` → optimizer as today. Never blocks; logged `gate_result: "failed"` with `ai.insight`-style activity row (`decision_type="squad"`, `result="spikes_failed"`). |
| Cache | `ai_reasoning_cache`, `pane_type="squad_spikes"`, key = digest hash. |
| Retired code | The AI-picks loop, `normalize_squad`, `repair_budget` are removed from the runner path (keep `repair_budget`/`normalize_squad` only if still referenced — they are not; delete). `validate_squad` + `optimize_squad` stay. |
| Logging (B10) | Every squad row logs `inputs.speculation`: the applied bonus map (player_id → level) — the decision is auditable: xP + AI signal + constants. |
| Page | New "AI speculation" section: spikes with reasons (highlighted on their cards), drops, `market_read` as the lead quote. Source badge semantics: `ai` = speculation active, `deterministic` = signals failed. |
| B4 | decision-engine.md §S-B updated: "Speculative input signal: LLM emits spike/drop levels; deterministic optimizer applies fixed bonuses (high +1.5, medium +0.75, drop −0.75/−1.5) to xP; legality unchanged; signal logged with every decision." |

## Gate rules (the law, unchanged in spirit)

1. Every `player_id` must exist in the digest.
2. `level` ∈ {"high", "medium"}.
3. Per-pick reason grounding (numbers in the reason must appear in that player's digest entry).
4. Retry ≤3 with feedback; all-fail → `None` → no signals (graceful).
5. Duplicates rejected.

## Testing

- Spikes runner: happy path caches; unknown id / bad level / ungrounded reason → retry then None; provider failure → None + `spikes_failed` row.
- Optimizer with bonus: a high-spike player outranks an equal-xp non-spike; a drop-signaled player is passed over; legality property test unchanged.
- Pipeline: signals attached to the returned squad; page/API expose spikes/drops/market_read.
- Frontend: speculation section renders; `deterministic` badge when signals absent.

## Docs (B13)

1. This spec. 2. `decision-engine.md` §S-B update (above). 3. AI architecture changelog v0.5.
4. runbook §10: "spikes_failed" triage line. 5. onboarding: speculation mention.

## Out of scope

- Learning/feedback on speculation quality (Phase 3 personalization).
- Multi-GW spike horizons beyond the level label.
