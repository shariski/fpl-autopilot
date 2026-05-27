# Retrospective Decision Audit Implementation Plan (Phase 3, S-G)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the retrospective decision audit (S-G) end-to-end: outcome backfill, residual
computation, cluster classification, audit assembly, Sonnet 4.6 narration, CLI command, and
dashboard view. Inside B-rules — describe-only.

**Architecture:** Bundles 5 new modules + 1 new table + 1 new provider + 1 new CLI command + 1 new
route. The settlement subsystem (S-G.5) is **part of this slice** — without it, the audit has no
data to reason about.

**Tech Stack:** Same as previous slices. Tests use frozen fixtures + a `StubProvider` for the AI
layer. Sonnet 4.6 is integrated via `anthropic` SDK; real-API smoke test once, mocked thereafter.

**Source spec:** `docs/superpowers/specs/2026-05-27-phase3-s-g-decision-audit-design.md`. **Read it
first.** Companion: `docs/superpowers/specs/2026-05-27-phase3-evaluation-and-feedback-loop-brainstorm.md`.

**B-rule stance:** B4 untouched (no `decision-engine.md` edit; proposals are advisory only). B5
honored (`xp.model_version` segmenting). B7 honored (no creds in Claude payload — explicit
property test). B8 untouched. B10 honored (audit runs themselves logged). **Git hygiene: NEVER
`git add -A`.** Footer: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.

**Execution recommendation:** This is a larger slice than S-A.4. Split execution across 2-3
sessions for sanity:
- **Session A:** T0-T2 (settlement + residuals + audit assembly). Real data flows end-to-end as
  JSON. No AI yet.
- **Session B:** T3-T4 (Sonnet provider + narrator). AI prose layered onto real audit data.
- **Session C:** T5-T6 (CLI + dashboard). User surfaces. T7 verification + push.

Each task ends with a focused commit. Mid-session commits are part of the safety net.

---

## File structure (locked)

**New files:**
- `src/data/settlement.py`
- `src/analytics/residuals.py`
- `src/analytics/clusters.py`
- `src/audit/__init__.py`
- `src/audit/audit.py`
- `src/audit/proposals.py`
- `src/audit/reports.py`
- `src/ai/audit_narrator.py`
- `src/ai/prompts/audit.txt`
- `src/ai/prompts/audit_examples.json`
- `frontend/src/routes/audit/+page.svelte`
- `frontend/src/routes/audit/+page.ts` (data loader)
- `tests/test_settlement.py`
- `tests/test_residuals.py`
- `tests/test_clusters.py`
- `tests/test_audit.py`
- `tests/test_proposals.py`
- `tests/test_claude_provider.py`
- `tests/test_ai_audit_narrator.py`
- `tests/test_ai_prompts_audit.py`
- `tests/test_cli_review.py`
- `tests/test_api_audit.py`
- `frontend/src/routes/audit/+page.test.ts`

**Modified files:**
- `src/data/schema.sql` — add `player_gw_stats` table
- `src/data/fpl_client.py` — add `event_live()` method
- `src/data/repository.py` — add `upsert_player_gw_stats`, `read_player_gw_stats`
- `src/data/db.py` — ensure schema migration runs
- `src/scheduler.py` — wire `settlement_run` into `refresh_and_recompute`
- `src/ai/provider.py` — add `ClaudeProvider` class
- `src/ai/grounding.py` — no change needed; existing `is_grounded` reused
- `src/config.py` — add `ai_audit_*` config accessors
- `src/cli.py` — add `review` subparser + `cmd_review`
- `src/interface/api.py` — add `/api/audit/{gw}` endpoint
- `config.yaml` — add `ai.audit` section
- `pyproject.toml` — add `anthropic` dependency

**No changes to:** any file in `src/decisions/`, any file in `src/execution/`, `docs/decision-engine.md`.

---

## Task 0: Settlement subsystem — `player_gw_stats` + `event_live` + `settlement_run`

**Goal:** Backfill actual GW points for every player in every finished GW. Idempotent. Runs on
hourly refresh. No audit logic yet — just the data substrate.

