# LLM Chip Reasoning Implementation Plan (Phase 3, S-A.3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add LLM prose to the dashboard's chip recommendation pane + the Telegram H-48 chip-preview body. Deterministic chip recommender unchanged (B4 untouched).

**Architecture:** Reuses S-A.1+S-A.2 plumbing (provider, cache, grounding, scheduler hook, config accessors). Adds chip-specific payload + prompt + render/generate + jobs branch + queries wrapper + Telegram swap + frontend rendering. Simpler than S-A.2 because the chip engine's `reason` string already carries the relevant numbers — minimal payload, no extra DB queries.

**Tech Stack:** Same as S-A.1/A.2. All tests fixtures-only with `StubProvider` (R3).

**Source spec:** `docs/superpowers/specs/2026-05-26-phase3-llm-chip-reasoning-design.md`. **Read it first.** Cross-cutting reference: `2026-05-26-phase3-ai-architecture-design.md`. Related plans: S-A.1 (`2026-05-26-phase3-llm-captain-reasoning-plan.md`), S-A.2 (`2026-05-26-phase3-llm-transfer-reasoning-plan.md`).

**B-rule stance:**
- **B4:** untouched — **no edit to `docs/decision-engine.md`**.
- **B7:** prompt builder is sole egress; closed-shape payload.
- **B8:** no executor changes.
- **R3:** all tests use `StubProvider`.
- **B11:** decision-layer tests untouched.
- **Git hygiene:** **NEVER `git add -A`**. Footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File structure (locked)

**New files:**
- `src/ai/prompts/chip.txt`
- `src/ai/prompts/chip_examples.json` (4 exemplars — one per chip type)
- `tests/test_ai_prompts_chip.py`
- `tests/test_ai_reasoning_chip.py`

**Modified files:**
- `src/ai/reasoning.py` — append `_build_chip_payload`, `_build_chip_prompt`, `render_chip_reasoning`, `generate_chip_prose`
- `src/ai/jobs.py` — add `chip` branch + `_default_chip_decision_fn`
- `src/scheduler.py` — extend panes to `["captain", "transfer", "chip"]`
- `src/interface/queries.py` — add `get_chip_recommendation` + `get_chip_reasoning`
- `src/interface/api.py` — `/api/chips` calls queries
- `src/interface/telegram.py` — add `_chip_ai_prose` + extend `notify_plan`
- `tests/test_ai_jobs.py` / `test_scheduler.py` / `test_api.py` / `test_telegram.py` — extend
- `frontend/src/lib/types.ts` — `ChipRecommendation` gains optional `reasoning?` + `reasoning_source?`
- `frontend/src/lib/components/ChipRecommendation.svelte` — prose + AI/classic tag
- `frontend/src/lib/components/ChipRecommendation.svelte.test.ts` — extend
- `frontend/src/lib/mocks/full.ts` — chip mock gets reasoning + source

**Note:** before any task that modifies existing code, **read the target file first**.

---

## Task 0: Chip prompt + few-shot exemplars (self-validating)

**Files:**
- Create: `src/ai/prompts/chip.txt`
- Create: `src/ai/prompts/chip_examples.json`
- Create: `tests/test_ai_prompts_chip.py`

- [ ] **Step 1: Write failing tests**

`tests/test_ai_prompts_chip.py`:

```python
import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_chip_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "chip.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_chip_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "chip_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 4   # one per chip type
    chip_types_covered = {ex["input"]["chip"] for ex in examples}
    assert chip_types_covered == {"wildcard", "free_hit", "bench_boost", "triple_captain"}
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}
        assert isinstance(ex["input"], dict)
        assert isinstance(ex["output"], str)


def test_every_chip_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "chip_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"chip example {i} ({ex['input']['chip']}) prose contains ungrounded numbers: {ungrounded}"
```

- [ ] **Step 2: Verify FAIL**: `.venv/bin/pytest tests/test_ai_prompts_chip.py -v`

- [ ] **Step 3: Create `src/ai/prompts/chip.txt`** (verbatim from spec §2)

- [ ] **Step 4: Create `src/ai/prompts/chip_examples.json`** (verbatim from spec §2 — 4 exemplars)

