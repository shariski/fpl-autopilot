# LLM Transfer Reasoning Implementation Plan (Phase 3, S-A.2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM prose to the dashboard's top transfer suggestion + the Telegram H-24 transfer line, grounded in next-3-GW fixture context. Deterministic transfers engine unchanged (B4 untouched).

**Architecture:** Reuses the cross-cutting AI sub-layer from S-A.1 (provider, cache, grounding, scheduler hook, config accessors). Adds a transfer-specific payload (richer than captain's — fixtures + status), prompt template + few-shot, render/generate functions, jobs.py branch, queries wrapper, Telegram swap, frontend rendering. Top suggestion gets AI prose + badge; suggestions #2/#3 unchanged.

**Tech Stack:** Same as S-A.1 — Python 3.14, SQLite, FastAPI, SvelteKit + vitest, Ollama with `qwen2.5:7b-instruct-q4_K_M`. All tests fixtures-only with `StubProvider` (R3).

**Source spec:** `docs/superpowers/specs/2026-05-26-phase3-llm-transfer-reasoning-design.md`. **Read it first.** Cross-cutting reference: `docs/superpowers/specs/2026-05-26-phase3-ai-architecture-design.md`. S-A.1 plan for related patterns: `docs/superpowers/plans/2026-05-26-phase3-llm-captain-reasoning-plan.md`.

**B-rule stance:**
- **B4:** untouched — deterministic transfers engine still picks. **No edit to `docs/decision-engine.md`.**
- **B7:** prompt builder is sole egress; closed-shape payload; AI module never imports `src/auth/`.
- **B8:** no executor changes.
- **R3:** all tests use `StubProvider`; no live Ollama in tests.
- **B11:** decision-layer tests untouched.
- **Git hygiene:** **NEVER `git add -A`.** Explicit paths only. Commit footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File structure (locked)

**New files:**
- `src/ai/prompts/transfer.txt`
- `src/ai/prompts/transfer_examples.json`
- `tests/test_ai_prompts_transfer.py`
- `tests/test_ai_reasoning_transfer.py`

**Modified files:**
- `src/ai/reasoning.py` — append `_build_transfer_payload`, `_fixtures_for`, `_status_for`, `_build_transfer_prompt`, `render_transfer_reasoning`, `generate_transfer_prose`
- `src/ai/jobs.py` — add `transfer` branch + `_default_transfer_decision_fn`
- `src/scheduler.py` — extend `panes=["captain"]` → `panes=["captain", "transfer"]`
- `src/interface/queries.py` — add `get_transfer_suggestions` + `get_transfer_reasoning`
- `src/interface/api.py` — route `/api/transfers` through queries
- `src/interface/telegram.py` — add `_transfer_ai_prose` + extend `notify_plan`
- `tests/test_ai_jobs.py` — extend for transfer pane
- `tests/test_scheduler.py` — extend for `['captain', 'transfer']`
- `tests/test_api.py` — extend for enriched `/api/transfers`
- `tests/test_telegram.py` — extend for transfer summary swap
- `frontend/src/lib/types.ts` — `TransferSuggestion` gains optional `reasoning?` + `reasoning_source?`
- `frontend/src/lib/components/TransferIdeas.svelte` — prose line + badge on top suggestion
- `frontend/src/lib/components/TransferIdeas.svelte.test.ts` — vitest cases
- `frontend/src/lib/mocks/full.ts` — top transfer mock gets reasoning + source

**Note:** before any task that modifies existing code, **read it first** to confirm current state. Line numbers may have drifted from this session's snapshot.

---

## Task 0: Transfer prompt template + few-shot exemplars (self-validating)

**Files:**
- Create: `src/ai/prompts/transfer.txt`
- Create: `src/ai/prompts/transfer_examples.json`
- Create: `tests/test_ai_prompts_transfer.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ai_prompts_transfer.py`:

```python
import json
from pathlib import Path

from src.ai import grounding

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "ai" / "prompts"


def test_transfer_template_exists_and_has_placeholders():
    template = (PROMPTS_DIR / "transfer.txt").read_text()
    assert "{examples}" in template
    assert "{payload_json}" in template


def test_transfer_examples_file_is_valid_json_list():
    examples = json.loads((PROMPTS_DIR / "transfer_examples.json").read_text())
    assert isinstance(examples, list)
    assert len(examples) >= 2
    for ex in examples:
        assert set(ex.keys()) == {"input", "output"}
        assert isinstance(ex["input"], dict)
        assert isinstance(ex["output"], str)


def test_every_transfer_example_output_is_grounded_in_its_input():
    examples = json.loads((PROMPTS_DIR / "transfer_examples.json").read_text())
    for i, ex in enumerate(examples):
        input_text = json.dumps(ex["input"], sort_keys=True)
        ok, ungrounded = grounding.is_grounded(ex["output"], input_text)
        assert ok, f"transfer example {i} prose contains ungrounded numbers: {ungrounded}"
```

- [ ] **Step 2: Verify FAIL**

```
.venv/bin/pytest tests/test_ai_prompts_transfer.py -v
```

- [ ] **Step 3: Create `src/ai/prompts/transfer.txt`** (verbatim from the spec §2):

```
You are explaining one FPL transfer to the team manager. Sell OUT, buy IN.

Constraints:
- 2 to 3 sentences. Plain English. No emojis. No exclamation marks.
- You may ONLY use numbers that appear in INPUT below. Do not invent any other number.
- Mention WHY the move helps: contrast the fixtures of OUT and IN over the listed gameweeks,
  reference the EP gain, and note any status concern.
- Do not editorialise beyond the inputs. Do not predict future scorelines. Do not name other players.
- Output the paragraph only. No preamble, no closing remarks.

EXAMPLES:
{examples}

INPUT:
{payload_json}

OUTPUT:
```

- [ ] **Step 4: Create `src/ai/prompts/transfer_examples.json`** (verbatim from the spec §2):

```json
[
  {
    "input": {
      "out": {"web_name": "Salah", "price": 13.1, "status": "a",
              "fixtures_3gw": [
                {"opponent": "BRE", "home": false, "fdr_attack": 4},
                {"opponent": "EVE", "home": true,  "fdr_attack": 2},
                {"opponent": "ARS", "home": false, "fdr_attack": 5}
              ]},
      "in": {"web_name": "Saka", "price": 10.4, "status": "a",
             "fixtures_3gw": [
               {"opponent": "BHA", "home": true,  "fdr_attack": 2},
               {"opponent": "WHU", "home": true,  "fdr_attack": 3},
               {"opponent": "FUL", "home": false, "fdr_attack": 3}
             ]},
      "ep_delta_5gw": 3.4, "hit_cost": 0, "confidence": 78, "free_transfers": 1
    },
    "output": "Sell Salah, buy Saka — Saka has 2 home fixtures including BHA at fdr 2, while Salah faces ARS away at fdr 5 in the same window. The free transfer adds 3.4 EP over 5 GWs at confidence 78."
  },
  {
    "input": {
      "out": {"web_name": "Watkins", "price": 9.0, "status": "d",
              "fixtures_3gw": [
                {"opponent": "LIV", "home": false, "fdr_attack": 5},
                {"opponent": "MCI", "home": false, "fdr_attack": 5}
              ]},
      "in": {"web_name": "Isak", "price": 9.3, "status": "a",
             "fixtures_3gw": [
               {"opponent": "WOL", "home": true,  "fdr_attack": 2},
               {"opponent": "CRY", "home": false, "fdr_attack": 3},
               {"opponent": "BHA", "home": true,  "fdr_attack": 2}
             ]},
      "ep_delta_5gw": 2.1, "hit_cost": 0, "confidence": 70, "free_transfers": 1
    },
    "output": "Sell Watkins, buy Isak — Watkins carries a doubt and faces LIV and MCI away at fdr 5, while Isak has 2 home fixtures at fdr 2. The free transfer adds 2.1 EP over 5 GWs at confidence 70."
  }
]
```

If a grounding assertion fails on an exemplar, the prose mentions a number that isn't in its input — either rewrite the prose to only use input numbers, or add the missing number. **Do not change the grounding rule.**

- [ ] **Step 5: Verify PASS**

```
.venv/bin/pytest tests/test_ai_prompts_transfer.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```
git add src/ai/prompts/transfer.txt src/ai/prompts/transfer_examples.json tests/test_ai_prompts_transfer.py
git commit -m "$(cat <<'EOF'
feat(ai): transfer prompt template + few-shot exemplars (S-A.2 task 0)

Per-pane structured prompt with 2 hand-curated exemplars demonstrating
fixture-context narrative. Self-validating: every exemplar's output passes
is_grounded against its input. Mirrors the S-A.1 captain pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Transfer payload + prompt builders

**Files:**
- Modify: `src/ai/reasoning.py` (append helpers + builders)
- Create: `tests/test_ai_reasoning_transfer.py`

- [ ] **Step 1: Read the existing reasoning.py first** (`Read tool, src/ai/reasoning.py`) so you know where to append.

- [ ] **Step 2: Write the failing tests**

`tests/test_ai_reasoning_transfer.py`:

```python
import json

from src.data.db import connect, init_db
from src.ai import reasoning


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


# Fixture that seeds the minimum rows so _fixtures_for + _status_for resolve.
def _seed_fixtures(conn):
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30:00Z', 0, 1, 0, 'PENDING')")
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (39, 'GW39', '2026-06-09T18:30:00Z', 0, 0, 0, 'PENDING')")
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (40, 'GW40', '2026-06-16T18:30:00Z', 0, 0, 0, 'PENDING')")
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), "
                 "(2, 'Brentford', 'BRE'), (3, 'Liverpool', 'LIV'), (4, 'Aston Villa', 'AVL')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), (20, 'Watkins', 'FWD', 4, 9.0, 'd')")
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0), "
                 "(2, 39, 3, 1, '2026-06-09T19:00Z', 0), "
                 "(3, 40, 1, 4, '2026-06-16T19:00Z', 0), "
                 "(4, 38, 3, 4, '2026-06-02T17:00Z', 0), "
                 "(5, 39, 4, 2, '2026-06-09T17:00Z', 0), "
                 "(6, 40, 3, 4, '2026-06-16T17:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES "
                 "(1, 38, 2, 2, '2026-05-19T00:00Z'), (1, 39, 5, 5, '2026-05-19T00:00Z'), "
                 "(1, 40, 2, 2, '2026-05-19T00:00Z'), (4, 38, 5, 5, '2026-05-19T00:00Z'), "
                 "(4, 39, 4, 4, '2026-05-19T00:00Z'), (4, 40, 5, 5, '2026-05-19T00:00Z')")
    conn.commit()


TRANSFER_DECISION_FIXTURE = {
    "suggestions": [
        {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
         "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
         "ep_delta_5gw": 3.45, "hit_cost": 0, "confidence": 78},
        {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
         "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
         "ep_delta_5gw": 2.0,  "hit_cost": 0, "confidence": 65},
    ],
    "empty_reason": None,
    "free_transfers": 1,
}


def test_status_for_returns_player_status():
    conn = _db(); _seed_fixtures(conn)
    assert reasoning._status_for(conn, 10) == "a"
    assert reasoning._status_for(conn, 20) == "d"


def test_status_for_returns_a_when_player_missing():
    """Defensive: unknown player_id returns 'a' (treat as available) — keeps the LLM payload sane."""
    conn = _db(); _seed_fixtures(conn)
    assert reasoning._status_for(conn, 99999) == "a"


def test_fixtures_for_returns_next_n_gws():
    conn = _db(); _seed_fixtures(conn)
    fixtures = reasoning._fixtures_for(conn, player_id=10, next_gw=38, horizon=3)
    assert len(fixtures) == 3
    # GW38: MCI (1) home vs BRE (2)
    assert fixtures[0] == {"opponent": "BRE", "home": True, "fdr_attack": 2}
    # GW39: MCI (1) away vs LIV (3)
    assert fixtures[1] == {"opponent": "LIV", "home": False, "fdr_attack": 5}
    # GW40: MCI (1) home vs AVL (4)
    assert fixtures[2] == {"opponent": "AVL", "home": True, "fdr_attack": 2}


def test_fixtures_for_handles_blank_gameweek():
    """When a team has no fixture in a given gw, that entry is skipped (list shorter than horizon)."""
    conn = _db(); _seed_fixtures(conn)
    # Delete MCI's GW39 fixture to simulate a BGW
    conn.execute("DELETE FROM fixtures WHERE id=2")
    conn.commit()
    fixtures = reasoning._fixtures_for(conn, player_id=10, next_gw=38, horizon=3)
    assert len(fixtures) == 2
    assert all(f["opponent"] != "LIV" for f in fixtures)


def test_build_transfer_payload_shape():
    conn = _db(); _seed_fixtures(conn)
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    assert payload is not None
    assert payload["out"]["web_name"] == "Watkins"
    assert payload["out"]["status"] == "d"
    assert payload["out"]["price"] == 9.0
    assert len(payload["out"]["fixtures_3gw"]) == 3
    assert payload["in"]["web_name"] == "Haaland"
    assert payload["in"]["status"] == "a"
    assert payload["ep_delta_5gw"] == 3.5      # rounded from 3.45 to 1dp (matches S-A.1 lesson)
    assert payload["hit_cost"] == 0
    assert payload["confidence"] == 78
    assert payload["free_transfers"] == 1


def test_build_transfer_payload_returns_none_on_empty_suggestions():
    conn = _db(); _seed_fixtures(conn)
    assert reasoning._build_transfer_payload(
        conn, {"suggestions": [], "empty_reason": "none", "free_transfers": 1}) is None


def test_build_transfer_payload_returns_none_when_no_next_gw():
    conn = _db()  # no gameweeks seeded
    assert reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE) is None


def test_build_transfer_prompt_includes_payload_and_examples():
    conn = _db(); _seed_fixtures(conn)
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    prompt = reasoning._build_transfer_prompt(payload)
    assert "Watkins" in prompt        # from payload
    assert "Saka" in prompt           # from first exemplar
    assert "Isak" in prompt           # from second exemplar
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "Do not invent" in prompt
```

- [ ] **Step 3: Verify FAIL**

```
.venv/bin/pytest tests/test_ai_reasoning_transfer.py -v
```

- [ ] **Step 4: Append helpers + builders to `src/ai/reasoning.py`**

```python


def _next_gw(conn) -> int | None:
    """Return next unfinished gameweek id, or None."""
    row = conn.execute(
        "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def _status_for(conn, player_id: int) -> str:
    """Player status flag (a/d/i/s/u). Defaults to 'a' when player is missing."""
    row = conn.execute(
        "SELECT status FROM players WHERE id=?", (player_id,)).fetchone()
    return row["status"] if row is not None else "a"


def _fixtures_for(conn, player_id: int, next_gw: int, horizon: int) -> list[dict]:
    """Up to `horizon` fixtures for the player's team starting at next_gw.

    Each item: {opponent: short_name, home: bool, fdr_attack: int}.
    BGW (no fixture for a given gw) is silently skipped.
    DGW (multiple fixtures for the same gw) is surfaced as multiple list entries.
    """
    team_row = conn.execute(
        "SELECT team_id FROM players WHERE id=?", (player_id,)).fetchone()
    if team_row is None:
        return []
    team_id = team_row["team_id"]
    rows = conn.execute(
        """SELECT f.gw, f.home_team_id, f.away_team_id,
                  th.short_name AS home_short, ta.short_name AS away_short,
                  fdr.fdr_attack AS fdr_attack
           FROM fixtures f
           JOIN teams th ON th.id = f.home_team_id
           JOIN teams ta ON ta.id = f.away_team_id
           LEFT JOIN fdr ON fdr.team_id = ? AND fdr.gw = f.gw
           WHERE f.gw BETWEEN ? AND ?
             AND (f.home_team_id = ? OR f.away_team_id = ?)
           ORDER BY f.gw, f.id""",
        (team_id, next_gw, next_gw + horizon - 1, team_id, team_id),
    ).fetchall()
    out = []
    for r in rows:
        is_home = r["home_team_id"] == team_id
        opp = r["away_short"] if is_home else r["home_short"]
        # fdr_attack may be NULL if the fdr table hasn't been computed for this gw — default to 3
        fdr_a = r["fdr_attack"] if r["fdr_attack"] is not None else 3
        out.append({"opponent": opp, "home": is_home, "fdr_attack": fdr_a})
    return out


def _build_transfer_payload(conn, transfer_decision: dict) -> dict | None:
    """Closed-shape payload for the TOP transfer suggestion, with rich fixture context.

    Returns None when:
    - no suggestions (LLM has nothing to render)
    - no next gw (post-season state)
    """
    suggestions = transfer_decision.get("suggestions", [])
    if not suggestions:
        return None
    next_gw = _next_gw(conn)
    if next_gw is None:
        return None
    top = suggestions[0]
    return {
        "out": {
            "web_name": top["out"]["web_name"],
            "price": top["out"]["price"],
            "status": _status_for(conn, top["out"]["player_id"]),
            "fixtures_3gw": _fixtures_for(conn, top["out"]["player_id"], next_gw, horizon=3),
        },
        "in": {
            "web_name": top["in"]["web_name"],
            "price": top["in"]["price"],
            "status": _status_for(conn, top["in"]["player_id"]),
            "fixtures_3gw": _fixtures_for(conn, top["in"]["player_id"], next_gw, horizon=3),
        },
        # 1dp round matches the few-shot exemplar style + S-A.1's lesson
        "ep_delta_5gw": round(top["ep_delta_5gw"], 1),
        "hit_cost": top["hit_cost"],
        "confidence": top["confidence"],
        "free_transfers": transfer_decision.get("free_transfers"),
    }


def _build_transfer_prompt(payload: dict) -> str:
    """Render transfer.txt with {examples} + {payload_json} substituted."""
    template = (_PROMPTS_DIR / "transfer.txt").read_text()
    examples = json.loads((_PROMPTS_DIR / "transfer_examples.json").read_text())
    examples_block = "\n\n".join(
        f"INPUT:\n{json.dumps(ex['input'], sort_keys=True, indent=2)}\n"
        f"OUTPUT:\n{ex['output']}"
        for ex in examples
    )
    payload_json = json.dumps(payload, sort_keys=True, indent=2)
    return template.replace("{examples}", examples_block).replace("{payload_json}", payload_json)
```

- [ ] **Step 5: Verify PASS + full suite**

```
.venv/bin/pytest tests/test_ai_reasoning_transfer.py -v
.venv/bin/pytest -q
```

- [ ] **Step 6: Commit**

```
git add src/ai/reasoning.py tests/test_ai_reasoning_transfer.py
git commit -m "$(cat <<'EOF'
feat(ai): transfer payload + prompt builders with fixture context (S-A.2 task 1)

_build_transfer_payload assembles a rich closed-shape dict from the top
suggestion: both players' next-3-GW fixtures (opponent, home, fdr_attack) +
status flag + ep_delta_5gw rounded to 1dp (S-A.1 lesson) + hit_cost +
confidence + free_transfers. _fixtures_for handles BGW (skipped) and DGW
(multi-row) cleanly. _build_transfer_prompt substitutes examples + payload.
B7 preserved: closed-shape payload — no auth path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: render_transfer_reasoning + generate_transfer_prose

**Files:**
- Modify: `src/ai/reasoning.py` (append render + generate)
- Modify: `tests/test_ai_reasoning_transfer.py` (append cases)

- [ ] **Step 1: Append the failing tests** to `tests/test_ai_reasoning_transfer.py`:

```python


from src.ai import cache as ai_cache, provider as prv


def test_render_transfer_reasoning_returns_classic_on_cache_miss():
    conn = _db(); _seed_fixtures(conn)
    prose, source = reasoning.render_transfer_reasoning(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE)
    assert source == "classic"
    assert prose == ""        # transfer engine has no per-suggestion `reason`; classic = empty


def test_render_transfer_reasoning_returns_ai_on_cache_hit():
    conn = _db(); _seed_fixtures(conn)
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="transfer", rec_hash=rec_hash,
                 prose="Sell Watkins, buy Haaland — fixtures favour the swap.",
                 model_id="qwen2.5:7b-instruct-q4_K_M")
    prose, source = reasoning.render_transfer_reasoning(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE)
    assert source == "ai"
    assert prose == "Sell Watkins, buy Haaland — fixtures favour the swap."


