# LLM Deadguard Summary Implementation Plan (Phase 3, S-A.4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.

**Goal:** AI prose for the deadguard post-execution Telegram body + dashboard banner. Deterministic deadguard unchanged (B4 untouched).

**Architecture:** Reuses S-A.1+S-A.2+S-A.3 plumbing. Generation happens at EXECUTION time inside `deadguard._run_trigger` (not scheduler). No frontend code change — the dashboard banner already takes arbitrary text from `/api/status`.

**Tech Stack:** Same as previous slices. All tests fixtures-only with `StubProvider` (R3).

**Source spec:** `docs/superpowers/specs/2026-05-26-phase3-llm-deadguard-summary-design.md`. **Read it first.** Cross-cutting reference: `2026-05-26-phase3-ai-architecture-design.md`.

**B-rule stance:** B4 untouched (no `decision-engine.md` edit). B7 closed payload. B8 no executor change beyond AI insertion. R3 all tests use `StubProvider`. **Git hygiene: NEVER `git add -A`.** Footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File structure (locked)

**New files:**
- `src/ai/prompts/deadguard.txt`
- `src/ai/prompts/deadguard_examples.json` (3 exemplars: captain-only / +bench / +transfer)
- `tests/test_ai_prompts_deadguard.py`
- `tests/test_ai_reasoning_deadguard.py`

**Modified files:**
- `src/ai/reasoning.py` — append 4 functions
- `src/interface/deadguard.py` — `_run_trigger` AI integration with catch-all try/except
- `src/interface/queries.py` — `_status_banners` reads cached deadguard prose for the banner text
- `tests/test_deadguard.py` — extend for AI prose path
- `tests/test_api.py` — extend for banner-text using cached prose

**No changes to:** `src/scheduler.py`, `src/ai/jobs.py` (S-A.4 is execution-driven, not scheduler-driven), `src/interface/api.py`, `src/interface/telegram.py`, any frontend file.

---

## Task 0: Deadguard prompt + 3 few-shot exemplars (self-validating)

**Files:**
- Create: `src/ai/prompts/deadguard.txt`
- Create: `src/ai/prompts/deadguard_examples.json`
- Create: `tests/test_ai_prompts_deadguard.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_prompts_deadguard.py`:

```python
import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_deadguard_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "deadguard.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_deadguard_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "deadguard_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 3
    # Cover the realistic outcome combos: captain-only, +bench, +transfer
    has_captain_only = any(ex["input"].get("transfer") is None and
                            not ex["input"].get("bench_changed", False) for ex in examples)
    has_bench = any(ex["input"].get("bench_changed") and ex["input"].get("transfer") is None
                    for ex in examples)
    has_transfer = any(ex["input"].get("transfer") is not None for ex in examples)
    assert has_captain_only and has_bench and has_transfer
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}


def test_every_deadguard_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "deadguard_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"deadguard example {i} prose contains ungrounded numbers: {ungrounded}"
```

- [ ] **Step 2: Verify FAIL**: `.venv/bin/pytest tests/test_ai_prompts_deadguard.py -v`

- [ ] **Step 3: Create `src/ai/prompts/deadguard.txt`** (verbatim from spec §2)

- [ ] **Step 4: Create `src/ai/prompts/deadguard_examples.json`** (verbatim from spec §2 — 3 exemplars)

- [ ] **Step 5: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_ai_prompts_deadguard.py -v && .venv/bin/pytest -q
```

- [ ] **Step 6: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/ai/prompts/deadguard.txt src/ai/prompts/deadguard_examples.json tests/test_ai_prompts_deadguard.py && git commit -m "$(cat <<'EOF'
feat(ai): deadguard prompt template + 3 few-shot exemplars (S-A.4 task 0)

Per-pane structured prompt covering 3 outcome combos: captain-only,
+bench-reorder, +transfer. Self-validating: every exemplar's output passes
is_grounded against its input. Test enforces all 3 outcome shapes are
covered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Deadguard payload + prompt + render + generate (bundled)

**Files:**
- Modify: `src/ai/reasoning.py` (append 4 functions)
- Create: `tests/test_ai_reasoning_deadguard.py`

- [ ] **Step 1: Read `src/ai/reasoning.py`** to see where to append. The file has captain + transfer + chip functions from S-A.1/2/3.

- [ ] **Step 2: Write failing tests** in `tests/test_ai_reasoning_deadguard.py`:

```python
import json

