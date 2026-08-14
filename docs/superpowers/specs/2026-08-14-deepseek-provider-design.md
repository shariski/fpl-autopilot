# DeepSeek API provider — design

Replace the local-Ollama AI reasoning stack with the DeepSeek API, using the OpenAI SDK
pointed at a configurable base URL so other OpenAI-compatible providers (Groq, OpenRouter,
etc.) work later with zero code changes. Also drops the Claude audit provider — DeepSeek
becomes the single provider for every LLM consumer.

**Status:** approved 2026-08-14 (brainstorming).
**Resolves:** `docs/risks.md` D2 (LLM choice for Phase 3) → DeepSeek API.
**Amends:** `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md` (provider
decision) and `docs/architecture.md` (AI sub-layer provider line).
**Source of truth:** the 2026-05-26 AI architecture spec for placement/prompt/grounding/cache
rules — this doc only changes the provider + wiring. All rules preserved: AI sub-layer stays
strictly downstream of Decision, strictly upstream of Interface; prompt builder is the sole
egress (B7); no tools, no FPL writes (R3); silent template fallback on failure.

## Why

- The local Ollama stack (qwen2.5:7b) was torn down with the LLM hosting on the VPS
  (`2026-05-27-vps-deployment-design.md`); no local LLM exists on `jumbo`.
- A season-long deployment wants a managed API: no GPU/disk management, one API key, model
  chosen per request (`deepseek-chat` default, `deepseek-reasoner` available without code).
- Provider independence: OpenAI SDK + configurable `base_url` means a future switch
  (Groq, OpenRouter, another OpenAI-compatible host) is config-only.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Provider | **DeepSeek API** (`https://api.deepseek.com/v1`) via the **`openai` Python SDK** (new dependency). One API key (`DEEPSEEK_API_KEY` env) covers all models. |
| Model | `deepseek-chat` (V3) — cheap, fast, ample for 200-token number-grounded pane prose. `deepseek-reasoner` available via config flip. |
| Config shape | `ai.provider: deepseek` (default; `ollama` / `none` still selectable). `ai.deepseek.base_url`, `ai.deepseek.model`. Key **never** in `config.yaml` — env only (B7). |
| Wiring | New `build_provider(cfg, conn)` factory in `src/ai/provider.py`. All three call sites route through it: scheduler AI job (`src/scheduler.py:49`), deadguard late-news (`src/interface/deadguard.py:164`), review/audit CLI (`src/cli.py` `_resolve_audit_provider_choice`). |
| Claude | `ClaudeProvider` deleted, along with its tests and the `anthropic` dependency (unused elsewhere). The review CLI's provider choice becomes `deepseek | none`. |
| Ollama | `OllamaProvider` class **kept** as a selectable config option (already written + tested); `ai.ollama.*` config block retained but unused by default. No removal work. |
| Guardrails | B7 credential-scan regexes (currently Claude-only) reused in `DeepSeekProvider` before send. Per-day audit quota **not** ported (Claude-specific cost guard; DeepSeek cost is low and the cache + grounding check already bound spend). |
| Usage log | `DeepSeekProvider` accepts an optional `conn`; when present (audit path), logs token usage to `activity_log` as `ai.audit` rows (B10). Pane-prose path passes no `conn` — unchanged behavior. |
| Fallback | Unchanged: provider failure → silent template fallback, per-pane `ai`/`classic` source tag, cache-first reads. |

## Provider interface

Unchanged `LLMProvider` Protocol — `generate(prompt, *, max_tokens, temperature) -> str`.
`DeepSeekProvider` implements it with the OpenAI SDK:

```
DeepSeekProvider(api_key: str, *, model: str, base_url: str,
                 timeout_seconds: float, conn: Connection | None = None)
  generate(prompt, *, max_tokens=200, temperature=0.2) -> str
    1. _refuse_credential_prompts(prompt)        # B7: shared regexes, raise before send
    2. client.chat.completions.create(model, [{"role":"user","content":prompt}],
                                      max_tokens, temperature)
    3. wrap SDK errors in DeepSeekError(RuntimeError); non-200 handled by SDK
    4. if conn: log usage (prompt/completion tokens) to activity_log
```

