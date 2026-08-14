# DeepSeek Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local-Ollama AI stack with the DeepSeek API (OpenAI SDK, configurable base URL) as the single provider, and drop the Claude audit provider.

**Architecture:** Add `DeepSeekProvider` implementing the existing `LLMProvider.generate()` Protocol in `src/ai/provider.py`, add a `build_provider(cfg, conn=None)` factory, route the three call sites (scheduler AI job, deadguard summary, review CLI) through it, delete `ClaudeProvider`, update config + env + docs.

**Tech Stack:** Python 3.11+, `openai` SDK (new dep), `requests`, pytest. Venv: `.venv/bin/` (`.venv/bin/pytest -q`, `.venv/bin/fpl-autopilot`).

## Global Constraints

- **B7:** API keys live in env (`DEEPSEEK_API_KEY`), never in `config.yaml`, never logged. Prompts are credential-scanned before send (shared `_CREDENTIAL_PATTERNS`).
- **R3:** Tests never make network calls — providers accept injected fake clients/sessions.
- **B4:** No changes to `docs/decision-engine.md` (execution/plumbing only).
- **B13:** Spec `docs/superpowers/specs/2026-08-14-deepseek-provider-design.md` is the source of truth; `docs/risks.md` D2 is resolved by this slice.
- **Commits:** per task, conventional style (`feat(ai):`, `chore(deps):`, `docs(ai):`), stage explicit paths only — **never `git add -A`** (sweeps worktree gitlinks).
- **Config default:** `ai.provider` → `deepseek`; `ai.deepseek.base_url` default `https://api.deepseek.com/v1`; `ai.deepseek.model` default `deepseek-chat`. `ollama` remains selectable; `OllamaProvider` class kept.
- **Full suite must stay green:** baseline `638 passed in ~11s` (`.venv/bin/pytest -q` from repo root).

---

### Task 1: DeepSeekProvider

**Files:**
- Modify: `src/ai/provider.py` (append `DeepSeekError` + `DeepSeekProvider` after the `OllamaProvider` class; `_CREDENTIAL_PATTERNS` block already exists in this file and is reused)
- Test: `tests/test_ai_provider.py` (append)
- Deps: run `.venv/bin/pip install openai` (import used in this task; `pyproject.toml` pinned in Task 4)

**Interfaces:**
- Produces: `DeepSeekError(RuntimeError)` and `DeepSeekProvider(api_key, *, model, base_url, timeout_seconds, conn=None, _client=None)` with `generate(prompt, *, max_tokens=200, temperature=0.2) -> str`. Constructor accepts `_client` for test injection (mirrors `ClaudeProvider._client` pattern). When `conn` is not None, success logs a usage row to `activity_log` (`decision_type='ai.audit'`, `action_taken='deepseek generate'`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ai_provider.py`:

```python
from types import SimpleNamespace

from src.ai.provider import DeepSeekError, DeepSeekProvider


def _fake_completions(text="Grounded 8.0 prose", usage=None):
    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=usage,
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_deepseek_generate_happy_path():
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=40)
    client = _fake_completions(usage=usage)
    p = DeepSeekProvider("sk-test", model="deepseek-chat",
                         base_url="https://api.deepseek.com/v1",
                         timeout_seconds=15, _client=client)
    out = p.generate("Captain Haaland for GW1.")
    assert out == "Grounded 8.0 prose"


def test_deepseek_refuses_credential_patterns():
    p = DeepSeekProvider("sk-test", model="deepseek-chat",
                         base_url="https://api.deepseek.com/v1",
                         timeout_seconds=15, _client=_fake_completions())
    with pytest.raises(DeepSeekError, match="credential"):
        p.generate("pl_profile leaked here")