**Files:**
- Modify: `src/data/schema.sql`
- Modify: `src/data/fpl_client.py`
- Modify: `src/data/repository.py`
- Create: `src/data/settlement.py`
- Modify: `src/scheduler.py`
- Create: `tests/test_settlement.py`

**Steps:**

- [ ] **Read** `src/data/schema.sql` lines 24-37 (player_stats), lines 81-97 (gameweeks), and
  understand the existing patterns.
- [ ] **Read** `src/data/repository.py` for existing `upsert_*` patterns (e.g.
  `upsert_fixtures`). Match style.
- [ ] **Read** `src/data/fpl_client.py::FPLClient` (lines 15-64) for the existing endpoint
  pattern.
- [ ] **Add to `schema.sql`** (append after the existing tables):
  ```sql
  CREATE TABLE IF NOT EXISTS player_gw_stats (
    player_id INTEGER NOT NULL,
    gw INTEGER NOT NULL,
    fixture_id INTEGER NOT NULL,
    minutes INTEGER NOT NULL,
    goals_scored INTEGER NOT NULL,
    assists INTEGER NOT NULL,
    clean_sheets INTEGER NOT NULL,
    bonus INTEGER NOT NULL,
    total_points INTEGER NOT NULL,
    was_substituted_in BOOLEAN,
    settled_at TIMESTAMP NOT NULL,
    PRIMARY KEY (player_id, gw, fixture_id)
  );
  CREATE INDEX IF NOT EXISTS idx_player_gw_stats_gw ON player_gw_stats(gw);
  ```
- [ ] **Write failing tests** in `tests/test_settlement.py`:
  - `test_event_live_returns_expected_shape` — mock FPLClient HTTP, assert it returns dict with
    `elements` key containing per-player stats.
  - `test_settlement_writes_player_gw_stats` — frozen GW + frozen FPL payload → rows written with
    correct schema.
  - `test_settlement_is_idempotent` — running twice writes 0 rows on second run.
  - `test_settlement_only_runs_for_finished_gws` — un-finished GWs are skipped.
  - `test_settlement_handles_dgw` — same (player_id, gw) with two different fixture_ids creates
    two rows.
  - `test_settlement_swallows_per_gw_errors` — one GW raises, the others still settle.
- [ ] **Verify FAIL:** `.venv/bin/pytest tests/test_settlement.py -v` should fail with import or
  missing-symbol errors.
- [ ] **Implement `FPLClient.event_live(event_id)`:**
  ```python
  def event_live(self, event_id: int) -> dict:
      return self._get(f"event/{event_id}/live/")
  ```
- [ ] **Implement `repository.upsert_player_gw_stats(conn, gw, payload)`:**
  - Parses `payload["elements"]` (list of `{id, stats, explain}` per FPL schema).
  - For each element with `stats.minutes > 0`, computes the fixture_id from `explain[0]["fixture"]`
    (FPL schema; verify against a saved fixture).
  - Writes rows with `INSERT OR IGNORE` keyed on `(player_id, gw, fixture_id)`.
  - Returns the count of rows actually written.
- [ ] **Implement `src/data/settlement.py::settlement_run(conn, client)`:**
  - Queries `gameweeks WHERE finished=1 AND id NOT IN (SELECT DISTINCT gw FROM player_gw_stats)`.
  - For each, calls `client.event_live(gw)` and `repository.upsert_player_gw_stats`.
  - Try/except per GW: a failure on GW3 doesn't block GW4.
  - Returns total rows written.
- [ ] **Wire into scheduler** — extend `refresh_and_recompute` in `src/scheduler.py:23` to call
  `settlement.settlement_run(conn, client)` after the existing `xp.compute_and_store` step.
- [ ] **Verify PASS + full suite:**
  ```
  .venv/bin/pytest tests/test_settlement.py -v
  .venv/bin/pytest -q
  ```
