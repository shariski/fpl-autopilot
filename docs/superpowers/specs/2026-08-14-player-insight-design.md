# Player Insight — AI deep-dive design

On-demand, per-player AI analysis of FPL data — pattern recognition over the stats the
deterministic engine already stores, surfaced on a new dashboard player page. Kerf-style
two-step chain: deterministic digest → LLM analysis (structured JSON) → grounding gate with
retry. Squad gameweek brief is the follow-up slice (same chain, different digest).

**Status:** approved 2026-08-14 (brainstorming).
**Builds on:** `docs/superpowers/specs/2026-08-14-deepseek-provider-design.md` (provider) and
`2026-05-26-phase3-ai-architecture-design.md` (AI sub-layer placement, grounding, cache).
**Source of truth:** this doc for the insight feature; the 2026-05-26 AI spec for the
sub-layer rules (AI strictly downstream of Decision, strictly upstream of Interface; prompt
builder is the sole egress (B7); silent fallback on failure; R3 no tools/no FPL writes).

## Why

The current AI narrators paraphrase the engine's outputs ("Mount is the captain this week at
8.1 xP") — form-letter prose, no added value. LLMs are good at pattern recognition, not
paraphrase. The insight feature gives the LLM the raw-ish player data and asks it to find
patterns the scoring model doesn't compute: xG-vs-return divergence, fixture alignment with
actual performance, minutes/role shifts, value and market dynamics — each claim grounded in
the digest's own numbers and gated so it cannot hallucinate.

## Design principles (from kerf's Coach)

1. **The LLM does what the engine can't.** The engine scores; the LLM finds patterns. Output
   is insight, not restatement.
2. **Evidence-grounded.** Every insight cites `evidence_used` — numbers verbatim from the
   digest. The grounding gate enforces this mechanically.
3. **Deterministic gate is the source of truth.** Ungrounded or malformed output is rejected;
   retry-with-feedback up to 3 attempts, then fail silent.
4. **Cost discipline.** On-demand generation, SQLite cache keyed by digest hash, digest-only
   context (aggregated stats — no raw event dumps).