def test_deepseek_wraps_sdk_errors():
    class Boom:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(RuntimeError("connection reset"))))

    p = DeepSeekProvider("sk-test", model="deepseek-chat",
                         base_url="https://api.deepseek.com/v1",
                         timeout_seconds=15, _client=Boom())
    with pytest.raises(DeepSeekError, match="deepseek request failed"):
        p.generate("hello")


def test_deepseek_logs_usage_only_when_conn_present(db):
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=25)
    client = _fake_completions(usage=usage)
    p = DeepSeekProvider("sk-test", model="deepseek-chat",
                         base_url="https://api.deepseek.com/v1",
                         timeout_seconds=15, conn=db, _client=client)
    p.generate("hello")
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='ai.audit'").fetchone()
    assert row is not None and row["action_taken"] == "deepseek generate"
    assert '"input_tokens": 50' in row["inputs_json"] and '"output_tokens": 25' in row["inputs_json"]


def test_deepseek_skips_usage_log_without_conn():
    p = DeepSeekProvider("sk-test", model="deepseek-chat",
                         base_url="https://api.deepseek.com/v1",
                         timeout_seconds=15, _client=_fake_completions())
    p.generate("hello")
    assert True  # no conn -> no activity_log write; absence of exception is the assertion
```

Note: `pytest` is already imported at the top of `tests/test_ai_provider.py`; the `db` fixture comes from the repo's conftest (used by `test_claude_provider.py` and `tests/test_repository.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ai_provider.py`
Expected: FAIL with `ImportError: cannot import name 'DeepSeekProvider'`

- [ ] **Step 3: Implement `DeepSeekProvider`**

Append to `src/ai/provider.py` (after the `OllamaProvider` class; `_CREDENTIAL_PATTERNS` is defined later in the file and resolves at call time, so order is fine):

```python
class DeepSeekError(RuntimeError):
    """Raised when the DeepSeek API call fails or is refused before send (B7)."""


class DeepSeekProvider:
    """DeepSeek API provider (OpenAI-compatible) — implements LLMProvider.

    Uses the `openai` SDK pointed at a configurable base_url, so other
    OpenAI-compatible providers (Groq, OpenRouter, ...) work with config-only
    changes. Guardrails: pre-send credential scan (B7); optional usage log to
    activity_log when a conn is provided (audit path, B10).
    """

    def __init__(self, api_key: str, *, model: str, base_url: str,
                 timeout_seconds: float, conn=None, _client=None):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._conn = conn
        if _client is not None:
            self._client = _client
        else:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key, base_url=base_url,
                                  timeout=timeout_seconds)

    def generate(self, prompt: str, *, max_tokens: int = 200,
                 temperature: float = 0.2) -> str:
        self._refuse_credential_prompts(prompt)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            raise DeepSeekError(f"deepseek request failed: {type(e).__name__}") from e
        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""
        self._log_usage(response)
        return text.strip()

    def _refuse_credential_prompts(self, prompt):
        for pat in _CREDENTIAL_PATTERNS:
            if pat.search(prompt):
                raise DeepSeekError(
                    f"prompt rejected: contains credential/sensitive pattern ({pat.pattern})")

    def _log_usage(self, response):
        if self._conn is None:
            return
        import json as _json
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        self._conn.execute(
            """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
                 inputs_json, executed)
               VALUES (datetime('now'), NULL, 'audit', 'ai.audit', 'deepseek generate',
                       ?, 1)""",
            (_json.dumps({"model": self.model,
                          "input_tokens": int(input_tokens),
                          "output_tokens": int(output_tokens)}),)
        )
        self._conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ai_provider.py`
Expected: PASS (all tests in file, old OllamaProvider tests included)

- [ ] **Step 5: Commit**

```bash
git add src/ai/provider.py tests/test_ai_provider.py
git commit -m "feat(ai): DeepSeekProvider via OpenAI SDK with B7 credential scan + usage log"
```

---

### Task 2: Config accessors + `build_provider` factory

**Files:**
- Modify: `src/config.py` (default + accessors), `src/ai/provider.py` (append `build_provider`), `tests/test_config.py`, `tests/test_ai_provider.py`

**Interfaces:**
- Consumes: `DeepSeekProvider` (Task 1), `OllamaProvider` (existing).
- Produces: `build_provider(cfg, conn=None)` → provider instance or `None`; raises `DeepSeekError` when provider is `deepseek` and `DEEPSEEK_API_KEY` env var is missing. Config accessors: `ai_deepseek_model(cfg=None) -> str` (default `"deepseek-chat"`), `ai_deepseek_base_url(cfg=None) -> str` (default `"https://api.deepseek.com/v1"`); `ai_provider(cfg=None)` default changes `"ollama"` → `"deepseek"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ai_provider.py`:

```python
def test_build_provider_deepseek(monkeypatch):
    from src.ai.provider import build_provider
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = {"ai": {"provider": "deepseek"}}
    provider = build_provider(cfg)
    assert isinstance(provider, DeepSeekProvider)
    assert provider.model == "deepseek-chat"