- [ ] **Commit:**
  ```
  feat(data): player_gw_stats table + settlement job (S-G task 0)

  Adds the missing outcome backfill: a new player_gw_stats table populated
  from FPL's event/{id}/live/ endpoint, written by settlement_run on every
  hourly refresh. Idempotent: re-running on a settled GW writes 0 rows.
  DGW-aware via (player_id, gw, fixture_id) primary key.

  This is the data substrate for S-G's residual computation. The audit
  itself doesn't ship in this commit.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 1: Residuals + clusters — pure data math

**Goal:** Given a settled GW range, compute residuals per decision and classify them into
clusters. Two pure modules, no I/O beyond reading the DB.

**Files:**
- Create: `src/analytics/residuals.py`
- Create: `src/analytics/clusters.py`
- Create: `tests/test_residuals.py`
- Create: `tests/test_clusters.py`

**Steps:**

- [ ] **Read** the spec §3 (residual formulas) and §4 (cluster taxonomy) again. Mind the
  per-decision-type formulas — they're not all the same shape.
- [ ] **Read** `src/data/repository.py::log_activity` to understand the `inputs_json` shape per
  decision_type. Look at a real `activity_log` row if one exists in dev DB.
- [ ] **Write failing tests** in `tests/test_residuals.py`:
  - `test_residual_captain_one_subject` — single captain decision, frozen inputs, frozen
    player_gw_stats → expected residual.
  - `test_residual_transfer_in_minus_out_minus_hit` — transfer with hit_cost=4 → residual
    formula correctness.
  - `test_residual_deadguard_aggregates_captain_bench_transfer` — multi-subject case.
  - `test_residual_skips_unsettled_decisions` — decision exists but player_gw_stats row missing →
    skip, don't error.
  - `test_residual_dgw_sums_fixture_rows` — two player_gw_stats rows for the same (player, gw) →
    summed.
  - `test_residual_segments_by_model_version` — decisions from different `xp.model_version` are
    tagged separately.
- [ ] **Write failing tests** in `tests/test_clusters.py`:
  - `test_classify_late_injury` — frozen residual + context with `status_changed_within_h6=True`
    + `residual <= -2` → `'late_injury'`.
  - `test_classify_rotation_miss` — status_at_decision='a', actual_minutes=30, xMinutes=80,
    residual=-3 → `'rotation_miss'`.
  - `test_classify_xp_model_miss` — status='a', minutes=75, residual=-4 → `'xp_model_miss'`.
  - `test_classify_lucky_outperform` — residual=+6 → `'lucky_outperform'`.
  - `test_classify_unclassified` — residual=-1 (small), nothing else matches → `'unclassified'`.
  - **Property test:** `test_classify_is_total_and_exclusive` — for a random sample of
    (residual, context) inputs, exactly one cluster is returned.
- [ ] **Verify FAIL:** `.venv/bin/pytest tests/test_residuals.py tests/test_clusters.py -v`.
- [ ] **Implement `src/analytics/residuals.py`:**
  - `@dataclass Residual` as specified in §3.
  - `compute_residuals(conn, gw_lo, gw_hi) -> list[Residual]` per spec §3.
  - Per-decision-type private helpers: `_residual_captain`, `_residual_transfer`,
    `_residual_deadguard`. Skip `bench` for v1 (covered by deadguard).
  - All helpers are pure given DB state.
- [ ] **Implement `src/analytics/clusters.py`:**
  - `classify(residual: Residual, context: dict) -> str` returning one of the 6 cluster strings.
  - Cluster conditions evaluated in the documented order (early-match wins).
  - No DB access — `context` is passed in by the caller.
- [ ] **Verify PASS + full suite.**
- [ ] **Commit:**
  ```
  feat(analytics): residual computation + cluster classification (S-G task 1)

  Pure functions for computing actual-vs-expected residuals per decision
  and classifying them into 5 clusters (+ unclassified). No DB writes, no
  external calls. Read-only over activity_log + xp + player_gw_stats.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 2: Audit assembly — `AuditReport`, `run_audit`, proposals, persistence

**Goal:** Orchestrate residuals + clusters into a serializable `AuditReport`. Compute aggregate
trends + statistical CIs. Generate advisory `Proposal` objects. Persist to disk.

**Files:**
- Create: `src/audit/__init__.py`
- Create: `src/audit/audit.py`
- Create: `src/audit/proposals.py`
- Create: `src/audit/reports.py`
- Create: `tests/test_audit.py`
- Create: `tests/test_proposals.py`

**Steps:**

