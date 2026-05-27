# Retrospective Decision Audit — Design (Phase 3, S-G)

**Status:** draft 2026-05-27
**Slice:** Phase 3 S-G — adds **retrospective evaluation of past decisions** by computing
residuals (`actual_points - expected_xp`) and surfacing them through a deterministic audit reader
plus a Sonnet 4.6 narration layer. Inside B-rules: purely descriptive, no decision-logic changes.
**Depends on:**
- **S-G.5 (this same slice — see §1)**: builds the missing `player_gw_stats` settlement job that
  backfills actual points per (player, gw) from the FPL `event/{event_id}/live/` endpoint.
  Without this, residuals cannot be computed. The settlement job is bundled into S-G because the
  audit literally cannot exist without it.
- Phase-3 S-A architecture (`src/ai/{provider,reasoning,cache,grounding}`, `ai_reasoning_cache`
  table) — reused for the narrator. **New addition: Sonnet 4.6 provider** alongside the existing
  Ollama provider. See §6.
- The `activity_log` table (already populated by every decision per B10).
- The `xp` table (already populated by every refresh, model_version-tagged per B5).
**Cross-cutting design (reused, not re-derived):**
[`2026-05-26-phase3-ai-architecture-design.md`](./2026-05-26-phase3-ai-architecture-design.md),
[`2026-05-27-phase3-evaluation-and-feedback-loop-brainstorm.md`](./2026-05-27-phase3-evaluation-and-feedback-loop-brainstorm.md).
**Source of truth for this slice:** this doc. **`docs/decision-engine.md` is NOT touched** —
audit describes past decisions, doesn't change how future ones are made. B4 untouched.

## Goal

After each settled gameweek, the user can run `fpl-autopilot review --gw N` (or `--last 4`) and
get a structured audit report covering:

- Which decisions hit, which missed, by how much (residual).
- Likely causes for misses, categorized by cluster (late injury, rotation, xP model miss, FDR
  miss, lucky outperform).
- Statistical trends over the lookback window: is the xP model running hot/cold for any class of
  decision? Are transfer EP-delta thresholds well-calibrated?
- A Sonnet 4.6-generated narrative summary explaining the findings in plain English.
- *Proposed* threshold adjustments where data supports them. **S-G proposes, does not apply** —
  applying belongs to a future S-H slice gated on a CLAUDE.md B15 amendment.

This is the **observer** half of the closed-loop feedback architecture described in the
companion brainstorm doc. S-H is the *actor* half and is out of scope here.

## Decision matrix

| Decision | Choice |
|----------|--------|
| Outcome backfill location | **New table `player_gw_stats`** (replaces the unused FPL-source rows in `player_stats`). Cleaner schema, no overload of an existing table. See §1. |
| Settlement trigger | **Hourly refresh job already in scheduler** invokes a new `settlement_run(conn, client)` step after the existing recompute. Runs in O(1) extra API calls per GW (one `event/{gw}/live/` call). See §1. |
| Residual definition | `actual_points - expected_xp` per decision-subject. For captain pick: actual_captain_points (post-multiplier) - xp_captain × 2. For transfer: actual_in - actual_out - hit_cost. For bench: position-weighted. See §3. |
| Cluster taxonomy | 5 fixed clusters at v1: `late_injury`, `rotation_miss`, `xp_model_miss`, `fdr_miss`, `lucky_outperform`. Audit may also report `unclassified`. Cluster expansion is a follow-up. See §4. |
| Statistical significance | One-sample mean test against zero residual. **Surfaced as "N=X observations, mean=Y, 95% CI=[a,b]"** in the report; no p-value gatekeeping at S-G (that's S-H's job). |
| LLM provider | **Sonnet 4.6** via Claude API for audit narration only. Ollama remains for per-pane S-A prose. Per-feature opt-in to hosted provider, gated by a new config flag `ai.audit.provider: claude` and an `ANTHROPIC_API_KEY` env var. |
| Privacy boundary | Audit payload sent to Sonnet contains: residuals (numbers), player names, decision context, cluster tags. **No cookies, no /my-team raw responses, no mini-league data.** Names of the user's own picks are fine — same level of disclosure as any FPL public dashboard. |
| Surfaces | CLI command (`fpl-autopilot review`), JSON file dump (`data/audit/audit_{gw}.json`), and a new dashboard "Audit" tab. Telegram surfacing is out of scope for S-G v1 (push that to S-D when personalization lands). |
| Caching | The audit JSON file is the cache. Same `(gw, window_size)` → same audit. Sonnet narration is cached in `ai_reasoning_cache` with `pane_type='audit'`. |
| B4 | **Untouched.** Deterministic engine still picks. Audit reads past `activity_log` rows + new `player_gw_stats`. No threshold change, no model change. |
| B5 | **xP versioning honored.** Audit reads `xp.model_version` and segments residuals by version. xP v1 vs (future) v2 can be compared directly. |
| B7 | **Honored.** No creds in Sonnet payload. Only structured numbers + names. |
| B8 | **Untouched.** Deadguard logic unchanged. |
| B11 | Audit reader has deterministic tests with frozen `activity_log` + `player_gw_stats` fixtures. Sonnet output is grounded against the same `is_grounded` check used by S-A. |