def test_build_provider_deepseek_missing_key_raises(monkeypatch):
    from src.ai.provider import build_provider
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = {"ai": {"provider": "deepseek"}}
    with pytest.raises(DeepSeekError, match="DEEPSEEK_API_KEY"):
        build_provider(cfg)


def test_build_provider_ollama():
    from src.ai.provider import build_provider, OllamaProvider
    cfg = {"ai": {"provider": "ollama", "ollama": {"host": "http://x:1", "model": "m"}}}
    provider = build_provider(cfg)
    assert isinstance(provider, OllamaProvider)


def test_build_provider_none_when_disabled_or_unknown():
    from src.ai.provider import build_provider
    assert build_provider({"ai": {"provider": "none"}}) is None
    assert build_provider({"ai": {}}) is None
```

Append to `tests/test_config.py`:

```python
def test_ai_deepseek_accessors():
    from src import config
    cfg = {"ai": {"deepseek": {"base_url": "https://example.com/v1", "model": "deepseek-reasoner"}}}
    assert config.ai_deepseek_model(cfg) == "deepseek-reasoner"
    assert config.ai_deepseek_base_url(cfg) == "https://example.com/v1"
    # defaults (explicit {} must NOT fall back to config.yaml)
    assert config.ai_deepseek_model({}) == "deepseek-chat"
    assert config.ai_deepseek_base_url({}) == "https://api.deepseek.com/v1"
```

And update `tests/test_config.py:79`:

```python
    assert config.ai_provider(cfg) == "deepseek"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_ai_provider.py tests/test_config.py`
Expected: FAIL — `ImportError: cannot import name 'build_provider'` and `assert config.ai_provider(cfg) == "deepseek"` fails (got `"ollama"`).

- [ ] **Step 3: Implement**

In `src/config.py`, change:

```python
def ai_provider(cfg=None):
    return _ai(cfg).get("provider", "ollama")
```

to:

```python
def ai_provider(cfg=None):
    return _ai(cfg).get("provider", "deepseek")
```

and append after `ai_ollama_model`:

```python
def _ai_deepseek(cfg):
    return _ai(cfg).get("deepseek", {})


def ai_deepseek_model(cfg=None):
    return _ai_deepseek(cfg).get("model", "deepseek-chat")


def ai_deepseek_base_url(cfg=None):
    return _ai_deepseek(cfg).get("base_url", "https://api.deepseek.com/v1")
