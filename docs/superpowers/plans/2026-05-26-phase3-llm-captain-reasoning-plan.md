# LLM Captain Reasoning Implementation Plan (Phase 3, S-A.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new AI sub-layer (`src/ai/`) that renders the captain ranker's existing `reason` string as LLM-generated prose grounded in the deterministic engine's existing outputs, pre-warmed in the scheduler, cached in SQLite, and rendered by the dashboard captain pane + Telegram notify body — with silent fallback to the existing template string when the LLM is down or hallucinates.

**Architecture:** A new sub-layer between Decision and Interface (`src/ai/`) with: `LLMProvider` Protocol + `OllamaProvider` (local Ollama, `requests.Session` pattern matching `src/interface/telegram.py`) + `StubProvider` (tests only — R3); per-pane structured prompts with few-shot exemplars; a new `ai_reasoning_cache` table keyed on `(gw, pane_type, recommendation_hash)`; a post-generation number-grounding check that rejects hallucinated prose; a scheduler-driven pre-warm job; an interface read path that returns `(prose, source)` where `source` ∈ `{'ai', 'classic'}`. **Describe-only — no `docs/decision-engine.md` change, B4 untouched.**

**Tech Stack:** Python 3.14 (`.venv` already built), SQLite, pytest, FastAPI (existing), SvelteKit + vitest (frontend), Ollama at `http://localhost:11434` with `qwen2.5:7b-instruct-q4_K_M`, `requests` (existing dep). All tests are fixtures-only via `StubProvider` — **no live Ollama call in tests, no live FPL call**.

**Source specs:**
- `docs/superpowers/specs/2026-05-26-phase3-scope-decomposition.md`
- `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md`
- `docs/superpowers/specs/2026-05-26-phase3-llm-captain-reasoning-design.md`

