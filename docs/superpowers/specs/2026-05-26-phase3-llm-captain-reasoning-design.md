# LLM Captain Reasoning — Design (Phase 3, S-A.1)

**Status:** approved 2026-05-26
**Slice:** Phase 3 S-A.1 — the first slice that exercises the cross-cutting AI architecture, scoped
**only to the captain pane** (dashboard + Telegram). Follow-ups S-A.2 (transfer), S-A.3 (chip),
S-A.4 (deadguard summary) repeat the same pattern for the other panes; they are out of scope here.
**Depends on:** Phase-1 captain ranker (`src/decisions/captain.py` — produces top-5 with template
reasoning strings, `xP`, vice, fixture, alternative gap), Phase-1 scheduler
(`src/scheduler.refresh_and_recompute`), Phase-1 dashboard (`src/interface/queries.py`,
`frontend/` captain pane), Phase-2 Telegram outbound (`src/interface/telegram.py` —
`notify_plan` body), and the **cross-cutting AI architecture spec**
[`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md)
(provider, cache, grounding, fallback, B-rule stance — **read that first**).
**Source of truth for this slice:** this doc + the architecture spec. `docs/decision-engine.md` is
**not** touched (describe-only — the deterministic captain ranker still picks; the LLM only
paraphrases its inputs and outputs).

## Goal

On the dashboard captain pane and in the Telegram H-24 preview, replace the template-string
reason next to the captain pick with 2–3 short sentences of LLM-generated prose grounded in the
actual numbers the deterministic engine already used. When Ollama is unavailable, the captain
pane and Telegram message look exactly as they do today (template prose). A small badge in the
dashboard makes the source visible to the user.

## Decisions (locked — brainstorming 2026-05-26)

| Decision | Choice |
|----------|--------|
| Pane scope | **Captain pane only** for first ship. Other panes (transfer / chip / deadguard summary) are S-A.2/3/4 follow-ups using the same architecture. |
| Surfaces | Dashboard captain pane **and** Telegram `notify_plan` body (both currently render the engine's template string; both swap to cached prose when present). |
| Inputs | Captain ranker's existing output: **top-1 pick** with name, position, club, fixture (opponent + venue), `xP[next_gw]`, vice (name + xP), gap to alternative (#2 xP), confidence score. No new data, no `activity_log`. |
| Output budget | 2–3 sentences (~60–120 words), `num_predict=200`. |
| Number-grounding | Per architecture spec §3 — every numeric token in the prose must appear verbatim in the input payload. Ungrounded prose is not cached; scheduler logs + backs off. |
| Fallback | Per architecture spec §6 — silent fallback to the captain ranker's existing `reason` string. No banner. Dashboard shows `AI` / `classic` badge so source is always visible. |
| B4 | **Untouched.** The deterministic ranker still picks the captain + vice. No `decision-engine.md` change. |

## Architecture

```
src/ai/prompts/captain.txt              ← system + user prompt template (the only string the user sees indirectly)
src/ai/prompts/captain_examples.json    ← 1–2 few-shot exemplars: (input payload → ideal prose)
src/ai/reasoning.py                     ← _build_captain_payload, _build_captain_prompt,
                                          render_captain_reasoning, generate_captain_prose
src/ai/jobs.py                          ← generate_ai_reasoning_job walks [captain] (S-A.1: just one pane)
src/scheduler.py                        ← invoke generate_ai_reasoning_job after refresh_and_recompute (gated by ai.enabled)
src/interface/queries.py                ← captain pane assembly reads render_captain_reasoning → (prose, source)
src/interface/telegram.py               ← notify_plan body uses cached prose when present (same fallback rule)
frontend/src/lib/types.ts               ← CaptainPick gains reasoning: string; reasoning_source: 'ai'|'classic'
frontend/src/lib/components/<captain>.svelte ← render prose + small AI/classic badge
docs/architecture.md                    ← four-layer diagram updated (per architecture spec)
docs/onboarding.md                      ← Ollama prerequisite + opt-out via config note
```

B2: `src/ai/reasoning.py` calls `src/decisions/captain.py` types (the existing typed output) +
the cache table. It does not import `src/auth/`, does not call `src/data/repository` for FPL
data. Scheduler integration is in-process. Interface reads `(prose, source)` from the AI sub-layer.

## §1 The payload (the LLM's entire view of the world)

A narrow, typed dict — the *only* thing the prompt builder is allowed to inject into the model:

```python
def _build_captain_payload(captain_decision: dict) -> dict:
    """captain_decision is the top-1 entry from src/decisions/captain.py's existing output."""
    top = captain_decision["picks"][0]                    # top-1
    vice = captain_decision["picks"][1]
    alt = captain_decision["picks"][1]                    # alternative used for gap calc
    return {
        "captain": {
            "name": top["web_name"],
            "position": top["position"],
            "club": top["team_short"],
            "xp_next_gw": round(top["xp"], 1),
        },
        "fixture": {
            "opponent": top["opponent_short"],
            "venue": "home" if top["is_home"] else "away",
            "fdr_attack": top["fdr_attack"],
        },
        "vice": {
            "name": vice["web_name"],
            "xp_next_gw": round(vice["xp"], 1),
        },
        "alternative_gap": round(top["xp"] - alt["xp"], 1),
        "confidence": captain_decision["confidence"],     # 0–100
    }
```

This is a **closed schema** — the prompt builder cannot inject anything else. There is no path
for cookies, `/my-team` data, or `activity_log` rows to reach the model. (B7.)

## §2 The prompt template

`src/ai/prompts/captain.txt`:

```
You are writing one short paragraph that explains an FPL captain pick to the team manager.

Constraints:
- 2 to 3 sentences. Plain English. No emojis. No exclamation marks.
- You may ONLY use numbers that appear in INPUT below. Do not invent any other number.
- Mention the captain's name, the xP value, the fixture (opponent and venue),
  and either the vice or the gap to the alternative — pick whichever reads better.
- Do not editorialise beyond the inputs. Do not predict the future. Do not name other players.
- Output the paragraph only. No preamble, no closing remarks.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

`{examples}` is rendered from `captain_examples.json` at prompt-build time (1–2 exemplars).
`{payload_json}` is the canonical JSON of §1's payload.

## §3 Few-shot exemplars

`src/ai/prompts/captain_examples.json` ships with 2 hand-curated exemplars committed to the repo.
Sketch (the actual prose is finalised in the implementation plan; this is the shape):

```json
[
  {
    "input": {
      "captain": {"name": "Haaland", "position": "FWD", "club": "MCI", "xp_next_gw": 7.2},
      "fixture": {"opponent": "BRE", "venue": "home", "fdr_attack": 2},
      "vice": {"name": "Salah", "xp_next_gw": 5.4},
      "alternative_gap": 1.8,
      "confidence": 82
    },
    "output": "Haaland is the captain this week with 7.2 expected points at home to BRE — the fixture rates 2 on attack difficulty. He clears Salah, the vice, by 1.8 xP, so this is a clean call."
  },
  {
    "input": {
      "captain": {"name": "Saka", "position": "MID", "club": "ARS", "xp_next_gw": 5.6},
      "fixture": {"opponent": "LIV", "venue": "away", "fdr_attack": 4},
      "vice": {"name": "Palmer", "xp_next_gw": 5.3},
      "alternative_gap": 0.3,
      "confidence": 68
    },
    "output": "Saka leads at 5.6 xP away at LIV, but only by 0.3 over Palmer — confidence is 68. The Liverpool fixture rates 4 on attack difficulty, so this is a close call you may want to override."
  }
]
```

Both exemplars are number-grounded by construction — every number in the output appears in the
input. They double as **golden tests** for `is_grounded` in `tests/test_ai_grounding.py`.

## §4 The render flow (captain)

```python
# src/ai/reasoning.py
def render_captain_reasoning(conn, gw: int, captain_decision: dict) -> tuple[str, str]:
    """Read path: returns (prose, source). 'source' ∈ {'ai', 'classic'}."""
    payload = _build_captain_payload(captain_decision)
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "captain", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    return (captain_decision["template_reason"], "classic")    # fallback


def generate_captain_prose(conn, gw: int, captain_decision: dict, *,
                           provider: LLMProvider, model_id: str) -> bool:
    """Write path: called by the scheduler. Returns True on grounded success."""
    payload = _build_captain_payload(captain_decision)
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "captain", rec_hash) is not None:
        return True
    prompt = _build_captain_prompt(payload)
    prose = provider.generate(prompt, max_tokens=200, temperature=0.2)
    ok, ungrounded = grounding.is_grounded(prose, json.dumps(payload, sort_keys=True))
    if not ok:
        logger.warning("ai.captain.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash, "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "captain", rec_hash, prose, model_id)
    return True
```

## §5 Scheduler integration

```python
# src/scheduler.py — inside refresh_and_recompute, after captain/transfer/chip recompute:
if config.ai_enabled(cfg):
    try:
        ai.jobs.generate_ai_reasoning_job(
            conn,
            panes=["captain"],                              # S-A.1: just the captain pane
            provider=ai.provider.OllamaProvider(
                host=config.ai_ollama_host(cfg),
                model=config.ai_ollama_model(cfg),
                timeout_seconds=config.ai_timeout_seconds(cfg),
            ),
            model_id=config.ai_ollama_model(cfg),
            consecutive_failure_backoff=config.ai_backoff(cfg),
        )
    except Exception:
        logger.exception("ai.generate_job_failed")           # never blocks the recompute cycle
```

S-A.2/3/4 add `"transfer"`, `"chip"`, `"deadguard_summary"` to the `panes` list.

## §6 Dashboard surface

`src/interface/queries.py` — the existing captain-pane assembly gains:

```python
prose, source = ai.reasoning.render_captain_reasoning(conn, gw, captain_decision)
captain_pane["reasoning"] = prose
captain_pane["reasoning_source"] = source
```

Frontend types (`frontend/src/lib/types.ts`):

```ts
export interface CaptainPick {
    /* ...existing fields... */
    reasoning: string;
    reasoning_source: 'ai' | 'classic';
}
```

The Svelte captain component renders `reasoning` where it currently renders the template string,
plus a small inline badge — `AI` (default tone) when `reasoning_source === 'ai'`, `classic`
(muted tone) when `'classic'`. The badge is subtle on purpose: it informs without alarming.

## §7 Telegram surface

`src/interface/telegram.py` — `notify_plan` already composes the H-24 preview body. The captain
line currently uses the deterministic template reason. After this slice, the function reads
`render_captain_reasoning(conn, gw, captain_decision)` and uses whichever string comes back. The
Telegram message does **not** show a source badge (terse channel — users care about content, not
provenance). The behaviour is identical to today when `source == 'classic'`.

## Safety & B-rules

- **B2:** `src/ai/reasoning.py` reads `src/decisions/captain.py` outputs in-process; writes to
  `ai_reasoning_cache`; no `src/data/repository` FPL calls, no `src/auth/` import. Interface →
  AI → Decision → Analytics → Data — strict layering preserved.
- **B4:** Untouched. The deterministic captain ranker still picks. No `decision-engine.md`
  change. The LLM only paraphrases. (If a future slice ever lets the LLM *override* the captain
  pick, that requires a `decision-engine.md` versioned entry first — out of scope here.)
- **B7:** Payload schema is closed (§1). The prompt builder is the sole egress to the LLM. No
  credentials, no cookies, no `/my-team` raw response, no `activity_log` rows are in the prompt.
  The `src/ai/` module never imports `src/auth/`.
- **B8:** No execution. AI emits text. Executor and deadguard paths untouched.
- **B9:** The Telegram message keeps the same one-tap Confirm / Reject inline buttons; the prose
  swap is in the body only. Functional copy, no emojis.
- **B10:** `generate_captain_prose` logs `(gw, rec_hash, model_id, latency_ms, grounded,
  prose_chars)` on every attempt. `activity_log` itself is **unchanged** — the AI sub-layer
  doesn't log to it (that's reserved for *decisions*, not their explanations).
