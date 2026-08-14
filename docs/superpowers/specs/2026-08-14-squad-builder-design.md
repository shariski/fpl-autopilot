# AI Squad Builder + Apply-on-Confirm — design

Two-part feature: (1) an **AI-picked starting squad** — the LLM chooses the optimal 15 from a
deterministic candidate pool, gated by a legal-squad validator; (2) **apply-on-confirm** — a
dry-run-first CLI that submits the transfers to FPL (pre-season unlimited-transfer window),
with typed confirm; you drive the live step (R3).

**Status:** approved 2026-08-14 (brainstorming).
**Resolves:** B3 scope expansion — initial squad creation was previously out of scope; this
adds it as decision-suggestion + confirmed-execution. B4 change: see §B4 entry (documented
before implementation per project rules).
**Builds on:** `2026-08-14-player-insight-design.md` (digest/grounding/runner patterns),
`2026-08-14-deepseek-provider-design.md` (provider), `2026-05-26-phase3-ai-architecture-design.md`
(AI sub-layer rules), Phase-2 executor (`src/execution/transfer.py`).

## Why

GW1 is days away and the user has no squad. The tool can't (until now) help create one. The
LLM is good at judgment across a constrained set — with a legal-squad validator as the gate,
the AI can pick a defensible template while the deterministic layer guarantees it is legal.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Decider | **AI picks the 15** from a deterministic candidate pool (~90-100 players). |
| Gate | **Deterministic validator is the law**: exactly 15, 2-5-5-3 formation, budget ≤100, ≤3 per club, unique ids, ids in the digest. Retry ≤3 with violation feedback; all-fail → greedy optimizer fallback (flagged `source: "optimizer"`). |
| Candidate pool | Per-position selection: top-N by `xp_6gw` + top-M by xp-per-million (value tier) so the AI has budget-flexible options. Pool is deterministic, logged with the result. |
| Digest | Closed-shape JSON per candidate (player_id, web_name, team, position, price, xp_next, xp_6gw, prior xg_per90/xa_per90, ownership_pct, form, next-3 fixtures w/ FDR). Same grounding discipline as player insight. |
| Cache | `ai_reasoning_cache`, `pane_type="squad"`, key = digest hash. Re-generation only when pool data changes. |
| Output | `{picks: [{player_id, slot, reason}], template_rationale, risks[], source}` — validator maps slots to exact positions. |
| Surface | Dashboard page `/squad-builder` (pitch layout + rationale + per-pick reasons + risks). Apply is CLI-only in v1 (no dashboard live write, consistent with 2.5c-3). |
| Apply | CLI `fpl-autopilot apply-squad` — dry-run default; `--live` requires master key + typed confirm (reuses `execute-transfer` pattern). Builds out/in pairs vs the current snapshot; sequential `run_transfer` calls in **rebuild mode** (bypasses free-transfer/hit preflight only in the pre-season unlimited window — the API is the final arbiter; any API refusal aborts with a report of what applied). |
| Idempotency | Refuse to apply when the current squad already matches the target. |
| Logging | B10: `decision_type="squad"` rows — pool hash, picks, validator result, source, budget_used, and per-transfer outcome. |
| B4 | New `decision-engine.md` §"Squad builder (S-B)": AI proposes; validator enforces; deterministic fallback; every output logged with inputs and alternatives. No threshold changes. |
| R3 | The agent never runs `--live`; the user drives it (CLI typed confirm). |
| Telegram one-tap confirm | **Scoped out of v1** — typed CLI confirm only; the `pending_decisions` one-tap flow is a follow-up. |

## Architecture

```
/squad-builder page
   │ GET /api/squad/builder
   ▼
src/interface/api.py        endpoint: cache-hit → return; else run builder
   │
   ▼
src/decisions/squad_builder.py      build_candidate_pool(conn, next_gw) -> [dict]
   │                                 deterministic: legal prefilter + per-position
   │                                 top-N xp6 + value tier, ≤ ~100 players
   ▼
src/ai/squad/digest.py              build_squad_digest(conn, pool, next_gw) -> dict
   │                                 (reuses digest helpers from ai/insight)
   ▼
src/ai/squad/runner.py              generate_squad(conn, *, provider, model_id)
   │                                 → AI JSON {picks, template_rationale, risks}
   ▼
src/decisions/squad_validator.py    validate_squad(picks, pool) -> problems[]
   │                                 retry ≤3 w/ feedback; fallback = greedy optimizer
   ▼
cache.put / activity_log            → API response
```

Apply path (separate entry point):

