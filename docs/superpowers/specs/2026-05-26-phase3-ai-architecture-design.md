# Phase 3 — Cross-cutting AI Architecture — Design

**Status:** approved 2026-05-26
**Scope:** the AI Layer plumbing that **every Phase-3 slice** reuses — provider client, prompt
builder, cache, grounding check, fallback ladder, and the architectural placement that keeps the
four-layer rule clean. This doc is long-lived: it gains a changelog and revisions as later slices
add panes / surfaces / providers.
**Depends on:** Phase-1 Analytics + Decision Layers (read-only), Phase-2 scheduler (`src/scheduler.py`).
**Source of truth:** this doc for AI-Layer architecture; `docs/architecture.md` for the four-layer
diagram (which this spec amends with a new sub-layer); `docs/decision-engine.md` is **not** touched
by S-A (describe-only first ship — see B4 below).
**Companion docs:** [`2026-05-26-phase3-scope-decomposition.md`](./2026-05-26-phase3-scope-decomposition.md)
(why S-A goes first) and [`2026-05-26-phase3-llm-captain-reasoning-design.md`](./2026-05-26-phase3-llm-captain-reasoning-design.md)
(the first slice that exercises this architecture).

## Goal

Add a thin, testable, replaceable sub-layer that takes the deterministic recommendation outputs
the Decision Layer already produces and renders short, number-grounded natural-language
explanations for the Interface Layer to display. The user, on opening the dashboard one hour
before deadline, reads a paragraph that *says the same thing the template already says* but is
specific to this gameweek's numbers and reads like a teammate rather than a form letter — and
when the LLM is unavailable, sees the existing template unchanged.

## Decisions (locked — brainstorming 2026-05-26)

| Decision | Choice |
|----------|--------|
| Provider | **Local Ollama** at `http://localhost:11434`, behind a one-method `LLMProvider.generate()` interface. Claude-API swap is a ~30-line follow-up if local quality is poor. |
| Model | `qwen2.5:7b-instruct-q4_K_M` (already installed on the box). Low temperature (0.2–0.3), `num_predict` ~200 per pane. |
| Architecture placement | New **sub-layer between Decision and Interface** at `src/ai/`. Decision stays deterministic + fast + testable. Interface reads cached prose; never calls the LLM directly. |
| Prompt shape | **Per-pane structured prompts** with 1–2 few-shot exemplars committed to the repo. One template per pane type (captain / transfer / chip / deadguard summary). JSON-only input. |
| Context shape | **Narrow:** only the inputs the deterministic engine already used (xP values, alternatives, FDR, confidence, fixture). **No** `activity_log` dump, **no** squad dump, **no** mini-league. Premature for ~1 GW of data and harmful for 7B-model attention. |
| Caching | **Pre-warm in scheduler** after `refresh_and_recompute`. New SQLite table `ai_reasoning_cache(gw, pane_type, recommendation_hash, prose, model_id, generated_at)`. Recommendation change → new hash → fresh row. Dashboard reads cache-first. |
| Hallucination guard | **Post-generation number-grounding check** — every numeric token in the prose must appear verbatim in the input payload (regex match against canonicalised input string). Failures are not cached; scheduler logs + backs off after `ai.consecutive_failure_backoff` consecutive failures. |
| Failure UX | **Silent fallback** to the existing template string (no banner — not actionable). **Per-pane source tag** (`"ai"` vs `"classic"`) preserves inspectability — the dashboard shows a tiny "AI" / "classic" badge. |
| B4 | **Untouched** for S-A — describe-only. The deterministic engine still picks. No `decision-engine.md` change. Later slices that change recommendations (S-E, S-F) need a B4 entry first; out of scope for this spec. |
| B7 | Prompt builder is the **sole egress** to the LLM. Accepts typed inputs from `src/decisions/`. No `src/auth/`, no `src/data/repository` access from the AI module. No credentials, no cookies, no `/my-team` raw response is ever in a prompt. |
| R3 | LLM has no tools, no write access, never makes an HTTP call to FPL. Output is `str`. |

## Updated four-layer diagram

`docs/architecture.md` §"High-level layers" is amended with a new optional sub-layer:

