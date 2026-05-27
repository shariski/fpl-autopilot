# LLM Deadguard Summary — Design (Phase 3, S-A.4)

**Status:** approved 2026-05-26
**Slice:** Phase 3 S-A.4 — **last slice of the S-A family**. Adds LLM prose to the **deadguard
post-execution summary** that surfaces on the Telegram notification body + the dashboard's
"Deadguard set your team this gameweek" banner. Structurally different from S-A.1/2/3 — generated
at execution time, not scheduler time.
**Depends on:** Phase-3 S-A.1+S-A.2+S-A.3 architecture (`src/ai/{provider,reasoning,cache,grounding}`,
the `ai_reasoning_cache` table, the `ai_*` config accessors); Phase-2.5 deadguard
(`src/interface/deadguard.py`); Phase-2.5c-3 dashboard banner system
(`src/interface/queries.py:_status_banners`).
**Cross-cutting design (reused, not re-derived):**
[`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md).
**Source of truth for this slice:** this doc. **`docs/decision-engine.md` is NOT touched** —
describe-only, the deterministic deadguard still picks. B4 untouched.

## Goal

When the deadguard fires at H-30 and successfully sets the team (captain/vice/bench + optionally a
transfer), the Telegram "executed" notification + the post-deadguard dashboard banner show an
LLM-generated paragraph describing what happened — "Deadguard set captain Haaland, optimized the
bench, and transferred out Watkins (flagged) for Calvert-Lewin in time for the GW38 deadline." —
rather than the current terse template "Deadguard: captain Haaland, bench optimized, transfer
applied."

When the LLM is unavailable, the existing template fires unchanged. No banner-change, no broken
deadguard, no degraded execution path.

## Why this is structurally different from S-A.1/A.2/A.3

| Aspect | S-A.1/A.2/A.3 | S-A.4 |
|---|---|---|
| When generated | Scheduler pre-warm (`refresh_and_recompute` hourly + weekly) | **At execution time** inside `deadguard._run_trigger`, after the lineup write succeeds |
| Trigger frequency | Every recompute cycle (frequent — most weeks) | Only when deadguard actually fires (rare — most weeks the user acts before H-30 and deadguard stays dormant) |
| Pre-warm path? | Yes (panes list in `jobs.py`) | **No.** Speculative pre-warm would burn LLM calls every cycle for prose that's usually unused. |
| Cache write site | Scheduler | Executor (`_run_trigger`) |
| Surfaces | Dashboard pane + Telegram body | Telegram body (immediately) + dashboard banner (next page load, via cache) |

## Decisions (locked — no further brainstorming for this slice)

| Decision | Choice |
|----------|--------|
| Generation point | **`deadguard._run_trigger`**, right before the existing `_notify("executed", ...)` call (line 140), after the bookkeeping is complete and `transfer_note` is composed. |
| Payload depth | **Outcome-shaped, not stat-shaped:** `{captain_name, vice_name, transfer: {out_name, in_name} | None, gw}`. The LLM describes what happened — it doesn't need xP/FDR/fixture context here. The deadguard already made the decision; this is narration. Note: bench-change signal was dropped post-review (see "Post-review change" below). |
| Telegram swap | **In-line at the `_notify` call site.** The `_notify` body uses the AI prose if generated successfully; falls back to the template otherwise. Not a `notify_plan`-style swap (deadguard doesn't route a plan — it acts directly). |
| Dashboard banner | `_status_banners` in `queries.py` enhances the existing "Deadguard set your team this gameweek" banner: read cached AI prose for the current gw; if present, use it as the banner `text`; else use the existing template. No frontend code change required — the banner type already supports any string. |
| B4 | **Untouched.** Deterministic deadguard still picks. No `decision-engine.md` change. |
| Cache identity | New `pane_type = 'deadguard_summary'`. `recommendation_hash` covers the outcome payload (captain_name, transfer in/out names, gw). Different deadguard outcomes → different hashes → fresh rows. |
| No frontend code change | The dashboard banner reads `Banner.text` from `/api/status` and renders whatever string. The AI prose replaces the template text at the backend layer. No `.svelte` edit, no mock change, no vitest case needed. |

## Architecture (delta from S-A.3)

```
src/ai/prompts/deadguard.txt              ← NEW: per-pane prompt template
src/ai/prompts/deadguard_examples.json    ← NEW: 3 hand-curated exemplars (captain only / +bench / +transfer)
src/ai/reasoning.py                       ← EXTEND: _build_deadguard_payload, _build_deadguard_prompt,
                                            render_deadguard_summary, generate_deadguard_summary
src/interface/deadguard.py                ← EDIT: _run_trigger generates AI prose before _notify,
                                            uses it as the notification body when grounded
src/interface/queries.py                  ← EDIT: _status_banners reads cached prose for the
                                            DEADGUARD_EXECUTED state banner

# NEW tests
tests/test_ai_prompts_deadguard.py        ← golden test for deadguard_examples.json grounding
tests/test_ai_reasoning_deadguard.py      ← payload + prompt + render + generate

# EXTENDED tests
tests/test_deadguard.py                   ← +cases for AI prose path in _run_trigger
tests/test_api.py                         ← +case for banner text using cached AI prose
```

**No changes to:** `src/scheduler.py`, `src/ai/jobs.py` (S-A.4 is execution-driven, not scheduler-driven),
`src/interface/api.py`, `src/interface/telegram.py` (the `notify_plan` swap pattern doesn't apply —
deadguard sends its notification via `_notify` directly), frontend code.

**B2:** AI module reads deadguard outcome dict (built inside the deadguard module); writes to
`ai_reasoning_cache`. No new Data Layer queries. The deadguard module is in the Interface layer
already; calling into AI from there is in-layer (or at most a sibling within Interface).

## §1 Deadguard payload shape

```python
def _build_deadguard_payload(conn, outcome: dict) -> dict | None:
    """Closed-shape payload describing what the deadguard did.

    `outcome` is composed by deadguard._run_trigger:
      {
        "captain_name": str,
        "vice_name": str | None,
        "transfer": {"out_name": str, "in_name": str} | None,
        "gw": int,
      }
    """
    if not outcome or not outcome.get("captain_name"):
        return None
    return {
        "captain": outcome["captain_name"],
        "vice": outcome.get("vice_name"),
        "transfer": outcome.get("transfer"),     # nested dict or None
        "gw": outcome["gw"],
    }
```

The payload contains **only names + an int gw**. The grounding check applies to the
gw integer + the names (which are strings, not numbers, so grounding-wise the gw is the only
number to track).

## §2 Prompt template + few-shot exemplars

`src/ai/prompts/deadguard.txt`:

```
You are explaining what the FPL deadguard automation just did, to the team manager.

The deadguard ran because the manager did not act before the H-30 window. It always sets captain
and vice; it can also optimize bench order; and it can optionally swap out one flagged player.

Constraints:
- 2 to 3 sentences. Plain English. No emojis. No exclamation marks.
- You may ONLY use names and numbers that appear in INPUT below. Do not invent any other.
- State the captain. Briefly note that the bench order was optimized. Mention the transfer only
  if a transfer is present.
- Do not editorialise or speculate. Describe only what was done.
- Output the paragraph only. No preamble, no closing remarks.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

`src/ai/prompts/deadguard_examples.json` — 3 exemplars covering the realistic outcome combinations:

```json
[
  {
    "input": {
      "captain": "Haaland", "vice": "Salah",
      "transfer": null, "gw": 38
    },
    "output": "Deadguard set Haaland as captain and Salah as vice for GW38, and optimized the bench order."
  },
  {
    "input": {
      "captain": "Saka", "vice": "Palmer",
      "transfer": null, "gw": 32
    },
    "output": "For GW32, deadguard set Saka as captain, Palmer as vice, and optimized the bench order."
  },
  {
    "input": {
      "captain": "Haaland", "vice": "Salah",
      "transfer": {"out_name": "Watkins", "in_name": "Calvert-Lewin"}, "gw": 38
    },
    "output": "Deadguard set Haaland as captain and Salah as vice for GW38, optimized the bench order, and transferred out Watkins for Calvert-Lewin."
  }
]
```

Self-validating: every numeric token in each output (`38`, `32`) appears in its corresponding
input. Names are string-grounded — they appear in the input as values, not as numbers, so the
grounding check (which only tracks digit-tokens) doesn't constrain them. Per the prompt's
"ONLY use names that appear in INPUT" constraint, the LLM is asked to obey name-grounding even
though it's not lexically enforced.

## §3 Render + generate functions

Mirror the S-A.3 captain pattern:

```python
def render_deadguard_summary(conn, gw: int, outcome: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> ('', 'classic').
    Note: classic returns empty (not a fallback prose), because the deadguard module composes
    its own template summary at the call site if the AI is unavailable."""
    ...


def generate_deadguard_summary(conn, gw: int, outcome: dict, *,
                               provider, model_id: str,
                               max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Same flow as S-A.1/2/3 (payload → cache check → prompt → provider →
    empty guard → grounding → cache.put)."""
    ...
```

## §4 Integration in `deadguard._run_trigger`

The change is at line 140 of `src/interface/deadguard.py` (the `_notify("executed", ...)` call site).
Before that line, build the outcome dict + try to generate AI prose:

```python
    # Build outcome + try AI prose (best-effort; never blocks the notification)
    template_summary = f"Deadguard: captain {name}, bench optimized, {transfer_note}."
    summary = template_summary
    try:
        if config.ai_enabled(cfg):
            transfer_info = None
            if transfer_applied:
                from src.data import repository as _repo
                out_name = _repo.player_web_name(conn, body["element_out"])
                in_name  = _repo.player_web_name(conn, body["element_in"])
                transfer_info = {"out_name": out_name, "in_name": in_name}
            vice_name = caps["picks"][1]["web_name"] if len(caps["picks"]) > 1 else None
            outcome = {
                "captain_name": name,
                "vice_name": vice_name,
                "transfer": transfer_info,
                "gw": gw,
            }
            from src.ai import reasoning as _ai_reasoning, provider as _ai_provider
            provider = _ai_provider.OllamaProvider(
                host=config.ai_ollama_host(cfg),
                model=config.ai_ollama_model(cfg),
                timeout_seconds=config.ai_timeout_seconds(cfg),
            )
            ok = _ai_reasoning.generate_deadguard_summary(
                conn, gw=gw, outcome=outcome,
                provider=provider, model_id=config.ai_ollama_model(cfg))
            if ok:
                prose, src = _ai_reasoning.render_deadguard_summary(conn, gw, outcome)
                if src == "ai" and prose:
                    summary = prose
    except Exception:
        log.exception("ai.deadguard.generation_failed")     # never blocks the notification

    _notify(conn, "executed", summary)
```

**Provider lazy-imported inside the try** to keep the existing fast deadguard path unchanged for
users who disable AI. The exception handler catches anything — Ollama down, OllamaError,
KeyError, etc. — so the deadguard notification ALWAYS sends.

Need a small helper: `src/data/repository.py:player_web_name(conn, player_id) -> str | None`. If
it doesn't exist (check first), add it as a one-line query. Used only here.

## §5 Dashboard banner — `_status_banners` enhancement

`src/interface/queries.py:_status_banners` currently emits this banner when state is
`DEADGUARD_EXECUTED`:

```python
banners.append({"level": "info",
                "text": "Deadguard set your team this gameweek. "
                        "Undo a transfer via Telegram or `undo-transfer` before the deadline."})
```

Change: read cached AI prose for `pane_type='deadguard_summary'` at this gw. If present, use it
as the banner's `text`. Else use the existing template. The "undo a transfer" hint can be
appended for both — it's actionable info, not LLM-generated.

```python
    if state == "DEADGUARD_EXECUTED":
        ai_prose = _read_deadguard_ai_prose(conn, nxt["id"])
        intro = ai_prose if ai_prose else "Deadguard set your team this gameweek."
        banners.append({"level": "info",
                        "text": f"{intro} Undo a transfer via Telegram or `undo-transfer` "
                                "before the deadline."})
```

`_read_deadguard_ai_prose` is a tiny helper that scans `ai_reasoning_cache` for the most recent
`pane_type='deadguard_summary'` row at this gw — it doesn't need to recompute the outcome hash
(unlike captain/transfer/chip, where the hash matters because the engine can produce different
outputs and the cache row must match). For deadguard, there's at most one summary per gw — return
the most recent row.

```python
def _read_deadguard_ai_prose(conn, gw: int) -> str | None:
    row = conn.execute(
        "SELECT prose FROM ai_reasoning_cache WHERE gw=? AND pane_type='deadguard_summary' "
        "ORDER BY generated_at DESC LIMIT 1", (gw,)).fetchone()
    return row["prose"] if row is not None else None
```

## Safety & B-rules

- **B2:** AI module reads outcome dict in-process; writes only to `ai_reasoning_cache`. The
  deadguard module (Interface layer) calls into the AI module — same layer or just upward. No
  inversion.
- **B4:** Untouched. Deterministic deadguard still picks. No `decision-engine.md` change.
- **B7:** Closed payload (names + bool + int). No path for credentials. AI module never imports
  `src/auth/`.
- **B8:** No executor change beyond the AI-call insertion. The deadguard's lineup/transfer writes
  are unchanged.
- **R3:** LLM has no tools. All tests fixtures-only.
- **B10:** Activity log entry (which already exists) is unchanged. AI prose is a UX layer, not a
  decision log. The terse `transfer_note` still goes into `activity_log.action_taken` for audit.
- **Critical: the AI call is wrapped in `try/except Exception`** so any failure falls through to the
  template summary. **The deadguard's notification path NEVER fails because of AI.** Even an
  Ollama hang past the timeout produces an `OllamaError` from the provider, which gets caught by
  the outer `except` (or by `generate_deadguard_summary`'s own try/except → False).

## Testing

All fixtures-only via `StubProvider`. Per-task scope in the plan. Highlights:

- `deadguard_examples.json` self-validates against `is_grounded` — 3 exemplars covering: captain-only,
  +bench, +transfer.
- `_build_deadguard_payload` returns `None` on missing captain_name; returns closed dict otherwise.
- `render_deadguard_summary`: cache hit → `('<prose>', 'ai')`; miss → `('', 'classic')`.
- `generate_deadguard_summary`: grounded → cached; ungrounded/empty/exception → no row + log.
- `_run_trigger` integration: with AI cache populated (or pre-existing on second deadguard run) +
  `ai.enabled=true` → `_notify` body is the AI prose. Without (Ollama down, ai disabled) →
  `_notify` body is the template.
- `_status_banners`: with cached deadguard prose → banner text starts with the prose. Without →
  banner text starts with the existing template.

## Scope boundary

- **IN:** prompt + 3 exemplars, payload + render + generate, deadguard `_run_trigger` integration,
  `_status_banners` banner-text enhancement, `repository.player_web_name` helper if missing. Tests.
- **OUT (this slice):** any change to deadguard SCOPE (what it does — captain/bench/transfer rules
  are in B8 + decision-engine.md and stay untouched), the re-eval flow (`_run_reevaluate`), the
  undo flow.
- **OUT (forever for S-A.4):** any change to deadguard trigger windows, EP thresholds, or
  the captain/transfer rankers it consumes.

## Definition of done (CLAUDE.md B14)

- Deadguard executes successfully → Telegram body shows AI prose (when cached + grounded). When
  Ollama is down or `ai.enabled: false`, the existing template summary fires unchanged.
- Dashboard banner after a deadguard execution shows the AI prose. Without cached prose, shows
  the existing template + undo hint.
- `ai_reasoning_cache` populates `pane_type='deadguard_summary'` row at execution time, not
  scheduler time.
- All tests green (pytest). All tests use `StubProvider`. **No `docs/decision-engine.md` change.**
- The deadguard notification path never fails because of an AI error (catch-all `try/except` at
  the integration site).
- The agent never ran a live Ollama or live FPL call during implementation (R3 + B11).
- Architecture spec referenced + reused without modification.

## Closing the S-A family

After S-A.4 lands, the four S-A panes (captain, transfer, chip, deadguard-summary) all carry LLM
prose. The cross-cutting architecture spec has been used by four slices without modification — a
strong signal that the architecture decisions made in the first session were the right ones.
Future Phase-3 slices (S-B/S-C/S-D/S-E/S-F) reuse the same infrastructure for new surfaces.

## Post-review change: `bench_changed` field dropped

The original spec defined `bench_changed: bool` in the outcome payload, intended to capture
"whether the bench order differed from pre-deadguard." The implementation hardcoded it to
`True` as a v1 simplification (acknowledged in a code comment) because `run_lineup` does not
return whether the bench was actually reordered — only whether it ran the bench optimizer.

The final code review (cumulative, post-merge) flagged this as a grounded-falsehood risk: the
prompt and exemplars conditioned on `bench_changed`, so a hardcoded `True` could cause the LLM
to claim "the bench was reordered" when the existing bench was already optimal.

**Resolution (instead of computing the real signal):** drop the field entirely. The deadguard
always runs the bench optimizer; "the bench order was optimized" is truthful regardless of
whether the order actually changed. The prompt now always notes the bench was optimized, and
exemplars no longer carry a `bench_changed` key.

Trade-off: prose can no longer say "bench was already correct" vs. "bench was reordered." If
the real signal becomes valuable, a future slice can re-introduce the field by having
`run_lineup` compute and return the comparison.