**B-rule stance (re-stated for executor's safety):**
- **B4:** untouched — the deterministic ranker still picks. **No edit to `docs/decision-engine.md`.**
- **B7:** prompt builder is the sole egress to the LLM. The AI module **never** imports `src/auth/`. No credentials, no cookies, no `/my-team` raw response in a prompt.
- **B8:** no executor change; no chip/transfer/lineup write path is touched.
- **R3:** LLM has no tools. All tests use `StubProvider`. No live Ollama process needs to be running during pytest/vitest.
- **B11:** decision-layer tests untouched. New AI tests use `StubProvider` only.
- **Git hygiene:** **NEVER `git add -A`**. Stage explicit paths only — the worktree contains `.claude/worktrees/` gitlinks that get swept up otherwise. Commit footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File structure (locked)

**New files:**
- `src/ai/__init__.py` — empty namespace package
- `src/ai/grounding.py` — `numbers_in`, `is_grounded`
- `src/ai/cache.py` — `recommendation_hash`, `get`, `put`
- `src/ai/provider.py` — `LLMProvider` Protocol, `OllamaProvider`, `StubProvider`
- `src/ai/prompts/__init__.py` — empty
- `src/ai/prompts/captain.txt` — prompt template
- `src/ai/prompts/captain_examples.json` — 2 few-shot exemplars (self-validating)
- `src/ai/reasoning.py` — `_build_captain_payload`, `_build_captain_prompt`, `render_captain_reasoning`, `generate_captain_prose`
- `src/ai/jobs.py` — `generate_ai_reasoning_job`
- `tests/test_ai_grounding.py`
- `tests/test_ai_cache.py`
- `tests/test_ai_provider.py`
- `tests/test_ai_prompts.py`
- `tests/test_ai_reasoning.py`
- `tests/test_ai_jobs.py`

**Modified files:**
- `src/data/schema.sql` — append `ai_reasoning_cache` table
- `src/config.py` — add 8 `ai_*` accessors
- `config.yaml` — add the `ai:` block with sensible defaults
- `src/scheduler.py` — call `generate_ai_reasoning_job` inside `refresh_and_recompute` after `xp.compute_and_store`, gated by `config.ai_enabled`, wrapped in try/except
- `src/interface/queries.py` — add `get_captain_picks(conn)` that wraps `captain.get_captain_picks` and enriches `picks[0]` with `(reasoning, reasoning_source)`; add tiny `get_captain_reasoning(conn, gw)` helper for the Telegram path
- `src/interface/api.py` — `/api/captain` calls `queries.get_captain_picks` (was direct `captain_engine` call)
- `src/interface/telegram.py` — `notify_plan` enriches the captain entry's summary with cached AI prose when present
- `tests/test_models_schema.py` — assert new table exists post-init_db
- `tests/test_config.py` — assert new `ai_*` accessors return documented defaults
- `tests/test_scheduler.py` — assert AI job is invoked / skipped / exception-safe per `ai.enabled`
- `tests/test_api.py` — `/api/captain` returns enriched payload with `reasoning_source`
- `tests/test_telegram.py` — `notify_plan` swaps captain summary when AI cache is populated
- `frontend/src/lib/types.ts` — `CaptainPick` gains `reasoning?: string; reasoning_source?: 'ai' | 'classic'`
- `frontend/src/lib/components/CaptainPicks.svelte` — render `reasoning` when present + show "AI"/"classic" badge
- `frontend/src/lib/components/CaptainPicks.svelte.test.ts` — vitest cases for badge + reasoning render
- `frontend/src/lib/mocks/full.ts` — extend the captain mock with one pick carrying `reasoning` + `reasoning_source`
- `docs/architecture.md` — insert the new sub-layer diagram per the architecture spec
- `docs/onboarding.md` — add Ollama prerequisite + `ai.enabled` opt-out note

**Note for executor:** before any task that modifies an existing file, **read it first** to confirm the exact line numbers and surrounding context. Line numbers cited below are from this session's snapshot and may have drifted slightly by the time you execute.

---

## Task 0: Schema + config (the table and the knobs every other task depends on)

**Files:**
- Modify: `src/data/schema.sql` (append at end)
- Modify: `src/config.py` (append at end)
- Modify: `config.yaml` (append at end)
- Test: `tests/test_models_schema.py` (extend)
- Test: `tests/test_config.py` (extend)

- [ ] **Step 1: Write the failing tests**

In `tests/test_models_schema.py`, add:

```python
def test_ai_reasoning_cache_table_exists(tmp_path):
    from src.data.db import connect, init_db
    conn = connect(str(tmp_path / "t.db"))
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ai_reasoning_cache)")}
    assert cols == {"gw", "pane_type", "recommendation_hash", "prose", "model_id", "generated_at"}
```

In `tests/test_config.py`, add:

```python
def test_ai_defaults_when_missing():
    from src import config
    cfg = {}
    assert config.ai_enabled(cfg) is True
    assert config.ai_provider(cfg) == "ollama"
    assert config.ai_ollama_host(cfg) == "http://localhost:11434"
    assert config.ai_ollama_model(cfg) == "qwen2.5:7b-instruct-q4_K_M"
    assert config.ai_timeout_seconds(cfg) == 15
    assert config.ai_consecutive_failure_backoff(cfg) == 3
    assert config.ai_temperature(cfg) == 0.2
    assert config.ai_max_tokens_per_pane(cfg) == 200


def test_ai_overrides_from_yaml():
    from src import config
    cfg = {"ai": {"enabled": False, "ollama": {"model": "llama3.1:8b"}, "timeout_seconds": 30}}
    assert config.ai_enabled(cfg) is False
    assert config.ai_ollama_model(cfg) == "llama3.1:8b"
    assert config.ai_timeout_seconds(cfg) == 30
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_models_schema.py::test_ai_reasoning_cache_table_exists tests/test_config.py::test_ai_defaults_when_missing tests/test_config.py::test_ai_overrides_from_yaml -v`

Expected: FAIL — table doesn't exist; accessor functions don't exist.

- [ ] **Step 3: Add the table to `src/data/schema.sql`**

Append at the end of `src/data/schema.sql`:

```sql

CREATE TABLE IF NOT EXISTS ai_reasoning_cache (
  gw INTEGER NOT NULL,
  pane_type TEXT NOT NULL,
  recommendation_hash TEXT NOT NULL,
  prose TEXT NOT NULL,
  model_id TEXT NOT NULL,
  generated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (gw, pane_type, recommendation_hash)
);

CREATE INDEX IF NOT EXISTS idx_ai_reasoning_cache_lookup
  ON ai_reasoning_cache (gw, pane_type, generated_at DESC);
```

- [ ] **Step 4: Add the config accessors to `src/config.py`**

Append at the end of `src/config.py`:

```python


def _ai(cfg):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("ai", {})


def _ai_ollama(cfg):
    return _ai(cfg).get("ollama", {})


def ai_enabled(cfg=None):
    return bool(_ai(cfg).get("enabled", True))


def ai_provider(cfg=None):
    return _ai(cfg).get("provider", "ollama")


def ai_ollama_host(cfg=None):
    return _ai_ollama(cfg).get("host", "http://localhost:11434")


def ai_ollama_model(cfg=None):
    return _ai_ollama(cfg).get("model", "qwen2.5:7b-instruct-q4_K_M")


def ai_timeout_seconds(cfg=None):
    return _ai(cfg).get("timeout_seconds", 15)


def ai_consecutive_failure_backoff(cfg=None):
    return _ai(cfg).get("consecutive_failure_backoff", 3)


def ai_temperature(cfg=None):
    return _ai(cfg).get("temperature", 0.2)


def ai_max_tokens_per_pane(cfg=None):
    return _ai(cfg).get("max_tokens_per_pane", 200)
```

- [ ] **Step 5: Add the `ai:` block to `config.yaml`**

Append at the end of `config.yaml`:

```yaml
ai:
  enabled: true
  provider: ollama
  ollama:
    host: "http://localhost:11434"
    model: "qwen2.5:7b-instruct-q4_K_M"
  timeout_seconds: 15
  consecutive_failure_backoff: 3
  temperature: 0.2
  max_tokens_per_pane: 200
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models_schema.py::test_ai_reasoning_cache_table_exists tests/test_config.py::test_ai_defaults_when_missing tests/test_config.py::test_ai_overrides_from_yaml -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/data/schema.sql src/config.py config.yaml \
        tests/test_models_schema.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat(ai): add ai_reasoning_cache table + config knobs (Phase 3 S-A.1 task 0)

Schema gains the new table; config gains 8 accessors with safe defaults
(ai.enabled=true, ollama at localhost:11434 with qwen2.5:7b-instruct-q4_K_M).
Foundation for Phase 3 S-A.1 — no logic change to existing code yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: `src/ai/grounding.py` — the number-grounding hallucination guard

**Files:**
- Create: `src/ai/__init__.py` (empty)
- Create: `src/ai/grounding.py`
- Create: `tests/test_ai_grounding.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_grounding.py`:

```python
from src.ai import grounding


def test_numbers_in_extracts_ints_and_decimals():
    assert grounding.numbers_in("xP 7.2 at home, gap 1.8, confidence 82") == {"7.2", "1.8", "82"}


def test_numbers_in_empty_string():
    assert grounding.numbers_in("") == set()


def test_numbers_in_no_numbers():
    assert grounding.numbers_in("just words here") == set()


def test_is_grounded_when_prose_numbers_subset_of_input():
    ok, ungrounded = grounding.is_grounded(
        prose="Haaland at 7.2 xP",
        input_payload_text='{"xp": 7.2, "confidence": 82}',
    )
    assert ok is True
    assert ungrounded == set()


def test_is_grounded_false_with_invented_number():
    ok, ungrounded = grounding.is_grounded(
        prose="Haaland at 7.2 xP, confidence 99",     # 99 not in input
        input_payload_text='{"xp": 7.2, "confidence": 82}',
    )
    assert ok is False
    assert ungrounded == {"99"}


def test_is_grounded_with_no_numbers_in_prose():
    ok, ungrounded = grounding.is_grounded(
        prose="Haaland is the captain this week",
        input_payload_text='{"xp": 7.2}',
    )
    assert ok is True
    assert ungrounded == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_grounding.py -v`

Expected: FAIL — `src.ai.grounding` does not exist.

- [ ] **Step 3: Write the minimal implementation**

`src/ai/__init__.py`: empty file.

`src/ai/grounding.py`:

```python
"""Post-generation number-grounding check for LLM prose.

Every numeric token in the LLM's prose must appear verbatim in the input payload
text. Failures are treated as hallucinations — the prose is not cached and the
scheduler logs the offence. This is the practical guard for a small open-weight
model under a "do not invent numbers" instruction.
"""
import re

NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Return the set of numeric tokens (ints + decimals) appearing in text."""
    return set(NUMERIC_RE.findall(text))


def is_grounded(prose: str, input_payload_text: str) -> tuple[bool, set[str]]:
    """Every number in prose must appear in input_payload_text.

    Returns (ok, set_of_ungrounded_numbers). ok=True iff ungrounded is empty.
    """
    ungrounded = numbers_in(prose) - numbers_in(input_payload_text)
    return (not ungrounded, ungrounded)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_grounding.py -v`

Expected: PASS — all 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ai/__init__.py src/ai/grounding.py tests/test_ai_grounding.py
git commit -m "$(cat <<'EOF'
feat(ai): grounding check — numbers_in / is_grounded (S-A.1 task 1)

Lexical guard against LLM number hallucination: every numeric token in
generated prose must appear verbatim in the input payload. Ungrounded prose
is reported with the offending number set so the caller can log + skip cache.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `src/ai/cache.py` — SQLite-backed cache for prose

**Files:**
- Create: `src/ai/cache.py`
- Create: `tests/test_ai_cache.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_cache.py`:

```python
from src.data.db import connect, init_db
from src.ai import cache


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def test_recommendation_hash_stable_across_key_ordering():
    a = {"x": 1, "y": [2, 3], "z": "hi"}
    b = {"z": "hi", "y": [2, 3], "x": 1}
    assert cache.recommendation_hash(a) == cache.recommendation_hash(b)


def test_recommendation_hash_changes_when_payload_changes():
    a = {"x": 1}
    b = {"x": 2}
    assert cache.recommendation_hash(a) != cache.recommendation_hash(b)


def test_recommendation_hash_is_short_hex():
    h = cache.recommendation_hash({"x": 1})
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


def test_get_returns_none_on_miss():
    conn = _db()
    assert cache.get(conn, gw=38, pane_type="captain", rec_hash="abc") is None


def test_put_then_get_round_trips():
    conn = _db()
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="Haaland captain.", model_id="qwen2.5:7b-instruct-q4_K_M")
    hit = cache.get(conn, gw=38, pane_type="captain", rec_hash="abc")
    assert hit is not None
    assert hit["prose"] == "Haaland captain."
    assert hit["model_id"] == "qwen2.5:7b-instruct-q4_K_M"
    assert hit["generated_at"] is not None


def test_put_is_idempotent_on_same_key():
    conn = _db()
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="v1", model_id="m")
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="v2", model_id="m")
    hit = cache.get(conn, gw=38, pane_type="captain", rec_hash="abc")
    assert hit["prose"] == "v2"


def test_different_panes_dont_collide():
    conn = _db()
    cache.put(conn, gw=38, pane_type="captain", rec_hash="abc",
              prose="cap", model_id="m")
    cache.put(conn, gw=38, pane_type="transfer", rec_hash="abc",
              prose="trn", model_id="m")
    assert cache.get(conn, gw=38, pane_type="captain", rec_hash="abc")["prose"] == "cap"
    assert cache.get(conn, gw=38, pane_type="transfer", rec_hash="abc")["prose"] == "trn"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_cache.py -v`

Expected: FAIL — `src.ai.cache` does not exist.

- [ ] **Step 3: Write the minimal implementation**

`src/ai/cache.py`:

```python
"""SQLite-backed cache for LLM-generated pane prose.

Cache key = (gw, pane_type, recommendation_hash). The hash is computed over the
canonicalised payload (sorted keys, compact separators) so identical inputs
produce identical hashes. When the deterministic engine's output changes, the
hash changes — automatic invalidation, no manual cache-bust logic.
"""
import hashlib
import json
from datetime import datetime, timezone


def recommendation_hash(payload: dict) -> str:
    """Stable 32-char hex of sorted-keys JSON of payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def get(conn, gw: int, pane_type: str, rec_hash: str) -> dict | None:
    """Return {'prose', 'model_id', 'generated_at'} or None on miss."""
    row = conn.execute(
        "SELECT prose, model_id, generated_at FROM ai_reasoning_cache "
        "WHERE gw=? AND pane_type=? AND recommendation_hash=?",
        (gw, pane_type, rec_hash),
    ).fetchone()
    if row is None:
        return None
    return {"prose": row["prose"], "model_id": row["model_id"],
            "generated_at": row["generated_at"]}


def put(conn, gw: int, pane_type: str, rec_hash: str, prose: str, model_id: str) -> None:
    """Upsert one cache row."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO ai_reasoning_cache "
        "(gw, pane_type, recommendation_hash, prose, model_id, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (gw, pane_type, rec_hash, prose, model_id, now),
    )
    conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_cache.py -v`

Expected: PASS — all 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ai/cache.py tests/test_ai_cache.py
git commit -m "$(cat <<'EOF'
feat(ai): cache helpers — recommendation_hash / get / put (S-A.1 task 2)

SQLite cache keyed on (gw, pane_type, recommendation_hash). Hash is sha256 of
canonical-JSON of the payload — order-independent, deterministic. Writes are
upserts; reads return None on miss. Automatic invalidation: when the
deterministic recommendation changes, the hash changes, fresh row generated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `src/ai/provider.py` — Protocol + StubProvider

**Files:**
- Create: `src/ai/provider.py`
- Create: `tests/test_ai_provider.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_provider.py`:

```python
from src.ai import provider as prv


def test_stub_provider_returns_fixed_response():
    p = prv.StubProvider("hello world")
    assert p.generate("anything", max_tokens=10, temperature=0.0) == "hello world"


def test_stub_provider_default_response():
    p = prv.StubProvider()
    out = p.generate("prompt")
    assert isinstance(out, str)
    assert out  # non-empty default
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_provider.py -v`

Expected: FAIL — `src.ai.provider` does not exist.

- [ ] **Step 3: Write the minimal implementation**

`src/ai/provider.py`:

```python
"""LLM provider interface + concrete providers.

The Protocol is intentionally tiny — one method, `generate`. Swapping providers
(e.g. Claude API later) is implementing this Protocol in a new class. Tests
inject `StubProvider`; production injects `OllamaProvider`.
"""
from typing import Protocol


class LLMProvider(Protocol):
    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        ...


class StubProvider:
    """Test/fixture provider — returns a canned response. Used everywhere a test
    needs an LLM (R3 — no live calls in tests)."""

    def __init__(self, fixed_response: str = "<stub prose>"):
        self.fixed_response = fixed_response

    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        return self.fixed_response
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_provider.py -v`

Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ai/provider.py tests/test_ai_provider.py
git commit -m "$(cat <<'EOF'
feat(ai): LLMProvider Protocol + StubProvider (S-A.1 task 3)

One-method Protocol. StubProvider returns canned response — used by all tests
(R3, no live LLM calls in tests). OllamaProvider lands in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `OllamaProvider` — local Ollama HTTP client

**Files:**
- Modify: `src/ai/provider.py` (append `OllamaProvider`)
- Modify: `tests/test_ai_provider.py` (extend with `OllamaProvider` cases using a fake session)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ai_provider.py`:

```python
class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._body = json_body

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def test_ollama_provider_posts_to_generate_endpoint():
    session = _FakeSession(_FakeResponse(200, {"response": "  Haaland.  "}))
    p = prv.OllamaProvider("http://localhost:11434", "qwen2.5:7b-instruct-q4_K_M",
                           timeout_seconds=15, session=session)
    out = p.generate("hello", max_tokens=128, temperature=0.3)
    assert out == "Haaland."          # stripped
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "http://localhost:11434/api/generate"
    assert call["timeout"] == 15
    assert call["json"]["model"] == "qwen2.5:7b-instruct-q4_K_M"
    assert call["json"]["prompt"] == "hello"
    assert call["json"]["stream"] is False
    assert call["json"]["options"]["num_predict"] == 128
    assert call["json"]["options"]["temperature"] == 0.3


def test_ollama_provider_raises_on_non_200():
    import pytest
    session = _FakeSession(_FakeResponse(500, {}))
    p = prv.OllamaProvider("http://localhost:11434", "m", timeout_seconds=15, session=session)
    with pytest.raises(prv.OllamaError):
        p.generate("hello")


def test_ollama_provider_raises_on_network_error():
    import pytest
    import requests
    session = _FakeSession(raise_exc=requests.ConnectionError("connection refused"))
    p = prv.OllamaProvider("http://localhost:11434", "m", timeout_seconds=15, session=session)
    with pytest.raises(prv.OllamaError):
        p.generate("hello")


def test_ollama_provider_raises_on_malformed_json():
    import pytest

    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    session = _FakeSession(_BadJson(200, None))
    p = prv.OllamaProvider("http://localhost:11434", "m", timeout_seconds=15, session=session)
    with pytest.raises(prv.OllamaError):
        p.generate("hello")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_provider.py -v`

Expected: FAIL — `OllamaProvider` / `OllamaError` do not exist.

- [ ] **Step 3: Extend `src/ai/provider.py`**

Append to `src/ai/provider.py`:

```python


import requests


class OllamaError(RuntimeError):
    """Raised when the Ollama call fails (network, non-200, malformed JSON)."""


class OllamaProvider:
    """Minimal HTTP client against Ollama's /api/generate endpoint.

    Single-shot completion, no streaming, no chat history. Session injection
    matches src/interface/telegram.py — tests pass a fake session, production
    gets a real requests.Session.
    """

    def __init__(self, host: str, model: str, timeout_seconds: float,
                 session: requests.Session | None = None):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout_seconds)
        except requests.RequestException as e:
            raise OllamaError(f"ollama request failed: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise OllamaError(f"ollama returned {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as e:
            raise OllamaError("ollama returned malformed json") from e
        text = body.get("response", "") if isinstance(body, dict) else ""
        return text.strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_provider.py -v`

Expected: PASS — 6 tests total (2 stub + 4 ollama).

- [ ] **Step 5: Commit**

```bash
git add src/ai/provider.py tests/test_ai_provider.py
git commit -m "$(cat <<'EOF'
feat(ai): OllamaProvider — local HTTP client (S-A.1 task 4)

Uses requests.Session injection (matches telegram.py pattern — no new dep).
Wraps network/HTTP/JSON failures in OllamaError so callers can branch
cleanly. Single-shot /api/generate; no streaming. Strips trailing whitespace.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Captain prompt + few-shot exemplars (self-validating)

**Files:**
- Create: `src/ai/prompts/__init__.py` (empty)
- Create: `src/ai/prompts/captain.txt`
- Create: `src/ai/prompts/captain_examples.json`
- Create: `tests/test_ai_prompts.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_prompts.py`:

```python
import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_captain_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "captain.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_captain_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "captain_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 2
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}
        assert isinstance(ex["input"], dict)
        assert isinstance(ex["output"], str)


def test_every_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "captain_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"example {i} prose contains ungrounded numbers: {ungrounded}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_prompts.py -v`

Expected: FAIL — files don't exist.

- [ ] **Step 3: Create `src/ai/prompts/__init__.py`** (empty file).

- [ ] **Step 4: Create `src/ai/prompts/captain.txt`**:

```
You are writing one short paragraph that explains an FPL captain pick to the team manager.

Constraints:
- 2 to 3 sentences. Plain English. No emojis. No exclamation marks.
- You may ONLY use numbers that appear in INPUT below. Do not invent any other number.
- Mention the captain's name, the xP value, the fixture, and either the vice or the gap.
- Do not editorialise beyond the inputs. Do not predict the future. Do not name other players.
- Output the paragraph only. No preamble, no closing remarks.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

- [ ] **Step 5: Create `src/ai/prompts/captain_examples.json`**:

```json
[
  {
    "input": {
      "captain": {"web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)"},
      "vice": {"web_name": "Salah", "xp": 5.4},
      "alternative_gap": 1.8,
      "confidence": 82
    },
    "output": "Haaland is the captain this week at 7.2 xP, MCI v BRE (H). He clears the vice Salah by 1.8 xP, and confidence is 82, so this is a clean call."
  },
  {
    "input": {
      "captain": {"web_name": "Saka", "xp": 5.6, "fixture": "ARS v LIV (A)"},
      "vice": {"web_name": "Palmer", "xp": 5.3},
      "alternative_gap": 0.3,
      "confidence": 68
    },
    "output": "Saka leads at 5.6 xP, ARS v LIV (A), but only by 0.3 over the vice Palmer. Confidence is 68, so this is a close call you may want to override."
  }
]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_prompts.py -v`

Expected: PASS — 3 tests.

If a grounding assertion fails on an exemplar, the exemplar prose mentions a number that isn't in its input. Either rewrite the prose to use only input numbers, or add the number to the input. The grounding test is intentional — **the exemplars are themselves golden tests.**

- [ ] **Step 7: Commit**

```bash
git add src/ai/prompts/__init__.py src/ai/prompts/captain.txt \
        src/ai/prompts/captain_examples.json tests/test_ai_prompts.py
git commit -m "$(cat <<'EOF'
feat(ai): captain prompt template + few-shot exemplars (S-A.1 task 5)

Per-pane structured prompt with 2 hand-curated few-shot examples. The
example file is self-validating: every example's output passes is_grounded
against its input — editing an exemplar in a way that breaks grounding
fails the test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `_build_captain_payload` + `_build_captain_prompt`

**Files:**
- Create: `src/ai/reasoning.py`
- Create: `tests/test_ai_reasoning.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_reasoning.py`:

```python
import json

from src.ai import reasoning


CAPTAIN_DECISION_FIXTURE = {
    "picks": [
        {"player_id": 10, "web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)",
         "reason": "Highest xP (7.2) MCI v BRE (H). Next best Salah 5.4 — gap 1.8."},
        {"player_id": 6, "web_name": "Salah", "xp": 5.4, "fixture": "LIV v EVE (A)",
         "reason": "xP 5.4 LIV v EVE (A)."},
        {"player_id": 7, "web_name": "Saka", "xp": 5.0, "fixture": "ARS v LIV (A)",
         "reason": "xP 5.0 ARS v LIV (A)."},
    ],
    "vice_player_id": 6,
    "confidence": 82,
}


def test_build_captain_payload_shape():
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    assert payload == {
        "captain": {"web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)"},
        "vice": {"web_name": "Salah", "xp": 5.4},
        "alternative_gap": 1.8,
        "confidence": 82,
    }


def test_build_captain_payload_with_single_pick():
    decision = {
        "picks": [{"player_id": 10, "web_name": "Haaland", "xp": 7.2,
                   "fixture": "MCI v BRE (H)", "reason": "..."}],
        "vice_player_id": None,
        "confidence": 60,
    }
    payload = reasoning._build_captain_payload(decision)
    assert payload["vice"] is None
    assert payload["alternative_gap"] is None
    assert payload["captain"]["web_name"] == "Haaland"


def test_build_captain_payload_returns_none_on_empty_picks():
    decision = {"picks": [], "vice_player_id": None, "confidence": None}
    assert reasoning._build_captain_payload(decision) is None


def test_build_captain_prompt_includes_payload_and_examples():
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    prompt = reasoning._build_captain_prompt(payload)
    # examples block populated
    assert "Haaland" in prompt
    assert "Saka" in prompt        # second exemplar
    # payload rendered as JSON in the prompt
    assert json.dumps(payload, sort_keys=True, indent=2) in prompt or \
           '"web_name": "Haaland"' in prompt
    # template constraints preserved
    assert "Do not invent" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_reasoning.py -v`

Expected: FAIL — `src.ai.reasoning` does not exist.

- [ ] **Step 3: Write the minimal implementation**

`src/ai/reasoning.py`:

```python
"""Per-pane LLM reasoning: payload + prompt builders, render (read) and
generate (write) functions. The AI sub-layer's only public surface for
consumers in src/interface and src/scheduler.
"""
import json
import logging
from pathlib import Path

from src.ai import cache, grounding

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _build_captain_payload(captain_decision: dict) -> dict | None:
    """Narrow, closed-shape payload built from get_captain_picks() output.

    Returns None if no picks (the LLM has nothing to render).
    """
    picks = captain_decision.get("picks", [])
    if not picks:
        return None
    top = picks[0]
    vice = picks[1] if len(picks) > 1 else None
    gap = round(top["xp"] - vice["xp"], 1) if vice is not None else None
    return {
        "captain": {
            "web_name": top["web_name"],
            "xp": top["xp"],
            "fixture": top["fixture"],
        },
        "vice": ({"web_name": vice["web_name"], "xp": vice["xp"]}
                 if vice is not None else None),
        "alternative_gap": gap,
        "confidence": captain_decision.get("confidence"),
    }


def _build_captain_prompt(payload: dict) -> str:
    """Render captain.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "captain.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "captain_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_reasoning.py -v`

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ai/reasoning.py tests/test_ai_reasoning.py
git commit -m "$(cat <<'EOF'
feat(ai): captain payload + prompt builders (S-A.1 task 6)

_build_captain_payload distills get_captain_picks() output into a closed-shape
dict (captain, vice, alternative_gap, confidence). B7: no credentials/cookies
have a path into this payload — only typed Decision-layer outputs flow in.
_build_captain_prompt renders the template with examples + payload populated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `render_captain_reasoning` (read path)

**Files:**
- Modify: `src/ai/reasoning.py` (append `render_captain_reasoning`)
- Modify: `tests/test_ai_reasoning.py` (append render tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ai_reasoning.py`:

```python
from src.data.db import connect, init_db
from src.ai import cache as ai_cache


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def test_render_captain_reasoning_returns_classic_on_cache_miss():
    conn = _db()
    prose, source = reasoning.render_captain_reasoning(conn, gw=38,
                                                       captain_decision=CAPTAIN_DECISION_FIXTURE)
    assert source == "classic"
    # falls back to the existing template reason from picks[0]
    assert prose == CAPTAIN_DECISION_FIXTURE["picks"][0]["reason"]


def test_render_captain_reasoning_returns_ai_on_cache_hit():
    conn = _db()
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="captain", rec_hash=rec_hash,
                 prose="LLM prose here.", model_id="qwen2.5:7b-instruct-q4_K_M")
    prose, source = reasoning.render_captain_reasoning(conn, gw=38,
                                                       captain_decision=CAPTAIN_DECISION_FIXTURE)
    assert source == "ai"
    assert prose == "LLM prose here."


def test_render_captain_reasoning_returns_classic_on_empty_picks():
    conn = _db()
    decision = {"picks": [], "vice_player_id": None, "confidence": None}
    prose, source = reasoning.render_captain_reasoning(conn, gw=38, captain_decision=decision)
    assert source == "classic"
    assert prose == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_reasoning.py -v`

Expected: FAIL — `render_captain_reasoning` not defined.

- [ ] **Step 3: Append `render_captain_reasoning` to `src/ai/reasoning.py`**

```python


def render_captain_reasoning(conn, gw: int, captain_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source) where source ∈ {'ai', 'classic'}.

    Never calls a provider — only reads from the cache. Falls back to the
    deterministic engine's existing `reason` string when nothing is cached.
    """
    payload = _build_captain_payload(captain_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "captain", rec_hash)
    if hit is not None:
        return (hit["prose"], "ai")
    return (captain_decision["picks"][0]["reason"], "classic")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_reasoning.py -v`

Expected: PASS — 7 tests total.

- [ ] **Step 5: Commit**

```bash
git add src/ai/reasoning.py tests/test_ai_reasoning.py
git commit -m "$(cat <<'EOF'
feat(ai): render_captain_reasoning — read path (S-A.1 task 7)

Cache-first read: hit returns ('ai', cached_prose); miss returns ('classic',
existing engine reason). Never calls the provider — pure cache lookup.
Empty-picks edge case returns ('classic', '') so the dashboard never breaks
on a fresh DB.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `generate_captain_prose` (write path with grounding check)

**Files:**
- Modify: `src/ai/reasoning.py` (append `generate_captain_prose`)
- Modify: `tests/test_ai_reasoning.py` (append generate tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ai_reasoning.py`:

```python
from src.ai import provider as prv


def test_generate_captain_prose_caches_grounded_prose():
    conn = _db()
    # grounded prose: every number in prose appears in payload JSON
    stub = prv.StubProvider("Haaland captain at 7.2 xP, gap 1.8, confidence 82.")
    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    # row landed in cache
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    hit = ai_cache.get(conn, gw=38, pane_type="captain", rec_hash=rec_hash)
    assert hit is not None
    assert hit["prose"] == "Haaland captain at 7.2 xP, gap 1.8, confidence 82."


def test_generate_captain_prose_rejects_ungrounded_prose():
    conn = _db()
    stub = prv.StubProvider("Haaland captain at 7.2 xP — confidence 99.")  # 99 not in payload
    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False
    # nothing cached
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="captain", rec_hash=rec_hash) is None


def test_generate_captain_prose_skips_provider_on_cache_hit():
    conn = _db()
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="captain", rec_hash=rec_hash,
                 prose="already cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("provider must not be called on cache hit")

    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_captain_prose_skips_on_empty_picks():
    conn = _db()
    decision = {"picks": [], "vice_player_id": None, "confidence": None}

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("provider must not be called with empty picks")

    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=decision,
        provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_captain_prose_swallows_provider_errors():
    """Provider exceptions don't bubble — they're logged + the row isn't cached."""
    conn = _db()

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("ollama is down")

    ok = reasoning.generate_captain_prose(
        conn, gw=38, captain_decision=CAPTAIN_DECISION_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_reasoning.py -v`

Expected: FAIL — `generate_captain_prose` not defined.

- [ ] **Step 3: Append `generate_captain_prose` to `src/ai/reasoning.py`**

```python


def generate_captain_prose(conn, gw: int, captain_decision: dict, *,
                           provider, model_id: str,
                           max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).

    Called by the scheduler. Provider errors are caught and logged — never
    bubble. Ungrounded prose is not cached; the grounding violation is logged.
    """
    payload = _build_captain_payload(captain_decision)
    if payload is None:
        logger.info("ai.captain.skipped_empty_picks", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "captain", rec_hash) is not None:
        return True
    prompt = _build_captain_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except Exception:
        logger.exception("ai.captain.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.captain.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "captain", rec_hash, prose, model_id)
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_reasoning.py -v`

Expected: PASS — 12 tests total in this file now.

- [ ] **Step 5: Commit**

```bash
git add src/ai/reasoning.py tests/test_ai_reasoning.py
git commit -m "$(cat <<'EOF'
feat(ai): generate_captain_prose — write path with grounding (S-A.1 task 8)

Build payload → check cache → build prompt → call provider → ground-check →
cache on success. Provider exceptions are caught + logged (never bubble);
ungrounded prose is logged with the specific offending numbers + not cached.
Cache hit short-circuits the provider call.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `generate_ai_reasoning_job` — the pane-walker

**Files:**
- Create: `src/ai/jobs.py`
- Create: `tests/test_ai_jobs.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_jobs.py`:

```python
import logging
from src.data.db import connect, init_db
from src.ai import jobs, provider as prv, cache as ai_cache, reasoning


def _db():
    conn = connect(":memory:")
    init_db(conn)
    # need a "next_gw" — insert a not-finished gameweek
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-05-20T11:00:00Z', 0, 1, 0, "
                 "'PENDING')")
    conn.commit()
    return conn