```

Append to `src/ai/provider.py`:

```python
def build_provider(cfg, conn=None):
    """Construct the configured LLM provider, or None when no provider is set.

    deepseek -> DeepSeekProvider (requires DEEPSEEK_API_KEY env var; raises
    DeepSeekError with a clear message when missing). ollama -> OllamaProvider.
    anything else / none -> None (AI job no-ops; classic templates render).
    """
    import os
    from src import config

    choice = config.ai_provider(cfg)
    if choice == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise DeepSeekError(
                "DEEPSEEK_API_KEY env var is not set; AI provider 'deepseek' cannot start")
        return DeepSeekProvider(
            api_key,
            model=config.ai_deepseek_model(cfg),
            base_url=config.ai_deepseek_base_url(cfg),
            timeout_seconds=config.ai_timeout_seconds(cfg),
            conn=conn,
        )
    if choice == "ollama":
        return OllamaProvider(
            host=config.ai_ollama_host(cfg),
            model=config.ai_ollama_model(cfg),
            timeout_seconds=config.ai_timeout_seconds(cfg),
        )
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_ai_provider.py tests/test_config.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/ai/provider.py tests/test_config.py tests/test_ai_provider.py
git commit -m "feat(ai): build_provider factory + deepseek config accessors (default provider deepseek)"
```

---

### Task 3: Wire scheduler + deadguard through the factory

**Files:**
- Modify: `src/scheduler.py:46-59`, `src/interface/deadguard.py:164-172`, `tests/test_scheduler.py`, `tests/test_deadguard.py`

**Interfaces:**
- Consumes: `build_provider(cfg)` (Task 2), `config.ai_deepseek_model(cfg)` (Task 2).
- Produces: scheduler and deadguard construct providers via `build_provider(cfg)`; `model_id` labels use `config.ai_deepseek_model(cfg)`.

- [ ] **Step 1: Update scheduler**

In `src/scheduler.py`, replace lines 46-59 (the `if config.ai_enabled(cfg):` block body):

```python
        if config.ai_enabled(cfg):
            try:
                from src.ai import jobs as ai_jobs
                from src.ai.provider import build_provider
                provider = build_provider(cfg)
                ai_jobs.generate_ai_reasoning_job(
                    conn, panes=["captain", "transfer", "chip"], provider=provider,
                    model_id=config.ai_deepseek_model(cfg))
            except Exception:
                log.exception("ai.generate_job_failed")
```

- [ ] **Step 2: Update deadguard**

In `src/interface/deadguard.py`, replace lines 164-172:

```python
            from src.ai import reasoning as ai_reasoning, provider as ai_provider
            provider = ai_provider.build_provider(cfg)
            if ai_reasoning.generate_deadguard_summary(
                    conn, gw=gw, outcome=outcome,
                    provider=provider, model_id=config.ai_deepseek_model(cfg)):
```

- [ ] **Step 3: Update scheduler tests**

In `tests/test_scheduler.py`:

1. `test_refresh_and_recompute_invokes_ai_job_when_enabled` (line 388) — add after `cfg = {...}` (line 397):
```python
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
```
2. `test_refresh_and_recompute_swallows_ai_exception` (line 430) — add after `cfg = {...}` (line 437):
```python
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
```
3. Append a new test:

```python
def test_refresh_and_recompute_logs_missing_deepseek_key(monkeypatch, caplog):
    """Missing DEEPSEEK_API_KEY -> factory raises, logged as ai.generate_job_failed, no crash."""
    import logging
    from src import scheduler
    from src.data.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    cfg = {"fpl": {"team_id": 1}, "ai": {"enabled": True}}

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("src.cli.refresh", lambda **kw: None)
    monkeypatch.setattr("src.analytics.fdr.compute_and_store", lambda c: None)
    monkeypatch.setattr("src.analytics.xp.compute_and_store", lambda c: None)
    called = {"ai": 0}
    monkeypatch.setattr("src.ai.jobs.generate_ai_reasoning_job",
                        lambda *a, **kw: called.__setitem__("ai", called["ai"] + 1) or {})

    with caplog.at_level(logging.WARNING, logger="src.scheduler"):
        scheduler.refresh_and_recompute(cfg=cfg, conn=conn)   # must NOT raise
    assert called["ai"] == 0
    assert any("ai.generate_job_failed" in r.message for r in caplog.records)