## Architecture (delta from S-A.4)

```
src/data/schema.sql                       ← ADD: player_gw_stats table
src/data/fpl_client.py                    ← ADD: event_live(event_id) → live event payload
src/data/repository.py                    ← ADD: upsert_player_gw_stats, read_player_gw_stats
src/data/settlement.py                    ← NEW: settlement_run(conn, client) — per-GW backfill
src/scheduler.py                          ← EXTEND: hourly_refresh also runs settlement_run
src/analytics/residuals.py                ← NEW: compute_residuals(conn, gw_range) → list[Residual]
src/analytics/clusters.py                 ← NEW: classify(residual, context) → cluster_id
src/audit/                                ← NEW package
src/audit/audit.py                        ←   run_audit(conn, gw_range) → AuditReport
src/audit/proposals.py                    ←   propose_threshold_adjustments(audit) → list[Proposal]
src/audit/reports.py                      ←   serialize / load / persist to data/audit/
src/ai/provider.py                        ← EXTEND: ClaudeProvider class (Sonnet 4.6) alongside OllamaProvider
src/ai/audit_narrator.py                  ← NEW: build_audit_payload + generate_audit_narrative
src/ai/prompts/audit.txt                  ← NEW: prompt template
src/ai/prompts/audit_examples.json        ← NEW: 2-3 few-shot exemplars
src/cli.py                                ← ADD: review subcommand
src/interface/api.py                      ← ADD: /api/audit/{gw} endpoint
frontend/src/routes/audit/+page.svelte    ← NEW: audit view
config.yaml                               ← ADD: ai.audit.* section
docs/superpowers/plans/2026-05-27-phase3-s-g-...md  ← writing-plans implementation plan
tests/test_settlement.py                  ← NEW
tests/test_residuals.py                   ← NEW
tests/test_clusters.py                    ← NEW
tests/test_audit.py                       ← NEW
tests/test_ai_audit_narrator.py           ← NEW
tests/test_claude_provider.py             ← NEW
```

Five logical concerns, separated into modules so they're independently testable:
1. **Settlement** (`src/data/settlement.py`) — backfill actual points.
2. **Residual computation** (`src/analytics/residuals.py`) — pure data math.
3. **Cluster classification** (`src/analytics/clusters.py`) — pure categorization.
4. **Audit assembly** (`src/audit/`) — runs the above, builds report objects.
5. **Narration** (`src/ai/audit_narrator.py`) — feeds Sonnet, returns prose.

## §1 Outcome backfill: `player_gw_stats` + settlement job

The single biggest gap in the current codebase: **`player_stats.total_points` is defined in the
schema but nothing writes to it.** Without actual points, residuals are uncomputable. S-G cannot
exist without this.