- [ ] **Read** spec §5 for the `AuditReport` / `AggregateStat` / `Proposal` dataclass shapes.
- [ ] **Write failing tests** in `tests/test_audit.py`:
  - `test_run_audit_with_no_decisions_returns_empty_report` — empty `activity_log` → valid empty
    report.
  - `test_run_audit_aggregates_by_decision_type_and_cluster` — frozen fixtures → expected
    aggregate counts.
  - `test_aggregate_stat_mean_and_ci` — n=10 fixed residuals → known mean, known 95% CI.
  - `test_run_audit_persists_to_disk` — calls `reports.persist(report)`, file exists at
    `data/audit/audit_{gw_lo}_{gw_hi}_{ts}.json`.
  - `test_reports_load_round_trip` — persist then load returns equivalent object.
  - `test_audit_logs_its_own_run_to_activity_log` — after `run_audit`, an activity_log row exists
    with `decision_type='audit'`.
- [ ] **Write failing tests** in `tests/test_proposals.py`:
  - `test_propose_higher_transfer_threshold_when_transfers_underperform` — fixture: 22 transfers,
    mean residual -0.7, lower CI < 0 → emit Proposal raising
    `thresholds.min_ep_delta_for_transfer`.
  - `test_no_proposal_when_n_below_threshold` — only 5 transfers → no proposal regardless of
    residual.
  - `test_no_proposal_when_ci_crosses_zero` — n=20 but 95% CI spans 0 → no proposal.
  - `test_proposal_includes_confidence_label` — high/medium/low based on CI tightness.
- [ ] **Verify FAIL.**
- [ ] **Implement `src/audit/audit.py::run_audit(conn, gw_lo, gw_hi, *, ai_provider=None)`:**
  - Calls `compute_residuals(conn, gw_lo, gw_hi)`.
  - For each residual, builds `context` dict from DB (status_at_decision from
    `activity_log.inputs_json`, status_changed timing from `players.status_changed_at` if
    available — else None and cluster falls through).
  - Calls `clusters.classify(r, context)` for each.
  - Aggregates per `(decision_type, cluster, model_version)`.
  - Computes `AggregateStat` with mean/std/CI (use `scipy.stats` if already a dep, otherwise
    manual formula — verify pyproject.toml first).
  - Calls `proposals.propose_threshold_adjustments(aggregates)`.
  - If `ai_provider` not None, calls `audit_narrator.generate_audit_narrative(report, provider)`
    and attaches `narrative` + `narrative_provider` — **deferred to T4 implementation**, just leave a
    NotImplementedError stub for now (audit can run without narrator).
  - Calls `reports.persist(report)`.
  - Logs to `activity_log` with `decision_type='audit'`.
  - Returns the report.
- [ ] **Implement `src/audit/proposals.py`:**
  - `propose_threshold_adjustments(aggregates: dict) -> list[Proposal]`.
  - For each known auto-tunable parameter, look at the relevant aggregate stat. If
    `n >= 20` AND CI does not cross zero AND mean residual is meaningfully nonzero → emit
    proposal.
  - **v1 covers `thresholds.min_ep_delta_for_transfer` only.** Other parameters are listed in
    the spec table but emit no proposals at v1 (extend in later slices).
- [ ] **Implement `src/audit/reports.py`:**
  - `persist(report) -> Path` writes JSON to `data/audit/`.
  - `load(path) -> AuditReport` round-trips.
  - `format_text(report) -> str` produces the CLI-friendly text output (used in T5).