from src.data.db import connect, init_db
from src.ai import reasoning, cache as ai_cache, provider as prv


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


OUTCOME_FIXTURE = {
    "captain_name": "Haaland",
    "vice_name": "Salah",
    "bench_changed": True,
    "transfer": {"out_name": "Watkins", "in_name": "Calvert-Lewin"},
    "gw": 38,
}


def test_build_deadguard_payload_shape():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    assert payload == {
        "captain": "Haaland",
        "vice": "Salah",
        "bench_changed": True,
        "transfer": {"out_name": "Watkins", "in_name": "Calvert-Lewin"},
        "gw": 38,
    }


def test_build_deadguard_payload_returns_none_on_missing_captain():
    conn = _db()
    assert reasoning._build_deadguard_payload(conn, {}) is None
    assert reasoning._build_deadguard_payload(conn, {"captain_name": None}) is None


def test_build_deadguard_payload_handles_no_transfer():
    conn = _db()
    outcome = {"captain_name": "Haaland", "vice_name": "Salah",
               "bench_changed": False, "transfer": None, "gw": 38}
    payload = reasoning._build_deadguard_payload(conn, outcome)
    assert payload["transfer"] is None
    assert payload["bench_changed"] is False


def test_build_deadguard_prompt_includes_payload_and_examples():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    prompt = reasoning._build_deadguard_prompt(payload)
    assert "Haaland" in prompt
    assert "Calvert-Lewin" in prompt   # from payload's transfer
    assert "Watkins" in prompt
    # At least one exemplar's captain should be in the prompt
    assert "Saka" in prompt or "Salah" in prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "ONLY use names and numbers" in prompt


def test_render_deadguard_summary_returns_empty_classic_on_cache_miss():
    """Classic returns empty — the deadguard module composes its own template
    summary at the _notify site when AI is unavailable."""
    conn = _db()
    prose, source = reasoning.render_deadguard_summary(conn, gw=38, outcome=OUTCOME_FIXTURE)
    assert source == "classic"
    assert prose == ""


def test_render_deadguard_summary_returns_ai_on_cache_hit():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="deadguard_summary", rec_hash=rec_hash,
                 prose="Deadguard set captain Haaland and ran a transfer.", model_id="m")
    prose, source = reasoning.render_deadguard_summary(conn, gw=38, outcome=OUTCOME_FIXTURE)
    assert source == "ai"
    assert prose == "Deadguard set captain Haaland and ran a transfer."


def test_render_deadguard_summary_returns_empty_classic_on_missing_outcome():
    conn = _db()
    prose, source = reasoning.render_deadguard_summary(conn, gw=38, outcome={})
    assert source == "classic"
    assert prose == ""


def test_generate_deadguard_summary_caches_grounded_prose():
    conn = _db()
    # Grounded: gw 38 + names appear in payload
    stub = prv.StubProvider("Deadguard set Haaland as captain and Salah as vice for GW38, "
                            "reordered the bench, and transferred out Watkins for Calvert-Lewin.")
    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="deadguard_summary", rec_hash=rec_hash) is not None


def test_generate_deadguard_summary_rejects_ungrounded_prose():
    conn = _db()
    # Wrong gw number not in payload
    stub = prv.StubProvider("Deadguard set Haaland captain for GW99, season standings 7.")
    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_deadguard_summary_rejects_empty_prose():
    conn = _db()
    stub = prv.StubProvider("")
    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_deadguard_summary_skips_on_missing_outcome():
    conn = _db()

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called when outcome is missing")

    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome={},
        provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_deadguard_summary_skips_provider_on_cache_hit():
    conn = _db()
    payload = reasoning._build_deadguard_payload(conn, OUTCOME_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="deadguard_summary", rec_hash=rec_hash,
                 prose="cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called on cache hit")

    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_deadguard_summary_swallows_provider_errors():
    conn = _db()

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("down")

    ok = reasoning.generate_deadguard_summary(
        conn, gw=38, outcome=OUTCOME_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
```

- [ ] **Step 3: Verify FAIL**: `.venv/bin/pytest tests/test_ai_reasoning_deadguard.py -v`

- [ ] **Step 4: Append to `src/ai/reasoning.py`** (at the END of the file, after the chip functions):

```python


def _build_deadguard_payload(conn, outcome: dict) -> dict | None:
    """Closed-shape payload describing what the deadguard did.

    `outcome` is composed by deadguard._run_trigger and looks like:
        {"captain_name": str, "vice_name": str | None, "bench_changed": bool,
         "transfer": {"out_name": str, "in_name": str} | None, "gw": int}

    Returns None if outcome is missing the captain name (defensive — without a
    captain there's no meaningful summary)."""
    if not outcome or not outcome.get("captain_name"):
        return None
    return {
        "captain": outcome["captain_name"],
        "vice": outcome.get("vice_name"),
        "bench_changed": bool(outcome.get("bench_changed", False)),
        "transfer": outcome.get("transfer"),
        "gw": outcome["gw"],
    }


def _build_deadguard_prompt(payload: dict) -> str:
    """Render deadguard.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "deadguard.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "deadguard_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)


def render_deadguard_summary(conn, gw: int, outcome: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> ('', 'classic').
    Note: classic returns empty (no engine fallback prose) because the deadguard
    module composes its own template at the _notify call site."""
    payload = _build_deadguard_payload(conn, outcome)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "deadguard_summary", rec_hash)
    return (hit["prose"], "ai") if hit is not None else ("", "classic")