def test_render_transfer_reasoning_returns_classic_on_empty_suggestions():
    conn = _db(); _seed_fixtures(conn)
    decision = {"suggestions": [], "empty_reason": "none", "free_transfers": 1}
    prose, source = reasoning.render_transfer_reasoning(conn, gw=38, transfer_decision=decision)
    assert source == "classic"
    assert prose == ""


def test_generate_transfer_prose_caches_grounded_prose():
    conn = _db(); _seed_fixtures(conn)
    # Grounded prose: every number in prose appears in the JSON of the payload
    stub = prv.StubProvider("Sell Watkins, buy Haaland — Haaland has 2 home fixtures at fdr 2, "
                            "Watkins faces LIV away at fdr 5. Free transfer adds 3.5 EP at 78.")
    ok = reasoning.generate_transfer_prose(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE,
        provider=stub, model_id="qwen2.5:7b-instruct-q4_K_M")
    assert ok is True
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    assert ai_cache.get(conn, gw=38, pane_type="transfer", rec_hash=rec_hash) is not None


def test_generate_transfer_prose_rejects_ungrounded_prose():
    conn = _db(); _seed_fixtures(conn)
    stub = prv.StubProvider("Sell Watkins, buy Haaland — EP gain 99.9 at confidence 99.")
    ok = reasoning.generate_transfer_prose(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_transfer_prose_rejects_empty_prose():
    conn = _db(); _seed_fixtures(conn)
    stub = prv.StubProvider("")
    ok = reasoning.generate_transfer_prose(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE,
        provider=stub, model_id="m")
    assert ok is False


def test_generate_transfer_prose_skips_provider_on_cache_hit():
    conn = _db(); _seed_fixtures(conn)
    payload = reasoning._build_transfer_payload(conn, TRANSFER_DECISION_FIXTURE)
    rec_hash = ai_cache.recommendation_hash(payload)
    ai_cache.put(conn, gw=38, pane_type="transfer", rec_hash=rec_hash,
                 prose="cached.", model_id="m")

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("provider must not be called on cache hit")

    ok = reasoning.generate_transfer_prose(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE,
        provider=_BoomProvider(), model_id="m")
    assert ok is True


def test_generate_transfer_prose_skips_on_empty_suggestions():
    conn = _db(); _seed_fixtures(conn)
    decision = {"suggestions": [], "empty_reason": "none", "free_transfers": 1}

    class _BoomProvider:
        def generate(self, prompt, **kw):
            raise AssertionError("provider must not be called with empty suggestions")

    ok = reasoning.generate_transfer_prose(
        conn, gw=38, transfer_decision=decision, provider=_BoomProvider(), model_id="m")
    assert ok is False


def test_generate_transfer_prose_swallows_provider_errors():
    conn = _db(); _seed_fixtures(conn)

    class _ErrProvider:
        def generate(self, prompt, **kw):
            from src.ai.provider import OllamaError
            raise OllamaError("ollama down")

    ok = reasoning.generate_transfer_prose(
        conn, gw=38, transfer_decision=TRANSFER_DECISION_FIXTURE,
        provider=_ErrProvider(), model_id="m")
    assert ok is False
```

- [ ] **Step 2: Verify FAIL**

```
.venv/bin/pytest tests/test_ai_reasoning_transfer.py -v
```

- [ ] **Step 3: Append render + generate to `src/ai/reasoning.py`**

```python


def render_transfer_reasoning(conn, gw: int, transfer_decision: dict) -> tuple[str, str]:
    """Read path. Returns (prose, source).
    Cache hit -> (cached_prose, 'ai'); miss -> ('', 'classic').
    Empty suggestions or no next_gw -> ('', 'classic')."""
    payload = _build_transfer_payload(conn, transfer_decision)
    if payload is None:
        return ("", "classic")
    rec_hash = cache.recommendation_hash(payload)
    hit = cache.get(conn, gw, "transfer", rec_hash)
    return (hit["prose"], "ai") if hit is not None else ("", "classic")


def generate_transfer_prose(conn, gw: int, transfer_decision: dict, *,
                            provider, model_id: str,
                            max_tokens: int = 200, temperature: float = 0.2) -> bool:
    """Write path. Returns True on grounded success (cache hit counts as success).
    Provider errors caught; empty/ungrounded prose not cached."""
    payload = _build_transfer_payload(conn, transfer_decision)
    if payload is None:
        logger.info("ai.transfer.skipped_empty", extra={"gw": gw})
        return False
    rec_hash = cache.recommendation_hash(payload)
    if cache.get(conn, gw, "transfer", rec_hash) is not None:
        return True
    prompt = _build_transfer_prompt(payload)
    try:
        prose = provider.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaError:
        logger.exception("ai.transfer.provider_error",
                         extra={"gw": gw, "model_id": model_id})
        return False
    if not prose:
        logger.warning("ai.transfer.empty_prose",
                       extra={"gw": gw, "model_id": model_id})
        return False
    payload_text = json.dumps(payload, sort_keys=True)
    ok, ungrounded = grounding.is_grounded(prose, payload_text)
    if not ok:
        logger.warning("ai.transfer.grounding_failed",
                       extra={"gw": gw, "rec_hash": rec_hash,
                              "ungrounded": sorted(ungrounded),
                              "model_id": model_id, "prose_chars": len(prose)})
        return False
    cache.put(conn, gw, "transfer", rec_hash, prose, model_id)
    return True
```

- [ ] **Step 4: Verify PASS + full suite**

```
.venv/bin/pytest tests/test_ai_reasoning_transfer.py -v
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```
git add src/ai/reasoning.py tests/test_ai_reasoning_transfer.py
git commit -m "$(cat <<'EOF'
feat(ai): render+generate transfer reasoning (S-A.2 task 2)

render_transfer_reasoning: cache-first read; miss returns ('', 'classic')
since the transfers engine has no per-suggestion template `reason` to fall
back on (the chips convey the data).

generate_transfer_prose: same flow as generate_captain_prose — payload
check → cache check → prompt → provider → empty guard → grounding check →
cache.put. Provider errors caught (OllamaError); empty/ungrounded prose
logged + not cached. Mirrors S-A.1's safety semantics exactly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: jobs.py 'transfer' branch + scheduler extension

**Files:**
- Modify: `src/ai/jobs.py`
- Modify: `src/scheduler.py`
- Modify: `tests/test_ai_jobs.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Read** `src/ai/jobs.py` and `src/scheduler.py` to confirm current state.

- [ ] **Step 2: Append failing tests to `tests/test_ai_jobs.py`** (after the existing tests):

```python


def test_generate_ai_reasoning_job_caches_transfer_prose():
    """The 'transfer' pane is processed analogously to 'captain'."""
    conn = _db()
    # Seed minimum players + fixtures so _build_transfer_payload succeeds
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), (2, 'Brentford', 'BRE')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), (20, 'Watkins', 'FWD', 2, 9.0, 'd')")
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) "
                 "VALUES (1, 38, 2, 2, '2026-05-19T00:00Z'), (2, 38, 4, 4, '2026-05-19T00:00Z')")
    conn.commit()

    transfer_decision = {
        "suggestions": [
            {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
             "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
             "ep_delta_5gw": 3.5, "hit_cost": 0, "confidence": 78}
        ],
        "empty_reason": None, "free_transfers": 1,
    }
    stub = prv.StubProvider("Sell Watkins (d), buy Haaland — fdr 2 over fdr 4. "
                            "Free transfer adds 3.5 EP at 78.")
    result = jobs.generate_ai_reasoning_job(
        conn, panes=["transfer"], provider=stub, model_id="m",
        transfer_decision_fn=lambda c: transfer_decision)
    assert result == {"transfer": "ok"}


def test_generate_ai_reasoning_job_handles_both_captain_and_transfer():
    conn = _db()
    # Use the same minimal seed as above so transfer payload builds
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), (2, 'Brentford', 'BRE')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), (20, 'Watkins', 'FWD', 2, 9.0, 'd')")
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) "
                 "VALUES (1, 38, 2, 2, '2026-05-19T00:00Z'), (2, 38, 4, 4, '2026-05-19T00:00Z')")
    conn.commit()

    captain_decision = CAPTAIN_DECISION   # reused from the top of this file
    transfer_decision = {
        "suggestions": [
            {"out": {"player_id": 20, "web_name": "Watkins", "price": 9.0},
             "in":  {"player_id": 10, "web_name": "Haaland", "price": 14.0},
             "ep_delta_5gw": 3.5, "hit_cost": 0, "confidence": 78}
        ],
        "empty_reason": None, "free_transfers": 1,
    }
    # Use one stub that returns grounded prose for both calls
    stub = prv.StubProvider("captain Haaland at 7.2 xP — gap 1.8 vs Salah, confidence 82.")

    # We need two different stubs for the two pane types. Override via a class.
    class _TwoResponseStub:
        def __init__(self):
            self.responses = iter([
                "captain Haaland at 7.2 xP — gap 1.8 vs Salah, confidence 82.",
                "Sell Watkins (d), buy Haaland — fdr 2 over fdr 4. Free transfer adds 3.5 EP at 78.",
            ])
        def generate(self, prompt, **kw):
            return next(self.responses)

    result = jobs.generate_ai_reasoning_job(
        conn, panes=["captain", "transfer"], provider=_TwoResponseStub(), model_id="m",
        captain_decision_fn=lambda c: captain_decision,
        transfer_decision_fn=lambda c: transfer_decision)
    assert result == {"captain": "ok", "transfer": "ok"}
```

- [ ] **Step 3: Append failing test to `tests/test_scheduler.py`**:

```python


def test_refresh_and_recompute_invokes_ai_with_both_panes(monkeypatch):
    """ai.enabled=True calls generate_ai_reasoning_job with panes=['captain', 'transfer']."""
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
    assert captured_panes == [["captain", "transfer"]]
```

- [ ] **Step 4: Verify FAIL**

```
.venv/bin/pytest tests/test_ai_jobs.py tests/test_scheduler.py -v -k "transfer or both"
```

- [ ] **Step 5: Modify `src/ai/jobs.py`** — add the transfer branch:

Replace the `generate_ai_reasoning_job` signature + body. Read the existing function first; the change is additive (adds a `transfer_decision_fn=None` param + a `transfer` branch in the for-loop):

```python
def _default_transfer_decision_fn(conn):
    from src.decisions import transfers
    return transfers.get_transfer_suggestions(conn)


def generate_ai_reasoning_job(
    conn,
    *,
    panes: list[str],
    provider,
    model_id: str,
    captain_decision_fn: Callable | None = None,
    transfer_decision_fn: Callable | None = None,
) -> dict:
    """Walk `panes`, generate prose per pane, cache on success.
    Returns {pane_type: 'ok'|'failed'|'skipped'}."""
    gw = _next_gw(conn)
    if gw is None:
        return {p: "skipped" for p in panes}
    result: dict[str, str] = {}
    captain_fn = captain_decision_fn or _default_captain_decision_fn
    transfer_fn = transfer_decision_fn or _default_transfer_decision_fn
    for pane in panes:
        if pane == "captain":
            decision = captain_fn(conn)
            ok = reasoning.generate_captain_prose(
                conn, gw=gw, captain_decision=decision,
                provider=provider, model_id=model_id)
            result[pane] = "ok" if ok else "failed"
        elif pane == "transfer":
            decision = transfer_fn(conn)
            ok = reasoning.generate_transfer_prose(
                conn, gw=gw, transfer_decision=decision,
                provider=provider, model_id=model_id)
            result[pane] = "ok" if ok else "failed"
        else:
            logger.warning("ai.jobs.unknown_pane", extra={"pane": pane})
            result[pane] = "skipped"
    return result
```

- [ ] **Step 6: Modify `src/scheduler.py`** — change `panes=["captain"]` to `panes=["captain", "transfer"]`. Read the existing AI block in `refresh_and_recompute`; the change is one literal substitution.

- [ ] **Step 7: Verify PASS + full suite**

```
.venv/bin/pytest tests/test_ai_jobs.py tests/test_scheduler.py -v
.venv/bin/pytest -q
```

- [ ] **Step 8: Commit**

```
git add src/ai/jobs.py src/scheduler.py tests/test_ai_jobs.py tests/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat(ai): jobs.py transfer branch + scheduler panes extension (S-A.2 task 3)

generate_ai_reasoning_job adds a 'transfer' branch + transfer_decision_fn
parameter mirroring the captain pattern. refresh_and_recompute now
pre-warms both panes per recompute cycle (panes=['captain', 'transfer']).
Cache table picks up rows for pane_type='transfer'. Unknown panes still
log + report 'skipped'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: queries.get_transfer_suggestions + api.py rewiring

**Files:**
- Modify: `src/interface/queries.py`
- Modify: `src/interface/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Read** `src/interface/queries.py` (locate `get_captain_picks` + `get_captain_reasoning` from S-A.1 — pattern reference) and `src/interface/api.py` (the `/api/transfers` endpoint).

- [ ] **Step 2: Append failing tests to `tests/test_api.py`** (adapt fixture names to whatever the file uses):

```python
def test_api_transfers_carries_reasoning_classic_on_cache_miss(client_with_data):
    """With no AI cache: top suggestion has reasoning='' + 'classic'; others same."""
    resp = client_with_data.get("/api/transfers")
    assert resp.status_code == 200
    body = resp.json()
    if not body["suggestions"]:
        return
    for s in body["suggestions"]:
        assert s["reasoning_source"] == "classic"
        assert s["reasoning"] == ""


def test_api_transfers_carries_reasoning_ai_on_cache_hit(client_with_data, conn):
    """Pre-warm the cache for the next GW's transfer payload -> top suggestion has 'ai'."""
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import transfers
    decision = transfers.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return
    payload = ai_reasoning._build_transfer_payload(conn, decision)
    if payload is None:
        return
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="transfer", rec_hash=rec_hash,
                 prose="Transfer AI prose.", model_id="qwen2.5:7b-instruct-q4_K_M")

    resp = client_with_data.get("/api/transfers")
    body = resp.json()
    assert body["suggestions"][0]["reasoning_source"] == "ai"
    assert body["suggestions"][0]["reasoning"] == "Transfer AI prose."
    for s in body["suggestions"][1:]:
        assert s["reasoning_source"] == "classic"
        assert s["reasoning"] == ""
```

If `client_with_data`/`conn` fixtures aren't named that way in the existing file, adapt. The semantic intent: a TestClient + a shared in-memory conn seeded with enough data to make `transfers.get_transfer_suggestions` return ≥1 suggestion.

- [ ] **Step 3: Verify FAIL**

```
.venv/bin/pytest tests/test_api.py -v -k "transfers"
```

- [ ] **Step 4: Append `get_transfer_suggestions` + `get_transfer_reasoning` to `src/interface/queries.py`**

```python


def get_transfer_suggestions(conn):
    """Wraps transfers.get_transfer_suggestions and enriches the TOP suggestion with
    (reasoning, reasoning_source). Other suggestions get reasoning='' + reasoning_source='classic'."""
    from src.decisions import transfers as transfers_engine
    from src.ai import reasoning as ai_reasoning
    decision = transfers_engine.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return decision
    gw = _next_gw(conn)
    if gw is None:
        return decision
    prose, source = ai_reasoning.render_transfer_reasoning(conn, gw, decision)
    enriched = list(decision["suggestions"])
    enriched[0] = {**enriched[0], "reasoning": prose, "reasoning_source": source}
    for i in range(1, len(enriched)):
        enriched[i] = {**enriched[i], "reasoning": "", "reasoning_source": "classic"}
    return {**decision, "suggestions": enriched}


def get_transfer_reasoning(conn, gw):
    """Cheap lookup for the Telegram path. Returns cached AI prose, or None on miss."""
    from src.decisions import transfers as transfers_engine
    from src.ai import reasoning as ai_reasoning
    decision = transfers_engine.get_transfer_suggestions(conn)
    if not decision["suggestions"]:
        return None
    prose, source = ai_reasoning.render_transfer_reasoning(conn, gw, decision)
    return prose if source == "ai" else None
```

- [ ] **Step 5: Update `src/interface/api.py` `/api/transfers` route**

Read the current endpoint first. Replace `transfers_engine.get_transfer_suggestions(conn)` with `queries.get_transfer_suggestions(conn)`. Keep the `transfers_engine` import only if other endpoints still use it (verify with grep — `chips` may still call it).

- [ ] **Step 6: Verify PASS**

```
.venv/bin/pytest tests/test_api.py -v
.venv/bin/pytest -q
```

- [ ] **Step 7: Commit**

```
git add src/interface/queries.py src/interface/api.py tests/test_api.py
git commit -m "$(cat <<'EOF'
feat(ai): /api/transfers returns enriched suggestions (S-A.2 task 4)

queries.get_transfer_suggestions wraps the deterministic ranker and
enriches the top suggestion with (reasoning, reasoning_source). Cache hit
-> 'ai' + cached prose; miss -> 'classic' + empty string (transfers engine
has no per-suggestion template `reason` to fall back on). Suggestions #2/#3
are always 'classic' + empty (no badge in the UI).

queries.get_transfer_reasoning is the Telegram-path helper (returns prose
or None).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Telegram notify_plan transfer swap

**Files:**
- Modify: `src/interface/telegram.py`
- Modify: `tests/test_telegram.py`

- [ ] **Step 1: Read** `src/interface/telegram.py` `notify_plan` + `_captain_ai_prose` (added in S-A.1 task 12).

- [ ] **Step 2: Append failing tests to `tests/test_telegram.py`**:

```python
def test_notify_plan_swaps_transfer_summary_when_ai_cache_populated(monkeypatch, tmp_path):
    """If cached AI transfer prose exists for the next gw, notify_plan uses it for the transfer entry."""
    from src.data.db import connect, init_db
    from src.interface import telegram
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import transfers

    conn = connect(":memory:")
    init_db(conn)
    _seed_transfer_db(conn)        # helper that seeds players, fixtures, fdr, my_team, xp

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    decision = transfers.get_transfer_suggestions(conn)
    assert decision["suggestions"], "seed must produce >= 1 transfer suggestion"
    payload = ai_reasoning._build_transfer_payload(conn, decision)
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="transfer", rec_hash=rec_hash,
                 prose="AI prose for transfer.", model_id="m")

    sent = []
    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "transfer", "summary": "template transfer summary", "executed": True}]
    telegram.notify_plan(conn, plan, mode="manual", session=_FakeSession())
    assert sent
    assert "AI prose for transfer." in sent[0]["text"]
    assert "template transfer summary" not in sent[0]["text"]