- [ ] **Verify PASS + full suite.**
- [ ] **Commit:**
  ```
  feat(audit): run_audit + AuditReport + advisory proposals (S-G task 2)

  Orchestrates residuals + clusters into a serializable report. Computes
  aggregate trends with 95% confidence intervals. Emits advisory Proposal
  objects for the single auto-tunable parameter currently in scope
  (transfer EP delta threshold). Persists to data/audit/. Logs each audit
  run itself to activity_log.

  Proposals are advisory only — never applied. Application is S-H's job
  gated on a future B15 amendment.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 3: Sonnet 4.6 provider — `ClaudeProvider`

**Goal:** Add a Claude API provider alongside Ollama. Same `complete(prompt) -> str` interface.
Cost guardrails. Privacy property test.

**Files:**
- Modify: `pyproject.toml` (add `anthropic` dep)
- Modify: `src/ai/provider.py` (add `ClaudeProvider`)
- Modify: `src/config.py` (add `ai_audit_*` accessors)
- Modify: `config.yaml` (add `ai.audit.*` section)
- Create: `tests/test_claude_provider.py`

**Steps:**

- [ ] **Read** `src/ai/provider.py::OllamaProvider` to match interface and error-handling style.
- [ ] **Read** `src/config.py::ai_*` accessors to match config-reading style.
- [ ] **Add `anthropic`** to `pyproject.toml` dependencies. Run `.venv/bin/pip install -e .` to
  install.
- [ ] **Add to `config.yaml`** (the `ai.audit` block per spec §9). Default `provider: claude` if
  user wants Claude; can override per-run via CLI.
- [ ] **Add to `src/config.py`** accessors:
  - `ai_audit_enabled(cfg) -> bool`
  - `ai_audit_provider(cfg) -> str` (`'claude'` | `'ollama'` | `'none'`)
  - `ai_audit_claude_model(cfg) -> str`
  - `ai_audit_claude_api_key_env(cfg) -> str`
  - `ai_audit_max_calls_per_day(cfg) -> int`
  - `ai_audit_estimated_cost_usd(cfg) -> float`
- [ ] **Write failing tests** in `tests/test_claude_provider.py`:
  - `test_claude_provider_calls_api_with_prompt` — mock anthropic.Anthropic.messages.create →
    assert prompt passed.
  - `test_claude_provider_returns_completion_text` — mock returns a response → provider returns
    the text.
  - `test_claude_provider_raises_on_api_error` — anthropic raises → `ClaudeError`.
  - `test_claude_provider_no_credential_pattern_in_prompt` — property test: any prompt sent to
    `complete()` doesn't contain cookie-shaped or session-token-shaped strings.
  - `test_claude_provider_respects_max_calls_per_day` — 6th call in 24h raises
    `ClaudeRateLimitError`.
  - `test_claude_provider_logs_token_usage_to_activity_log` — after a successful call, an
    activity_log row exists with `decision_type='ai.audit'` and the token counts in
    `inputs_json`.
- [ ] **Verify FAIL.**
- [ ] **Implement `ClaudeProvider`** in `src/ai/provider.py`:
  ```python
  class ClaudeError(Exception): ...
  class ClaudeRateLimitError(ClaudeError): ...

  class ClaudeProvider:
      def __init__(self, *, api_key, conn, model="claude-sonnet-4-6",
                   timeout_seconds=60, max_tokens=1500, max_calls_per_day=5):
          self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
          ...

      def complete(self, prompt: str) -> str:
          # 1. Privacy check: assert no credential-shaped pattern in prompt
          # 2. Quota check: count today's ai.audit rows in activity_log; raise if exceeded
          # 3. Call self._client.messages.create(...)
          # 4. Log usage to activity_log
          # 5. Return text
  ```
- [ ] **Verify PASS + full suite.**
- [ ] **Commit:**
  ```
  feat(ai): ClaudeProvider for Sonnet 4.6 audit narration (S-G task 3)

  Adds a hosted-API LLM provider alongside the local OllamaProvider. Same
  complete(prompt) interface. Per-feature opt-in via ai.audit.provider
  config. Cost guardrails (max calls/day, surfaced cost estimate). Privacy
  property test enforces no credential patterns in prompts.

  API key never persisted — read from env var at construction.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 4: Audit narrator + prompt + exemplars

**Goal:** Layer Sonnet narration onto an `AuditReport`. Same plumbing pattern as the S-A
narrators.

**Files:**
- Create: `src/ai/prompts/audit.txt`
- Create: `src/ai/prompts/audit_examples.json` (2-3 exemplars per spec §6)
- Create: `src/ai/audit_narrator.py`
- Create: `tests/test_ai_prompts_audit.py`
- Create: `tests/test_ai_audit_narrator.py`
- Modify: `src/audit/audit.py` — replace the NotImplementedError stub from T2 with a real call
  to `audit_narrator.generate_audit_narrative`.

**Steps:**