- **B11:** Decision-layer tests stay deterministic; this slice does not modify
  `src/decisions/captain.py`. AI-module tests use `StubProvider` (fixtures-only, R3).
- **R3:** LLM has no tools, no write access, never makes an HTTP call to FPL. The agent never
  executes a live write. Output is `str`.

## Testing

All tests are fixtures-only (R3). The full AI-architecture test surface is in the architecture
spec §"Testing"; this slice adds the **captain-pane-specific** tests:

- **`tests/test_ai_reasoning_captain.py`**:
  - `_build_captain_payload` produces the expected narrow dict from a frozen captain-decision
    fixture (verifies key names, types, rounding, closed shape).
  - `_build_captain_prompt` renders the template with `{examples}` populated from the JSON file
    and `{payload_json}` from `_build_captain_payload`. Snapshot test on a fixed payload.
  - `render_captain_reasoning` returns `('ai', cached_prose)` on cache hit; returns `('classic',
    decision.template_reason)` on miss; never calls a provider.
  - `generate_captain_prose` with a `StubProvider` returning grounded prose → `True` + row in
    `ai_reasoning_cache`. With a stub returning ungrounded prose (invents a number not in the
    payload) → `False` + no row + a logged warning. Cache hit on second call → returns `True`
    without calling the provider.