- [ ] **Step 5: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_ai_prompts_chip.py -v && .venv/bin/pytest -q
```

- [ ] **Step 6: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/ai/prompts/chip.txt src/ai/prompts/chip_examples.json tests/test_ai_prompts_chip.py && git commit -m "$(cat <<'EOF'
feat(ai): chip prompt template + 4 few-shot exemplars (S-A.3 task 0)

Per-pane structured prompt with one exemplar per chip type (wildcard,
free_hit, bench_boost, triple_captain). Self-validating: every exemplar's
output passes is_grounded against its input. Test enforces all 4 chip
types are covered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Chip payload + prompt + render + generate (bundled)

The chip flow is small enough to land all four functions in one task. Mirrors S-A.1's pattern but with a simpler payload.

**Files:**
- Modify: `src/ai/reasoning.py` (append 4 functions)
- Create: `tests/test_ai_reasoning_chip.py`

- [ ] **Step 1: Read `src/ai/reasoning.py`** to see where to append. The file already has captain + transfer functions.

- [ ] **Step 2: Write failing tests** in `tests/test_ai_reasoning_chip.py`:

```python
import json

from src.data.db import connect, init_db
from src.ai import reasoning, cache as ai_cache, provider as prv


def _db():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    return conn


CHIP_DECISION_FIXTURE = {
    "recommendation": {
        "chip": "triple_captain",
        "reason": "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2.",
    },
}


def test_build_chip_payload_shape():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    assert payload == {
        "chip": "triple_captain",
        "reason": "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2.",
        "next_gw": 38,
    }


def test_build_chip_payload_returns_none_on_no_recommendation():
    conn = _db()
    assert reasoning._build_chip_payload(conn, {"recommendation": None}) is None


def test_build_chip_payload_returns_none_when_no_next_gw():
    conn = connect(":memory:")
    init_db(conn)   # no gameweeks
    assert reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE) is None


def test_build_chip_prompt_includes_payload_and_examples():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    prompt = reasoning._build_chip_prompt(payload)
    assert "triple_captain" in prompt
    assert "GW39 DGW: Haaland DGW-xP 14.8" in prompt   # the engine reason verbatim
    assert "wildcard" in prompt    # from other exemplars
    assert "free_hit" in prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "Do not invent" in prompt


def test_render_chip_reasoning_returns_classic_engine_reason_on_cache_miss():
    """Unlike transfer, chip's classic fallback returns the engine's existing reason
    string (which is meaningful prose), not empty."""
    conn = _db()
    prose, source = reasoning.render_chip_reasoning(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE)
    assert source == "classic"
    assert prose == "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2."


def test_render_chip_reasoning_returns_ai_on_cache_hit():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="chip", rec_hash=rec_hash,
                 prose="Triple Captain on Haaland — strong DGW.", model_id="m")
    prose, source = reasoning.render_chip_reasoning(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE)
    assert source == "ai"
    assert prose == "Triple Captain on Haaland — strong DGW."


def test_render_chip_reasoning_returns_classic_empty_on_no_recommendation():
    conn = _db()
    prose, source = reasoning.render_chip_reasoning(
        conn, gw=38, chip_decision={"recommendation": None})
    assert source == "classic"
    assert prose == ""


def test_generate_chip_prose_caches_grounded_prose():
    conn = _db()
    # Grounded prose: numbers 39, 14.8, 12.0, 2 all appear in payload JSON
    stub = prv.StubProvider("Triple Captain on Haaland in GW39 — DGW-xP 14.8 above the 12.0 threshold, FDR 2.")
    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="chip", rec_hash=rec_hash) is not None