- [ ] **Read** spec §6 (prompt + privacy boundary).
- [ ] **Read** `src/ai/prompts/deadguard.txt` + `deadguard_examples.json` as the most recent
  reference exemplar set.
- [ ] **Write failing tests** in `tests/test_ai_prompts_audit.py`:
  - `test_audit_template_has_placeholders` — `{examples}` + `{payload_json}` in template.
  - `test_audit_examples_self_validating` — every numeric token in each output appears in its
    input.
  - `test_audit_examples_cover_three_scenarios` — at least one "everything calibrated", one
    "threshold needs raising", one "DGW bias" exemplar.
- [ ] **Write failing tests** in `tests/test_ai_audit_narrator.py`:
  - `test_build_audit_payload_closed_shape` — given an AuditReport, payload contains only the
    expected fields (no extras, no `null`-only fields, no credential-shaped strings).
  - `test_build_audit_prompt_includes_payload_and_examples` — prompt has report values + at
    least one exemplar's content.
  - `test_generate_audit_narrative_skips_empty_report` — `n_total = 0` → no LLM call, returns
    None.
  - `test_generate_audit_narrative_caches_by_report_hash` — same report → cache hit, no second
    LLM call.
  - `test_generate_audit_narrative_rejects_ungrounded_numbers` — provider returns prose with a
    number not in payload → narrator rejects and returns None (logs to grounding-failures).
  - `test_generate_audit_narrative_attaches_provider_id` — successful call attaches
    `narrative_provider='claude'` (or whichever) to the returned report.
- [ ] **Verify FAIL.**
- [ ] **Create** `src/ai/prompts/audit.txt` per spec §6.
- [ ] **Create** `src/ai/prompts/audit_examples.json` with 2-3 self-validating exemplars.
- [ ] **Implement `src/ai/audit_narrator.py`:**
  - `_build_audit_payload(report) -> dict` — closed shape: cluster counts, aggregate stats per
    decision_type, proposals (parameter/current/proposed/justification), top-N residuals by
    magnitude.
  - `_build_audit_prompt(payload) -> str` — interpolates template.
  - `_report_hash(report) -> str` — for caching, hash the canonical payload.
  - `render_audit_narrative(conn, report) -> tuple[str, str] | tuple[None, None]` — read path
    from `ai_reasoning_cache` with `pane_type='audit'`.
  - `generate_audit_narrative(conn, report, *, provider, model_id) -> bool` — write path: skips
    empty, skips on cache hit, calls provider, grounds, caches on success.
- [ ] **Wire into `src/audit/audit.py::run_audit`** — replace the T2 stub with a real call. Same
  guard: if `ai_provider is None`, skip narration.
- [ ] **Verify PASS + full suite.**
- [ ] **Commit:**
  ```
  feat(ai): audit narrator + Sonnet prompt + 3 exemplars (S-G task 4)

  Layers AI prose onto AuditReport via the same prompt+payload+render+
  generate pattern as the S-A narrators. New pane_type='audit' in
  ai_reasoning_cache. Provider-agnostic — uses ClaudeProvider when
  ai.audit.provider=claude, OllamaProvider when =ollama. Grounding check
  rejects narrative that invents numbers.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 5: CLI `review` subcommand

**Goal:** Surface the audit via CLI. `fpl-autopilot review --gw N` or `--last N`.

**Files:**
- Modify: `src/cli.py`
- Create: `tests/test_cli_review.py`

**Steps:**

- [ ] **Read** `src/cli.py` for existing subparser patterns. Match style.
- [ ] **Read** `src/audit/reports.py::format_text` (built in T2) — that's what `review` outputs.
- [ ] **Write failing tests** in `tests/test_cli_review.py`:
  - `test_review_default_runs_last_4_gws` — invokes `cmd_review` with default args, asserts
    `run_audit` called with the expected gw range.
  - `test_review_gw_argument` — `--gw 3` → audit only GW3.
  - `test_review_last_argument` — `--last 2` → audit the last 2 settled GWs.
  - `test_review_format_json` — `--format json` → JSON output.
  - `test_review_ai_override_none` — `--ai none` → no narrator called.
  - `test_review_handles_no_settled_gws` — empty `gameweeks` → graceful "no settled GWs" message,
    not a stack trace.
- [ ] **Verify FAIL.**
- [ ] **Implement `cmd_review`** in `src/cli.py`.
- [ ] **Add subparser:** `sub.add_parser("review", help="audit past decisions vs outcomes")` with
  `--gw`, `--last`, `--ai`, `--format` args.
- [ ] **Verify PASS + full suite.**
- [ ] **Commit:**
  ```
  feat(cli): fpl-autopilot review subcommand (S-G task 5)

  CLI surface for the retrospective audit. --gw N / --last N selects the
  window. --ai overrides provider for the run. --format json|text. Outputs
  via reports.format_text() or json.dumps() of the AuditReport.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 6: API endpoint + dashboard `/audit` route