```
┌─────────────────────────────────────────────┐
│  Interface Layer                            │
│   PWA dashboard, Telegram bot               │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  AI Reasoning (Phase 3, optional)           │  ← NEW
│   Provider (Ollama / Claude)                │
│   Prompt builder + few-shot                 │
│   Number-grounding check                    │
│   ai_reasoning_cache                        │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  Decision Layer (unchanged)                 │
│   Captain ranker, transfer engine, chips,   │
│   mode router, deadguard                    │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  Analytics Layer (unchanged)                │
└──────────────────┬──────────────────────────┘
                   ↓
┌─────────────────────────────────────────────┐
│  Data Layer (unchanged)                     │
└─────────────────────────────────────────────┘
```

Rules added / preserved:

- The AI Reasoning sub-layer reads the **outputs of** the Decision Layer (via typed in-process
  calls — never the DB directly). It is **strictly downstream** of Decision, **strictly upstream**
  of Interface.
- The Decision Layer is unaware of the AI Reasoning sub-layer. The four-layer "each layer only
  calls the layer immediately below it" rule holds: Interface → AI Reasoning → Decision →
  Analytics → Data.
- If the AI Reasoning sub-layer is disabled (`ai.enabled: false`) or fails, Interface falls back
  to reading the deterministic engine's existing template strings directly. The system degrades
  to Phase-2 behaviour exactly.

## Architecture

```
src/ai/__init__.py              ← public surface: render_*_reasoning(decision) -> str
src/ai/provider.py              ← LLMProvider Protocol; OllamaProvider (httpx); StubProvider (tests)
src/ai/prompts/                 ← per-pane prompt templates + few-shot exemplars
    captain.txt                 ← S-A.1 (this slice)
    captain_examples.json       ← few-shot input→output exemplars
    # transfer.txt, chip.txt, deadguard_summary.txt → added by S-A.2/3/4
src/ai/reasoning.py             ← per-pane render functions: build_prompt → provider.generate
                                   → grounding.check → cache.put; or cache.get for warm path
src/ai/cache.py                 ← put/get on ai_reasoning_cache; hashing helper
src/ai/grounding.py             ← number-extraction + verbatim-match check
src/ai/jobs.py                  ← generate_ai_reasoning_job (called by scheduler after recompute)
src/scheduler.py                ← register generate_ai_reasoning_job after refresh_and_recompute
src/data/migrations/<n>_ai_reasoning_cache.sql   ← new table (matches existing migration style)
src/interface/queries.py        ← get_captain_pane reads cached prose; falls back to template
frontend/src/lib/types.ts       ← Captain pane DTO gains { reasoning, reasoning_source }
frontend/src/lib/components/<CaptainPane>.svelte ← render prose + small "AI"/"classic" badge
docs/architecture.md            ← diagram + rule update (above)
docs/onboarding.md              ← Ollama prerequisite + opt-out via config
```

**B2:** `src/ai/` reads `src/decisions/` outputs (in-process, typed) and writes to a dedicated
cache table. It does not call `src/data/repository` for FPL data and does not import
`src/auth/`. It is in-process callable from `src/scheduler.py` (pre-warm) and `src/interface/queries.py`
(read cache). This preserves the four-layer rule with the AI sub-layer inserted cleanly.

## §1 Provider interface

```python
# src/ai/provider.py
from typing import Protocol

class LLMProvider(Protocol):
    def generate(self, prompt: str, *, max_tokens: int = 200, temperature: float = 0.2) -> str: ...

class OllamaProvider:
    """Minimal HTTP client against Ollama's /api/generate endpoint.

    No retries, no streaming, no chat history — single-shot completion only.
    Timeouts come from ai.timeout_seconds (default 15s).
    """
    def __init__(self, host: str, model: str, timeout_seconds: float): ...
    def generate(self, prompt: str, *, max_tokens: int = 200, temperature: float = 0.2) -> str:
        # httpx.post(f"{host}/api/generate", json={...}, timeout=timeout_seconds)
        # returns response["response"].strip()
        ...

class StubProvider:
    """Test/fixture provider — returns a canned string. Used in all tests (R3)."""
    def __init__(self, fixed_response: str = "<stub prose>"): ...
    def generate(self, prompt: str, *, max_tokens: int = 200, temperature: float = 0.2) -> str: ...
```