- **`tests/test_ai_prompts_captain_examples.py`**:
  - Every `output` in `captain_examples.json` passes `is_grounded` against its `input`. (This
    means the few-shot file is its own golden test — if someone edits an exemplar in a way that
    breaks grounding, the test fails.)
- **`tests/test_interface_queries_captain.py`** (extends existing):
  - With AI cache populated → captain pane DTO carries `reasoning_source: 'ai'` + the cached
    prose. With AI cache empty → carries `reasoning_source: 'classic'` + the deterministic
    template string.
- **`tests/test_telegram_notify_plan.py`** (extends existing):
  - With AI cache populated → captain line in `notify_plan` body uses cached prose. With cache
    empty → uses template string (same as today). No source-tag rendered in the Telegram body.
- **vitest** (`frontend`):
  - Captain component renders the `reasoning` string. With `reasoning_source === 'ai'` →
    `AI` badge visible. With `'classic'` → muted `classic` label visible.
- `.venv/bin/pytest -q` green; `cd frontend && npm test` green.

## Manual smoke check (out-of-band, by the user — agent never runs Ollama live in tests)

1. `ollama serve` (or confirm Ollama is already up via `ollama list`).
2. Run the scheduler once with `ai.enabled: true` in `config.yaml`: prose appears in
   `ai_reasoning_cache` for `(gw=next_gw, pane_type='captain')` after recompute.