def test_notify_plan_uses_classic_summary_when_no_transfer_ai_cache(monkeypatch, tmp_path):
    from src.data.db import connect, init_db
    from src.interface import telegram

    conn = connect(":memory:")
    init_db(conn)
    _seed_transfer_db(conn)

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

    plan = [{"decision": "transfer", "summary": "template transfer summary", "executed": True}]
    telegram.notify_plan(conn, plan, mode="manual", session=_FakeSession())
    assert sent
    assert "template transfer summary" in sent[0]["text"]


def _seed_transfer_db(conn):
    """Minimal seed so transfers.get_transfer_suggestions returns >=1 row."""
    import json as _json
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), "
                 "(2, 'Brentford', 'BRE'), (3, 'Aston Villa', 'AVL')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), "
                 "(20, 'Watkins', 'FWD', 3, 9.0, 'a'), "
                 "(30, 'Isak', 'FWD', 2, 9.3, 'a')")
    conn.execute("INSERT INTO my_team(gw, picks_json, bank) VALUES (38, ?, 0.5)",
                 (_json.dumps([{"element": 20, "position": 11, "multiplier": 1,
                                "is_captain": False, "is_vice_captain": False}]),))
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0), (2, 38, 3, 2, '2026-06-02T19:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES "
                 "(1, 38, 2, 2, '2026-05-19T00:00Z'), (3, 38, 4, 4, '2026-05-19T00:00Z')")
    conn.execute("INSERT INTO xp(player_id, gw, model_version, xp, xminutes, computed_at) VALUES "
                 "(10, 38, 'v1', 7.5, 90, '2026-05-19T00:00Z'), "
                 "(20, 38, 'v1', 4.0, 90, '2026-05-19T00:00Z')")
    conn.commit()