Rather than retroactively populate the existing `player_stats` table (which has FPL/Understat
source overloading), introduce a clean new table:

```sql
CREATE TABLE IF NOT EXISTS player_gw_stats (
  player_id INTEGER NOT NULL,
  gw INTEGER NOT NULL,
  minutes INTEGER NOT NULL,
  goals_scored INTEGER NOT NULL,
  assists INTEGER NOT NULL,
  clean_sheets INTEGER NOT NULL,
  bonus INTEGER NOT NULL,
  total_points INTEGER NOT NULL,         -- The number we need
  was_substituted_in BOOLEAN,             -- distinguishes started-then-came-off vs benched-then-on
  fixture_id INTEGER,                     -- for DGW disambiguation
  settled_at TIMESTAMP NOT NULL,
  PRIMARY KEY (player_id, gw, fixture_id)
);
```

**FPL endpoint:** `event/{event_id}/live/` returns every player's stats for that GW in one
response. Add to `FPLClient`:

```python
def event_live(self, event_id: int) -> dict:
    return self._get(f"event/{event_id}/live/")
```

**`src/data/settlement.py`:**

```python
def settlement_run(conn, client) -> int:
    """For each finished GW that hasn't been settled yet, fetch live data and write player_gw_stats.
    Returns the number of (player, gw) rows written.

    Idempotent: re-running for an already-settled GW is a no-op (PRIMARY KEY conflict is silently
    ignored via INSERT OR IGNORE). Failures don't crash the caller — logged + counted.
    """
    finished_gws = conn.execute(
        "SELECT id FROM gameweeks WHERE finished=1 AND id NOT IN "
        "(SELECT DISTINCT gw FROM player_gw_stats)"
    ).fetchall()
    written = 0
    for row in finished_gws:
        try:
            payload = client.event_live(row["id"])
            written += repository.upsert_player_gw_stats(conn, row["id"], payload)
        except Exception:
            log.exception(f"settlement failed for gw={row['id']}")
    return written
```

**`src/scheduler.py`** — extend `refresh_and_recompute` to also run settlement after the regular
recompute. No new scheduler job; settlement piggybacks on the hourly refresh. Cheap (1 API call
per unsettled GW, almost always 0 calls in steady state).

**Why not reuse `player_stats`?** Three reasons:
1. `player_stats` is keyed by `(player_id, gw, source)` with `source` being `"fpl"` or
   `"understat"`. Reusing it would require a third source value (`"fpl_live"`) and risk
   shadowing existing data.
2. Schema fields don't line up — `player_stats` lacks `bonus`, `was_substituted_in`, `fixture_id`.
3. A clean table makes the audit query path trivial: `JOIN player_gw_stats ON player_id, gw`.

**S-G.5 is, in practice, this whole §1.** It's a sub-slice in name only — separating it as a
distinct slice would mean shipping a settlement job with no consumer, which violates the project's
"no half-finished implementations" rule (CLAUDE.md system prompt).

## §2 Activity log shape (no schema change required)

The existing `activity_log` schema (per `src/data/schema.sql:98`) is sufficient:

```sql
CREATE TABLE activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TIMESTAMP,
  gw INTEGER,
  mode TEXT,
  decision_type TEXT,     -- 'captain' | 'transfer' | 'bench' | 'chip' | 'deadguard' | 'lineup'
  action_taken TEXT,
  inputs_json TEXT,       -- xP values, FDR, alternatives, confidence — already captured per B10
  alternatives_json TEXT,
  executed BOOLEAN,
  exec_outcome_json TEXT  -- HTTP status / request body — NOT the GW outcome
);
```

The `inputs_json` field already contains every input the audit needs:
- For captain: `{"pick": {...captain pick with xP...}, "alternatives": [...top-5...]}`
- For transfer: `{"buy": {...}, "sell": {...}, "ep_delta": ...}`
- For deadguard: `{"pick": {...}}`

**No schema migration.** The audit reads existing `inputs_json` and parses the relevant subjects.