**Goal:** Surface the audit on the dashboard.

**Files:**
- Modify: `src/interface/api.py` (new `/api/audit/{gw}` route)
- Create: `frontend/src/routes/audit/+page.svelte`
- Create: `frontend/src/routes/audit/+page.ts` (server data loader)
- Create: `tests/test_api_audit.py`
- Create: `frontend/src/routes/audit/+page.test.ts`

**Steps:**

- [ ] **Read** `src/interface/api.py` for existing route patterns.
- [ ] **Read** an existing dashboard route (e.g. captain or transfer view) for Svelte patterns
  + the data-loader convention.
- [ ] **Write failing tests** in `tests/test_api_audit.py`:
  - `test_audit_endpoint_returns_404_when_no_audit_persisted` — empty `data/audit/` → 404.
  - `test_audit_endpoint_returns_latest_for_gw` — frozen file in `data/audit/` → returns the
    parsed report.
  - `test_audit_endpoint_serializes_correctly` — all fields present in JSON response.
- [ ] **Write failing vitest** in `frontend/src/routes/audit/+page.test.ts`:
  - Renders cluster counts.
  - Renders residuals table.
  - Renders narrative section when present, empty-state when missing.
  - Proposal rows render with "I'll consider" / "Dismiss" buttons (no-op at S-G — clicks just
    log).
- [ ] **Verify FAIL** for both backend + frontend tests.
- [ ] **Implement `/api/audit/{gw}`** in `src/interface/api.py`:
  - Reads the most recent `data/audit/audit_*_{gw}_*.json` matching the requested gw.
  - Returns the report JSON.
- [ ] **Implement `+page.ts` data loader** — fetches `/api/audit/{currentGw}`.
- [ ] **Implement `+page.svelte`:**
  - Top: gw_range, model_version, cluster_counts (simple bar chart or table).
  - Middle: residuals table.
  - Bottom: narrative + proposals.
- [ ] **Verify PASS:**
  ```
  .venv/bin/pytest -q
  cd frontend && npm test
  ```
- [ ] **Smoke-test manually:** run `fpl-autopilot review` to generate a report, then `serve` + `npm
  run dev` and open `/audit` in browser.
- [ ] **Commit:**
  ```
  feat(audit): /api/audit endpoint + /audit dashboard view (S-G task 6)

  Frontend surface for the retrospective audit. /api/audit/{gw} returns the
  persisted report; the /audit route renders cluster counts, residual table,
  narrative, and proposals. Proposal action buttons are no-ops at S-G — they
  log clicks to activity_log for future S-H consumption.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```

---

## Task 7: Verification + final review + finishing + push

- [ ] **Step 1: Full pytest:**
  ```
  .venv/bin/pytest -q
  ```
  Expected: 556 (current baseline) + however many new tests this slice adds.

- [ ] **Step 2: Vitest:**
  ```
  cd frontend && npm test
  ```
  Expected: 62 + new audit-page tests.

- [ ] **Step 3: Confirm no `decision-engine.md` change:**
  ```
  git log --name-only main..HEAD | grep -i decision-engine && echo "FAIL" || echo "OK"
  ```