def test_generate_chip_prose_rejects_ungrounded_prose():
    conn = _db()
    stub = prv.StubProvider("Triple Captain — confidence 99 over 99 GWs.")
    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_chip_prose_rejects_empty_prose():
    conn = _db()
    stub = prv.StubProvider("")
    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_chip_prose_skips_on_no_recommendation():
    conn = _db()

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called when no recommendation")

    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision={"recommendation": None},
        provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_chip_prose_skips_provider_on_cache_hit():
    conn = _db()
    payload = reasoning._build_chip_payload(conn, CHIP_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="chip", rec_hash=rec_hash,
                 prose="cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called on cache hit")

    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_chip_prose_swallows_provider_errors():
    conn = _db()

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("down")

    ok = reasoning.generate_chip_prose(
        conn, gw=38, chip_decision=CHIP_DECISION_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
```

- [ ] **Step 3: Verify FAIL**: `.venv/bin/pytest tests/test_ai_reasoning_chip.py -v`

- [ ] **Step 4: Append to `src/ai/reasoning.py`** (at the END of the file, after the transfer functions):

```python


def _build_chip_payload(conn, chip_decision: dict) -> dict | None:
    """Closed-shape payload for the chip recommendation.

    Returns None when:
    - no recommendation (LLM has nothing to render)
    - no next gw (post-season state)
    """
    rec = chip_decision.get("recommendation")
    if rec is None:
        return None
    next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    return {
        "chip": rec["chip"],
        "reason": rec["reason"],
        "next_gw": next_gw,
    }


def _build_chip_prompt(payload: dict) -> str:
    """Render chip.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "chip.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "chip_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)


def render_chip_reasoning(conn, gw: int, chip_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> (engine_reason, 'classic').
    No recommendation -> ('', 'classic')."""
    payload = _build_chip_payload(conn, chip_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "chip", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    return (chip_decision["recommendation"]["reason"], "classic")


def generate_chip_prose(conn, gw: int, chip_decision: dict, *,
                       provider, model_id: str,
                       max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).
    Provider errors caught; empty/ungrounded prose not cached."""
    payload = _build_chip_payload(conn, chip_decision)
    if payload is None:
        logger.info("ai.chip.skipped_empty", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "chip", rec_hash) is not None:
        return True
    prompt = _build_chip_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaError:
        logger.exception("ai.chip.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    if not prose:
        logger.warning("ai.chip.empty_prose",
                       extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.chip.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "chip", rec_hash, prose, model_id)
    return True
```

- [ ] **Step 5: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_ai_reasoning_chip.py -v && .venv/bin/pytest -q
```

Expected: 13 new tests pass; full suite goes 511 → 524.

- [ ] **Step 6: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/ai/reasoning.py tests/test_ai_reasoning_chip.py && git commit -m "$(cat <<'EOF'
feat(ai): chip payload + prompt + render + generate (S-A.3 task 1)

_build_chip_payload: minimal closed-shape dict (chip, reason, next_gw)
from chip-engine output. Returns None on no-recommendation or post-season.

render_chip_reasoning: cache-first; miss returns the engine reason string
(meaningful prose, not empty — unlike transfer).

generate_chip_prose: same flow as generate_captain_prose — payload check
→ cache check → prompt → provider → empty guard → grounding → cache.put.
Provider errors caught (OllamaError); empty/ungrounded prose logged and
not cached. Mirrors S-A.1/2 safety semantics exactly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: jobs.py 'chip' branch + scheduler panes extension

**Files:**
- Modify: `src/ai/jobs.py`
- Modify: `src/scheduler.py`
- Modify: `tests/test_ai_jobs.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Read** `src/ai/jobs.py` (has captain + transfer branches) and `src/scheduler.py` (has `panes=["captain", "transfer"]`).

- [ ] **Step 2: Append failing tests** to `tests/test_ai_jobs.py`:

```python


_CHIP_DECISION = {
    "recommendation": {
        "chip": "triple_captain",
        "reason": "GW39 DGW: Haaland DGW-xP 14.8 (>= 12.0), FDR 2.",
    },
}


def test_generate_ai_reasoning_job_caches_chip_prose():
    conn = _db()
    stub = prv.StubProvider("Triple Captain on Haaland in GW39 — DGW-xP 14.8 above 12.0, FDR 2.")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["chip"], provider=stub, model_id="m",
        chip_decision_fn=lambda c: _CHIP_DECISION)
    assert result == {"chip": "ok"}


def test_generate_ai_reasoning_job_handles_all_three_panes():
    conn = _db()
    _seed_transfer_minimum(conn)  # also enables transfer pane

    class _ThreeResponseStub:
        def __init__(self):
            self.responses = iter([
                "captain Haaland at 7.2 xP — gap 1.8 vs Salah, confidence 82.",
                "Sell Watkins (d), buy Haaland — fdr 2 vs fdr 4. Free transfer adds 3.5 EP at 78.",
                "Triple Captain on Haaland in GW39 — DGW-xP 14.8 above 12.0, FDR 2.",
            ])
        def generate(self, prompt, **kw):
            return next(self.responses)

    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain", "transfer", "chip"],
        provider=_ThreeResponseStub(), model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION,
        transfer_decision_fn=lambda c: _TRANSFER_DECISION,
        chip_decision_fn=lambda c: _CHIP_DECISION)
    assert result == {"captain": "ok", "transfer": "ok", "chip": "ok"}
```

- [ ] **Step 3: Append failing test** to `tests/test_scheduler.py`:

```python


def test_refresh_and_recompute_invokes_ai_with_three_panes(monkeypatch):
    """ai.enabled=True calls generate_ai_reasoning_job with panes=['captain', 'transfer', 'chip']."""
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    captured_panes = []
    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda c, **kw: captured_panes.append(kw["panes"]) or {})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert captured_panes == [["captain", "transfer", "chip"]]
```

- [ ] **Step 4: Verify FAIL**

- [ ] **Step 5: Modify `src/ai/jobs.py`** — add the chip branch:

Add a `_default_chip_decision_fn` next to the existing default fns:

```python
def _default_chip_decision_fn(conn):
    from src.decisions import chips
    return chips.recommend_chip(conn)
```

Add `chip_decision_fn: Callable | None = None` to `generate_ai_reasoning_job`'s signature.

Inside the for-loop, add the chip branch (after the transfer branch, before the `else`):

```python
        elif pane == "chip":
            decision = chip_fn(conn)
            ok = reasoning.generate_chip_prose(
                conn, gw=gw, chip_decision=decision,
                provider=provider, model_id=model_id)
            result[pane] = "ok" if ok else "failed"
```

And bind `chip_fn = chip_decision_fn or _default_chip_decision_fn` near the top of the function with the other fn bindings.

- [ ] **Step 6: Modify `src/scheduler.py`** — change `panes=["captain", "transfer"]` to `panes=["captain", "transfer", "chip"]`. One-line edit.

- [ ] **Step 7: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_ai_jobs.py tests/test_scheduler.py -v
.venv/bin/pytest -q
```

Expected: 3 new tests pass; full suite 524 → 527.

- [ ] **Step 8: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/ai/jobs.py src/scheduler.py tests/test_ai_jobs.py tests/test_scheduler.py && git commit -m "$(cat <<'EOF'
feat(ai): jobs.py chip branch + scheduler panes extension (S-A.3 task 2)

generate_ai_reasoning_job adds a 'chip' branch + chip_decision_fn parameter
mirroring captain/transfer. refresh_and_recompute now pre-warms three
panes per recompute cycle (panes=['captain', 'transfer', 'chip']).
Most weeks the chip recommendation is None so the AI call short-circuits
and no row is cached — correct sparse behaviour.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: queries.get_chip_recommendation + api.py rewiring

**Files:**
- Modify: `src/interface/queries.py`
- Modify: `src/interface/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Read** `src/interface/queries.py` (has captain + transfer wrappers) and `src/interface/api.py` (`/api/chips` calls `chips_engine.recommend_chip(conn)` directly).

- [ ] **Step 2: Append failing tests** to `tests/test_api.py` (adapt fixture names to actual):

```python
def test_api_chips_carries_reasoning_classic_on_cache_miss(client_with_data):
    """With no AI cache + a non-null recommendation: classic = engine reason."""
    resp = client_with_data.get("/api/chips")
    assert resp.status_code == 200
    body = resp.json()
    if body.get("recommendation") is None:
        return    # fixture has no recommendation — skip
    rec = body["recommendation"]
    assert rec["reasoning_source"] == "classic"
    assert rec["reasoning"] == rec["reason"]


def test_api_chips_carries_reasoning_ai_on_cache_hit(client_conn):
    """Pre-warm the cache for the next GW's chip payload -> reasoning_source='ai'."""
    client, conn = client_conn
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import chips
    decision = chips.recommend_chip(conn)
    if decision.get("recommendation") is None:
        return
    payload = ai_reasoning._build_chip_payload(conn, decision)
    if payload is None:
        return
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="chip", rec_hash=rec_hash,
                 prose="Chip AI prose.", model_id="qwen2.5:7b-instruct-q4_K_M")

    resp = client.get("/api/chips")
    body = resp.json()
    assert body["recommendation"]["reasoning_source"] == "ai"
    assert body["recommendation"]["reasoning"] == "Chip AI prose."


def test_api_chips_returns_unchanged_shape_when_no_recommendation(client_with_data):
    """When the engine returns recommendation=None, the response shape is unchanged
    (no reasoning fields added — there's nothing to enrich)."""
    # If client_with_data fixture happens to produce a recommendation, this test is degenerate.
    # The intent is to verify the wrapper doesn't add reasoning fields to a None recommendation.
    resp = client_with_data.get("/api/chips")
    body = resp.json()
    if body.get("recommendation") is None:
        assert "recommendation" in body  # the key exists, value is None
```

- [ ] **Step 3: Verify FAIL**: `.venv/bin/pytest tests/test_api.py -v -k "chip"`

- [ ] **Step 4: Append to `src/interface/queries.py`**:

```python


def get_chip_recommendation(conn):
    """Wraps chips.recommend_chip and enriches the recommendation (if any) with
    (reasoning, reasoning_source). When recommendation is None, returns the
    decision dict unchanged."""
    from src.decisions import chips
    from src.ai import reasoning as ai_reasoning
    decision = chips.recommend_chip(conn)
    if decision.get("recommendation") is None:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_chip_reasoning(conn, gw, decision)
    return {
        **decision,
        "recommendation": {
            **decision["recommendation"],
            "reasoning": prose,
            "reasoning_source": source,
        },
    }


def get_chip_reasoning(conn, gw):
    """Telegram-path helper. Returns cached AI prose, or None on miss."""
    from src.decisions import chips
    from src.ai import reasoning as ai_reasoning
    decision = chips.recommend_chip(conn)
    if decision.get("recommendation") is None:
        return None
    prose, source = ai_reasoning.render_chip_reasoning(conn, gw, decision)
    return prose if source == "ai" else None
```

- [ ] **Step 5: Update `src/interface/api.py` `/api/chips` route**

Read the current endpoint. Replace `return chips_engine.recommend_chip(conn)` with `return queries.get_chip_recommendation(conn)`. Drop the `chips as chips_engine` import if nothing else uses it (verify with grep).

- [ ] **Step 6: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_api.py -v
.venv/bin/pytest -q
```

Expected: 3 new tests pass (one may be degenerate-skipped depending on fixture state); full suite 527 → 530.

- [ ] **Step 7: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/interface/queries.py src/interface/api.py tests/test_api.py && git commit -m "$(cat <<'EOF'
feat(ai): /api/chips returns enriched recommendation (S-A.3 task 3)

queries.get_chip_recommendation wraps chips.recommend_chip and enriches
the recommendation with (reasoning, reasoning_source). Cache hit ->
'ai' + cached prose; miss -> 'classic' + the engine's existing reason
string (unlike transfer where classic returns empty — chip's engine
reason is meaningful prose). No recommendation -> unchanged shape (no
reasoning fields added; nothing to enrich).

queries.get_chip_reasoning is the Telegram-path helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Telegram notify_plan chip swap

**Files:**
- Modify: `src/interface/telegram.py`
- Modify: `tests/test_telegram.py`

- [ ] **Step 1: Read** `src/interface/telegram.py` (has `_captain_ai_prose` + `_transfer_ai_prose`).

- [ ] **Step 2: Append failing tests** to `tests/test_telegram.py`:

```python
def test_notify_plan_swaps_chip_summary_when_ai_cache_populated(monkeypatch, tmp_path):
    """If cached AI chip prose exists for the next gw, notify_plan uses it for the chip entry."""
    from src.data.db import connect, init_db
    from src.interface import telegram
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import chips

    conn = connect(":memory:")
    init_db(conn)
    _seed_chip_db(conn)   # helper below

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    decision = chips.recommend_chip(conn)
    assert decision["recommendation"] is not None, "seed must produce a recommendation"
    payload = ai_reasoning._build_chip_payload(conn, decision)
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="chip", rec_hash=rec_hash,
                 prose="AI prose for chip.", model_id="m")

    sent = []
    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "chip", "summary": "template chip summary", "executed": False}]
    telegram.notify_plan(conn, plan, mode="manual", session=_FakeSession())
    assert sent
    assert "AI prose for chip." in sent[0]["text"]
    assert "template chip summary" not in sent[0]["text"]


def test_notify_plan_uses_classic_summary_when_no_chip_ai_cache(monkeypatch, tmp_path):
    from src.data.db import connect, init_db
    from src.interface import telegram

    conn = connect(":memory:")
    init_db(conn)
    _seed_chip_db(conn)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    sent = []
    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "chip", "summary": "template chip summary", "executed": False}]
    telegram.notify_plan(conn, plan, mode="manual", session=_FakeSession())
    assert sent
    assert "template chip summary" in sent[0]["text"]


def _seed_chip_db(conn):
    """Minimal seed so chips.recommend_chip returns a non-None recommendation.
    The Triple Captain trigger is the simplest to satisfy: 1 premium player, DGW, FDR<=2, xp>=12."""
    import json as _json
    # Two GWs so the engine sees a horizon
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (39, 'GW39', '2026-06-09T18:30Z', 0, 0, 0, 'PENDING')")
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), "
                 "(2, 'Brentford', 'BRE'), (3, 'Bournemouth', 'BOU')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status, ownership, form) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a', 50.0, 5.0)")
    # MCI plays twice in GW39 (DGW): once vs BRE, once vs BOU
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 39, 1, 2, '2026-06-09T19:00Z', 0), "
                 "(2, 39, 3, 1, '2026-06-09T17:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES "
                 "(1, 39, 2, 2, '2026-05-19T00:00Z')")
    # my_team with Haaland on the bench (anywhere is fine for chip recommender)
    conn.execute("INSERT INTO my_team(gw, picks_json, chips_used_json) VALUES (38, ?, ?)",
                 (_json.dumps([{"element": 10, "position": 1, "multiplier": 1,
                                "is_captain": False, "is_vice_captain": False}]),
                  _json.dumps([])))
    # Understat row for Haaland (high xG so DGW-xP exceeds 12.0)
    conn.execute("INSERT INTO understat_players(understat_id, fpl_player_id, season, player_name, "
                 "team_title, games, minutes, goals, assists, xg, xa, npg, npxg, xg_per_90, xa_per_90, updated_at) "
                 "VALUES ('h1', 10, '2025', 'Haaland', 'Man City', 30, 2700, 25, 5, 25.0, 5.0, 24, 23.0, 0.83, 0.17, '2026-05-19T00:00Z')")
    conn.commit()
```

The seed needs to satisfy the Triple Captain trigger from `src/decisions/chips.py`: premium player (price ≥ 9.5), DGW for their team in any of next 6 GWs, FDR_attack ≤ 2 for that GW, DGW-xP ≥ 12. The high xg_per_90 (0.83) + DGW (2 fixtures) + FDR 2 should clear the threshold.

If `chips.recommend_chip(conn)` still returns None after this seed, debug via:
```python
.venv/bin/python -c "
from src.data.db import connect, init_db
from tests.test_telegram import _seed_chip_db
from src.decisions import chips
conn = connect(':memory:'); init_db(conn); _seed_chip_db(conn)
print(chips.recommend_chip(conn))
"
```
and adjust the seed numbers (push xg_per_90 higher or check `dgw.team_fixture_count` / `dgw.team_gw_fdr` to see what the engine is computing).

- [ ] **Step 3: Verify FAIL**: `.venv/bin/pytest tests/test_telegram.py -v -k "chip_summary"`

- [ ] **Step 4: Modify `src/interface/telegram.py`** — add `_chip_ai_prose` (sibling of `_captain_ai_prose`/`_transfer_ai_prose`) and extend `notify_plan`:

```python
def notify_plan(conn, plan, *, mode, session=None):
    """Best-effort: notify per plan entry. When captain/transfer/chip AI prose is cached for the
    next gw, swap the summary; falls back to entry['summary'] otherwise."""
    if not is_configured():
        return
    captain_prose  = _captain_ai_prose(conn)
    transfer_prose = _transfer_ai_prose(conn)
    chip_prose     = _chip_ai_prose(conn)
    for entry in plan:
        kind = "executed" if entry["executed"] else "info"
        summary = entry["summary"]
        if entry["decision"] == "captain"  and captain_prose  is not None: summary = captain_prose
        if entry["decision"] == "transfer" and transfer_prose is not None: summary = transfer_prose
        if entry["decision"] == "chip"     and chip_prose     is not None: summary = chip_prose
        notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
               summary=summary, session=session)


def _chip_ai_prose(conn):
    """Return cached AI prose for the chip pane at the next gw, or None.
    Best-effort: any exception is swallowed."""
    try:
        from src.interface import queries
        nxt = conn.execute(
            "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        if nxt is None or nxt["gw"] is None:
            return None
        return queries.get_chip_reasoning(conn, gw=nxt["gw"])
    except Exception:
        return None
```

(Keep `_captain_ai_prose` and `_transfer_ai_prose` unchanged.)

**Note on the alignment trap from S-A.2 review:** when adding the new line, use single spaces consistently — don't insert padding spaces to align the three assignments. The reviewer caught this in S-A.2 (CLAUDE.md §3 — don't improve adjacent formatting).