## §3 Residual computation — `src/analytics/residuals.py`

```python
@dataclass
class Residual:
    activity_log_id: int
    gw: int
    decision_type: str          # 'captain' | 'transfer' | 'bench' | 'chip'
    subject_player_ids: list[int]  # one or more depending on decision_type
    expected_points: float       # from inputs_json + xp table
    actual_points: float          # from player_gw_stats
    residual: float               # actual - expected
    model_version: str            # xp version at decision time
    inputs_summary: dict          # frozen for traceability


def compute_residuals(conn, gw_lo: int, gw_hi: int) -> list[Residual]:
    """For all decisions in [gw_lo, gw_hi], compute the residual against actual outcomes.

    Skips decisions whose subject is not yet settled (player_gw_stats row missing).
    """
```

**Per-decision-type formulas:**

| Decision type | Expected | Actual |
|---|---|---|
| `captain` | `xp_captain × 2` | `actual_captain_points × 2` (multiplier from FPL is already baked into the *captain pick*; we double the player's raw GW points to get captain-effective points) |
| `transfer` (one in, one out) | `(xp_in - xp_out) × horizon_multiplier - hit_cost` | `(actual_in - actual_out) - hit_cost` (single-GW window for v1; multi-GW horizon comparison is a follow-up) |
| `bench` (auto-sub-flagged scenario only) | `xp_sub_in` (replacement) | `actual_sub_in` |
| `chip` (flagged only, not executed at S-A scope) | flag-yes residual = (engine recommended chip, user took it or didn't): comparison vs. counterfactual is out of scope for v1 | n/a — defer |
| `deadguard` | sum of (`xp_captain × 2`, `xp_bench`, `xp_transfer_delta` if any) | sum of same actuals |

**xMinutes-adjusted variant:** the formula above uses raw xP. A useful follow-up enrichment is to
compute the `xp_per_actual_minute` residual to separate "model was right about p90 output but the
player only played 25 minutes" from "model was wrong about p90 output." Mark this as a v2
extension; v1 reports raw residual only.

**DGW handling:** if `player_gw_stats` has two rows for (player_id, gw) (different `fixture_id`),
sum them. This is correct because the FPL game also sums DGW points. The xP comparison is
single-fixture per the known v1 limitation in `xp.py`, so DGW residuals will be biased — flag in
the audit report.

## §4 Cluster classification — `src/analytics/clusters.py`

Each residual is tagged with at most one cluster label. v1 clusters:

| Cluster | Condition (deterministic) |
|---|---|
| `late_injury` | Player's status changed to `i` / `d` within H-6 of deadline AND residual ≤ −2 |
| `rotation_miss` | Status was `a` at decision time AND actual minutes < 45 AND xMinutes prediction ≥ 75 |
| `xp_model_miss` | Status `a`, played ≥ 60 minutes, residual ≤ −3 (or ≥ +3 for over-prediction symmetry — flagged separately) |
| `fdr_miss` | Player's team's actual goals_for vs. xG-implied disagrees with FDR-implied (e.g. FDR=2 expected, team scored 0 in 4 fixtures of difficulty 2-3) |
| `lucky_outperform` | Status `a`, played, residual ≥ +5 (large positive surprise) |
| `unclassified` | No condition met |

The cluster module is a pure function: `classify(residual, context) → cluster_id`. Each
classifier returns the first matching cluster; order matters and is encoded explicitly. Property
tests verify exclusivity (no residual gets two cluster tags).

**v1 deliberately simple.** Cluster expansion (e.g. "set-piece taker change", "team manager
change") is a follow-up once we see real audit data.

## §5 Audit assembly — `src/audit/`

```python
@dataclass
class AuditReport:
    gw_range: tuple[int, int]
    generated_at: datetime
    model_version: str
    residuals: list[Residual]
    cluster_counts: dict[str, int]
    aggregate_trends: dict[str, AggregateStat]   # per decision_type
    proposals: list[Proposal]                    # from src/audit/proposals.py
    narrative: str | None                        # filled by audit_narrator if AI enabled
    narrative_provider: str | None               # 'claude' | 'ollama' | None


@dataclass
class AggregateStat:
    n: int
    mean_residual: float
    stddev: float
    ci_95: tuple[float, float]


@dataclass
class Proposal:
    parameter: str                # e.g. 'thresholds.min_ep_delta_for_transfer'
    current_value: float
    proposed_value: float
    justification: str            # human-readable
    n_observations: int
    confidence: str               # 'high' | 'medium' | 'low'
    bounded_range: tuple[float, float] | None  # set when S-H bounds exist; None for v1
```

**`run_audit(conn, gw_lo, gw_hi, *, ai_provider=None)`** orchestrates:

1. Fetch all `activity_log` rows in range.
2. Call `compute_residuals` for each.
3. Classify each via `clusters.classify`.
4. Aggregate residuals per (decision_type, cluster, model_version).
5. Run `propose_threshold_adjustments` (deterministic).
6. If `ai_provider` is configured, call `audit_narrator.generate_audit_narrative(report,
   provider)` and attach `narrative` + `narrative_provider`.
7. Serialize to `data/audit/audit_{gw_range}_{generated_at}.json`.
8. Return the report.

**Proposals are advisory only.** `src/audit/proposals.py::propose_threshold_adjustments` reads
aggregate stats and emits proposals like:

> "thresholds.min_ep_delta_for_transfer: current 2.0, proposed 2.5. Over N=22 transfers, mean
> residual = -0.7 EP (95% CI [-1.1, -0.3]); transfers with delta in [2.0, 2.5] underperformed by
> mean -1.2 EP. Confidence: high."

S-G **never applies** these. They're surfaced for the user to act on manually, until S-H is built.

## §6 Sonnet 4.6 provider + audit narrator

### Provider plumbing

Extend `src/ai/provider.py` with a `ClaudeProvider`:

```python
class ClaudeProvider:
    def __init__(self, *, api_key: str, model: str = "claude-sonnet-4-6",
                 timeout_seconds: int = 60, max_tokens: int = 1500):
        self._client = anthropic.Anthropic(api_key=api_key)
        ...

    def complete(self, prompt: str) -> str:
        """Returns the text response. Raises ClaudeError on failure."""
```

Same interface contract as `OllamaProvider.complete(prompt)`. The audit narrator selects between
them based on `ai.audit.provider` config (`'claude'` vs `'ollama'` — default `'ollama'` to keep
the project's local-first default; user opts into Claude per-feature).

**Cost guardrails:**
- Default `ai.audit.max_calls_per_day: 5` — prevents runaway cost if buggy code loops.
- Default `ai.audit.estimated_cost_per_call_usd: 0.50` — surfaced in `/api/status` so the user
  knows the running spend.
- Per-call usage (`input_tokens`, `output_tokens`) logged to `activity_log` with
  `decision_type='ai.audit'`.

**Privacy note (B7):** the audit payload to Claude contains:
- Player names (already public via FPL site).
- Numerical residuals.
- Decision contexts (which player was captained, which transfer was made).
- The user's own squad composition is visible to anyone who looks them up on FPL — same level.

It does **not** contain:
- Cookies, session tokens, master password — never.
- Mini-league standings (S-C scope, not built; that'd be a real privacy step).
- `/my-team` raw FPL responses.

### Prompt template (`src/ai/prompts/audit.txt`)

```
You are explaining the results of an FPL decision audit to the team manager.

You are given a structured report of the past N gameweeks: which decisions were made, what was
expected vs what happened, and where systematic biases appear.

Constraints:
- 3 to 6 paragraphs. Plain English. No emojis.
- You may ONLY use names and numbers that appear in INPUT below. Do not invent any other.
- Discuss the most material findings first. Skip decisions with N < 3 sample size.
- For each material finding, identify (a) what happened (b) the likely cause cluster
  (c) whether the model or thresholds appear miscalibrated.
- For each PROPOSED ADJUSTMENT in INPUT, restate it in user-friendly terms and indicate whether
  you'd endorse it given the data.
- Do not editorialise or speculate beyond what the data supports.
- Do not recommend code changes or model changes — those are out of scope here.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

### Few-shot exemplars (2-3, in `audit_examples.json`)

One "everything calibrated" exemplar, one "transfer threshold needs raising" exemplar, one
"DGW-induced miss" exemplar. Self-validating: numbers in outputs trace to inputs.

### Grounding

`is_grounded` (existing) validates numeric grounding. The same caveat applies as in S-A: player
name grounding is implicit, not enforced. For the audit narrative this is lower-risk than for S-A
because the names come from `activity_log` which we control end-to-end.

## §7 CLI integration — `fpl-autopilot review`

```
fpl-autopilot review [--gw N] [--last N] [--ai (claude|ollama|none)] [--format (text|json)]
```

- `--gw N`: audit only GW N.
- `--last N`: audit the last N settled GWs (default 4).
- `--ai`: override the config provider for this run. `none` skips narration.
- `--format`: `text` (default, human-readable) or `json` (the AuditReport serialized).

CLI command body:

```python
def cmd_review(args):
    cfg = config.load_config()
    conn = connect(config.db_path(cfg))
    init_db(conn)
    gw_lo, gw_hi = _resolve_gw_window(conn, args)
    provider = _build_audit_provider(cfg, override=args.ai)
    report = audit.run_audit(conn, gw_lo, gw_hi, ai_provider=provider)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(reports.format_text(report))
```

## §8 Dashboard integration

**New route:** `/audit` (SvelteKit).

**New API:** `/api/audit/{gw}` — returns the most recent persisted audit report for the gw range
ending at `gw`. Returns 404 if no audit has been run.

**Frontend:**
- Top section: `gw_range`, `model_version`, `cluster_counts` (bar chart of cluster frequencies).
- Middle section: per-decision residual table, sortable by residual magnitude.
- Bottom section: Sonnet narrative + proposed adjustments (with "I'll consider this" /
  "dismiss" no-op buttons — the buttons just log to `activity_log`; no actual config write at
  S-G scope, that's S-H).

**No frontend code change is OPTIONAL for this slice.** Unlike S-A.4 where the banner read
arbitrary text, the audit view is a genuinely new surface — Svelte work is required. Treat the
frontend work as a real subtask, not a backend-only swap.

## §9 Config additions

`config.yaml`:

```yaml
ai:
  audit:
    enabled: true
    provider: claude         # 'claude' | 'ollama' | 'none'
    claude:
      model: "claude-sonnet-4-6"
      api_key_env: "ANTHROPIC_API_KEY"  # name of env var holding the key; never store the key in config
      timeout_seconds: 60
      max_tokens: 1500
    max_calls_per_day: 5
    estimated_cost_per_call_usd: 0.50  # for the /api/status spend surface
```

`API key handling`: per CLAUDE.md B7-ish thinking — the API key is in an env var, never logged,
never persisted. Loaded at process start, used to instantiate `ClaudeProvider`, garbage-collected
with process.

## Safety & B-rules

- **B4 (decision engine is sacred):** S-G is purely descriptive. It reads past decisions and
  past outcomes. No decision logic runs. No thresholds change. No code in `src/decisions/` is
  edited. **The "Proposal" data type is advisory — it does not apply.**
- **B5 (xP versioning):** Residuals are segmented by `xp.model_version`. The audit explicitly
  surfaces v1-only or v2-only stats. When v2 lands, parallel-run comparison reuses this same
  infrastructure.
- **B7 (no creds in side channels):** Verified by §6 privacy note. The ClaudeProvider receives
  structured data only, never cookies/credentials/raw FPL responses. Property test enforces:
  any string sent to Claude must not match credential-shaped patterns (cookie names, sessionid
  tokens, etc.).
- **B8 (deadguard scope):** Untouched. Deadguard `_run_trigger` is not edited.
- **B10 (logging discipline):** Every `run_audit` call is itself logged to `activity_log` with
  `decision_type='audit'`. AI-narration calls log per-call token usage.
- **B11 (testing rules):** §11 below.
- **B13 (doc-first):** This doc lands before code. Plan + implementation come after.

## §11 Testing

Five new test files mirror the five modules:

| File | Coverage |
|---|---|
| `tests/test_settlement.py` | Settlement is idempotent. Re-running on a settled GW writes 0 rows. Failure on one GW doesn't prevent settling another. Schema integrity (PRIMARY KEY enforced). |
| `tests/test_residuals.py` | Per-decision-type formula correctness with frozen fixtures (captain, transfer, bench, deadguard). DGW summing. Missing-outcome row → skipped, not erroring. |
| `tests/test_clusters.py` | Property test: cluster classification is total (every residual gets a cluster) and exclusive (no overlap). Each cluster's condition exercised. |
| `tests/test_audit.py` | End-to-end with frozen `activity_log` + `player_gw_stats` fixtures. AuditReport shape. Proposals correctness. Idempotent persistence to `data/audit/`. |
| `tests/test_ai_audit_narrator.py` | Payload shape. Prompt builder. Render. Generate (with mock provider). Grounding rejection. Cache hit/miss. |
| `tests/test_claude_provider.py` | Provider initialization. `complete()` happy path with mocked Claude API. Error handling (network failure, rate limit). Cost-guardrail enforcement (max_calls_per_day). |

## Scope boundary (out of scope for S-G v1)

- **Auto-application of any proposal** → S-H.
- **xP v2 model implementation** → separate slice triggered by S-G findings.
- **Personalization / user-history analysis** (S-D from scope decomp) → separate slice.
- **Mini-league comparison** (S-C) → separate slice.
- **Telegram surfacing of audit reports** → S-D or a small follow-up.
- **xMinutes-adjusted residuals** → v2 extension noted in §3.
- **Chip residual** (counterfactual analysis) → v2 extension; v1 reports chips as
  "flagged/not-flagged" only.
- **Set-piece taker / manager change clusters** → v2 cluster expansion.

## Definition of done (CLAUDE.md B14)

- `player_gw_stats` table created, settlement job wired into hourly refresh, deterministic tests
  pass.
- `compute_residuals` returns correct residuals for fixtures spanning all five decision_types.
- `classify` is total + exclusive (property test).
- `run_audit` produces a valid `AuditReport`, serializes to disk, reloads identically.
- `ClaudeProvider.complete()` works against the real Claude API (manual smoke test once; mocked
  thereafter).
- `fpl-autopilot review --last 4` produces text output on a real DB after the first audit.
- `/api/audit/{gw}` returns the persisted report; the dashboard `/audit` view renders it.
- Full pytest suite green. Vitest green (after frontend work).
- `decision-engine.md` unchanged.
- Activity log captures the audit run itself.

## What S-G unlocks downstream

| Next slice | Becomes possible because of S-G |
|---|---|
| **S-H (bounded auto-tuning)** | Reads `Proposal` rows + applies bounded changes. Without S-G's proposals, nothing to apply. |
| **xP v2** | Residual evidence identifies *which* parts of xP are miscalibrated. Without S-G, "is xP good?" is unanswerable. |
| **S-D (personalization)** | The activity log + audit residuals are the dataset. Without S-G's residual computation, personalization has no signal. |
| **S-E (wildcard / drafting)** | Multi-GW xP projection trustworthy only if past xP residuals look unbiased. S-G is the validation. |

S-G is small (one slice) and self-contained, but it's the load-bearing piece that unblocks the
strategic-feature stack.

---

## Next step

Write `docs/superpowers/plans/2026-05-27-phase3-s-g-decision-audit-plan.md` (TDD implementation
plan with task-level test-first checkpoints, following the same pattern as
`2026-05-26-phase3-llm-deadguard-summary-plan.md`).

Approve this spec first.