- [ ] **Step 4: Dispatch final code review** via the orchestrator (sonnet):

  Brief:
  > Review cumulative diff of feat/phase3-sg-audit vs main. S-G = retrospective decision audit.
  > Larger surface than S-A.4: new table, new provider, new analytics modules, new CLI command,
  > new dashboard route.
  >
  > Focus:
  > - **Critical safety check:** ClaudeProvider must enforce the privacy property test (no
  >   credential-shaped strings in prompts) at every call. Confirm this is wired correctly.
  > - **Critical safety check:** S-G never applies a Proposal automatically. The Proposal
  >   dataclass + UI buttons must be no-ops. Confirm no path from the audit to a config write.
  > - **B5 compliance:** residuals correctly segment by xp.model_version (so v1 vs future-v2
  >   comparison works).
  > - **DGW handling:** verify residual sums correctly across multiple player_gw_stats rows for
  >   the same (player, gw).
  > - **Idempotence of settlement:** re-running settlement_run for a settled GW writes 0 rows
  >   and doesn't error.
  > - **Cost guardrails:** ClaudeProvider's max_calls_per_day actually blocks (not just warns).
  >
  > Report Critical/Important/Minor.

  Apply blocking findings as focused fix commits; re-run tests.

- [ ] **Step 5: Invoke `superpowers:finishing-a-development-branch`.**
  Option 1 (merge to main locally + push). Likely a clean fast-forward.

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| `player_gw_stats` table + schema migration | T0 |
| `FPLClient.event_live()` | T0 |
| `settlement_run` wired into hourly refresh | T0 |
| Idempotent settlement | T0 |
| DGW summing in residual computation | T1 |
| `compute_residuals` covering captain/transfer/deadguard | T1 |
| Cluster classification (5 + unclassified) | T1 |
| Property test: classifier is total + exclusive | T1 |
| `AuditReport` dataclass + serialization | T2 |
| `AggregateStat` with mean + 95% CI | T2 |
| `Proposal` (advisory) + threshold logic | T2 |
| `data/audit/` persistence + round-trip | T2 |
| Audit run logs itself to activity_log | T2 |
| `ClaudeProvider` with `complete(prompt) -> str` | T3 |
| Cost guardrails (`max_calls_per_day`) | T3 |
| Privacy property test (no creds in prompts) | T3 |
| Token usage logged to activity_log | T3 |
| Audit prompt + 3 self-validating exemplars | T4 |
| `audit_narrator` (payload + render + generate) | T4 |
| Grounding check on audit narrative | T4 |
| `fpl-autopilot review` CLI command | T5 |
| `/api/audit/{gw}` endpoint | T6 |
| `/audit` dashboard route | T6 |
| Full suite green + B-rules preserved | T7 |
| Final code review | T7 |
| Finishing a development branch + push | T7 |
| **`decision-engine.md` unchanged** | Verified by T7 step 3 |
| **No auto-application of proposals** | Verified by code review focus area |

Every spec requirement maps to at least one task. ✓

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `event/{id}/live/` schema unknown — FPL has no public docs | T0 step 1: manually inspect one real response before implementing parser. Schema-assertion tests per B6. |
| Sonnet API key handling — leak risk | Env var only, never persisted. Privacy property test in T3. |
| Sonnet cost runaway | T3 max_calls_per_day guard. T3 token usage logged. |
| Audit prose hallucinates numbers | Existing grounding check in T4. Reject + log. Audit ships without narrative if grounding fails (deterministic findings still surface). |
| Sonnet API outage breaks audit | `--ai none` flag (T5). Provider-agnostic narrator. Deterministic findings always surface regardless of AI. |
| DGW residual bias from xp.py limitation | Flagged in spec §3 + cluster taxonomy includes "DGW-induced". Future xP v2 will close this. |
| Activity log doesn't have status_changed_at for cluster context | Cluster falls through to `unclassified`. Audit still ships. v2 enrichment can backfill the column. |

---

## Definition of done (CLAUDE.md B14)

- All 7 tasks committed.
- Full pytest green. Vitest green.
- `decision-engine.md` unchanged.
- `fpl-autopilot review --last 4` produces text output on a real DB.
- `/audit` dashboard view renders a real persisted report.
- ClaudeProvider verified once against the real Anthropic API (manual smoke test).
- Final code review applied.
- Branch merged to main + pushed.
- The next-slice unblockers (S-H, xP v2, S-D, S-E) are now feasible because S-G's residuals
  + proposals exist as a substrate.