def generate_deadguard_summary(conn, gw: int, outcome: dict, *,
                              provider, model_id: str,
                              max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).
    Provider errors caught; empty/ungrounded prose not cached."""
    payload = _build_deadguard_payload(conn, outcome)
    if payload is None:
        logger.info("ai.deadguard.skipped_empty", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "deadguard_summary", rec_hash) is not None:
        return True
    prompt = _build_deadguard_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaError:
        logger.exception("ai.deadguard.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    if not prose:
        logger.warning("ai.deadguard.empty_prose",
                       extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.deadguard.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "deadguard_summary", rec_hash, prose, model_id)
    return True
```

- [ ] **Step 5: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_ai_reasoning_deadguard.py -v && .venv/bin/pytest -q
```

Expected: 13 new tests pass; full suite goes 535 → 551 (+13 reasoning, +3 prompts).

- [ ] **Step 6: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/ai/reasoning.py tests/test_ai_reasoning_deadguard.py && git commit -m "$(cat <<'EOF'
feat(ai): deadguard payload + prompt + render + generate (S-A.4 task 1)

_build_deadguard_payload: closed-shape outcome dict (captain, vice,
bench_changed, transfer, gw). Returns None on missing captain — defensive.

render_deadguard_summary: cache-first; miss returns ('', 'classic')
because the deadguard module composes its own template summary at the
_notify call site (unlike captain/chip which fall back to engine reason).

generate_deadguard_summary: same flow as the other panes — payload check
-> cache check -> prompt -> provider -> empty guard -> grounding ->
cache.put. Provider errors caught (OllamaError).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Deadguard `_run_trigger` integration

**Files:**
- Modify: `src/interface/deadguard.py` (insert AI call in `_run_trigger`)
- Modify: `tests/test_deadguard.py` (extend with AI-path cases)

- [ ] **Step 1: Read `src/interface/deadguard.py`** carefully. Locate `_run_trigger`. The critical line is the `_notify(conn, "executed", f"Deadguard: captain {name}, bench optimized, {transfer_note}.")` call near line 140 — that's the insertion point.

Also locate how `name`, `caps`, `body`, `transfer_applied` are bound in the surrounding code so you can use them to build the outcome dict.

- [ ] **Step 2: Read `tests/test_deadguard.py`** to understand the existing test fixtures (likely `_seed_*` helpers + monkeypatch patterns for the executor + run_lineup).

- [ ] **Step 3: Append failing tests to `tests/test_deadguard.py`** (at the end). The exact fixture/monkeypatch pattern depends on what the existing tests use — adapt as needed. Approximate intent (adapt fixture names):

```python
def test_run_trigger_uses_ai_prose_in_notify_when_cache_populated(monkeypatch, db):
    """When ai.enabled=true and cached prose exists for this gw's outcome,
    _notify('executed', ...) sends the AI prose instead of the template."""
    from src.interface import deadguard
    # Seed the AI cache for the exact outcome the deadguard will produce
    from src.ai import reasoning, cache as ai_cache
    # ... (set up monkeypatches: ensure_session returns fake, run_lineup returns ok=True,
    #      _pick_flagged_transfer returns None to keep the outcome simple,
    #      caps["picks"] contains the expected captain/vice...)
    # Pre-populate cache: build outcome matching the run, hash, put prose
    # Run deadguard._run_trigger
    # Assert _notify was called with the AI prose body


def test_run_trigger_falls_back_to_template_when_ai_unavailable(monkeypatch, db):
    """When ai.enabled=true but provider fails, _notify uses the template summary."""
    # ... monkeypatch OllamaProvider.generate to raise OllamaError
    # Run deadguard._run_trigger
    # Assert _notify was called with the template body ("Deadguard: captain ..., bench optimized, ...")


def test_run_trigger_uses_template_when_ai_disabled(monkeypatch, db):
    """When ai.enabled=false, deadguard never calls the AI module."""
    # monkeypatch config.ai_enabled to return False
    # Run _run_trigger
    # Assert _notify called with template; assert AI provider was never instantiated
```

**Note:** the deadguard test file is large (~1000+ lines). Look for the existing `_run_trigger` happy-path test as a template — the new cases are variations of it with different monkeypatch setups. Use the same `db` fixture and other helpers that exist.

- [ ] **Step 4: Verify FAIL**: `.venv/bin/pytest tests/test_deadguard.py -v -k "run_trigger_uses_ai or run_trigger_falls_back or run_trigger_uses_template_when_ai_disabled"`

- [ ] **Step 5: Modify `src/interface/deadguard.py`**

Read the file. Locate the exact lines around `_notify(conn, "executed", ...)`. The change:

(a) Before the `_notify("executed", ...)` call, build the outcome + try AI generation:

```python
    # Build outcome + AI prose (best-effort; never blocks the notification)
    template_summary = f"Deadguard: captain {name}, bench optimized, {transfer_note}."
    summary = template_summary
    try:
        if config.ai_enabled(cfg):
            transfer_info = None
            if transfer_applied:
                # Look up out/in player names directly from the players table
                out_row = conn.execute(
                    "SELECT web_name FROM players WHERE id=?",
                    (body["element_out"],)).fetchone()
                in_row = conn.execute(
                    "SELECT web_name FROM players WHERE id=?",
                    (body["element_in"],)).fetchone()
                if out_row is not None and in_row is not None:
                    transfer_info = {"out_name": out_row["web_name"],
                                     "in_name": in_row["web_name"]}
            vice_name = (caps["picks"][1]["web_name"]
                         if len(caps["picks"]) > 1 else None)
            outcome = {
                "captain_name": name,
                "vice_name": vice_name,
                "bench_changed": True,   # run_lineup always re-ranks the bench (S-A.4 v1 assumption)
                "transfer": transfer_info,
                "gw": gw,
            }
            from src.ai import reasoning as ai_reasoning, provider as ai_provider
            ollama = ai_provider.OllamaProvider(
                host=config.ai_ollama_host(cfg),
                model=config.ai_ollama_model(cfg),
                timeout_seconds=config.ai_timeout_seconds(cfg),
            )
            if ai_reasoning.generate_deadguard_summary(
                    conn, gw=gw, outcome=outcome,
                    provider=ollama, model_id=config.ai_ollama_model(cfg)):
                prose, src = ai_reasoning.render_deadguard_summary(conn, gw, outcome)
                if src == "ai" and prose:
                    summary = prose
    except Exception:
        log.exception("ai.deadguard.generation_failed")   # never blocks the notification
        summary = template_summary

    _notify(conn, "executed", summary)
```

(b) The `cfg` variable needs to be in scope. Check the existing `_run_trigger`. If `cfg` isn't bound, derive it: `cfg = config.load_config()` at the top of the AI block, OR pass it through from the caller. Read the function carefully to see what's already available.

(c) Imports at top of the file: confirm `from src import config` is already there (it likely is). If not, add it.

**Important: the catch-all `try/except Exception` is intentional.** AI errors must NEVER block the deadguard notification. Even an internal logic bug in the AI module degrades silently to the template.

- [ ] **Step 6: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_deadguard.py -v
.venv/bin/pytest -q
```

Expected: existing deadguard tests still green + 3 new AI-path tests. Full suite 551 → 554.

If existing tests fail because they assert on the exact `_notify` body string, the failures will show what to adapt — but the AI block is OPT-IN via `config.ai_enabled(cfg)`. If `cfg` in the existing tests has no `ai:` block, `config.ai_enabled` falls through to its DEFAULT of `True`. **This means existing tests may break because the AI path now runs with no cache → no prose → falls through to template.** If a test fails because the AI path executed (e.g. `OllamaProvider.__init__` did something the test didn't expect), the cleanest fix is to monkeypatch `config.ai_enabled` to return `False` in those tests — but ONLY if the test isn't checking the new AI behavior. Read the failing test message before deciding.

- [ ] **Step 7: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/interface/deadguard.py tests/test_deadguard.py && git commit -m "$(cat <<'EOF'
feat(ai): deadguard _run_trigger generates summary prose (S-A.4 task 2)

After the lineup writes + transfer attempt succeed and before sending
the Telegram 'executed' notification, _run_trigger builds an outcome
dict (captain/vice/bench_changed/transfer/gw), calls the AI module to
generate prose, and uses the prose as the _notify body when it lands
cleanly. Any failure (Ollama down, ungrounded prose, internal logic
error) falls through to the existing template summary — the deadguard
notification path NEVER fails because of AI.

The AI call is gated by config.ai_enabled. When the cache already has
a row for this outcome hash, the generator short-circuits without
calling the provider — useful if deadguard re-fires the same outcome.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Dashboard banner reads cached deadguard prose

**Files:**
- Modify: `src/interface/queries.py` (`_status_banners` enhancement)
- Modify: `tests/test_api.py` (extend)

- [ ] **Step 1: Read `src/interface/queries.py`** — locate `_status_banners` (added in 2.5c-3). The DEADGUARD_EXECUTED branch is around line ~21-26 (was, in the earlier session).

- [ ] **Step 2: Append failing tests to `tests/test_api.py`**:

```python
def test_api_status_banner_uses_cached_deadguard_prose(client_conn):
    """When ai_reasoning_cache has a deadguard_summary row for the next gw,
    the DEADGUARD_EXECUTED banner uses that prose as its text."""
    from src.ai import cache as ai_cache
    client, conn = client_conn
    # Find the next GW + put it in DEADGUARD_EXECUTED state
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    if nxt is None or nxt["gw"] is None:
        return  # fixture has no upcoming gw; skip
    gw = nxt["gw"]
    conn.execute("UPDATE gameweeks SET state='DEADGUARD_EXECUTED' WHERE id=?", (gw,))
    conn.commit()
    ai_cache.put(conn, gw=gw, pane_type="deadguard_summary", rec_hash="abc123",
                 prose="Deadguard set Haaland captain and ran a transfer.",
                 model_id="qwen2.5:7b-instruct-q4_K_M")

    resp = client.get("/api/status")
    body = resp.json()
    deadguard_banners = [b for b in body.get("banners", [])
                          if "deadguard" in b.get("text", "").lower() or "Deadguard" in b.get("text", "")]
    assert any("Deadguard set Haaland captain and ran a transfer." in b["text"]
               for b in deadguard_banners)


def test_api_status_banner_uses_template_when_no_deadguard_ai_cache(client_conn):
    """When no AI prose is cached, the banner uses the existing template text."""
    client, conn = client_conn
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    if nxt is None or nxt["gw"] is None:
        return
    gw = nxt["gw"]
    conn.execute("UPDATE gameweeks SET state='DEADGUARD_EXECUTED' WHERE id=?", (gw,))
    conn.commit()

    resp = client.get("/api/status")
    body = resp.json()
    # The existing template text should be present
    assert any("Deadguard set your team this gameweek" in b.get("text", "")
               for b in body.get("banners", []))
```

- [ ] **Step 3: Verify FAIL**: `.venv/bin/pytest tests/test_api.py -v -k "deadguard"`

- [ ] **Step 4: Modify `src/interface/queries.py`**

Add a helper near the top of the file (next to `_next_gw`):

```python
def _read_deadguard_ai_prose(conn, gw):
    """Read the most recent cached deadguard_summary prose for this gw, or None."""
    row = conn.execute(
        "SELECT prose FROM ai_reasoning_cache "
        "WHERE gw=? AND pane_type='deadguard_summary' "
        "ORDER BY generated_at DESC LIMIT 1", (gw,)).fetchone()
    return row["prose"] if row is not None else None
```

Then modify the DEADGUARD_EXECUTED branch in `_status_banners`. Read the current code first. The existing block is approximately:

```python
    if state == "DEADGUARD_EXECUTED":
        banners.append({"level": "info",
                        "text": "Deadguard set your team this gameweek. "
                                "Undo a transfer via Telegram or `undo-transfer` "
                                "before the deadline."})
```

Change to:

```python
    if state == "DEADGUARD_EXECUTED":
        ai_prose = _read_deadguard_ai_prose(conn, nxt["id"])
        intro = ai_prose if ai_prose else "Deadguard set your team this gameweek."
        banners.append({"level": "info",
                        "text": f"{intro} Undo a transfer via Telegram or `undo-transfer` "
                                "before the deadline."})
```

Adapt to whatever the exact existing text/formatting is. Preserve the "Undo" hint — that's actionable info, not LLM-generated.

- [ ] **Step 5: Verify PASS + full suite**:
```
.venv/bin/pytest tests/test_api.py -v
.venv/bin/pytest -q
```

Expected: 2 new tests pass; full suite 554 → 556.

- [ ] **Step 6: Commit**:
```
cd /Users/shariski/Work/fpl-autopilot-phase3 && git add src/interface/queries.py tests/test_api.py && git commit -m "$(cat <<'EOF'
feat(ai): dashboard banner reads cached deadguard prose (S-A.4 task 3)

queries._status_banners checks ai_reasoning_cache for a deadguard_summary
row at the current gw. If present, the cached prose replaces the template
'Deadguard set your team this gameweek' intro. The 'undo via Telegram or
undo-transfer' hint stays — it's actionable info, not LLM-generated.

No frontend code change needed — the banner already takes arbitrary text.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Verification + final review + finishing + push

- [ ] **Step 1: Full pytest**:
```
.venv/bin/pytest -q
```

Expected: 556 (or whatever the running total is). All green.

- [ ] **Step 2: Vitest** (should be unchanged — S-A.4 has no frontend changes):
```
cd frontend && npm test
```

Expected: 62 (unchanged from S-A.3).

- [ ] **Step 3: Confirm no `decision-engine.md` change**:
```
git log --name-only main..HEAD | grep -i decision-engine && echo "FAIL" || echo "OK"
```

- [ ] **Step 4: Dispatch final code review** via the orchestrator (sonnet):

Brief:
> Review cumulative diff of `feat/phase3-sa4-deadguard` vs `main`. S-A.4 = LLM deadguard summary prose. Same B-rule profile as S-A.1/2/3. Focus:
> - **Critical safety check:** AI errors in `_run_trigger` MUST NOT break the deadguard notification. The catch-all `try/except Exception` is intentional. Confirm the fallback to template summary is rock-solid.
> - Smaller surface than S-A.2/A.3: no scheduler integration, no jobs.py change, no frontend change, no /api endpoint rewiring.
> - Banner text enhancement in `queries._status_banners` is the only `/api/status` change.
> - Inline SQL for player name lookup in `_run_trigger` (no new repository helper) — fine for a single call site.
> - The `_read_deadguard_ai_prose` helper in `queries.py` returns the MOST RECENT prose row for the gw (not hash-matched against the outcome). This is intentional — at most one deadguard summary per gw — but worth confirming the assumption.
>
> Report Critical/Important/Minor.

Apply blocking findings as focused fix commits; re-run tests.

- [ ] **Step 5: Invoke `superpowers:finishing-a-development-branch`**

Option 1 (merge to main locally). Likely a clean fast-forward — main hasn't moved during this slice. **DO push** when prompted (matches the user's pattern from S-A.2 + S-A.3).

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| `src/ai/prompts/deadguard.txt` + 3 exemplars (captain-only/+bench/+transfer) | T0 |
| Self-validating exemplars | T0 |
| `_build_deadguard_payload` (closed-shape outcome dict) | T1 |
| `_build_deadguard_prompt` | T1 |
| `render_deadguard_summary` (read path; classic = empty) | T1 |
| `generate_deadguard_summary` (write path with empty + grounding guards) | T1 |
| `_run_trigger` AI integration with catch-all try/except | T2 |
| Telegram body swap at `_notify` call site | T2 |
| Player name lookup (inline SQL, no new repo helper) | T2 |
| `_status_banners` reads cached deadguard prose | T3 |
| Banner template fallback preserved | T3 |
| Full suite green + B4/B7/B8 preserved | T4 |
| Final code review | T4 |
| Finishing a development branch + push | T4 |
| **No frontend code change** (banner takes arbitrary text) | N/A — verified via grep at finish time |

Every spec requirement maps to at least one task. ✓
