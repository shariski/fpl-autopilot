# LLM Chip Reasoning — Design (Phase 3, S-A.3)

**Status:** approved 2026-05-26
**Slice:** Phase 3 S-A.3 — third slice of the AI-reasoning family. Adds LLM prose to the dashboard's
**chip recommendation** pane + the Telegram H-48 chip-preview line. Closer in shape to S-A.1
(captain) than S-A.2 (transfer): the deterministic chip recommender already emits a single
recommendation with a concrete `reason` string that the AI can elaborate into 2-3 sentences.
**Depends on:** Phase-3 S-A.1+S-A.2 architecture (`src/ai/{provider,reasoning,cache,grounding,jobs}.py`,
the `ai_reasoning_cache` table, the scheduler hook, the `ai_*` config accessors); Phase-1 chips
engine (`src/decisions/chips.py`); Phase-1 dashboard
(`frontend/src/lib/components/ChipRecommendation.svelte`).
**Cross-cutting design (reused, not re-derived):**
[`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md).
**Source of truth for this slice:** this doc. **`docs/decision-engine.md` is NOT touched** —
describe-only, deterministic chip recommender still picks. B4 untouched.

## Goal

When the dashboard's chip pane shows a recommendation (e.g. "Triple Captain — GW39 DGW: Haaland
DGW-xP 14.8 (>= 12.0), FDR 2."), render an LLM-generated paragraph below the chip-type label that
explains the move in human terms. When the chip pane is empty (no recommendation), nothing
changes. When the LLM is unavailable, the existing terse engine reason renders as today. Telegram
H-48 chip-preview body gets the same treatment via the established `notify_plan` swap pattern.

## Why this is the easiest of the four S-A panes

The chip recommender emits one recommendation (or `None`). Each chip's `reason` string already
contains the concrete numbers the prose needs (GW, xP, FDR, counts) — the AI just needs to weave
them into natural prose. No multi-pick ranking (unlike captain's 5 picks), no per-suggestion
chips layout (unlike transfer's structured chip rows), no fixture-context queries needed (the
engine already pre-computed the relevant numbers).

## Decisions (locked — no further brainstorm needed for this slice)

| Decision | Choice |
|----------|--------|
| Scope | **Single recommendation only.** The engine returns at most one chip per call. AI prose either renders for it or doesn't render at all (no recommendation → no LLM call → no cache row). |
| Payload depth | **Minimal:** `{chip: <type>, reason: <engine-emitted str>, next_gw: int}`. The `reason` string already carries the numbers (xP, FDR, counts) — the LLM only needs them in plain form. No per-chip-type payload switching. |
| Telegram swap | **Extended to chip.** `notify_plan` adds `_chip_ai_prose` helper analogous to captain/transfer. When cached prose exists for the chip pane at the next GW, the chip entry's summary in the Telegram body uses the prose instead of the template. |
| Frontend layout | **Prose paragraph + small AI/classic source-tag** rendered below the existing chip-type label inside `.chip-rec`. Distinct CSS class (`.ai-tag`, `.ai-tag-ai`, `.ai-tag-classic`) to avoid collision with the existing `.badge` (chip-type label). When prose is empty (`classic` source on a fresh DB), fall back to the existing engine `reason` string — no badge. When prose is present (`ai`), show the prose + AI badge; the engine `reason` is dropped from view (the prose subsumes it). |
| B4 | **Untouched.** Deterministic chip recommender still picks. No `decision-engine.md` change. |
| Cache identity | New `pane_type = 'chip'`. `recommendation_hash` covers the engine output's chip type + reason string — any change to the recommendation invalidates the row. |

## Architecture (delta from S-A.2)

```
src/ai/prompts/chip.txt                  ← NEW: per-pane prompt template
src/ai/prompts/chip_examples.json        ← NEW: 4 hand-curated exemplars (one per chip type), self-validating
src/ai/reasoning.py                      ← EXTEND: _build_chip_payload, _build_chip_prompt,
                                           render_chip_reasoning, generate_chip_prose
src/ai/jobs.py                           ← EXTEND: 'chip' branch in the pane walker
src/interface/queries.py                 ← NEW: get_chip_recommendation (wraps chips engine,
                                           enriches with reasoning+source) + get_chip_reasoning helper
src/interface/api.py                     ← EDIT: /api/chips calls queries.get_chip_recommendation
src/interface/telegram.py                ← EXTEND: _chip_ai_prose + notify_plan swap for decision=='chip'
src/scheduler.py                         ← EDIT: panes list extends to ['captain', 'transfer', 'chip']
frontend/src/lib/types.ts                ← EXTEND: ChipRecommendation gains optional
                                           reasoning?: string; reasoning_source?: 'ai' | 'classic'
frontend/src/lib/components/ChipRecommendation.svelte ← EDIT: render prose + AI/classic tag
frontend/src/lib/mocks/full.ts           ← EDIT: chip mock gets reasoning + reasoning_source

# NEW tests
tests/test_ai_prompts_chip.py            ← golden test for chip_examples.json grounding (4 exemplars)
tests/test_ai_reasoning_chip.py          ← payload + prompt + render + generate

# EXTENDED tests
tests/test_ai_jobs.py                    ← +case for 'chip' pane
tests/test_api.py                        ← +cases for /api/chips enriched payload
tests/test_telegram.py                   ← +cases for chip summary swap
tests/test_scheduler.py                  ← +case for ['captain','transfer','chip'] pane list
frontend/src/lib/components/ChipRecommendation.svelte.test.ts ← +cases for prose + AI/classic tag
```

**B2 (layer boundaries):** `src/ai/reasoning.py` reads Decision-layer outputs in-process; writes only
to `ai_reasoning_cache`. No new Data-Layer queries needed — the chip engine already provides
everything the payload needs. Interface → AI → Decision → Analytics → Data preserved.

## §1 Chip payload shape

The payload is built from the chips engine output + the next gw:

```python
def _build_chip_payload(conn, chip_decision: dict) -> dict | None:
    """Closed-shape payload for the chip recommendation, or None when no recommendation."""
    rec = chip_decision.get("recommendation")
    if rec is None:
        return None
    next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    return {
        "chip": rec["chip"],     # one of 'wildcard'/'free_hit'/'bench_boost'/'triple_captain'
        "reason": rec["reason"], # engine-emitted string with concrete numbers (GW, xP, FDR, counts)
        "next_gw": next_gw,
    }
```

The `reason` field is a *string* (not a list of numbers). The grounding check still applies: numeric
tokens in the LLM output must appear in the JSON dump of the payload, and `reason` carries all the
relevant numbers as embedded substrings.

## §2 Prompt template + few-shot exemplars

`src/ai/prompts/chip.txt`:

```
You are explaining an FPL chip recommendation to the team manager.

Constraints:
- 2 to 3 sentences. Plain English. No emojis. No exclamation marks.
- You may ONLY use numbers that appear in INPUT below. Do not invent any other number.
- Mention the chip type and the gameweek. Elaborate on the engine reason in human terms.
- Do not editorialise beyond the inputs. Do not predict future scorelines. Do not name other players.
- Output the paragraph only. No preamble, no closing remarks.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

`src/ai/prompts/chip_examples.json` — 4 hand-curated exemplars (one per chip type), each with prose
where every numeric token appears in `input`:

```json
[
  {
    "input": {
      "chip": "triple_captain",
      "reason": "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2.",
      "next_gw": 38
    },
    "output": "Triple Captain on Haaland in GW39 — he has a double gameweek with FDR 2 and a combined DGW-xP of 14.8, comfortably above the 12.0 threshold. This is the kind of fixture window the chip was designed for."
  },
  {
    "input": {
      "chip": "bench_boost",
      "reason": "GW32: DGW with all 15 having a fixture; bench xP 6.2 (> 4.0).",
      "next_gw": 30
    },
    "output": "Bench Boost in GW32 — all 15 players have a fixture, your bench projects 6.2 xP combined which clears the 4.0 trigger, and the DGW means even the cheap bench picks get two games."
  },
  {
    "input": {
      "chip": "free_hit",
      "reason": "Blank GW29: only 5 of 15 squad players have a fixture.",
      "next_gw": 28
    },
    "output": "Free Hit in GW29 — only 5 of your 15 squad players have a fixture in that blank gameweek. The chip lets you field a one-week temporary squad of 15 active players without permanently disrupting your team."
  },
  {
    "input": {
      "chip": "wildcard",
      "reason": "3 squad players face FDR worsening by 2+ over the next 3 GW.",
      "next_gw": 26
    },
    "output": "Wildcard worth considering — 3 of your squad players face FDR worsening by 2 or more over the next 3 GWs. A reset now would let you pivot toward easier-fixture assets while the rest of the league is locked in."
  }
]
```

Self-validating: `test_every_chip_example_output_is_grounded_in_its_input` confirms every numeric
token in each `output` appears in its `input` JSON dump.

## §3 Render + generate functions

Mirrors S-A.1's captain pattern exactly:

```python
def render_chip_reasoning(conn, gw: int, chip_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> (engine_reason_or_empty, 'classic').
    No recommendation -> ('', 'classic')."""
    payload = _build_chip_payload(conn, chip_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "chip", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    # classic fallback: the engine's existing reason string is meaningful, not empty
    return (chip_decision["recommendation"]["reason"], "classic")


def generate_chip_prose(conn, gw: int, chip_decision: dict, *,
                        provider, model_id: str,
                        max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Same flow as generate_captain_prose / generate_transfer_prose."""
    ...
```

Note the asymmetry with transfer: chip's `classic` fallback returns the engine's existing terse
`reason` string (which is meaningful to a human), not empty. Captain does the same; transfer
returns empty because its engine doesn't emit a per-suggestion reason.

## §4 Scheduler + jobs

`src/ai/jobs.py` adds a `chip` branch with `chip_decision_fn=None` default. Scheduler `panes` list
extends to `["captain", "transfer", "chip"]`. Three LLM calls per recompute cycle now (instead of
two for S-A.2 or one for S-A.1) — still trivial.

## §5 Interface — queries + api

`src/interface/queries.py` adds:

```python
def get_chip_recommendation(conn):
    """Wraps chips.recommend_chip; enriches the recommendation (if any) with
    (reasoning, reasoning_source). Returns the same shape as the chips engine when
    recommendation is None."""
    ...


def get_chip_reasoning(conn, gw):
    """Telegram-path helper. Returns cached AI prose, or None on miss."""
    ...
```

`src/interface/api.py` routes `/api/chips` through `queries.get_chip_recommendation` (same pattern
as the S-A.2 transfers rewiring).

## §6 Telegram

`src/interface/telegram.py` adds `_chip_ai_prose` helper (sibling of `_captain_ai_prose` and
`_transfer_ai_prose`), extends `notify_plan` to swap when `entry["decision"] == "chip"`.

## §7 Frontend

`frontend/src/lib/types.ts` — `ChipRecommendation` gains optional `reasoning?` + `reasoning_source?`.

`frontend/src/lib/components/ChipRecommendation.svelte` — current markup renders the chip-type
label (`<div class="badge">`) + the engine reason (`<p class="reason">`). Change:

- When `rec.reasoning` is present and non-empty: render the prose (replacing the engine reason
  paragraph) + a small AI/classic source-tag.
- When `rec.reasoning` is empty or absent: render the engine reason as today, no source-tag.

Distinct CSS classes for the AI tag (avoiding the existing `.badge` chip-type label class):
`.ai-tag`, `.ai-tag-ai`, `.ai-tag-classic`. Same blue/grey palette as captain/transfer.

`frontend/src/lib/mocks/full.ts` — chip mock gets `reasoning` + `reasoning_source: 'ai'` for visual
smoke.

## Safety & B-rules

- **B2:** AI sub-layer reads chip engine output; writes only to `ai_reasoning_cache`. No new
  Data-Layer queries. Layer boundaries preserved.
- **B4:** Untouched. Deterministic chip recommender still picks (priority TC > BB > FH > WC per
  decision-engine.md v0.6). No `decision-engine.md` change.
- **B7:** Closed payload (`chip`, `reason`, `next_gw`). The `reason` string is engine-emitted, never
  raw user content or auth material. No path for credentials.
- **B8:** No executor change. Chips never auto-execute (B3 — confirmed in deadguard / project
  rules); this slice doesn't touch that. AI just produces text.
- **B10:** Same logging as S-A.1/2 — `ai.chip.empty_prose`/`provider_error`/`grounding_failed`.
- **B11:** Decision-layer tests untouched. New tests use `StubProvider` (R3).
- **R3:** LLM has no tools. All tests fixtures-only.

## Testing

All tests fixtures-only via `StubProvider`. Per-task scope similar to S-A.2; see the plan for the
full test list. Highlights:

- `chip_examples.json` self-validates against `is_grounded` — 4 exemplars (one per chip type).
- `_build_chip_payload` returns `None` on no-recommendation; returns the closed dict otherwise.
- `render_chip_reasoning`: cache hit → `('<prose>', 'ai')`; miss → `('<engine reason>', 'classic')`;
  no-recommendation → `('', 'classic')`.
- `generate_chip_prose`: grounded prose caches; empty/ungrounded/exception → no row + log.
- `/api/chips` enriched: with cache → `reasoning_source='ai'`; without → `'classic'` + engine reason.
- `notify_plan` swap: with cached chip prose → Telegram body uses prose; without → template.
- Vitest cases for the chip pane: AI prose visible + tag; classic fallback shows engine reason.

## Scope boundary

- **IN:** chip prompt + few-shot, payload + render + generate, jobs branch + scheduler panes
  extension, queries wrapper + api rewiring, telegram swap, frontend prose + tag + mock. Tests.
- **OUT (this slice):** deadguard summary (→ S-A.4), conversational interface (→ S-B),
  mini-league (→ S-C), personalization (→ S-D), scenario simulator (→ S-E), player-news (→ S-F).
- **OUT (forever for S-A.3):** any change to chip-trigger thresholds, the chip priority order,
  the chips-used suppression logic. LLM only paraphrases.

## Definition of done (CLAUDE.md B14)

- `/api/chips` returns the recommendation with `reasoning` + `reasoning_source` fields when a
  recommendation exists. No recommendation → unchanged shape (empty / null per existing engine).
- Dashboard chip pane renders prose + AI tag when prose is cached; falls back to engine reason
  string with no tag when classic. When no recommendation, pane is empty as today.
- Telegram H-48 chip-preview line uses cached AI prose when present; falls back to template
  otherwise.
- `ai_reasoning_cache` populates `pane_type='chip'` rows on each `refresh_and_recompute` cycle when
  a recommendation exists.
- Grounding + empty-prose guards from S-A.1/S-A.2 apply to chip generation.
- All tests green (pytest + vitest). All tests use `StubProvider`. **No `docs/decision-engine.md`
  change.**
- Architecture spec referenced + reused without modification.
- The agent never ran a live Ollama call or live FPL call during implementation (R3 + B11).