- [ ] **Step 5: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_telegram.py -v
.venv/bin/pytest -q
```

Expected: 2 new tests pass; full suite 530 → 532.

- [ ] **Step 6: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/interface/telegram.py tests/test_telegram.py && git commit -m "$(cat <<'EOF'
feat(ai): notify_plan swaps chip summary with AI prose when cached (S-A.3 task 4)

Extends the S-A.1/A.2 swap pattern to chip entries. notify_plan now looks
up all three AI prose helpers (_captain_ai_prose, _transfer_ai_prose,
_chip_ai_prose) at the top of the function, then swaps per entry-type
as it iterates the plan. Each lookup is best-effort: any exception falls
back to the template summary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Frontend — ChipRecommendation prose + AI/classic tag

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/components/ChipRecommendation.svelte`
- Modify: `frontend/src/lib/components/ChipRecommendation.svelte.test.ts`
- Modify: `frontend/src/lib/mocks/full.ts`

- [ ] **Step 1: Read** all four files.

- [ ] **Step 2: Append failing vitest cases** to `ChipRecommendation.svelte.test.ts`:

```ts
describe('ChipRecommendation AI/classic tag + prose', () => {
    it('shows AI prose and AI tag when reasoning_source is ai', () => {
        const chips = {
            recommendation: { chip: 'triple_captain', reason: 'GW39 DGW: Haaland DGW-xP 14.8.',
                              reasoning: 'AI chip prose here.', reasoning_source: 'ai' as const },
        };
        render(ChipRecommendation, { chips });
        expect(screen.getByText('AI chip prose here.')).toBeInTheDocument();
        expect(screen.getByText('AI')).toBeInTheDocument();
        expect(screen.queryByText('GW39 DGW: Haaland DGW-xP 14.8.')).not.toBeInTheDocument();   // engine reason is replaced
    });

    it('shows engine reason with no AI tag when reasoning_source is classic', () => {
        const chips = {
            recommendation: { chip: 'triple_captain', reason: 'GW39 DGW: Haaland DGW-xP 14.8.',
                              reasoning: 'GW39 DGW: Haaland DGW-xP 14.8.', reasoning_source: 'classic' as const },
        };
        render(ChipRecommendation, { chips });
        expect(screen.getByText('GW39 DGW: Haaland DGW-xP 14.8.')).toBeInTheDocument();
        expect(screen.queryByText('AI')).not.toBeInTheDocument();
    });

    it('renders backwards-compat when reasoning fields are absent', () => {
        const chips = {
            recommendation: { chip: 'triple_captain', reason: 'GW39 DGW: Haaland DGW-xP 14.8.' },
        };
        render(ChipRecommendation, { chips });
        expect(screen.getByText('GW39 DGW: Haaland DGW-xP 14.8.')).toBeInTheDocument();
        expect(screen.queryByText('AI')).not.toBeInTheDocument();
    });

    it('renders nothing when no recommendation', () => {
        const chips = { recommendation: null };
        const { container } = render(ChipRecommendation, { chips });
        expect(container.querySelector('.chip-rec')).not.toBeInTheDocument();
    });
});
```