```

- [ ] **Step 4: Update deadguard tests**

In `tests/test_deadguard.py`, replace the three `monkeypatch.setattr(ai_prv, "OllamaProvider", X)` lines (894, 920, 953):

- Line 894 (`test_run_trigger_uses_ai_prose_when_enabled`) →
```python
    monkeypatch.setattr(ai_prv, "build_provider", lambda cfg, conn=None: _StubProvider())
```
- Line 920 (`test_run_trigger_falls_back_to_template_when_ai_unavailable`) →
```python
    monkeypatch.setattr(ai_prv, "build_provider", lambda cfg, conn=None: _ErrProvider())
```
- Line 953 (`test_run_trigger_uses_template_when_ai_disabled`) → delete the `_BoomProvider` class and the monkeypatch line entirely (with `ai_enabled` patched False, `build_provider` is never called; the `boom` list + its assert are also dead — check the tail of the test at lines 958-964 and remove the `assert boom == []` if present).

- [ ] **Step 5: Run the affected tests**

Run: `.venv/bin/pytest -q tests/test_scheduler.py tests/test_deadguard.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py src/interface/deadguard.py tests/test_scheduler.py tests/test_deadguard.py
git commit -m "refactor(ai): scheduler + deadguard build provider via factory"
```

---

### Task 4: Review CLI → deepseek; drop ClaudeProvider

**Files:**
- Modify: `src/cli.py` (review provider branch ~lines 247-265, `--ai` choices line 586, docstring line 226), `src/ai/provider.py` (delete `ClaudeError`, `ClaudeRateLimitError`, `ClaudeProvider`; keep `_CREDENTIAL_PATTERNS`), `src/ai/audit_narrator.py` (lines 13 + 120), `tests/test_cli_review.py`, `pyproject.toml`, `requirements.txt`
- Delete: `tests/test_claude_provider.py`

**Interfaces:**
- Consumes: `build_provider(cfg, conn=conn)` (Task 2).
- Produces: `fpl-autopilot review --ai` choices `["deepseek", "ollama", "none"]`; audit narration catches `(OllamaError, DeepSeekError)`.

- [ ] **Step 1: Update the review CLI**

In `src/cli.py`, update the docstring (line 226):

```python
    settled GWs). AI provider: ai_override ∈ {'deepseek','ollama','none', None}; None falls back
```

Replace the provider-build block (lines 247-265):

```python
        # Build the provider (if any).
        provider, model_id = None, None
        provider_choice = ai_override or _resolve_audit_provider_choice()
        if provider_choice == "deepseek":
            if not os.environ.get("DEEPSEEK_API_KEY"):
                print("Error: --ai deepseek requires DEEPSEEK_API_KEY env var. Aborting.")
                return
            from .ai.provider import build_provider
            provider = build_provider(config.load_config(), conn=conn)
            model_id = config.ai_deepseek_model()
        elif provider_choice == "ollama":
            from .ai.provider import build_provider
            provider = build_provider(config.load_config())
            model_id = config.ai_ollama_model()
```

(`os` is already imported inside `_cmd_review_cli` at line 228.)

Update the argparse choices (line 586):

```python
    p_review.add_argument("--ai", choices=["deepseek", "ollama", "none"], default=None,
                          help="override the AI provider for this run (default: from config)")