```

If the existing test file has a `_seed_captain_db` helper from S-A.1 task 12, you may consider lifting both to a fixture module or just colocating `_seed_transfer_db` in the same test file.

- [ ] **Step 3: Verify FAIL**

```
.venv/bin/pytest tests/test_telegram.py -v -k "transfer_swap or transfer_summary"
```

- [ ] **Step 4: Modify `src/interface/telegram.py`** — add `_transfer_ai_prose` (sibling of `_captain_ai_prose`) and extend `notify_plan`. Read the file first.

The change (preserving everything else):

```python
def notify_plan(conn, plan, *, mode, session=None):
    """Best-effort: notify per plan entry. When captain/transfer AI prose is cached for the next gw,
    swap the summary; falls back to entry['summary'] otherwise."""
    if not is_configured():
        return
    captain_prose  = _captain_ai_prose(conn)
    transfer_prose = _transfer_ai_prose(conn)
    for entry in plan:
        kind = "executed" if entry["executed"] else "info"
        summary = entry["summary"]
        if entry["decision"] == "captain" and captain_prose is not None:
            summary = captain_prose
        if entry["decision"] == "transfer" and transfer_prose is not None:
            summary = transfer_prose
        notify(conn, kind=kind, decision_type=entry["decision"], mode=mode,
               summary=summary, session=session)