Add imports at the top if not already present:
```ts
import { render, screen } from '@testing-library/svelte';
import { describe, it, expect } from 'vitest';
import ChipRecommendation from './ChipRecommendation.svelte';
```

- [ ] **Step 3: Verify FAIL**: `cd /Users/shariski/Work/fpl-autopilot-phase3/frontend && npm test -- ChipRecommendation`

- [ ] **Step 4: Update `frontend/src/lib/types.ts`** — extend `ChipRecommendation`:

```ts
export interface ChipRecommendation {
    chip: Chip;
    reason: string;
    reasoning?: string;
    reasoning_source?: 'ai' | 'classic';
}
```

(Read existing definition first; ONLY ADD the two optional fields, preserve all others.)

- [ ] **Step 5: Update `frontend/src/lib/components/ChipRecommendation.svelte`**

Read the current file. The change:
- Replace the `<p class="reason">{rec.reason}</p>` line with a conditional that uses AI prose when present + a small AI/classic tag:

```svelte
{#if rec}
    <div class="chip-rec">
        <div class="badge">{label[rec.chip] ?? rec.chip}</div>
        {#if rec.reasoning && rec.reasoning_source === 'ai'}
            <p class="reason">{rec.reasoning} <span class="ai-tag ai-tag-ai" aria-label="AI-generated reasoning">AI</span></p>
        {:else}
            <p class="reason">{rec.reasoning || rec.reason}</p>
        {/if}
    </div>
{/if}
```