```
fpl-autopilot apply-squad [--live]      src/cli.py
   ▼
src/execution/squad.py                 apply_squad(conn, key, live) 
   │                                    read target (fresh builder result or cache),
   │                                    snapshot current my_team, build out/in pairs
   ▼
src/execution/transfer.py              run_transfer(..., rebuild=True) × N
   │                                    pre-season unlimited window bypasses FT preflight;
   │                                    API refusal → abort + report applied/remaining
   ▼
activity_log (decision_type="squad")   per-transfer outcomes
```

## Candidate pool (deterministic)

- Inputs: players (position, price, status, team_id), xp rows (v1, next 6 GW), prior understat
  (xg_per90/xa_per90), ownership, form, fixtures+FDR for the next 3 GWs.
- Filters: status in (a, d); xp_6gw present; price ≥ 4.0.
- Per position: top 15 by `xp_6gw`, plus top 10 by `xp_6gw / price` (value tier), union.
- Expected size ≈ 4 positions × 25 = ~100. Exact counts logged.

## AI output schema (in the prompt, kerf-style)

```json
{
  "picks": [
    {"player_id": 449, "slot": "DEF1", "reason": "one sentence"},
    ...
  ],
  "template_rationale": "2-3 sentences on the structure chosen",
  "risks": ["player-level or structure-level risks"]
}
```

Slots: GKP1-2, DEF1-5, MID1-5, FWD1-3. The validator checks each `slot` maps to the player's
position; duplicate slots and duplicate player_ids are violations.

## Validator rules (deterministic, the law)

1. Exactly 15 picks; every slot present exactly once.
2. Slot position matches the player's position.
3. `sum(price) <= 100.0 + 1e-9`.
4. ≤3 players per club.
5. All player_ids unique and present in the digest.
6. Retry feedback names the specific violations ("3 Man City players; max is 3").
7. Fallback (3 failures): greedy optimizer — fill slots by `xp_6gw / price` desc, enforce all
   rules; guaranteed legal; `source="optimizer"`.

## API contract

`GET /api/squad/builder` → 200:

```json
{
  "status": "cached" | "generated",
  "gw": 1,
  "source": "ai" | "optimizer",
  "picks": [{"player_id": 449, "web_name": "Hall", "team": "NEW", "position": "DEF",
             "price": 5.0, "xp_6gw": 24.3, "slot": "DEF1", "reason": "..."}],
  "template_rationale": "...",
  "risks": ["..."],
  "budget_used": 99.5,
  "model_id": "deepseek-chat",
  "generated_at": "..."
}
```

- 404 nothing special (pool always exists when data is present); `unavailable` if AI disabled
  or provider/gate failures after fallback (fallback should make this unreachable; defensive).

## Apply CLI

`fpl-autopilot apply-squad [--live]`
- Dry-run (default): prints the transfer plan (out → in pairs with prices) + `--live` hint;
  writes nothing.
- `--live`: loads master key (existing `_maybe_load_key` path), typed confirm
  (`Type "apply" to confirm`), executes pairs sequentially via `run_transfer(rebuild=True)`.
- Preflight refusals: current squad already equals target (idempotency); no session;
  API-side refusal at any step → abort, report applied/remaining, exit non-zero.
- Logs every outcome `decision_type="squad"`.

## Frontend (/squad-builder)

- Page renders the AI XI in pitch layout (reuse `PlayerCard`), each with slot + reason;
  template_rationale as lead; risks as a muted list; source badge (`AI` / `optimizer`);
  budget line (`99.5m used / 100m`).
- Footer hint: apply via `fpl-autopilot apply-squad --live` (no dashboard live write, per 2.5c-3).
- Loading + unavailable states mirror the insight page.

## Testing

- Pool: deterministic size/shape, position spread, price-tier presence, legal prefilter.
- Validator: each violation rule unit-tested; optimizer fallback guarantees a legal squad
  (property test: 100 random pools → always legal).
- Runner (StubProvider): happy path caches; illegal picks → retry feedback names violations;
  3 fails → optimizer source; grounding not needed for ids (ids are not numeric claims —
  validator covers integrity).
- Apply: pair building vs snapshot; dry-run writes nothing; idempotency refusal; API refusal
  aborts with report; activity_log rows written per outcome.
- API endpoint + frontend page tests (vitest) mirror the insight slices.

## Docs (B13)

1. This spec. 2. `decision-engine.md` — new §"Squad builder (S-B)" (B4 entry, first).
3. AI architecture spec changelog v0.4. 4. `docs/architecture.md` AI line.
5. `docs/runbook.md` §10 extension (squad builder triage). 6. `docs/onboarding.md` (builder + apply instructions).

## Out of scope (v1)

- Telegram one-tap apply confirm (`pending_decisions` flow — follow-up).
- Wildcard-aware rebuild mid-season (the builder is season-agnostic; apply only works when the
  API allows — pre-season or wildcard window).
- Auto-apply without confirm.