```

- [ ] **Step 2: Drop Claude from provider.py + audit_narrator.py**

In `src/ai/provider.py`, delete `ClaudeError`, `ClaudeRateLimitError`, and the whole `ClaudeProvider` class (lines ~79-168). **Keep** the `import re as _re` + `_CREDENTIAL_PATTERNS` block (DeepSeekProvider uses it).

In `src/ai/audit_narrator.py`:
- Line 13: `from src.ai.provider import ClaudeError, OllamaError` → `from src.ai.provider import DeepSeekError, OllamaError`
- Line 120: `except (OllamaError, ClaudeError) as e:` → `except (OllamaError, DeepSeekError) as e:`

Delete the file `tests/test_claude_provider.py`:

```bash
git rm tests/test_claude_provider.py
```

- [ ] **Step 3: Update pyproject.toml + requirements.txt**

`pyproject.toml` dependencies line:

```toml
dependencies = ["requests", "pydantic>=2", "pyyaml", "fastapi", "uvicorn", "APScheduler", "cryptography>=44", "openai>=1.40"]
```

`requirements.txt`:

```
requests
pydantic>=2
pyyaml
fastapi
uvicorn
APScheduler
cryptography>=44
openai>=1.40
```

Reinstall editable package so the venv metadata matches:

```bash
.venv/bin/pip install -e ".[dev]" -q
```

- [ ] **Step 4: Replace the claude review test with a deepseek test**

In `tests/test_cli_review.py`, delete `test_review_ai_override_claude_requires_api_key` (lines 129-139) and append in its place:

```python
def test_review_ai_override_deepseek_requires_api_key(db, monkeypatch, capsys):
    """--ai deepseek with no DEEPSEEK_API_KEY → graceful error, audit not run."""
    _seed_settled_gws(db, [3])
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with patch("src.audit.audit.run_audit") as mock_audit:
        cli._cmd_review_cli(conn=db, gw=3, ai_override="deepseek")

    mock_audit.assert_not_called()
    out = capsys.readouterr().out
    assert "requires DEEPSEEK_API_KEY" in out
```

- [ ] **Step 5: Verify no Claude references remain; run tests**

Run:
```bash
grep -rn "Claude\|anthropic\|claude" src tests --include="*.py"
```
Expected: no matches.

Run: `.venv/bin/pytest -q tests/test_ai_audit_narrator.py tests/test_cli_review.py tests/test_ai_provider.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cli.py src/ai/provider.py src/ai/audit_narrator.py tests/test_cli_review.py pyproject.toml requirements.txt
git commit -m "refactor(ai): drop ClaudeProvider, review CLI uses deepseek via factory"
```

---

### Task 5: Config, env, and docs

**Files:**
- Modify: `config.yaml`, `.env.example`, `docker-compose.yml.example`, `docs/risks.md` (D2), `docs/architecture.md` (line 16), `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md` (Changelog, line 389), `docs/onboarding.md` (lines 435-462), `docs/runbook.md` (new §10)

**Interfaces:** none — documentation + config only.

- [ ] **Step 1: Update config.yaml + env templates**

`config.yaml` — replace the `ai:` block's `provider: ollama` line and add the deepseek block:

```yaml
ai:
  enabled: true
  provider: deepseek   # deepseek | ollama | none
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    model: "deepseek-chat"
  ollama:
    host: "http://localhost:11434"
    model: "qwen2.5:7b-instruct-q4_K_M"
  timeout_seconds: 60
  consecutive_failure_backoff: 3
  temperature: 0.2
  max_tokens_per_pane: 200
```

`.env.example` — append:

```
DEEPSEEK_API_KEY=
```

`docker-compose.yml.example` — after the `env_file: .env` line (line 22), add a comment:

```yaml
    env_file: .env    # must include DEEPSEEK_API_KEY=sk-... (AI provider); TELEGRAM_BOT_TOKEN etc.
```

- [ ] **Step 2: Update risks.md (D2)**

Replace the D2 section (lines ~113-117):

```markdown
## D2 — LLM choice for Phase 3