CAPTAIN_DECISION = {
    "picks": [
        {"player_id": 10, "web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)",
         "reason": "Highest xP (7.2) MCI v BRE (H). Next best Salah 5.4 — gap 1.8."},
        {"player_id": 6, "web_name": "Salah", "xp": 5.4, "fixture": "LIV v EVE (A)",
         "reason": "xP 5.4 LIV v EVE (A)."},
    ],
    "vice_player_id": 6,
    "confidence": 82,
}


def test_generate_ai_reasoning_job_caches_captain_prose():
    conn = _db()
    stub = prv.StubProvider("Haaland at 7.2 xP, gap 1.8, confidence 82.")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "ok"}
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="captain", rec_hash=rec_hash) is not None


def test_generate_ai_reasoning_job_reports_cached_on_second_run():
    conn = _db()
    stub = prv.StubProvider("Haaland at 7.2 xP, gap 1.8, confidence 82.")
    jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("must not be called — already cached")

    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=_BoomProvider(), model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "ok"}


def test_generate_ai_reasoning_job_reports_failed_on_grounding_violation():
    conn = _db()
    stub = prv.StubProvider("Haaland xP 9.9 confidence 99.")        # ungrounded
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "failed"}


def test_generate_ai_reasoning_job_returns_skipped_when_no_next_gw():
    conn = connect(":memory:")
    init_db(conn)
    # no gameweeks at all
    stub = prv.StubProvider("anything")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain"], provider=stub, model_id="m",
        captain_decision_fn=lambda c: CAPTAIN_DECISION)
    assert result == {"captain": "skipped"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ai_jobs.py -v`

Expected: FAIL — `src.ai.jobs` does not exist.

- [ ] **Step 3: Write the minimal implementation**

`src/ai/jobs.py`:

```python
"""Scheduler-facing entry point. Walks the requested pane types and generates
prose for the next gameweek's recommendation, caching on success.

In Phase 3 S-A.1, only 'captain' is implemented. S-A.2/3/4 add 'transfer',
'chip', 'deadguard_summary' by adding branches here.
"""
import logging
from typing import Callable

from src.ai import reasoning

logger = logging.getLogger(__name__)


def _next_gw(conn) -> int | None:
    row = conn.execute(
        "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def _default_captain_decision_fn(conn):
    from src.decisions import captain
    return captain.get_captain_picks(conn)


def generate_ai_reasoning_job(
    conn,
    *,
    panes: list[str],
    provider,
    model_id: str,
    captain_decision_fn: Callable | None = None,
) -> dict:
    """Walk `panes`, generate prose for each, cache on success.

    Returns {pane_type: status_str} where status_str ∈
    {'ok', 'failed', 'skipped'}. Used by the scheduler for log diagnostics.
    """
    gw = _next_gw(conn)
    if gw is None:
        return {p: "skipped" for p in panes}
    result: dict[str, str] = {}
    captain_fn = captain_decision_fn or _default_captain_decision_fn
    for pane in panes:
        if pane == "captain":
            decision = captain_fn(conn)
            ok = reasoning.generate_captain_prose(
                conn, gw=gw, captain_decision=decision,
                provider=provider, model_id=model_id)
            result[pane] = "ok" if ok else "failed"
        else:
            logger.warning("ai.jobs.unknown_pane", extra={"pane": pane})
            result[pane] = "skipped"
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ai_jobs.py -v`

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ai/jobs.py tests/test_ai_jobs.py
git commit -m "$(cat <<'EOF'
feat(ai): generate_ai_reasoning_job (S-A.1 task 9)

Walks pane list for the next gameweek, generates+caches prose per pane.
S-A.1 implements 'captain'; S-A.2/3/4 add 'transfer', 'chip',
'deadguard_summary'. Reports {pane: 'ok'|'failed'|'skipped'} for the
scheduler's log.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Scheduler integration

**Files:**
- Modify: `src/scheduler.py` (`refresh_and_recompute`)
- Modify: `tests/test_scheduler.py` (add 3 cases)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scheduler.py` (this file is large — append the cases at the end):

```python
def test_refresh_and_recompute_invokes_ai_job_when_enabled(monkeypatch, tmp_path):
    """ai.enabled=True → generate_ai_reasoning_job is called after recompute."""
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-05-20T11:00:00Z', 0, 1, 0, 'PENDING')")
    conn.commit()
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    calls = {"refresh": 0, "fdr": 0, "xp": 0, "ai": 0}
    monkeypatch.setattr("src.cli.refresh", lambda **kw: calls.__setitem__("refresh", calls["refresh"] + 1))
    monkeypatch.setattr("src.analytics.fdr.compute_and_store",
                        lambda c: calls.__setitem__("fdr", calls["fdr"] + 1))
    monkeypatch.setattr("src.analytics.xp.compute_and_store",
                        lambda c: calls.__setitem__("xp", calls["xp"] + 1))
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda *a, **kw: calls.__setitem__("ai", calls["ai"] + 1) or {"captain": "ok"})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert calls == {"refresh": 1, "fdr": 1, "xp": 1, "ai": 1}


def test_refresh_and_recompute_skips_ai_when_disabled(monkeypatch):
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": False}}

    called = {"ai": 0}
    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda *a, **kw: called.__setitem__("ai", called["ai"] + 1) or {})

    scheduler.refresh_and_recompute(cfg=cfg, conn=conn)
    assert called["ai"] == 0


def test_refresh_and_recompute_swallows_ai_exception(monkeypatch, caplog):
    """An exception in the AI job is logged but never blocks the recompute cycle."""
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)

    def _boom(*a, **kw):
        raise RuntimeError("ollama is down")
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job", _boom)

    with caplog.at_level(logging.WARNING, logger="src.scheduler"):
        scheduler.refresh_and_recompute(cfg=cfg, conn=conn)   # must NOT raise
    assert any("ai.generate_job_failed" in r.message or "ai" in r.message.lower()
               for r in caplog.records)
```

Make sure `import logging` is present at the top of `tests/test_scheduler.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_refresh_and_recompute_invokes_ai_job_when_enabled tests/test_scheduler.py::test_refresh_and_recompute_skips_ai_when_disabled tests/test_scheduler.py::test_refresh_and_recompute_swallows_ai_exception -v`

Expected: FAIL — AI hook not wired into `refresh_and_recompute`.

- [ ] **Step 3: Modify `src/scheduler.py`**

In `refresh_and_recompute`, after `xp.compute_and_store(conn)` and before `_ping_healthcheck()`, add:

```python
        if config.ai_enabled(cfg):
            try:
                from src.ai import jobs as ai_jobs
                from src.ai.provider import OllamaProvider
                provider = OllamaProvider(
                    host=config.ai_ollama_host(cfg),
                    model=config.ai_ollama_model(cfg),
                    timeout_seconds=config.ai_timeout_seconds(cfg),
                )
                ai_jobs.generate_ai_reasoning_job(
                    conn, panes=["captain"], provider=provider,
                    model_id=config.ai_ollama_model(cfg))
            except Exception:
                log.exception("ai.generate_job_failed")
```

(Result: the `try` block now wraps refresh + fdr + xp + ai + healthcheck. Verify by reading the function before and after.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scheduler.py -v` (run the whole file to confirm no regressions).

Expected: PASS — all existing scheduler tests + 3 new AI tests.

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat(ai): scheduler wires AI prose pre-warm after recompute (S-A.1 task 10)

refresh_and_recompute now calls generate_ai_reasoning_job after
xp.compute_and_store when ai.enabled=true. The AI call is wrapped in
try/except: any failure (Ollama down, etc.) is logged via
'ai.generate_job_failed' but never blocks the recompute cycle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Interface — queries.py + api.py wire AI prose into /api/captain

**Files:**
- Modify: `src/interface/queries.py` (add `get_captain_picks`, helper `get_captain_reasoning`)
- Modify: `src/interface/api.py` (`/api/captain` now uses `queries.get_captain_picks`)
- Modify: `tests/test_api.py` (extend with the enriched-shape cases)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py` (locate the existing captain test block — there should be a section that hits `/api/captain` and asserts on its shape; the new cases extend that surface):

```python
def test_api_captain_carries_reasoning_classic_on_cache_miss(client_with_data):
    """Default state: no AI prose cached → reasoning_source='classic' + the
    deterministic engine's existing reason string."""
    resp = client_with_data.get("/api/captain")
    assert resp.status_code == 200
    body = resp.json()
    if not body["picks"]:
        return  # the fixture has no captain to test against — skip
    top = body["picks"][0]
    assert top["reasoning_source"] == "classic"
    assert top["reasoning"] == top["reason"]


def test_api_captain_carries_reasoning_ai_on_cache_hit(client_with_data, conn):
    """Pre-warm the cache for the next GW's payload → reasoning_source='ai'."""
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import captain
    decision = captain.get_captain_picks(conn)
    if not decision["picks"]:
        return
    payload = ai_reasoning._build_captain_payload(decision)
    rec_hash = ai_cache.recommendation_hash(payload)
    # find the next gw from the fixture
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="captain", rec_hash=rec_hash,
                 prose="AI prose here.", model_id="qwen2.5:7b-instruct-q4_K_M")

    resp = client_with_data.get("/api/captain")
    body = resp.json()
    top = body["picks"][0]
    assert top["reasoning_source"] == "ai"
    assert top["reasoning"] == "AI prose here."
```

**Note for executor:** the existing `tests/test_api.py` uses fixtures like `client_with_data` and `conn`. If those fixtures don't exist with those exact names, adapt to whatever the file actually uses — read the file first. The semantic intent is: have a test client + a conn that point at the same in-memory DB seeded with at least one gameweek and one captain pick.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k "captain"`

Expected: FAIL — the captain endpoint payload doesn't carry `reasoning_source` yet.

- [ ] **Step 3: Add `get_captain_picks` and `get_captain_reasoning` to `src/interface/queries.py`**

Append to `src/interface/queries.py`:

```python


def get_captain_picks(conn):
    """Wraps src.decisions.captain.get_captain_picks and enriches picks[0]
    with (reasoning, reasoning_source). Other picks keep the engine's reason
    string under both keys (no AI prose for vice/alts in S-A.1)."""
    from src.decisions import captain as captain_engine
    from src.ai import reasoning as ai_reasoning
    decision = captain_engine.get_captain_picks(conn)
    if not decision["picks"]:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_captain_reasoning(conn, gw, decision)
    enriched = list(decision["picks"])
    enriched[0] = {**enriched[0], "reasoning": prose, "reasoning_source": source}
    for i in range(1, len(enriched)):
        enriched[i] = {**enriched[i], "reasoning": enriched[i]["reason"],
                       "reasoning_source": "classic"}
    return {**decision, "picks": enriched}


def get_captain_reasoning(conn, gw):
    """Cheap lookup used by the Telegram path. Returns the cached AI prose for
    the captain pane at `gw`, or None on miss."""
    from src.decisions import captain as captain_engine
    from src.ai import reasoning as ai_reasoning, cache as ai_cache
    decision = captain_engine.get_captain_picks(conn)
    if not decision["picks"]:
        return None
    payload = ai_reasoning._build_captain_payload(decision)
    if payload is None:
        return None
    rec_hash = ai_cache.recommendation_hash(payload)
    hit = ai_cache.get(conn, gw=gw, pane_type="captain", rec_hash=rec_hash)
    return hit["prose"] if hit is not None else None
```

- [ ] **Step 4: Update `src/interface/api.py` `/api/captain` to use the new helper**

In `src/interface/api.py`, replace:

```python
@app.get("/api/captain")
def captain(conn=Depends(get_db)):
    return captain_engine.get_captain_picks(conn)
```

with:

```python
@app.get("/api/captain")
def captain(conn=Depends(get_db)):
    return queries.get_captain_picks(conn)
```

(The `captain_engine` import can stay — other endpoints may still reference it. If your read of the file shows it's no longer used anywhere, you can remove the import.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`

Expected: PASS — all existing + 2 new captain cases.

- [ ] **Step 6: Commit**

```bash
git add src/interface/queries.py src/interface/api.py tests/test_api.py
git commit -m "$(cat <<'EOF'
feat(ai): /api/captain returns enriched reasoning (S-A.1 task 11)

queries.get_captain_picks wraps the deterministic ranker and enriches
picks[0] with (reasoning, reasoning_source). Cache hit → 'ai' + cached
prose; miss → 'classic' + the engine's existing reason. Other picks are
classic (no AI prose for vice/alts in S-A.1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Telegram — `notify_plan` swaps captain summary when AI prose cached

**Files:**
- Modify: `src/interface/telegram.py` (`notify_plan`)
- Modify: `tests/test_telegram.py` (add 2 cases)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_telegram.py` (read the file first to see the existing fixture style — it likely uses monkeypatched env vars + a captured-send mock):

```python
def test_notify_plan_swaps_captain_summary_when_ai_cache_populated(monkeypatch, conn_with_captain):
    """If a cached AI captain prose exists for the next gw, notify_plan uses it
    as the captain entry's summary instead of the plan's existing summary."""
    from src.interface import telegram
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import captain
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    # populate the cache for the captain pane
    decision = captain.get_captain_picks(conn_with_captain)
    payload = ai_reasoning._build_captain_payload(decision)
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn_with_captain.execute(
        "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn_with_captain, gw=nxt, pane_type="captain", rec_hash=rec_hash,
                 prose="AI prose for captain.", model_id="m")

    sent = []

    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)

            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "captain", "summary": "template summary", "executed": True}]
    telegram.notify_plan(conn_with_captain, plan, mode="manual", session=_FakeSession())
    assert sent, "telegram.send_message should have been called"
    assert "AI prose for captain." in sent[0]["text"]
    assert "template summary" not in sent[0]["text"]


def test_notify_plan_uses_classic_summary_when_no_ai_cache(monkeypatch, conn_with_captain):
    from src.interface import telegram
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

    plan = [{"decision": "captain", "summary": "template summary", "executed": True}]
    telegram.notify_plan(conn_with_captain, plan, mode="manual", session=_FakeSession())
    assert sent
    assert "template summary" in sent[0]["text"]
```

**Note for executor:** if `conn_with_captain` doesn't exist as a fixture, build one in the test (or extract a `_seed_captain_db` helper). The fixture needs: schema initialised, ≥1 unfinished gameweek, ≥1 player in `my_team.picks_json`, with matching `players`/`teams`/`xp`/`fdr` rows so `captain.get_captain_picks(conn)` returns at least one pick.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_telegram.py -v -k "notify_plan_swaps or notify_plan_uses_classic"`

Expected: FAIL — captain swap not wired.

- [ ] **Step 3: Modify `src/interface/telegram.py` `notify_plan`**

Replace the existing `notify_plan` function with:

```python
def notify_plan(conn, plan, *, mode, session=None):
    """Best-effort: notify per plan entry (executed -> confirmation, else pending info).
    Early-returns when unconfigured so callers with minimal plan dicts never touch
    summary/executed keys (keeps the existing scheduler/router tests untouched).
    When a captain entry has cached AI prose for the next gw, the AI prose replaces
    the entry's summary (S-A.1)."""
    if not is_configured():
        return
    captain_prose = _captain_ai_prose(conn)
    for entry in plan:
        kind = "executed" if entry["executed"] else "info"
        summary = entry["summary"]
        if entry["decision"] == "captain" and captain_prose is not None:
            summary = captain_prose
        notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
               summary=summary, session=session)


def _captain_ai_prose(conn):
    """Return cached AI prose for the captain pane at the next gw, or None.
    Best-effort: any exception is swallowed (Telegram should never fail because
    of an AI lookup)."""
    try:
        from src.interface import queries
        nxt = conn.execute(
            "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        if nxt is None or nxt["gw"] is None:
            return None
        return queries.get_captain_reasoning(conn, gw=nxt["gw"])
    except Exception:
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_telegram.py -v`

Expected: PASS — all existing telegram tests + 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "$(cat <<'EOF'
feat(ai): notify_plan swaps captain summary with AI prose when cached (S-A.1 task 12)

When ai_reasoning_cache has prose for the next gw's captain pane, notify_plan
uses it as the summary for the captain entry. Lookup is best-effort: any
exception returns to the template summary so Telegram never breaks on an AI
lookup. No badge in the Telegram body — terse channel.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Frontend — types + CaptainPicks component + badge

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/components/CaptainPicks.svelte`
- Modify: `frontend/src/lib/components/CaptainPicks.svelte.test.ts`
- Modify: `frontend/src/lib/mocks/full.ts`

- [ ] **Step 1: Read the existing CaptainPicks.svelte + test + types + mock**

Run: `cat frontend/src/lib/components/CaptainPicks.svelte frontend/src/lib/components/CaptainPicks.svelte.test.ts frontend/src/lib/mocks/full.ts | head -150`

You need to know the exact shape used by the component so the type/test/mock additions line up. The component currently renders picks list with `reason` strings.

- [ ] **Step 2: Write the failing vitest cases**

Add to `frontend/src/lib/components/CaptainPicks.svelte.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte';
import { describe, it, expect } from 'vitest';
import CaptainPicks from './CaptainPicks.svelte';

describe('CaptainPicks AI/classic badge', () => {
    it('shows AI badge when top pick has reasoning_source ai', () => {
        const captain = {
            picks: [
                { player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BRE (H)',
                  reason: 'template reason', reasoning: 'AI prose here.', reasoning_source: 'ai' },
            ],
            vice_player_id: null,
            confidence: 82,
        };
        render(CaptainPicks, { captain });
        expect(screen.getByText('AI')).toBeInTheDocument();
        expect(screen.getByText('AI prose here.')).toBeInTheDocument();
    });

    it('shows classic label when top pick has reasoning_source classic', () => {
        const captain = {
            picks: [
                { player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BRE (H)',
                  reason: 'template reason', reasoning: 'template reason',
                  reasoning_source: 'classic' },
            ],
            vice_player_id: null,
            confidence: 82,
        };
        render(CaptainPicks, { captain });
        expect(screen.getByText('classic')).toBeInTheDocument();
        expect(screen.getByText('template reason')).toBeInTheDocument();
    });

    it('falls back to reason when reasoning fields absent (backwards-compat)', () => {
        const captain = {
            picks: [
                { player_id: 10, web_name: 'Haaland', xp: 7.2, fixture: 'MCI v BRE (H)',
                  reason: 'template reason' },
            ],
            vice_player_id: null,
            confidence: 82,
        };
        render(CaptainPicks, { captain });
        expect(screen.getByText('template reason')).toBeInTheDocument();
    });
});
```

- [ ] **Step 3: Run the vitest cases to verify they fail**

Run: `cd frontend && npm test -- CaptainPicks`

Expected: FAIL — fields don't exist on the type / badge not rendered.

- [ ] **Step 4: Update `frontend/src/lib/types.ts` `CaptainPick`**

Locate the `CaptainPick` interface (around line 45). Add the optional fields:

```ts
export interface CaptainPick {
    player_id: number;
    web_name: string;
    xp: number;
    fixture: string;
    reason: string;
    reasoning?: string;
    reasoning_source?: 'ai' | 'classic';
}
```

(Keep all other existing fields; this is showing the shape to be reached — read the existing first.)

- [ ] **Step 5: Update `frontend/src/lib/components/CaptainPicks.svelte`**

Read the existing file first to understand its structure. The change is: render `pick.reasoning ?? pick.reason` for the top pick, and add a small badge inline. Pattern (adapt to the file's actual structure):

```svelte
<script lang="ts">
    import type { Captain } from '$lib/types';
    let { captain }: { captain: Captain } = $props();
</script>

{#if captain.picks.length === 0}
    <!-- existing empty state -->
{:else}
    {#each captain.picks as pick, i (pick.player_id)}
        <div class="pick">
            <strong>{pick.web_name}</strong> ({pick.xp} xP) — {pick.fixture}
            <div class="reason">
                {pick.reasoning ?? pick.reason}
                {#if i === 0 && pick.reasoning_source === 'ai'}
                    <span class="badge badge-ai">AI</span>
                {:else if i === 0 && pick.reasoning_source === 'classic'}
                    <span class="badge badge-classic">classic</span>
                {/if}
            </div>
        </div>
    {/each}
{/if}

<style>
    .badge { font-size: 0.7em; padding: 0.1em 0.4em; border-radius: 0.3em; margin-left: 0.4em; }
    .badge-ai { background: #2563eb; color: white; }
    .badge-classic { background: #e5e7eb; color: #4b5563; }
</style>
```

(The exact markup must match the existing file's structure — this is a template, not a verbatim replacement. Preserve any existing CSS classes the project uses.)

- [ ] **Step 6: Update `frontend/src/lib/mocks/full.ts`** to extend at least the captain top-pick with `reasoning` + `reasoning_source: 'ai'` so visual smoke testing in mock mode shows the badge.

Find the `captain: {` block (around line 67) and the first pick. Add:

```ts
reasoning: 'Haaland is the captain this week at 7.2 xP, MCI v BRE (H).',
reasoning_source: 'ai' as const,
```

- [ ] **Step 7: Run the vitest cases to verify they pass**

Run: `cd frontend && npm test`

Expected: PASS — all existing vitest + 3 new CaptainPicks cases.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/components/CaptainPicks.svelte \
        frontend/src/lib/components/CaptainPicks.svelte.test.ts \
        frontend/src/lib/mocks/full.ts
git commit -m "$(cat <<'EOF'
feat(ai): CaptainPicks renders reasoning + AI/classic badge (S-A.1 task 13)

CaptainPick type gains optional reasoning + reasoning_source. The component
renders pick.reasoning ?? pick.reason and shows an 'AI' or 'classic' badge
on the top pick. Backwards-compatible: panes without the new fields render
exactly as today (no badge). Mocks updated for visual smoke in mock mode.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Docs — architecture.md + onboarding.md

**Files:**
- Modify: `docs/architecture.md` (insert the new sub-layer in the diagram)
- Modify: `docs/onboarding.md` (Ollama prerequisite + opt-out)

- [ ] **Step 1: Update `docs/architecture.md`**

Find the "High-level layers" diagram (around the top of the file). Insert a new layer between Interface and Decision, matching the AI architecture spec:

```
┌─────────────────────────────────────────────┐
│  AI Reasoning (Phase 3, optional)           │
│   Provider (Ollama / Claude)                │
│   Prompt builder + few-shot                 │
│   Number-grounding check                    │
│   ai_reasoning_cache                        │
└──────────────────┬──────────────────────────┘
                   ↓
```

And update the rules paragraph below the diagram to add:

> The AI Reasoning sub-layer is optional (disabled by `ai.enabled: false`). When enabled, it sits **strictly downstream** of Decision and **strictly upstream** of Interface. Interface → AI Reasoning → Decision → Analytics → Data. The Decision Layer is unaware of the AI sub-layer; degrading to the Phase-2 behaviour requires only the `ai.enabled: false` flag. Cross-cutting design: `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md`.

Also add the new table to the data model section (locate the `cache_meta` table; add this block after it):

```markdown
### `ai_reasoning_cache` (Phase 3, S-A.1)

| Column | Type | Notes |
|---|---|---|
| gw | INTEGER | |
| pane_type | TEXT | 'captain' / 'transfer' / 'chip' / 'deadguard_summary' |
| recommendation_hash | TEXT | sha256(canonical-JSON of payload)[:32] |
| prose | TEXT | LLM-generated paragraph |
| model_id | TEXT | e.g. 'qwen2.5:7b-instruct-q4_K_M' |
| generated_at | TIMESTAMP | |
| (PK) | (gw, pane_type, recommendation_hash) | |
```

- [ ] **Step 2: Update `docs/onboarding.md`**

Append a new section (or insert at a fitting location):

```markdown
## Optional: AI prose for captain pick (Phase 3, S-A.1)

The captain pane on the dashboard and the Telegram H-24 preview body can show
LLM-generated prose grounded in the deterministic engine's existing numbers.

**Prerequisite:** an Ollama daemon running on localhost with the
`qwen2.5:7b-instruct-q4_K_M` model installed:

    brew install ollama          # or your platform's equivalent
    ollama serve                  # in a background shell
    ollama pull qwen2.5:7b-instruct-q4_K_M

**Enabled by default.** The scheduler pre-warms the cache after each
`refresh_and_recompute`; the dashboard and Telegram read cached prose.

**To disable** (and revert to the deterministic engine's template strings), set:

    # config.yaml
    ai:
      enabled: false

When disabled (or when Ollama is unreachable), the dashboard renders the
template string with a small `classic` badge, and the Telegram body uses the
template string. No banner, no broken page.
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md docs/onboarding.md
git commit -m "$(cat <<'EOF'
docs: AI sub-layer + ai_reasoning_cache + Ollama onboarding (S-A.1 task 14)

architecture.md gains the new optional sub-layer between Interface and
Decision, the layer-call rules, and the ai_reasoning_cache table.
onboarding.md gains the Ollama prerequisite + the ai.enabled opt-out flag.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Full test suite green (verification gate)

This task ships nothing new — it just verifies the cumulative state is healthy before review.

- [ ] **Step 1: Run the full pytest suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass (count = previous-passing + ~30 new tests from Tasks 0–12).

If any test fails, fix it in a new commit before proceeding. Don't suppress failures.

- [ ] **Step 2: Run the full vitest suite**

Run: `cd frontend && npm test`

Expected: all tests pass (count = previous-passing + 3 new CaptainPicks cases).

- [ ] **Step 3: Read the full diff between this branch and main**

Run: `git diff main..HEAD --stat && git log main..HEAD --oneline`

Confirm: ~15 commits, each scoped to one task, no commit touches `docs/decision-engine.md`, no commit touches `src/auth/`, no commit touches `src/execution/`.

- [ ] **Step 4: Commit if any test-stabilising fixes were needed**

If you made fixes in Step 1/2, commit them with:

```bash
git commit -m "fix: stabilise tests after S-A.1 cumulative integration

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Final code review

This is delegated to a fresh `pr-review-toolkit:code-reviewer` agent on the cumulative branch diff vs. `main`. The agent runs against the diff with the project conventions in `CLAUDE.md` loaded.

- [ ] **Step 1: Dispatch the code-reviewer agent**

Invoke the agent with this brief (the orchestrator does this — not a subagent task in the per-task subagent rotation):

> Review the cumulative diff of branch `feat/phase3-brainstorm` vs. `main` — this is the Phase-3 S-A.1 slice (LLM captain reasoning). Focus areas:
> - **B-rule compliance:** no `decision-engine.md` change; no `src/auth/` import from `src/ai/`; prompt builder is the sole egress; no credentials in prompts; LLM has no write tools; all tests use `StubProvider` (no live Ollama).
> - **Layer integrity:** `src/ai/` reads Decision outputs in-process, writes only to `ai_reasoning_cache`. Interface → AI → Decision → Analytics → Data preserved.
> - **Failure modes:** silent fallback to template prose on cache miss / Ollama down / ungrounded output / provider exception.
> - **Test quality:** every new test deterministic, fixtures-only, no network.
> - **Spec adherence:** referenced specs are `docs/superpowers/specs/2026-05-26-phase3-{scope-decomposition,ai-architecture-design,llm-captain-reasoning-design}.md`. Flag any divergence.
> 
> Report blocking findings vs. nice-to-haves. Be terse.

- [ ] **Step 2: Apply blocking findings**

For each blocking finding, make a focused fix commit. Re-run pytest + vitest. Iterate until the reviewer reports no blockers.

- [ ] **Step 3: Re-run the full suite**

```
.venv/bin/pytest -q && (cd frontend && npm test)
```

Expected: green.

---

## Task 17: `finishing-a-development-branch`

- [ ] **Step 1: Invoke the `superpowers:finishing-a-development-branch` skill**

The skill walks through the merge/PR/cleanup options. For this slice:

- **Merge to main locally** — the project convention is to land slices on local `main` and push only when asked. Per `HANDOFF.md`: "Per-slice branch `feat/<slice>`; merge to `main` locally; push only when asked."
- **Do NOT push** to `origin` unless the user explicitly asks.
- The brainstorm branch is `feat/phase3-brainstorm`, which contains both the spec commits AND the implementation commits — that's acceptable for this end-to-end session.

- [ ] **Step 2: Report back to the user with:**
  - Final commit count on the branch
  - pytest + vitest summary line
  - Whether code review surfaced any blockers (and what was fixed)
  - The push-to-origin question (ask explicitly before pushing)

---

## Spec coverage self-check (run after writing this plan)

| Spec requirement | Task |
|---|---|
| `LLMProvider` Protocol + `OllamaProvider` + `StubProvider` | T3, T4 |
| `qwen2.5:7b-instruct-q4_K_M` default + config | T0 |
| `src/ai/` sub-layer between Decision and Interface | T1–T9 |
| Per-pane structured prompt + few-shot exemplars | T5 |
| Narrow JSON payload (closed schema) | T6 |
| `ai_reasoning_cache` table | T0, T2 |
| Pre-warm in scheduler after recompute | T10 |
| Post-generation number-grounding check | T1, T8 |
| Silent fallback to template + per-pane source tag | T7, T11, T13 |
| Captain pane swap in dashboard | T11, T13 |
| Captain swap in Telegram H-24 body | T12 |
| B4 untouched (no `decision-engine.md` edit) | (negative — verified in T15/T16) |
| B7 (no creds in prompts, sole egress) | T6 (closed schema), T16 (review) |
| R3 (no live calls in tests) | every task (StubProvider) |
| Documentation updates | T14 |
| Definition of done verified | T15 (suite green), T16 (review), T17 (finish) |

Every spec requirement maps to at least one task. ✓