5. **No hype, no verdicts.** Numbers and confidence levels, not adjectives. No captain/transfer
   commands — implications only (B4: decisions stay with the deterministic engine).

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Surface | New dashboard route `/players/[id]` with an AI insight panel. |
| Chain | One analysis call (digest → prompt → structured JSON → gate → cache). No separate generation step (unlike kerf's 2-step authoring, the insight IS the output). |
| Compute | On-demand, synchronous request with kerf-style loading copy; cached per (player, next_gw, digest-hash). |
| Insight categories | `overperformance` (xG/xA vs returns), `fixture_alignment` (upcoming fixtures vs FDR + actual splits), `minutes_role` (minutes/role stability), `value_market` (price/ownership/momentum). 4-8 insights max. |
| Digest | Deterministic builder in `src/ai/insight/digest.py`. Adapts to season state: pre-season = prior-season understat + projections + market; mid-season adds last-N-GW actuals from `player_gw_stats` (settlement already writes these). Digest includes an explicit `data_limits` list the LLM must not paper over. |
| Gate | Reuse `src/ai/grounding.py` (`is_grounded`). Retry ≤3 with corrective feedback; all-fail → cache nothing, return `unavailable`. |
| Cache | Reuse `ai_reasoning_cache` table, `pane_type='insight'`, `gw = next_gw`, key = sha256(digest JSON). `prose` column stores the compact JSON payload (documented deviation from the column name). No schema change. |
| Model | `config.ai.deepseek.model` (currently `deepseek-chat`). If a reasoning-toggle model (e.g. kerf's `deepseek-v4-flash`) proves better, it's config-only. |
| Logging | B10: structured `ai.insight` activity_log entry on every generation: player, gw, model, tokens, gate result. |
| B4 | Untouched — insight is describe-only, never feeds the decision layer. No `decision-engine.md` change. |
| Failure UX | Gate-reject or provider error → page shows a small "analysis unavailable" note with a retry button. Same silent-fallback philosophy as panes. |

## Architecture

```
/players/[id] page (SvelteKit)
        │ GET /api/players/{id}/insight
        ▼
src/interface/api.py           endpoint: read cache → HIT: return
        │                                  MISS: run analysis → cache → return
        ▼
src/ai/insight/runner.py       generate_player_insight(conn, player_id, *, provider, model_id)
        │                        digest → prompt → provider.generate → parse → gate → retry
        ▼
src/ai/insight/digest.py       build_player_digest(conn, player_id, next_gw) -> dict  (no LLM)
src/ai/insight/prompt.py       analysis.md template + substitution
src/ai/insight/cache.py        get/put wrappers over ai_reasoning_cache (pane_type="insight")
```

Data flow detail:

1. `GET /api/players/{id}/insight` — 404 on unknown player id; `next_gw = is_next=1`; digest =
   `build_player_digest`; hash = `cache.recommendation_hash(digest)`; cache hit →
   `{status:"cached", insights, summary, data_limits, model_id, generated_at}`.
2. Miss: `provider = build_provider(cfg)` (factory from the DeepSeek slice); build prompt from
   `analysis.md`; `provider.generate(prompt, max_tokens≈2000, temperature≈0.2)`.
3. Parse JSON (strip fences, first `{`..last `}` — kerf's `extractJsonObject` pattern);
   malformed → retry with feedback "response was not valid JSON".
4. Gate: for each insight, every numeric token in `claim`+`evidence_used`+`implication` must
   appear verbatim in the digest JSON text (`grounding.is_grounded`); ungrounded insights are
   listed in retry feedback; 0 valid insights after 3 attempts → return `unavailable`.
5. Cache `{status:"generated", ...}`; log `ai.insight` row (B10).

## Digest shape (deterministic, no LLM)

```json
{
  "player": {"web_name": "...", "position": "MID", "team": "ARS", "price": 8.5,
             "status": "a", "ownership_pct": 32.1, "form": 6.2},
  "prior_season": {"xg_per90": 0.42, "xa_per90": 0.28, "minutes": 2980, "games": 36,
                   "goals": 15, "assists": 8},
  "current_season_gws": [{"gw": 4, "minutes": 90, "goals": 1, "assists": 0,
                          "total_points": 8}],          // from player_gw_stats; [] pre-season
  "projection": {"xp_next_gw": 5.9, "xp_6gw": 34.2},    // from xp table, model v1
  "fixtures": [{"gw": 5, "opponent": "HUL", "venue": "H", "fdr_attack": 2,
                "fdr_defense": 3}],
  "data_limits": ["no current-season minutes yet (pre-season)", "..."]
}
```

`data_limits` is computed deterministically: e.g. `current_season_gws` empty → the limit line;
understat season older than the FPL season → prior-season caveat. The prompt is instructed to
never contradict `data_limits`.

## Prompt (src/ai/insight/prompts/analysis.md — kerf-style ## system / ## user split)

```markdown
## system

You are an FPL analyst and pattern-recognition specialist. You are given a
deterministic data digest for one player — numbers already computed and stored
by the system. Your job is to find patterns the scoring model does not compute:
the WHY behind the numbers, divergences, alignments, and risks.

Output ONLY valid JSON matching this schema:

{
  "insights": [
    {
      "category": "overperformance | fixture_alignment | minutes_role | value_market",
      "claim": "one concrete pattern found in the data",
      "evidence_used": ["numbers verbatim from the digest that support this claim"],
      "confidence": "high | medium | low",
      "implication": "what this means for FPL decision-making, 1 sentence"
    }
  ],
  "summary": "2-3 sentence plain-language takeaway",
  "data_limits": ["restate, do not paper over, the digest's stated limits"]
}

Rules:

1. Patterns, not restatement. Never restate a stat ("he has 15 goals") without
   a pattern ("goals are concentrated against bottom-half sides and dry against
   the top 6"). If no pattern exists, say so in the summary.
2. Ground every claim: every number you use must appear verbatim in the digest.
   No invented statistics, no extrapolation, no estimates.
3. Distinguish current-season from prior-season data explicitly. Never present
   prior-season numbers as current form.
4. Respect data_limits: if the digest says current-season data is unavailable,
   do not claim trends. Lower confidence when evidence is thin.
5. 4-8 insights total, spread across categories when the data supports it.
   rank by (strength of evidence) x (impact on FPL decisions).
6. implication is a decision aid, never a command. No "you should captain/trade"
   phrasing — the decision layer owns that.
7. No hype words: "elite", "monster", "must-have", "bargain" etc. are banned.
8. summary is 2-3 plain sentences a casual player understands.

## user

Here is the deterministic digest for this player:

```json
<DIGEST_JSON>
```

Analyze. Output ONLY the JSON.
```

Runtime additions (runner): on retry, append corrective feedback to the user
message: `Previous attempt rejected: <problem>. Rewrite so every number appears
verbatim in the digest.` or `Previous response was not valid JSON. Output ONLY
the JSON object.`

## API contract

`GET /api/players/{id}/insight`

- 404 `{"detail": "unknown player"}` on bad id.
- 200 on success:
```json
{
  "status": "cached" | "generated",
  "player_id": 442,
  "gw": 5,
  "insights": [{"category": "...", "claim": "...", "evidence_used": ["..."],
                "confidence": "high", "implication": "..."}],
  "summary": "...",
  "data_limits": ["..."],
  "model_id": "deepseek-chat",
  "generated_at": "..."
}
```
- 200 `{"status": "unavailable", "reason": "gate_rejected" | "provider_error"}` when analysis
  could not be produced (nothing cached).
- Timeout budget: up to 180s on miss (provider timeout is config-driven, 60s default × retries).

## Frontend (/players/[id])

- Route in the SvelteKit app; header row: web_name, position, team, price, form, status badge.
- Insight panel: summary paragraph → insight cards (category chip + confidence badge; claim;
  evidence chips render the digest numbers); `data_limits` as a muted footer note; "AI" source
  badge (matches the existing per-pane `ai`/`classic` convention).
- Loading (miss): "Analyzing patterns in this player's data — the first time takes up to a
  minute." No hype, no skeleton flash; button disabled while in flight.
- Unavailable: one-line note + Retry button (re-hits the endpoint).
- Uses the existing `client.ts` fetch pattern; add `fetchInsight(playerId)`.

## Cost & cache invalidation

- One generation ≈ $0.01-0.02 (2000 output tokens, reasoning model if enabled later).
- Cache keyed by digest hash: a data refresh that changes any digest number produces a new
  hash → new generation only when that player's page is reopened. Reopening a page without
  data changes → cache hit (free).
- `gw` in the cache key means an insight from GW5 never serves at GW6 even if the digest hash
  collides across gameweeks (fixtures differ → digest differs anyway).

## Logging (B10)

On every generation attempt, one `activity_log` row:
`decision_type='ai.insight'`, `mode='ai'`, inputs_json = `{player_id, gw, model_id,
input_tokens, output_tokens, gate_result: "passed" | "rejected" | "retry" | "failed"}`,
executed=1. Gate failures logged at warning with the rejected claims (numbers only, no prose).

## Testing

- Digest: deterministic frozen fixtures — pre-season shape (no current_season_gws, data_limits
  populated), mid-season shape (player_gw_stats present), unknown player → None.
- Prompt builder: substitution correct, full system prompt present (kerf test pattern).
- Runner (StubProvider + fake responses): happy path caches; malformed JSON retries then fails;
  ungrounded claims rejected with feedback, retry count ≤3; all-fail → None + no cache row;
  `unavailable` returned without crashing when provider raises (OllamaError/DeepSeekError).
- Cache: hit path skips generation (provider never called); hash changes on digest change.
- API: FastAPI testclient — cached hit, generated miss, 404, unavailable.
- Grounding reuse: covered by existing grounding tests.
- Frontend (vitest): insight panel renders cards/evidence/limits; loading + unavailable states.

## Docs (B13)

1. This spec. 2. AI architecture spec (2026-05-26) changelog v0.3: insight feature added.
3. `docs/architecture.md` — AI sub-layer line gains "player insight (deep-dive)".
4. `docs/runbook.md` — §10 triage extended: "insight unavailable" causes.
5. `docs/onboarding.md` — optional section: player deep-dive page.

## Out of scope (this slice)

- Squad gameweek brief (next slice — same chain, squad digest).
- Conversational follow-up ("why did it say that?").
- Mini-league context and personalization.
- Streaming; batch precompute; scheduled insight generation.