DeepSeek API (`deepseek-chat`) via the OpenAI SDK, chosen 2026-08-14
(`docs/superpowers/specs/2026-08-14-deepseek-provider-design.md`). Base URL +
model are configurable, so switching to another OpenAI-compatible provider is
config-only. Local LLMs (Ollama) remain a config-selectable fallback but are
not deployed.
```

- [ ] **Step 3: Update architecture.md provider line**

Line 16: `│   Provider (Ollama / Claude)                │` →

```
│   Provider (DeepSeek / Ollama)              │
```

- [ ] **Step 4: Update the AI architecture spec changelog**

In `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md`, prepend to the changelog table (after the header row):

```markdown
| v0.2 | 2026-08-14 | Provider decision revised: local Ollama → **DeepSeek API** (`deepseek-chat`) via the OpenAI SDK with configurable `base_url`; Claude audit provider dropped; `build_provider(cfg, conn)` factory in `src/ai/provider.py`; `DEEPSEEK_API_KEY` env var. See `2026-08-14-deepseek-provider-design.md`. |
```

- [ ] **Step 5: Update onboarding.md AI section**

Replace the "Prerequisite" block (lines 443-449):

```markdown
**Prerequisite:** a DeepSeek API key:

    # .env
    DEEPSEEK_API_KEY=sk-...

**Enabled by default.** The scheduler pre-warms the cache after each
`refresh_and_recompute`; the dashboard and Telegram read cached prose.

**To disable** (and revert to the deterministic engine's template strings), set:

    # config.yaml
    ai:
      enabled: false

When disabled (or when DeepSeek is unreachable), the dashboard renders the
template string with a small `classic` badge, and the Telegram body uses the
template string. No banner, no broken page.
```

(Remove the `brew install ollama` / `ollama serve` / `ollama pull` block.)

- [ ] **Step 6: Add runbook §10**

Append to `docs/runbook.md`:

```markdown
## §10 — AI prose missing (DeepSeek) 🟡

**Symptom:** Dashboard panes show the `classic` badge instead of `AI` prose; scheduler logs `ai.generate_job_failed`.

### Triage

1. Check the key is set in the container env:
   ```bash
   docker compose exec fpl-autopilot env | grep DEEPSEEK_API_KEY
   ```
   Expected: `DEEPSEEK_API_KEY=sk-...` (value redacted in app logs by design — B7).
2. If missing: add `DEEPSEEK_API_KEY=sk-...` to the host `.env`, then `docker compose up -d` to recreate.
3. If set but still failing: check the key is valid (DeepSeek platform dashboard) and the configured
   `ai.deepseek.model` exists. Watch `logs/` for `ai.*.provider_error` entries.
4. If the key is revoked: create a new one on the platform, update `.env`, restart. Prose keeps
   falling back to templates until then — nothing breaks, the classic badge is the tell.
```

- [ ] **Step 7: Full suite + smoke check**

```bash
.venv/bin/pytest -q
```
Expected: `638 passed` (or 637 — the count drops by exactly the deleted `test_claude_provider.py` tests; verify no other failures).

Smoke: `.venv/bin/fpl-autopilot review --ai none --gw 1` runs without crashing (no settled GWs → "No settled gameweeks" message is fine).

- [ ] **Step 8: Commit**

```bash
git add config.yaml .env.example docker-compose.yml.example docs/risks.md docs/architecture.md docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md docs/onboarding.md docs/runbook.md
git commit -m "docs(ai): switch config + docs to DeepSeek provider; resolve D2"
```

---

## Self-review notes

- **Spec coverage:** provider (T1), factory + config (T2), scheduler/deadguard wiring (T3), Claude removal + review CLI (T4), config/env/docs/deploy note (T5). Spec items "Ollama kept as option" (T2/T5), "usage log audit-only" (T1), "credential scan reused" (T1), "fail-fast with clear log on missing key" (T3 test).
- **Type consistency:** `build_provider(cfg, conn=None)` used identically at all three call sites; `config.ai_deepseek_model(cfg)` label used in scheduler + deadguard; review CLI passes `conn=conn` for audit usage logging.
- **Deploy (out of scope, documented):** jumbo needs `DEEPSEEK_API_KEY` in its `.env` + a pull/restart of the rollover fix — runbook §10 covers the triage side.