Error taxonomy mirrors `OllamaError`/`ClaudeError`: one `DeepSeekError(RuntimeError)`.
Callers already catch generically (`logger.exception("ai.captain.provider_error", ...)`)
so no caller-side changes are needed beyond construction.

## Wiring

`build_provider(cfg, conn=None)` reads `config.ai_provider(cfg)`:

- `deepseek` → `DeepSeekProvider(os.environ["DEEPSEEK_API_KEY"], model=config.ai_deepseek_model(cfg),
  base_url=config.ai_deepseek_base_url(cfg), timeout_seconds=..., conn=conn)` — missing env key
  raises a clear error naming the missing variable. Callers surface it: the review CLI exits
  non-zero; the scheduler AI job catches it and logs clearly, then honors the existing
  `consecutive_failure_backoff` — a missing key is a loud per-run log line, not a silent
  season-long fallback.
- `ollama` → existing `OllamaProvider` construction (moved into the factory).
- `none` / anything else → `None` (AI job no-ops; dashboard shows classic templates).

Call-site changes:

- `src/scheduler.py:49` — replace inline `OllamaProvider(...)` with factory call. Job already
  swallows exceptions and skips when `ai.enabled` is false.
- `src/interface/deadguard.py:164` — same replacement.
- `src/cli.py` `review` — replace `claude`/`ollama` branch with `deepseek`/`none` via the factory.
- `src/config.py` — add `ai_deepseek_model`, `ai_deepseek_base_url` accessors; change
  `ai_provider()` default from `"ollama"` to `"deepseek"`.

## Config

```yaml
ai:
  enabled: true
  provider: deepseek      # deepseek | ollama | none
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    model: "deepseek-chat"
  timeout_seconds: 60
  consecutive_failure_backoff: 3
  temperature: 0.2
  max_tokens_per_pane: 200
  # ollama: block kept, unused by default
```

`.env.example`: add `DEEPSEEK_API_KEY=`. `docker-compose.yml.example`: pass the env var to
the container. Runbook/onboarding: document the env var + how to switch models.

## Testing

- `DeepSeekProvider.generate` happy path with an injected fake client (SDK `chat.completions`
  is monkeypatchable; tests never make network calls — R3).
- Credential-scan refusal (B7): prompts containing `pl_profile` / `sessionid=` / CSRF patterns
  raise `DeepSeekError` before any client call.
- SDK error → `DeepSeekError` wrapping.
- Usage logging when `conn` is present; absent when it isn't.
- `build_provider` resolution: `deepseek` / `ollama` / `none` / missing-env-key fail-fast.
- Review CLI: `ai.audit.provider: deepseek` resolves; `none` skips narration.
- Existing reasoning/jobs/deadguard/audit tests keep passing with `StubProvider` (unchanged).
- Remove `test_claude_*` / ClaudeProvider tests along with the class.

## Docs (B13, docs-first)

1. `docs/superpowers/specs/2026-08-14-deepseek-provider-design.md` (this doc).
2. `docs/risks.md` — D2 resolved: DeepSeek API, 2026-08-14.
3. `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md` — changelog entry
   (Provider: local Ollama → DeepSeek API via OpenAI SDK; Claude audit provider dropped).
4. `docs/architecture.md` — AI sub-layer line "Provider (Ollama / Claude)" → "Provider (DeepSeek / Ollama fallback)".
5. `docs/runbook.md` + `docs/onboarding.md` — `DEEPSEEK_API_KEY` env var, model switching.
6. `.env.example`, `docker-compose.yml.example`, `config.yaml` — updated.
7. `pyproject.toml` — add `openai`, drop `anthropic` (verify no other importers).

## Deploy note (jumbo)

The daemon on `jumbo` needs `DEEPSEEK_API_KEY` set in its compose environment. The rollover
fix (`8a7f46b`) also needs pulling. Both are operator steps on the host; out of scope for
this slice but documented in runbook.

## Out of scope

- Prompt tuning for DeepSeek (current prompts are model-agnostic; revisit if grounding check
  rejection rate rises).
- Mini-league context, personalization, conversational interface (later Phase 3 slices).
- Removing `OllamaProvider` / `ai.ollama` config (kept as fallback option).