def _transfer_ai_prose(conn):
    """Return cached AI prose for the transfer pane at the next gw, or None.
    Best-effort: any exception is swallowed."""
    try:
        from src.interface import queries
        nxt = conn.execute(
            "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()
        if nxt is None or nxt["gw"] is None:
            return None
        return queries.get_transfer_reasoning(conn, gw=nxt["gw"])
    except Exception:
        return None
```

(Existing `_captain_ai_prose` stays unchanged.)

- [ ] **Step 5: Verify PASS + full suite**

```
.venv/bin/pytest tests/test_telegram.py -v
.venv/bin/pytest -q
```

- [ ] **Step 6: Commit**

```
git add src/interface/telegram.py tests/test_telegram.py
git commit -m "$(cat <<'EOF'
feat(ai): notify_plan swaps transfer summary with AI prose when cached (S-A.2 task 5)

Extends the S-A.1 captain swap pattern to transfer entries. notify_plan
now looks up both _captain_ai_prose and _transfer_ai_prose at the top of
the function (one DB read each), then swaps per entry-type as it iterates
the plan. Both lookups are best-effort: any exception falls back to the
template summary. No badge in the Telegram body (terse channel — same
as captain).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend — TransferIdeas prose + AI badge

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/components/TransferIdeas.svelte`
- Modify: `frontend/src/lib/components/TransferIdeas.svelte.test.ts`
- Modify: `frontend/src/lib/mocks/full.ts`

- [ ] **Step 1: Read** the four files to see current structure.

- [ ] **Step 2: Append failing vitest cases to `TransferIdeas.svelte.test.ts`**:

```ts
import { render, screen } from '@testing-library/svelte';
import { describe, it, expect } from 'vitest';
import TransferIdeas from './TransferIdeas.svelte';

describe('TransferIdeas AI/classic badge + prose', () => {
    it('shows AI badge and prose on the top suggestion when reasoning_source is ai', () => {
        const transfers = {
            suggestions: [
                { out: { player_id: 1, web_name: 'Salah', price: 13.1 },
                  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
                  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78,
                  reasoning: 'AI transfer prose here.', reasoning_source: 'ai' as const },
                { out: { player_id: 3, web_name: 'Isak',     price: 9.3 },
                  in:  { player_id: 4, web_name: 'Watkins',  price: 9.0 },
                  ep_delta_5gw: 1.2, hit_cost: 0, confidence: 65,
                  reasoning: '', reasoning_source: 'classic' as const },
            ],
            empty_reason: null,
            free_transfers: 1,
        };
        render(TransferIdeas, { transfers });
        expect(screen.getByText('AI transfer prose here.')).toBeInTheDocument();
        expect(screen.getByText('AI')).toBeInTheDocument();
    });

    it('shows classic label on top suggestion when reasoning is empty', () => {
        const transfers = {
            suggestions: [
                { out: { player_id: 1, web_name: 'Salah', price: 13.1 },
                  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
                  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78,
                  reasoning: '', reasoning_source: 'classic' as const },
            ],
            empty_reason: null,
            free_transfers: 1,
        };
        render(TransferIdeas, { transfers });
        // No prose line is rendered for empty classic (the chips already convey the data)
        expect(screen.queryByText('AI')).not.toBeInTheDocument();
        expect(screen.queryByText('classic')).not.toBeInTheDocument();
    });

    it('does not show badge on suggestions other than the top', () => {
        const transfers = {
            suggestions: [
                { out: { player_id: 1, web_name: 'Salah', price: 13.1 },
                  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
                  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78,
                  reasoning: 'AI top.', reasoning_source: 'ai' as const },
                { out: { player_id: 3, web_name: 'Isak',     price: 9.3 },
                  in:  { player_id: 4, web_name: 'Watkins',  price: 9.0 },
                  ep_delta_5gw: 1.2, hit_cost: 0, confidence: 65,
                  reasoning: 'AI second.', reasoning_source: 'ai' as const },
            ],
            empty_reason: null,
            free_transfers: 1,
        };
        render(TransferIdeas, { transfers });
        // Only ONE AI badge rendered (on the top suggestion); the second's reasoning is NOT shown
        expect(screen.getAllByText('AI')).toHaveLength(1);
        expect(screen.queryByText('AI second.')).not.toBeInTheDocument();
    });

    it('renders backwards-compat when reasoning fields are absent', () => {
        const transfers = {
            suggestions: [
                { out: { player_id: 1, web_name: 'Salah', price: 13.1 },
                  in:  { player_id: 2, web_name: 'Saka',  price: 10.4 },
                  ep_delta_5gw: 3.4, hit_cost: 0, confidence: 78 },
            ],
            empty_reason: null,
            free_transfers: 1,
        };
        render(TransferIdeas, { transfers });
        // Chips render as today; no badge, no prose line
        expect(screen.getByText('Salah')).toBeInTheDocument();
        expect(screen.queryByText('AI')).not.toBeInTheDocument();
    });
});
```

- [ ] **Step 3: Verify FAIL**

```
cd frontend && npm test -- TransferIdeas
```

- [ ] **Step 4: Update `frontend/src/lib/types.ts` `TransferSuggestion`**

Add the two optional fields:

```ts
export interface TransferSuggestion {
    /* ...existing fields: out, in, ep_delta_5gw, hit_cost, confidence... */
    reasoning?: string;
    reasoning_source?: 'ai' | 'classic';
}
```

(Read the existing definition; only ADD, don't remove.)

- [ ] **Step 5: Update `frontend/src/lib/components/TransferIdeas.svelte`**

Read the current file. The change: in the `{#each transfers.suggestions as s, i ...}` block, after the `<div class="nums tnum">` chip row, add a conditional prose line for `i === 0 && s.reasoning`. Preserve all existing markup + classes.

Sketch:

```svelte
{#each transfers.suggestions as s, i (s.out.player_id + '-' + s.in.player_id)}
    <li class="xfer">
        <div class="move">…existing…</div>
        <div class="nums tnum">…existing chips…</div>
        {#if i === 0 && s.reasoning}
            <div class="why">
                <em>{s.reasoning}</em>
                {#if s.reasoning_source === 'ai'}
                    <span class="badge badge-ai" aria-label="AI-generated reasoning">AI</span>
                {:else}
                    <span class="badge badge-classic" aria-label="Template-based reasoning">classic</span>
                {/if}
            </div>
        {/if}
    </li>
{/each}
```

Add CSS:
```css
.why { margin-top: 6px; font-size: 0.82rem; color: var(--text-dim); line-height: 1.4; }
.why em { font-style: italic; }
.badge { font-size: 0.7em; padding: 0.1em 0.4em; border-radius: 0.3em; margin-left: 0.4em; }
.badge-ai { background: #2563eb; color: white; }
.badge-classic { background: #e5e7eb; color: #4b5563; }
```

(`.badge` classes match `CaptainPicks.svelte` from S-A.1. Consider extracting to a shared component in a future cleanup, not now.)

- [ ] **Step 6: Update `frontend/src/lib/mocks/full.ts`** — find the `transfers.suggestions` array. Add to the first suggestion:

```ts
reasoning: 'Sell Salah, buy Saka — Saka has 2 home fixtures at fdr 2 in the next 3 GWs while Salah faces ARS away at fdr 5.',
reasoning_source: 'ai' as const,
```

Other suggestions: `reasoning: '', reasoning_source: 'classic' as const`.

- [ ] **Step 7: Verify PASS**

```
cd frontend && npm test
```

- [ ] **Step 8: Commit**

```
git add frontend/src/lib/types.ts frontend/src/lib/components/TransferIdeas.svelte frontend/src/lib/components/TransferIdeas.svelte.test.ts frontend/src/lib/mocks/full.ts
git commit -m "$(cat <<'EOF'
feat(ai): TransferIdeas renders prose + AI/classic badge on top suggestion (S-A.2 task 6)

TransferSuggestion type gains optional reasoning + reasoning_source.
The component renders an italic prose line + AI/classic badge below the
chip row of the TOP suggestion only — suggestions #2/#3 keep chip-only
rendering with no badge. Backwards-compatible: when reasoning is empty
(classic on fresh DB) or absent, no prose line is rendered. Mocks updated
for visual smoke in mock mode.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Full test suite green (verification gate)

This task ships nothing new — verify the cumulative state is healthy.

- [ ] **Step 1: Run full pytest**

```
.venv/bin/pytest -q
```

Expected: 483 (pre-S-A.2 baseline post-rounding-fix) + ~20 new from Tasks 0–5 = ~500+ passing.

- [ ] **Step 2: Run full vitest**

```
cd frontend && npm test
```

Expected: 54 (pre-S-A.2 baseline) + 4 new from Task 6 = 58 passing.

- [ ] **Step 3: Confirm no `decision-engine.md` change**

```
git log --name-only main..HEAD | grep -i decision-engine && echo "FAIL: B4 violated" || echo "OK: decision-engine.md untouched"
```

- [ ] **Step 4: If any stabilising fix needed, commit it** with a clear message before proceeding.

---

## Task 8: Final code review

Dispatch via the orchestrator (not a subagent task in the rotation).

Brief for the `pr-review-toolkit:code-reviewer` agent:

> Review the cumulative diff of `feat/phase3-sa2-transfer` vs `main`. This is S-A.2, the second slice in the AI-reasoning family. **Same B-rule profile as S-A.1:** B4 untouched (no `decision-engine.md` change), B7 closed-shape payload, B8 no executor changes, R3 all tests fixtures-only with `StubProvider`.
>
> Focus areas:
> - The `_fixtures_for` helper queries `fixtures` + `fdr` directly — confirm B2 layering is preserved (Interface/Scheduler call AI module, AI module reads Data; no inversion).
> - Grounding check on the richer payload (12-16 numbers) — does the model's fixture-mention pattern reliably ground? Watch for edge cases like opponent short-names matching numeric tokens (unlikely but worth flagging).
> - The empty-prose guard from S-A.1's final-review fix carries over to generate_transfer_prose.
> - Telegram swap: both `_captain_ai_prose` and `_transfer_ai_prose` lookups run on every notify_plan call — 2 DB reads per call, fine for cadence.
> - Frontend: badge logic correctly limited to top suggestion only.
>
> Report Critical / Important / Minor issues + final Assessment.

Apply blocking findings as focused fix commits; re-run tests; iterate until clean.

---

## Task 9: `finishing-a-development-branch`

- [ ] **Step 1: Invoke `superpowers:finishing-a-development-branch`**

The slice convention is Option 1 (merge to main locally). The branch `feat/phase3-sa2-transfer` may need to integrate `origin/main` first if a parallel agent landed more work (check `git status -s -b` for "behind N"). Resolve any conflicts (likely none for S-A.2 — `src/interface/queries.py` was already touched in S-A.1; the new functions append cleanly).

- [ ] **Step 2: Do NOT remove the worktree** — it's harness-owned per the provenance check.

- [ ] **Step 3: Do NOT push** unless the user explicitly asks.

- [ ] **Step 4: Report back to the user with:**
- Final commit count
- pytest + vitest pass counts
- Whether code review surfaced any blockers + what was fixed
- The "ready to push?" question

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| `src/ai/prompts/transfer.txt` + few-shot exemplars | T0 |
| Golden test on exemplar grounding | T0 |
| `_build_transfer_payload` with rich fixtures+status | T1 |
| `_fixtures_for` helper handling BGW + DGW | T1 |
| `_status_for` helper with default fallback | T1 |
| `_build_transfer_prompt` template substitution | T1 |
| `render_transfer_reasoning` (read path) | T2 |
| `generate_transfer_prose` (write path with empty + grounding guards) | T2 |
| `jobs.py` 'transfer' branch + `transfer_decision_fn` | T3 |
| Scheduler `panes=['captain', 'transfer']` | T3 |
| `queries.get_transfer_suggestions` + `get_transfer_reasoning` | T4 |
| `/api/transfers` rewired through queries | T4 |
| Telegram `_transfer_ai_prose` + `notify_plan` swap | T5 |
| Frontend `TransferSuggestion` type + prose + badge + mocks | T6 |
| Full test suite green | T7 |
| Final code review | T8 |
| Finishing a development branch | T9 |

Every spec requirement maps to at least one task. ✓