3. Open the dashboard: captain pane shows the LLM prose with the `AI` badge. The numbers in the
   prose match the numbers in the captain ranker's existing template (xP, gap, opponent, FDR).
4. Set `ai.enabled: false` and reload: captain pane shows the template string with the `classic`
   badge — identical content to before this slice shipped.
5. With `ai.enabled: true` but Ollama stopped: scheduler logs `ai.generate_job_failed`; dashboard
   silently falls back to template + `classic` badge; no banner, no broken page.
6. Run `init-fpl` / `auth-status` / `route-gameweek` exactly as before — no auth surface changes,
   no executor surface changes.

## Scope boundary

- **IN:** `src/ai/{provider,reasoning,cache,grounding,jobs}.py`, `src/ai/prompts/captain.txt`,
  `src/ai/prompts/captain_examples.json`, the new `ai_reasoning_cache` migration, scheduler
  integration gated by `ai.enabled`, captain pane wiring in `src/interface/queries.py`, captain
  line wiring in `src/interface/telegram.py`, `frontend/` captain component update + types,
  config schema additions, `docs/architecture.md` diagram update, `docs/onboarding.md` Ollama
  section.
- **OUT (this slice):** transfer / chip / deadguard-summary prose (→ S-A.2 / S-A.3 / S-A.4 —
  same architecture, different prompt + few-shot, one new line in `jobs.py`'s pane list).
- **OUT (Phase 3 later):** conversational interface (S-B), mini-league context (S-C),
  personalization (S-D), scenario simulator (S-E), player-news ingestion (S-F).
- **OUT (forever for this slice):** any change to the captain pick itself, the vice pick, the
  ranking algorithm, the confidence score, or any threshold in `decision-engine.md`. This slice
  is describe-only — those changes are a different slice with a B4 doc entry first.

## Definition of done (CLAUDE.md B14)

- Captain pane prose is LLM-generated when Ollama is running + `ai.enabled: true` + grounding
  check passes; cleanly falls back to the existing template string otherwise.
- Per-pane source tag (`AI` / `classic`) renders correctly in the dashboard.
- Telegram H-24 preview captain line carries the same prose (no badge — terse channel).
- `ai_reasoning_cache` table created via migration; rows are populated by the scheduler after
  `refresh_and_recompute`; cache hits short-circuit provider calls.
- Number-grounding check rejects hallucinated prose; failures are logged + not cached.
- Consecutive-failure backoff prevents Ollama-down spam in the scheduler logs.
- All tests green (pytest + vitest). All tests use `StubProvider`; no live Ollama call in tests.
- `docs/architecture.md` diagram updated; `docs/onboarding.md` mentions the Ollama prerequisite
  + the `ai.enabled` opt-out. **No `docs/decision-engine.md` change** (describe-only).
- The agent never ran a live Ollama call or a live FPL call during implementation (R3 + B11).
- Manual smoke check (steps above) confirmed by the user out-of-band.