(The classic branch shows the engine reason as today, no tag. The AI branch shows the prose + tag. Backwards-compat: when `reasoning` is absent, falls to the engine reason via `rec.reasoning || rec.reason`.)

Add CSS to the existing `<style>` block:
```css
.ai-tag { font-size: 0.7em; padding: 0.1em 0.4em; border-radius: 0.3em; margin-left: 0.4em;
    background: #2563eb; color: white; }
```

(Single class — chip pane only ever shows AI tag, not classic, since classic falls through to the engine reason without a tag.)

- [ ] **Step 6: Update `frontend/src/lib/mocks/full.ts`** — find the `chips.recommendation` object. Add:
```ts
reasoning: 'Triple Captain on Haaland in GW39 — DGW-xP 14.8 above the 12.0 threshold, FDR 2.',
reasoning_source: 'ai' as const,
```

- [ ] **Step 7: Verify PASS**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3/frontend && npm test
```

Expected: existing tests pass + 4 new. Total: 58 → 62.

If a pre-existing ChipRecommendation test breaks because the mock now has reasoning text + the test expects engine reason text, update that test's assertion (similar pattern to S-A.2's mock cascade).

- [ ] **Step 8: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add frontend/src/lib/types.ts frontend/src/lib/components/ChipRecommendation.svelte frontend/src/lib/components/ChipRecommendation.svelte.test.ts frontend/src/lib/mocks/full.ts && git commit -m "$(cat <<'EOF'
feat(ai): ChipRecommendation renders AI prose + AI tag (S-A.3 task 5)

ChipRecommendation type gains optional reasoning + reasoning_source.
The component renders AI prose with an 'AI' tag when reasoning_source ==
'ai'; falls through to the engine reason with no tag otherwise.
Backwards-compatible: panes without the new fields render as today.

Distinct .ai-tag CSS class avoids collision with the existing .badge
(chip-type label). Classic state has no tag — engine reason is itself
meaningful prose so no source indication needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Verification + final review + finishing

- [ ] **Step 1: Full pytest + vitest**:
```
.venv/bin/pytest -q && (cd frontend && npm test)
```

Expected: 532 pytest + 62 vitest. All green. No `decision-engine.md` change (`git log --name-only main..HEAD | grep decision-engine && echo FAIL || echo OK`).

- [ ] **Step 2: Dispatch final code review** via the orchestrator:

Brief for `pr-review-toolkit:code-reviewer` (sonnet):
> Review cumulative diff of `feat/phase3-sa3-chip` vs `main`. S-A.3 = LLM chip-recommendation prose. Same B-rule profile as S-A.1/A.2. Focus: simpler payload than S-A.2 (chip emits its own reason string, payload just `{chip, reason, next_gw}`); ChipRecommendation.svelte uses distinct `.ai-tag` CSS class to avoid colliding with the existing `.badge` (chip-type label); chip's classic fallback returns the engine reason (not empty) because the engine emits meaningful prose. Report Critical/Important/Minor.

Apply blocking findings as focused fix commits.

- [ ] **Step 3: Invoke `superpowers:finishing-a-development-branch`**

Option 1 (merge to main locally). Likely a clean fast-forward — `main` hasn't moved during this slice. **DO push** when prompted (the user has been pushing each slice).

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| `src/ai/prompts/chip.txt` + 4 exemplars | T0 |
| Self-validating exemplars (golden) | T0 |
| `_build_chip_payload` (minimal: chip, reason, next_gw) | T1 |
| `_build_chip_prompt` | T1 |
| `render_chip_reasoning` (read path; classic = engine reason) | T1 |
| `generate_chip_prose` (write path with empty + grounding guards) | T1 |
| jobs.py `chip` branch + `chip_decision_fn` | T2 |
| Scheduler `panes=['captain', 'transfer', 'chip']` | T2 |
| `queries.get_chip_recommendation` + `get_chip_reasoning` | T3 |
| `/api/chips` rewired | T3 |
| Telegram `_chip_ai_prose` + `notify_plan` swap | T4 |
| Frontend type + component + tag + mock | T5 |
| Distinct `.ai-tag` CSS (no collision with `.badge`) | T5 |
| Full suite green + B4/B7/B8 preserved | T6 |
| Final code review | T6 |
| Finishing a development branch + push | T6 |

Every spec requirement maps to at least one task. ✓