One function. Anything more is YAGNI for a one-user tool. Swapping to Claude API is a new class
implementing the same Protocol, swapped via `config.ai.provider`.

## §2 Cache table + helpers

New migration `src/data/migrations/<n>_ai_reasoning_cache.sql`:

```sql
CREATE TABLE IF NOT EXISTS ai_reasoning_cache (
    gw                   INTEGER NOT NULL,
    pane_type            TEXT    NOT NULL,    -- 'captain' / 'transfer' / 'chip' / 'deadguard_summary'
    recommendation_hash  TEXT    NOT NULL,    -- sha256 hex of canonicalised inputs
    prose                TEXT    NOT NULL,
    model_id             TEXT    NOT NULL,    -- e.g. 'qwen2.5:7b-instruct-q4_K_M'
    generated_at         TIMESTAMP NOT NULL,
    PRIMARY KEY (gw, pane_type, recommendation_hash)
);
CREATE INDEX IF NOT EXISTS idx_ai_reasoning_cache_lookup
    ON ai_reasoning_cache (gw, pane_type, generated_at DESC);
```

Cache helpers in `src/ai/cache.py`:

```python
def recommendation_hash(payload: dict) -> str:
    # sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')))[:32]
    ...

def get(conn, gw: int, pane_type: str, rec_hash: str) -> dict | None:
    # returns {"prose": ..., "model_id": ..., "generated_at": ...} or None
    ...

def put(conn, gw: int, pane_type: str, rec_hash: str, prose: str, model_id: str) -> None: ...
```

Invalidation is automatic: when the deterministic engine's output changes (new xP recompute, new
captain pick, new alternatives), the hash changes and a new row is generated. Old rows stay until
a GW-rollover GC pass (deferred — disk cost is trivial).

## §3 Grounding check

```python
# src/ai/grounding.py
import re

NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?")

def numbers_in(text: str) -> set[str]:
    return set(NUMERIC_RE.findall(text))

def is_grounded(prose: str, input_payload_text: str) -> tuple[bool, set[str]]:
    """Every numeric token in `prose` must appear in `input_payload_text` verbatim.
    Returns (ok, set_of_ungrounded_numbers)."""
    inp = numbers_in(input_payload_text)
    out = numbers_in(prose)
    ungrounded = out - inp
    return (not ungrounded, ungrounded)
```

Ungrounded prose is **not cached**. The scheduler logs the failure (`pane_type`, `gw`, the
specific ungrounded numbers, the model id) and continues. This is the practical hallucination
guard for the 7B model: it cannot make up an xP value or a price that the input did not contain.

