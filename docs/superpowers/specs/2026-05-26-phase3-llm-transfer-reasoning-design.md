# LLM Transfer Reasoning — Design (Phase 3, S-A.2)

**Status:** approved 2026-05-26
**Slice:** Phase 3 S-A.2 — second slice of the AI-reasoning family. Adds LLM prose to the dashboard's
**top transfer suggestion** + the corresponding Telegram H-24 line. Mirrors S-A.1's pattern at the
*surface* but with a richer payload (fixture-aware) because the chip-based transfer pane already
shows the structured numbers — the AI's value-add is the *why*, not the *what*.
**Depends on:** Phase-3 S-A.1 (`src/ai/{provider,reasoning,cache,grounding,jobs}.py`, the
`ai_reasoning_cache` table, the scheduler hook, the `ai_*` config accessors); Phase-1 transfers
engine (`src/decisions/transfers.py`); Phase-1 dashboard (`frontend/src/lib/components/TransferIdeas.svelte`).
**Cross-cutting design (reused, not re-derived):**
[`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md).
**Scope-decomposition rationale:**
[`2026-05-26-phase3-scope-decomposition.md`](./2026-05-26-phase3-scope-decomposition.md) §"S-A".
**Source of truth for this slice:** this doc. **`docs/decision-engine.md` is NOT touched** —
describe-only, the deterministic transfers engine still picks. B4 untouched.

## Goal

When the dashboard shows the top transfer suggestion (e.g. `Salah → Saka, +3.4 EP, free, 78%`),
add a one-paragraph LLM-generated explanation **below the chip row** explaining the move with
fixture context — something the chips themselves can't show. When the LLM is unavailable, the
chips render exactly as today with no prose line (silent fallback, no banner). The Telegram H-24
transfer line uses the same cached prose when present; falls back to the existing terse template
otherwise. The deterministic engine is unchanged; the AI never overrides the ranker's choice.

## Why this slice is different from S-A.1

S-A.1's captain pane displays `reason` as the primary content (the engine's template line was the
*only* thing there). S-A.2's transfer pane displays **chips** (out → in, EP delta, hit, confidence)
— the structured numbers are already glanceable. If the AI payload contained only those structured
fields, the prose would just paraphrase what the user already sees. **For S-A.2 to add value, the
payload must include fixture-context the chips don't show.** The fixture difficulty over the next
3 GWs is the engine's actual reason for ranking this swap above others — surfacing it in prose is
the slice's whole point.

## Decisions (locked — brainstorming 2026-05-26)

| Decision | Choice |
|----------|--------|
| Scope of AI prose | **Top suggestion only** (mirrors captain). Suggestions #2 and #3 keep chip-only rendering with no badge. One LLM call per recompute for the transfer pane. |
| Payload depth | **Rich:** both players' next-3-GW fixtures (`opponent_short`, `home`, `fdr_attack` 1–5) + `status` flag + existing fields (`web_name`, `position`, `price`, `xp_5gw`, `ep_delta_5gw`, `hit_cost`, `confidence`, `free_transfers`). xp values rounded to 1dp following the S-A.1 lesson (precision must match exemplar style or model recomputes). |
| Telegram swap | **Extended to transfer.** `notify_plan` adds a `_transfer_ai_prose` helper analogous to `_captain_ai_prose`. When cached prose exists for the transfer pane at the next GW, the transfer entry's summary in the Telegram body uses the prose instead of the template. |
| Frontend layout | **Prose below the top chip row**, italic, with the `AI` badge inline at the end of the prose line. Suggestions #2 and #3 unchanged. Backwards-compatible: if `reasoning`/`reasoning_source` aren't on the payload, the prose line + badge don't render. |
| B4 | **Untouched.** No `docs/decision-engine.md` change. Deterministic ranker still picks. |
| Cache identity | New `pane_type = 'transfer'`. The `recommendation_hash` covers the top-suggestion's payload — any change to the ranker output (new top suggestion, different EP delta) invalidates the row automatically. |

## Architecture (delta from S-A.1)

```
src/ai/prompts/transfer.txt              ← NEW: per-pane template (mirrors captain.txt)
src/ai/prompts/transfer_examples.json    ← NEW: 2 hand-curated exemplars, self-validating
src/ai/reasoning.py                      ← EXTEND: _build_transfer_payload, _build_transfer_prompt,
                                           render_transfer_reasoning, generate_transfer_prose
src/ai/jobs.py                           ← EXTEND: 'transfer' branch in the pane walker
src/interface/queries.py                 ← NEW: get_transfer_suggestions (wraps transfers engine,
                                           enriches top suggestion with reasoning+source), and
                                           extends get_transfer_reasoning helper for Telegram
src/interface/api.py                     ← EDIT: /api/transfers calls queries.get_transfer_suggestions
src/interface/telegram.py                ← EXTEND: _transfer_ai_prose helper + notify_plan swap
                                           when entry.decision == 'transfer'
src/scheduler.py                         ← EDIT: panes list extends to ['captain', 'transfer']
                                           (single-line change inside the existing ai_jobs call)
frontend/src/lib/types.ts                ← EXTEND: TransferSuggestion gains optional
                                           reasoning?: string; reasoning_source?: 'ai' | 'classic'
frontend/src/lib/components/TransferIdeas.svelte ← EDIT: render reasoning line + AI/classic badge
                                           on top suggestion only
frontend/src/lib/mocks/full.ts           ← EDIT: top transfer mock gets reasoning + reasoning_source

# NEW tests
tests/test_ai_prompts_transfer.py        ← golden test for transfer_examples.json grounding
tests/test_ai_reasoning_transfer.py      ← payload + prompt + render + generate (mirrors S-A.1)

# EXTENDED tests
tests/test_ai_jobs.py                    ← +cases for 'transfer' pane
tests/test_api.py                        ← +cases for /api/transfers enriched payload
tests/test_telegram.py                   ← +cases for transfer summary swap
tests/test_scheduler.py                  ← +case for ['captain','transfer'] pane list
frontend/src/lib/components/TransferIdeas.svelte.test.ts ← +cases for prose + badge

docs/architecture.md                     ← OPTIONAL: changelog note if anyone wants it (no diagram change)
```

**B2 (layer boundaries):** `src/ai/reasoning.py` reads Decision-layer outputs in-process; writes only to
`ai_reasoning_cache`. Does NOT import `src/auth/`, does NOT touch `src/data/repository` for FPL data.
Interface → AI → Decision → Analytics → Data preserved. The new fixture/FDR queries are scoped to
the AI payload builder — they read the existing `fixtures` + `fdr` tables (Data Layer) via the
already-injected `conn`, which is appropriate for an Interface-adjacent enrichment.

## §1 Transfer payload shape

The payload is built from the top suggestion (`get_transfer_suggestions(conn)["suggestions"][0]`)
plus targeted fixture/FDR lookups for OUT + IN players over the next 3 GWs:

```python
def _build_transfer_payload(conn, transfer_decision: dict) -> dict | None:
    """Build a closed-shape payload for the top transfer suggestion.
    Returns None if no suggestions (LLM has nothing to render).
    """
    suggestions = transfer_decision.get("suggestions", [])
    if not suggestions:
        return None
    top = suggestions[0]
    next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    out_fixtures = _fixtures_for(conn, top["out"]["player_id"], next_gw, horizon=3)
    in_fixtures  = _fixtures_for(conn, top["in"]["player_id"],  next_gw, horizon=3)
    out_status   = _status_for(conn, top["out"]["player_id"])
    in_status    = _status_for(conn, top["in"]["player_id"])
    return {
        "out": {
            "web_name": top["out"]["web_name"],
            "price": top["out"]["price"],
            "status": out_status,
            "fixtures_3gw": out_fixtures,    # list of {opponent, home: bool, fdr_attack: int}
        },
        "in": {
            "web_name": top["in"]["web_name"],
            "price": top["in"]["price"],
            "status": in_status,
            "fixtures_3gw": in_fixtures,
        },
        "ep_delta_5gw": round(top["ep_delta_5gw"], 1),    # 1dp matches S-A.1 lesson
        "hit_cost": top["hit_cost"],
        "confidence": top["confidence"],
        "free_transfers": transfer_decision.get("free_transfers"),
    }
```

Helper `_fixtures_for(conn, player_id, next_gw, horizon=3)`:
```python
def _fixtures_for(conn, player_id, next_gw, horizon):
    """Return up to `horizon` fixture dicts for the player's team, starting from next_gw.
    Each dict: {opponent: short_name, home: bool, fdr_attack: int}.
    Blank gameweeks are skipped (the list may be shorter than `horizon`).
    Double gameweeks: both fixtures appear as separate list entries (one per fixture row).
    """
```

Helper `_status_for(conn, player_id) -> str` reads `players.status` (single char: `a`/`d`/`i`/`s`/`u`).

**Closed-schema discipline (B7):** the payload type is a typed dict literal. No path for credentials,
cookies, or `/my-team` raw responses to reach the LLM. The payload builder is the sole egress.

## §2 Prompt template + few-shot exemplars

`src/ai/prompts/transfer.txt`:

```
You are explaining one FPL transfer to the team manager. Sell OUT, buy IN.

Constraints:
- 2 to 3 sentences. Plain English. No emojis. No exclamation marks.
- You may ONLY use numbers that appear in INPUT below. Do not invent any other number.
- Mention WHY the move helps: contrast the fixtures of OUT and IN over the listed gameweeks,
  reference the EP gain, and note any status concern.
- Do not editorialise beyond the inputs. Do not predict future scorelines. Do not name other players.
- Output the paragraph only. No preamble, no closing remarks.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

`src/ai/prompts/transfer_examples.json` — 2 hand-curated exemplars where every numeric token in
`output` also appears in `input` (golden-tested by `test_ai_prompts_transfer.py`):

```json
[
  {
    "input": {
      "out": {"web_name": "Salah", "price": 13.1, "status": "a",
              "fixtures_3gw": [
                {"opponent": "BRE", "home": false, "fdr_attack": 4},
                {"opponent": "EVE", "home": true,  "fdr_attack": 2},
                {"opponent": "ARS", "home": false, "fdr_attack": 5}
              ]},
      "in": {"web_name": "Saka", "price": 10.4, "status": "a",
             "fixtures_3gw": [
               {"opponent": "BHA", "home": true,  "fdr_attack": 2},
               {"opponent": "WHU", "home": true,  "fdr_attack": 3},
               {"opponent": "FUL", "home": false, "fdr_attack": 3}
             ]},
      "ep_delta_5gw": 3.4, "hit_cost": 0, "confidence": 78, "free_transfers": 1
    },
    "output": "Sell Salah, buy Saka — Saka has 2 home fixtures including BHA at fdr 2, while Salah faces ARS away at fdr 5 in the same window. The free transfer adds 3.4 EP over 5 GWs at confidence 78."
  },
  {
    "input": {
      "out": {"web_name": "Watkins", "price": 9.0, "status": "d",
              "fixtures_3gw": [
                {"opponent": "LIV", "home": false, "fdr_attack": 5},
                {"opponent": "MCI", "home": false, "fdr_attack": 5}
              ]},
      "in": {"web_name": "Isak", "price": 9.3, "status": "a",
             "fixtures_3gw": [
               {"opponent": "WOL", "home": true,  "fdr_attack": 2},
               {"opponent": "CRY", "home": false, "fdr_attack": 3},
               {"opponent": "BHA", "home": true,  "fdr_attack": 2}
             ]},
      "ep_delta_5gw": 2.1, "hit_cost": 0, "confidence": 70, "free_transfers": 1
    },
    "output": "Sell Watkins, buy Isak — Watkins carries a doubt and faces LIV and MCI away at fdr 5, while Isak has 2 home fixtures at fdr 2. The free transfer adds 2.1 EP over 5 GWs at confidence 70."
  }
]
```

The exemplars demonstrate:
- Fixture contrast (the value-add over the chips)
- Status mention when OUT carries a flag
- Concrete numbers preserved verbatim (every number in `output` appears in `input` — golden-tested)

## §3 Render + generate functions

In `src/ai/reasoning.py`, mirror the S-A.1 captain functions:

```python
def render_transfer_reasoning(conn, gw: int, transfer_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> ('<cached prose>', 'ai'); miss -> ('', 'classic').
    Empty suggestions -> ('', 'classic').
    Note: unlike captain (which has a template `reason` field on each pick), the transfers engine
    doesn't emit a per-suggestion prose template. On 'classic', we return an empty string and
    the frontend handles "no prose line" naturally (the chips already convey the data).
    """
    payload = _build_transfer_payload(conn, transfer_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "transfer", rec_hash)
    return (hit["prose"], "ai") if hit is not None else ("", "classic")


def generate_transfer_prose(conn, gw: int, transfer_decision: dict, *,
                            provider, model_id: str,
                            max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Same flow as generate_captain_prose: payload → cache check → prompt
    → provider.generate → grounding → cache.put on success. Provider errors caught; empty/ungrounded
    prose not cached.
    """
    ...   # exact mirror of generate_captain_prose, swapping captain → transfer
```

The grounding check is the same `is_grounded(prose, json.dumps(payload, sort_keys=True))`. With ~12–16
numbers in the payload, the grounding surface is larger but still tractable (regex-extracted set
difference). The empty-prose guard from S-A.1's fix carries over (`if not prose: return False`).

## §4 jobs.py extension

In `src/ai/jobs.py`, add a `transfer` branch to the pane-walker:

```python
def _default_transfer_decision_fn(conn):
    from src.decisions import transfers
    return transfers.get_transfer_suggestions(conn)


def generate_ai_reasoning_job(conn, *, panes, provider, model_id,
                              captain_decision_fn=None, transfer_decision_fn=None) -> dict:
    ...
    transfer_fn = transfer_decision_fn or _default_transfer_decision_fn
    for pane in panes:
        if pane == "captain":
            ...
        elif pane == "transfer":
            decision = transfer_fn(conn)
            ok = reasoning.generate_transfer_prose(
                conn, gw=gw, transfer_decision=decision,
                provider=provider, model_id=model_id)
            result[pane] = "ok" if ok else "failed"
        else:
            ...
```

Scheduler integration (`src/scheduler.py`): change `panes=["captain"]` → `panes=["captain", "transfer"]`
inside the existing AI hook in `refresh_and_recompute`. One-line edit.

## §5 Interface — queries.get_transfer_suggestions + api.py rewiring

`src/interface/queries.py` adds:

```python
def get_transfer_suggestions(conn):
    """Wraps src.decisions.transfers.get_transfer_suggestions and enriches the TOP suggestion
    with (reasoning, reasoning_source). Other suggestions get reasoning='' + reasoning_source='classic'.
    """
    from src.decisions import transfers as transfers_engine
    from src.ai import reasoning as ai_reasoning
    decision = transfers_engine.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_transfer_reasoning(conn, gw, decision)
    enriched = list(decision["suggestions"])
    enriched[0] = {**enriched[0], "reasoning": prose, "reasoning_source": source}
    for i in range(1, len(enriched)):
        enriched[i] = {**enriched[i], "reasoning": "", "reasoning_source": "classic"}
    return {**decision, "suggestions": enriched}


def get_transfer_reasoning(conn, gw):
    """Cheap lookup for the Telegram path. Returns cached AI prose or None."""
    from src.decisions import transfers as transfers_engine
    from src.ai import reasoning as ai_reasoning
    decision = transfers_engine.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return None
    prose, source = ai_reasoning.render_transfer_reasoning(conn, gw, decision)
    return prose if source == "ai" else None
```

`src/interface/api.py`: `/api/transfers` switches from calling `transfers_engine.get_transfer_suggestions(conn)`
directly to `queries.get_transfer_suggestions(conn)`. (The `transfers_engine` alias may stay as it's
imported alongside `chips_engine` etc.; or be dropped if no other endpoint uses it — verify at impl
time per S-A.1's task-11 cleanup pattern.)

## §6 Telegram — notify_plan swap extension

In `src/interface/telegram.py`, add `_transfer_ai_prose` mirroring `_captain_ai_prose`:

```python
def _transfer_ai_prose(conn):
    try:
        from src.interface import queries
        nxt = conn.execute(
            "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        if nxt is None or nxt["gw"] is None:
            return None
        return queries.get_transfer_reasoning(conn, gw=nxt["gw"])
    except Exception:
        return None


def notify_plan(conn, plan, *, mode, session=None):
    if not is_configured():
        return
    captain_prose  = _captain_ai_prose(conn)
    transfer_prose = _transfer_ai_prose(conn)
    for entry in plan:
        kind = "executed" if entry["executed"] else "info"
        summary = entry["summary"]
        if entry["decision"] == "captain"  and captain_prose:  summary = captain_prose
        if entry["decision"] == "transfer" and transfer_prose: summary = transfer_prose
        notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
               summary=summary, session=session)
```

Both swaps are best-effort: any exception in the lookup returns to the template summary.

## §7 Frontend — TransferIdeas.svelte rendering

`frontend/src/lib/types.ts`:

```ts
export interface TransferSuggestion {
    /* ...existing fields: out, in, ep_delta_5gw, hit_cost, confidence... */
    reasoning?: string;
    reasoning_source?: 'ai' | 'classic';
}
```

`frontend/src/lib/components/TransferIdeas.svelte` — current markup iterates `transfers.suggestions`
and renders chips per `<li>`. Change: on the TOP suggestion (index 0) only, after the chip row,
render a prose line if `s.reasoning` is non-empty, with an inline badge:

```svelte
{#each transfers.suggestions as s, i (s.out.player_id + '-' + s.in.player_id)}
    <li class="xfer">
        <div class="move">
            <span class="out">{s.out.web_name}</span>
            <span class="arrow">→</span>
            <span class="in">{s.in.web_name}</span>
        </div>
        <div class="nums tnum">
            <span class="delta">{fmtDelta(s.ep_delta_5gw)} EP</span>
            <span class="hit" class:free={s.hit_cost === 0}>
                {s.hit_cost === 0 ? 'free' : s.hit_cost}
            </span>
            <span class="conf">{s.confidence}%</span>
        </div>
        {#if i === 0 && s.reasoning}
            <div class="why">
                <em>{s.reasoning}</em>
                {#if s.reasoning_source === 'ai'}
                    <span class="badge badge-ai" aria-label="AI-generated reasoning">AI</span>
                {:else}
                    <span class="badge badge-classic" aria-label="Template-based reasoning">classic</span>
                {/if}
            </div>
        {/if}
    </li>
{/each}
```

CSS: reuse the `.badge`, `.badge-ai`, `.badge-classic` classes from `CaptainPicks.svelte` (or extract
to a shared component if a third pane lands later). Add `.why` styling: small, dim text, italic,
mt-1.

Backwards-compat: when `s.reasoning` is empty (classic on a fresh DB) or absent (older API shape),
the `{#if i === 0 && s.reasoning}` guard renders nothing — chips are unchanged. Good for the
realistic "no prose cached yet" state.

**Mocks:** `frontend/src/lib/mocks/full.ts` — find the `transfers.suggestions[0]` block, add
`reasoning: 'Sell Salah, buy Saka — ...'` + `reasoning_source: 'ai' as const`. The other suggestions
get the same fields with empty `reasoning` + `'classic'` so the type shape is consistent in mock mode.

## Safety & B-rules

- **B2:** `src/ai/reasoning.py` reads Decision-layer outputs in-process; writes only to
  `ai_reasoning_cache`. New fixture/FDR helper queries are scoped to the AI payload builder, which
  is itself called from Interface (queries.py) and Scheduler (via jobs.py) — both are upstream of
  the Decision layer in the four-layer model. No layer inversion.
- **B4:** Untouched. The deterministic transfers engine still picks. No `decision-engine.md`
  change. The LLM only paraphrases. (Any future slice where the LLM overrides the engine's ranked
  list requires a `decision-engine.md` versioned entry first — out of scope here.)
- **B7:** Payload is a closed dict literal. No credentials, cookies, `/my-team` raw responses, or
  `activity_log` rows reach the LLM. `src/ai/` does not import `src/auth/`.
- **B8:** No executor changes. No transfer write path touched. The LLM cannot trigger a transfer
  POST — it emits text.
- **B9:** Telegram inline buttons unchanged. Only the body's summary text is swapped when prose is
  available.
- **B10:** Same logging as S-A.1 — `ai.transfer.empty_prose` / `provider_error` / `grounding_failed`
  with `gw`, `model_id`, and (for grounding) the offending number set. No `activity_log` writes
  from the AI sub-layer (`activity_log` is for *decisions*, not their explanations).
- **B11:** Decision-layer tests stay deterministic. New AI tests use `StubProvider` (R3).
- **R3:** LLM has no tools, no write access, never makes an HTTP call to FPL. All tests fixtures-only.

## Testing

All tests fixtures-only (R3). `StubProvider` everywhere. Surface:

- **`tests/test_ai_prompts_transfer.py`** — `transfer_examples.json` golden tests:
  - Both exemplars pass `grounding.is_grounded(output, json.dumps(input, sort_keys=True))`
  - File parses as a list of `{input, output}` dicts
  - Template has `{examples}` and `{payload_json}` placeholders
- **`tests/test_ai_reasoning_transfer.py`** — payload builder, prompt builder, render, generate:
  - `_build_transfer_payload` produces the closed-shape dict from a frozen fixture
  - `_build_transfer_payload` returns `None` on empty suggestions
  - `_build_transfer_payload` rounds `ep_delta_5gw` to 1dp (regression test for S-A.1's float lesson)
  - `_build_transfer_prompt` substitutes `{examples}` and `{payload_json}` (no markers remain)
  - `render_transfer_reasoning`: cache hit → `('<prose>', 'ai')`; miss → `('', 'classic')`;
    empty suggestions → `('', 'classic')`
  - `generate_transfer_prose`: grounded prose caches; empty/ungrounded/exception → `False` + no row;
    cache hit short-circuits provider
- **`tests/test_ai_jobs.py`** — extend:
  - `panes=['transfer']` → calls generate_transfer_prose; result includes `'transfer'` key
  - `panes=['captain', 'transfer']` → both panes processed; result has both keys
  - Unknown pane still logged + `'skipped'`
- **`tests/test_api.py`** — extend:
  - `/api/transfers` with no AI cache → suggestions all have `reasoning_source: 'classic'` + `reasoning: ''`
  - With AI cache populated for the top suggestion → `suggestions[0].reasoning_source == 'ai'` +
    cached prose; `suggestions[1..N].reasoning_source == 'classic'`
- **`tests/test_telegram.py`** — extend:
  - notify_plan with cached transfer prose for the next gw → captain entry uses captain prose, transfer
    entry uses transfer prose; both swapped independently
  - notify_plan without cached transfer prose → transfer entry uses template summary
- **`tests/test_scheduler.py`** — extend:
  - `refresh_and_recompute` with `ai.enabled=True` calls `generate_ai_reasoning_job` with
    `panes=['captain', 'transfer']` (assertion via monkeypatch capture)
- **vitest** (`TransferIdeas.svelte.test.ts`) — extend:
  - Top suggestion with `reasoning_source: 'ai'` + non-empty `reasoning` → prose visible + `AI` badge
  - Top suggestion with `reasoning_source: 'classic'` + empty `reasoning` → no prose line, no badge
  - Suggestion at index ≥1 with `reasoning_source: 'ai'` (shouldn't happen in practice, but defensive)
    → no badge rendered
  - Backwards-compat: missing `reasoning`/`reasoning_source` keys → component renders as today
    (chips only)

Live smoke (out-of-band, by the user — agent never runs Ollama live):
1. Ensure GW38 in demo-poke state (per the S-A.1 demo's earlier instructions).
2. Run `refresh_and_recompute()` → both `('captain','ok')` and `('transfer','ok')` in result.
3. `sqlite3 data/fpl_autopilot.db "SELECT pane_type, length(prose) FROM ai_reasoning_cache;"` → two rows.
4. Open dashboard → top transfer suggestion shows the prose line + `AI` badge; suggestions #2/#3
   unchanged.
5. Stop Ollama, clear `ai_reasoning_cache` row for transfer → dashboard top suggestion shows chips
   only (no prose line). Captain pane still shows AI prose (its row wasn't cleared). Graceful.

## Scope boundary

- **IN:** transfer payload + prompt + few-shot + render + generate + jobs branch + scheduler
  panes-list extension + queries wrapper + api rewiring + telegram swap extension + frontend prose
  + badge + mocks. Tests for every new surface.
- **OUT (this slice):** chip recommendation pane (→ S-A.3), deadguard summary (→ S-A.4),
  conversational "why X?" query (→ S-B), mini-league (→ S-C), personalization (→ S-D), scenario
  simulator (→ S-E), player-news ingestion (→ S-F).
- **OUT (forever for S-A.2):** any change to which transfer the engine recommends, the EP delta
  threshold, the hit calculator, the squad validity check. The LLM only paraphrases.

## Definition of done (CLAUDE.md B14)

- `/api/transfers` returns suggestions with `reasoning` + `reasoning_source` fields. Top suggestion
  carries AI prose when cached; others carry `''` + `'classic'`.
- Dashboard TransferIdeas pane renders a prose line + `AI` badge under the top suggestion's chips
  when AI prose is cached; renders chips only otherwise. Suggestions #2 and #3 unchanged.
- Telegram H-24 transfer line uses cached AI prose when present; falls back to template summary.
- `ai_reasoning_cache` populates `pane_type='transfer'` rows on each `refresh_and_recompute` cycle.
- Number-grounding check rejects empty + hallucinated prose for the transfer pane (S-A.1's empty
  guard already in `generate_*_prose`; verified by per-pane test).
- All tests green (pytest + vitest). All tests use `StubProvider`. **No `docs/decision-engine.md`
  change** (S-A.2 is describe-only).
- Architecture spec referenced + reused without modification.
- The agent never ran a live Ollama call or live FPL call during implementation (R3 + B11).
- Manual smoke (steps above) confirmed by the user out-of-band.