Known limitation: the check is purely lexical. The model can still misattribute a number (call
Haaland's xP "Salah's xP") without triggering the check. Mitigation: keep prompts tight, per-pane
few-shots good, output cap small. If misattribution proves a real failure mode in practice, S-A
follow-ups can tighten to "every number in prose must match its labelled key from the input" —
out of scope for the first ship.

## §4 Per-pane render flow

Each pane type has the same flow, parameterised by template + payload-builder:

```python
# src/ai/reasoning.py (sketch)
def render_captain_reasoning(conn, gw: int, decision: dict, *, provider: LLMProvider,
                             model_id: str) -> tuple[str, str]:
    """Returns (prose, source) where source ∈ {'ai', 'classic'}."""
    payload = _build_captain_payload(decision)           # narrow, typed
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "captain", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    # cache miss → caller decides whether to generate (scheduler) or fall back (interface)
    return (decision["template_reason"], "classic")

def generate_captain_prose(conn, gw: int, decision: dict, *, provider, model_id: str) -> bool:
    """Called by the scheduler. Generates, grounding-checks, caches. Returns True on success."""
    payload = _build_captain_payload(decision)
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "captain", rec_hash) is not None:
        return True                                       # already warm
    prompt = _build_prompt("captain", payload)
    prose = provider.generate(prompt, max_tokens=200, temperature=0.2)
    ok, ungrounded = grounding.is_grounded(prose, json.dumps(payload, sort_keys=True))
    if not ok:
        logger.warning("ai.grounding_failed", extra={...})
        return False
    cache.put(conn, gw, "captain", rec_hash, prose, model_id)
    return True
```

The scheduler calls `generate_*_prose` for each pane in turn. The interface calls
`render_*_reasoning` on read — which returns AI prose if cached, template if not.

## §5 Scheduler integration

```python
# src/ai/jobs.py
def generate_ai_reasoning_job(conn, *, provider: LLMProvider, model_id: str,
                              consecutive_failure_backoff: int = 3) -> dict:
    """Walk the active pane types for the next GW; warm cache for each.
    Returns {pane_type: 'ok'|'cached'|'failed'|'skipped_backoff'} for logging."""
    ...

# src/scheduler.py — inside refresh_and_recompute, after the recompute step completes:
if config.ai_enabled(cfg):
    try:
        generate_ai_reasoning_job(conn, provider=provider, model_id=model_id, ...)
    except Exception:
        logger.exception("ai.generate_job_failed")     # never blocks the recompute cycle
```

Backoff is per-pane and per-job-run: 3 consecutive failures → skip the pane for this run; next
recompute cycle retries cleanly. No persistent freeze (the AI sub-layer is purely additive — no
need to mirror the heavyweight `override.maybe_auto_freeze` mechanism).

## §6 Interface integration

```python
# src/interface/queries.py — extend the existing captain-pane assembly:
prose, source = reasoning.render_captain_reasoning(conn, gw, captain_decision,
                                                   provider=stub_or_real, model_id=model_id)
captain_pane["reasoning"] = prose
captain_pane["reasoning_source"] = source        # 'ai' or 'classic'
```

The interface holds **no provider state** — it asks `render_*` for the cached prose, which can
return template if nothing is cached. The scheduler is the only path that calls `provider.generate`
in production. (Tests inject `StubProvider`.)

Frontend types gain:

```ts
export interface CaptainPane {
    /* ...existing... */
    reasoning: string;
    reasoning_source: 'ai' | 'classic';
}
```

`Captain.svelte` renders the prose + a small badge (`AI` for `'ai'`, no badge or muted `classic`
label for `'classic'`). Source tag is intentionally subtle — the user shouldn't be alarmed by
seeing `classic`, just informed.

## §7 Config schema

`config.yaml` gains:

```yaml
ai:
  enabled: true                                    # master kill-switch
  provider: ollama                                 # ollama | claude (claude added later)
  ollama:
    host: "http://localhost:11434"
    model: "qwen2.5:7b-instruct-q4_K_M"
  timeout_seconds: 15
  consecutive_failure_backoff: 3                   # per pane per scheduler run
  temperature: 0.2
  max_tokens_per_pane: 200
```

Defaults are friendly: `ai.enabled: true` so the system uses AI prose out of the box on a machine
where Ollama is running, but a single config flag disables it entirely if the user wants the old
template behaviour back.

## Safety & B-rules

- **B2:** AI sub-layer sits between Decision and Interface; reads `src/decisions/` outputs only;
  does not call `src/data/repository` for FPL data; does not import `src/auth/`. Interface →
  AI → Decision → Analytics → Data — strict layering preserved.
- **B4:** **Untouched.** The deterministic engine still produces every recommendation. No
  `decision-engine.md` change. Future slices (S-E scenario sim, S-F news ingestion) that change
  *what* is recommended require their own `decision-engine.md` entry first; out of scope here.
- **B7:** Prompt builder is the sole egress to the LLM, accepts typed `decision` inputs only.
  No credentials, no cookies, no `/my-team` response is ever in a prompt. The AI sub-layer never
  imports `src/auth/`. Inputs are explicitly narrowed (xP, FDR, alternatives, confidence) — there
  is no path for credentials to slip in through a wide payload.
- **B8:** No execution. AI emits text. Executor, deadguard, chip-execution paths untouched.
- **B10:** Every generation attempt logs `(gw, pane_type, model_id, latency_ms, grounded, prose_chars)`
  — cache hit / miss / fail rates are inspectable. Activity log is unchanged (the AI sub-layer
  doesn't log to `activity_log` — that's reserved for *decisions*).
- **B11:** Decision-layer tests stay deterministic — they don't touch the AI module. AI-module
  tests use `StubProvider` (fixtures-only, R3).
- **R3:** LLM has no tools, no write access, never makes an HTTP call to FPL. Output is a
  `str` rendered into HTML.

## Testing

All tests are fixtures-only (R3): no live Ollama call, no network. `StubProvider` returns canned
prose for each test case. Test surface for this architecture (slice-spec adds per-pane tests):

- **`tests/test_ai_provider.py`** — `OllamaProvider.generate` calls the right URL + JSON shape
  (`respx` or `httpx_mock`); honours `timeout_seconds`; returns `response["response"].strip()`;
  raises on non-200. `StubProvider.generate` returns its `fixed_response`.
- **`tests/test_ai_cache.py`** — `recommendation_hash` is stable across dict key orderings;
  `get` returns `None` for misses; `put` then `get` round-trips; identical payload → same hash;
  any change to payload → different hash; PK conflict is upsert-friendly (or test the
  `INSERT OR REPLACE` behaviour we choose).
- **`tests/test_ai_grounding.py`** — `numbers_in` extracts ints + decimals; `is_grounded` is `True`
  when every prose number appears in input; `False` with the precise ungrounded set when not.
- **`tests/test_ai_jobs.py`** — `generate_ai_reasoning_job` calls provider per pane; caches on
  grounded success; logs + skips on grounding failure; honours `consecutive_failure_backoff`;
  cache hits on second run.
- **`tests/test_ai_interface.py`** — `render_captain_reasoning` returns `('ai', cached_prose)`
  on hit, `('classic', decision.template_reason)` on miss; never calls provider on read.

`vitest` for the frontend:
- `Captain.svelte` (or the actual file name) renders `reasoning` and shows `AI` badge when
  `reasoning_source === 'ai'`, no badge / muted label when `'classic'`.

## Scope boundary

- **IN:** `src/ai/{provider,reasoning,cache,grounding,jobs}.py`, `src/ai/prompts/`, the new
  `ai_reasoning_cache` migration, scheduler integration, config schema additions, interface
  read path for the **captain pane only** (other panes added in S-A.2/3/4 reusing this exact
  architecture), `docs/architecture.md` diagram update, `docs/onboarding.md` Ollama section.
- **OUT (this spec):** the captain prompt template itself, the few-shot exemplar JSON, the
  captain pane-specific render function — those are S-A.1's slice spec.
- **OUT (Phase 3 later):** transfer/chip/deadguard-summary panes (S-A.2/3/4), conversational
  surface (S-B), mini-league context (S-C), personalization from `activity_log` (S-D), scenario
  simulator (S-E), player-news ingestion (S-F), Claude-API provider (added as a sibling of
  `OllamaProvider` only if local quality is insufficient — same `LLMProvider` Protocol).
- **OUT (forever for S-A):** any change to *what* the engine recommends. That is a B4 change and
  belongs in a different slice with a `decision-engine.md` entry first.

## Definition of done (CLAUDE.md B14)

This architecture spec is done when the three companion files exist and agree:

- The first-slice spec (S-A.1 captain reasoning) references this doc for provider, prompt,
  cache, grounding, fallback, config, and B-rule stance — and adds only the captain-specific
  template + few-shot + render function on top.
- A future `writing-plans` plan can be derived from these specs alone (no further brainstorming
  needed before implementation begins next session).
- No `decision-engine.md` change (S-A is describe-only).
- No code in `src/` and no installed package (this session is brainstorm-only).

## Changelog

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-05-26 | Initial architecture: Ollama provider, qwen2.5:7b-instruct-q4_K_M, AI sub-layer between Decision and Interface, per-pane structured prompts + few-shot, narrow JSON context, pre-warm cache in scheduler, post-generation number-grounding check, silent fallback + per-pane source tag, B4/B7/R3 stance locked. |
