# Speculation Insights (User-Curated Knowledge) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user's qualitative football knowledge (new-manager effects, transfer cohesion, player traits — their GW1 Chelsea/Newcastle reads) enter the speculation engine as curated notes, cross-checked against the system's own stats.

**Architecture:** `speculation_notes` table (note + optional team/player) → repository functions → REST endpoints (POST/GET/DELETE) + CLI (`note add/list/rm`) + a `/speculation` dashboard page → the AI spike-signals prompt consumes active notes as qualitative context (grounding preserved: cited numbers must come from the digest) → `speculate --json` gains a deterministic `theses` cross-check section. The user's GW1 insights are seeded verbatim (spec §3.1).

**Tech Stack:** Python 3.14, SQLite, FastAPI, SvelteKit, vitest, pytest.

## Global Constraints

- **Doc-first (B13):** `docs/agent-contract.md` + `docs/runbook.md` updated BEFORE code (Task 1).
- **B4 boundary:** insights feed the speculation/AI layer only — never xP/FDR/decision thresholds.
- **Grounding:** any number in an AI reason must appear in the stats digest (existing validator); insights are qualitative context only.
- **Club data from the DB, never real-world priors** (Wissa-regression pinned in tests).
- **Never commit without the full suite green** (`.venv/bin/pytest -q` + `cd frontend && npm test`); never `git add -A`.
- Commit style: conventional (`feat(scope):`, `docs(scope):`, `test(scope):`).

---

### Task 1: Doc-first — agent-contract + runbook

**Files:**
- Modify: `docs/agent-contract.md` (agent-safe list ~line 102; note command docs after `refresh`; speculate shape)
- Modify: `docs/runbook.md` (one-liner + read-safe list ~line 684)

**Interfaces:** none (docs only).

- [ ] **Step 1: agent-contract.md**

Add `note` to the agent-safe command list (line ~102):

```markdown
       "agent_safe_commands": ["status", "resume", "log", "captain", "transfers", "chips",
                               "squad", "insight", "speculate", "refresh", "note",
                               "freeze-status", "auth-status", "review"],
```

Add after the `refresh` section (v0.25/0.26 marker):

```markdown
### note (speculation insights, v0.26)

    fpl-autopilot note add "newcastle are incredibly good at playing and scoring goals" [--team NEW] [--player Wissa] [--json]
    fpl-autopilot note list [--json]
    fpl-autopilot note rm <id> [--json]

User-curated qualitative knowledge for the AI speculation layer (manager effects,
transfer cohesion, player traits). Agent-safe: writes the local DB only, never FPL.
`--team`/`--player` resolve by short_name/web_name from the DB (club data is never
assumed). `speculate --json` cross-checks each active note against the system's
stats in a `theses` section: {"theses": [{note_id, note, team_short, player_name,
verdict: "matches"|"contradicts"|"neutral", checks: {...}}]} — verdicts are
deterministic code, never the AI.
```

- [ ] **Step 2: runbook.md**

After the "Manual full cycle" paragraph add:

```markdown
**Speculation insights:** `docker compose --project-directory /opt/fpl-autopilot run --rm -T app note list --json` —
the user's curated reads (manager/cohesion/traits); `speculate --json` shows their data cross-check in `theses`.
```

Add `note` to the read-safe list (line ~684).

- [ ] **Step 3: Verify + commit**

Run: `git diff --stat` — expected 2 files modified.

```bash
git add docs/agent-contract.md docs/runbook.md
git commit -m "docs(speculation): note commands + theses in the contract"
```

---

### Task 2: Data layer — speculation_notes

**Files:**
- Modify: `src/data/schema.sql` (new table)
- Modify: `src/data/repository.py` (3 functions after `backfill_player_gw_stats`)
- Create: `tests/test_speculation_notes.py`

**Interfaces:**
- Produces: `repository.add_speculation_note(conn, note, team_id=None, player_id=None) -> int`; `repository.list_speculation_notes(conn, active_only=True) -> list[dict]` (keys: id, note, team_id, player_id, team_short, player_name, created_at, active); `repository.deactivate_speculation_note(conn, note_id) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_speculation_notes.py`:

```python
"""v0.26: user-curated speculation insights — repository round-trip."""
from src.data import repository


def test_note_round_trip(db):
    db.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE')")
    db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
               "VALUES (7,'Morgan Rogers','Rogers',1,'MID','a')")
    db.commit()
    nid = repository.add_speculation_note(db, "new manager xabi alonso is pretty good",
                                          team_id=1, player_id=7)
    notes = repository.list_speculation_notes(db)
    assert len(notes) == 1
    n = notes[0]
    assert n["id"] == nid
    assert n["team_short"] == "CHE" and n["player_name"] == "Rogers"
    assert n["active"] == 1 and n["created_at"]


def test_note_list_joins_teams_and_players(db):
    db.execute("INSERT INTO teams (id, name, short_name) VALUES (2,'Newcastle','NEW')")
    db.commit()
    repository.add_speculation_note(db, "newcastle incredibly good", team_id=2)
    repository.add_speculation_note(db, "loose note")
    notes = repository.list_speculation_notes(db)
    assert len(notes) == 2
    assert notes[0]["team_short"] == "NEW"      # newest first
    assert notes[1]["team_short"] is None


def test_note_deactivate(db):
    nid = repository.add_speculation_note(db, "rogers takes long shots")
    assert repository.deactivate_speculation_note(db, nid) is True
    assert repository.list_speculation_notes(db) == []
    assert len(repository.list_speculation_notes(db, active_only=False)) == 1
    assert repository.deactivate_speculation_note(db, nid) is False  # already off
    assert repository.deactivate_speculation_note(db, 9999) is False  # unknown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_speculation_notes.py`
Expected: FAIL — `no such table: speculation_notes`.

- [ ] **Step 3: Implement**

In `src/data/schema.sql`, append:

```sql
CREATE TABLE IF NOT EXISTS speculation_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note TEXT NOT NULL,
  team_id INTEGER,
  player_id INTEGER,
  created_at TIMESTAMP,
  active BOOLEAN NOT NULL DEFAULT 1
);
```

(`init_db` runs schema.sql on every connect — no ALTER migration needed for a new table.)

In `src/data/repository.py`, after `backfill_player_gw_stats` (end of file), add:

```python
def add_speculation_note(conn, note, team_id=None, player_id=None):
    """Store a user-curated speculation insight (v0.26). Returns the new id."""
    cur = conn.execute(
        "INSERT INTO speculation_notes (note, team_id, player_id, created_at, active) "
        "VALUES (?,?,?,?,1)",
        (note, team_id, player_id, _now()))
    conn.commit()
    return cur.lastrowid


def list_speculation_notes(conn, active_only=True):
    """Speculation notes joined for display (team short + player web_name), newest first."""
    where = "WHERE n.active=1" if active_only else ""
    return [dict(r) for r in conn.execute(
        f"""SELECT n.id, n.note, n.team_id, n.player_id, n.created_at, n.active,
                   t.short_name AS team_short, p.web_name AS player_name
            FROM speculation_notes n
            LEFT JOIN teams t ON t.id = n.team_id
            LEFT JOIN players p ON p.id = n.player_id
            {where} ORDER BY n.created_at DESC""")]


def deactivate_speculation_note(conn, note_id):
    """Soft-delete a speculation note. True when a row was deactivated."""
    cur = conn.execute(
        "UPDATE speculation_notes SET active=0 WHERE id=? AND active=1", (note_id,))
    conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_speculation_notes.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/data/schema.sql src/data/repository.py tests/test_speculation_notes.py
git commit -m "feat(speculation): speculation_notes store (add/list/deactivate)"
```

---

### Task 3: API endpoints + CORS + B10

**Files:**
- Modify: `src/interface/api.py` (CORS DELETE + 3 endpoints)
- Create: `tests/test_speculation_notes_api.py`

**Interfaces:**
- Consumes: repository functions (Task 2).
- Produces: `POST /api/speculation/notes` (400 empty note; returns `{"note": {...}}`), `GET /api/speculation/notes` (`{"notes": [...]}`), `DELETE /api/speculation/notes/{id}` (404 unknown). All note mutations write an activity_log entry (B10).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_speculation_notes_api.py`:

```python
"""v0.26: speculation notes API (dashboard form backend)."""
import json


def _seed(db):
    db.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE')")
    db.commit()


def test_notes_empty_list(client):
    r = client.get("/api/speculation/notes")
    assert r.status_code == 200
    assert r.json() == {"notes": []}


def test_notes_post_get_delete(client):
    r = client.post("/api/speculation/notes",
                    json={"note": "xabi alonso is pretty good", "team_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["note"]["team_short"] == "CHE"
    nid = body["note"]["id"]

    r = client.get("/api/speculation/notes")
    assert [n["id"] for n in r.json()["notes"]] == [nid]

    r = client.delete(f"/api/speculation/notes/{nid}")
    assert r.status_code == 200
    assert client.get("/api/speculation/notes").json() == {"notes": []}


def test_notes_post_empty_note_rejected(client):
    r = client.post("/api/speculation/notes", json={"note": "   "})
    assert r.status_code == 400


def test_notes_delete_unknown_404(client):
    r = client.delete("/api/speculation/notes/9999")
    assert r.status_code == 404


def test_notes_activity_logged(db, client):
    client.post("/api/speculation/notes", json={"note": "rogers takes long shots"})
    rows = db.execute("SELECT decision_type, action_taken FROM activity_log").fetchall()
    assert rows and rows[0]["decision_type"] == "speculation"
    assert "rogers takes long shots" in rows[0]["action_taken"]
```

Note: the `client` fixture (tests/conftest.py) auto-creates `db`; the team row must exist before POST (the test seeds via the `db` fixture — add `db` as a fixture param alongside `client` where needed).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_speculation_notes_api.py`
Expected: FAIL — 404 on `/api/speculation/notes`.

- [ ] **Step 3: Implement**

In `src/interface/api.py`:
- CORS (line 15): `allow_methods=["GET", "POST", "DELETE"],`
- Append the endpoints at the end of the file:

```python
@app.get("/api/speculation/notes")
def speculation_notes(conn=Depends(get_db)):
    return {"notes": repository.list_speculation_notes(conn)}


@app.post("/api/speculation/notes")
def add_speculation_note(payload: dict, conn=Depends(get_db)):
    note = str((payload or {}).get("note") or "").strip()
    if not note:
        return JSONResponse(status_code=400, content={"detail": "note must be non-empty"})
    team_id = (payload or {}).get("team_id")
    player_id = (payload or {}).get("player_id")
    nid = repository.add_speculation_note(conn, note, team_id=team_id, player_id=player_id)
    repository.log_activity(conn, decision_type="speculation", mode="manual",
                            action_taken=f"note add: {note[:80]}", executed=True,
                            inputs={"note_id": nid})
    row = [n for n in repository.list_speculation_notes(conn) if n["id"] == nid]
    return {"note": row[0] if row else None}


@app.delete("/api/speculation/notes/{note_id}")
def delete_speculation_note(note_id: int, conn=Depends(get_db)):
    if not repository.deactivate_speculation_note(conn, note_id):
        return JSONResponse(status_code=404, content={"detail": "note not found"})
    repository.log_activity(conn, decision_type="speculation", mode="manual",
                            action_taken=f"note rm: id {note_id}", executed=True,
                            inputs={"note_id": note_id})
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_speculation_notes_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/interface/api.py tests/test_speculation_notes_api.py
git commit -m "feat(api): speculation notes endpoints (POST/GET/DELETE) + B10 log"
```

---

### Task 4: CLI — note add/list/rm

**Files:**
- Modify: `src/cli.py` (subcommand parsers ~line 1494; dispatch ~line 1566; agent-safe lists ~lines 111 and 230)
- Create: `tests/test_cli_note.py`

**Interfaces:**
- Consumes: repository functions (Task 2).
- Produces: `fpl-autopilot note add "..." [--team SHORT] [--player NAME] [--json]`; `note list [--json]`; `note rm <id> [--json]`. Agent-safe.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_note.py`:

```python
"""v0.26: note add/list/rm CLI."""
import json

from src import cli


def _seed(db):
    db.execute("INSERT INTO teams (id, name, short_name) VALUES (1,'Chelsea','CHE')")
    db.execute("INSERT INTO players (id, name, web_name, team_id, position, status) "
               "VALUES (7,'Morgan Rogers','Rogers',1,'MID','a')")
    db.commit()


def test_note_add_resolves_team_and_player(db, capsys):
    _seed(db)
    cli.main(["note", "add", "rogers takes long shots", "--team", "CHE", "--player", "Rogers"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    note = out["data"]["note"]
    assert note["team_short"] == "CHE" and note["player_name"] == "Rogers"


def test_note_add_unknown_team_errors(db, capsys):
    cli.main(["note", "add", "x", "--team", "ZZZ"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NOT_FOUND"


def test_note_list_and_rm(db, capsys):
    from src.data import repository
    nid = repository.add_speculation_note(db, "newcastle incredibly good")
    cli.main(["note", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert [n["id"] for n in out["data"]["notes"]] == [nid]
    cli.main(["note", "rm", str(nid), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["removed"] is True
    cli.main(["note", "rm", "9999", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["error"]["code"] == "E_NOT_FOUND"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_cli_note.py`
Expected: FAIL — argparse error (unknown command `note`).

- [ ] **Step 3: Implement**

In `src/cli.py`:

Parsers (after `p_speculate`, ~line 1496):

```python
    p_note = sub.add_parser("note", help="speculation insights (user-curated; agent-safe)")
    note_sub = p_note.add_subparsers(dest="note_command", required=True)
    p_note_add = note_sub.add_parser("add", help="add a speculation insight")
    p_note_add.add_argument("note", help="free text, e.g. 'xabi alonso is pretty good'")
    p_note_add.add_argument("--team", help="team short name (e.g. CHE)")
    p_note_add.add_argument("--player", help="player web_name (e.g. Rogers)")
    p_note_add.add_argument("--json", action="store_true")
    p_note_list = note_sub.add_parser("list", help="list active speculation insights")
    p_note_list.add_argument("--json", action="store_true")
    p_note_rm = note_sub.add_parser("rm", help="deactivate a speculation insight by id")
    p_note_rm.add_argument("id", type=int)
    p_note_rm.add_argument("--json", action="store_true")
```

Handler (near `_cmd_speculate_cli`):

```python
def _cmd_note_cli(args, conn=None, cfg=None):
    """note add/list/rm — user-curated speculation insights (v0.26)."""
    from .data import repository
    cfg = cfg or load_config()
    owns = conn is None
    conn = conn or connect(cfg_db_path(cfg))
    init_db(conn)
    try:
        if args.note_command == "add":
            team_id = player_id = None
            if args.team:
                row = conn.execute("SELECT id FROM teams WHERE short_name=?", (args.team,)).fetchone()
                if row is None:
                    return _json_err("note", "E_NOT_FOUND", f"unknown team {args.team!r}",
                                     "use a short_name from the DB (e.g. CHE)")
                team_id = row["id"]
            if args.player:
                q = "SELECT id FROM players WHERE web_name=?"
                qp = [args.player]
                if team_id is not None:
                    q += " AND team_id=?"
                    qp.append(team_id)
                row = conn.execute(q, qp).fetchone()
                if row is None:
                    return _json_err("note", "E_NOT_FOUND", f"unknown player {args.player!r}",
                                     "use a web_name from the DB (club data is never assumed)")
                player_id = row["id"]
            nid = repository.add_speculation_note(conn, args.note,
                                                  team_id=team_id, player_id=player_id)
            repository.log_activity(conn, decision_type="speculation", mode="manual",
                                    action_taken=f"note add: {args.note[:80]}", executed=True,
                                    inputs={"note_id": nid})
            row = [n for n in repository.list_speculation_notes(conn) if n["id"] == nid][0]
            if args.json:
                _json_ok("note", {"note": row})
            else:
                print(f"  note #{row['id']} added: {row['note']}")
                if row["team_short"]:
                    print(f"    team: {row['team_short']}" +
                          (f", player: {row['player_name']}" if row["player_name"] else ""))
        elif args.note_command == "list":
            notes = repository.list_speculation_notes(conn)
            if args.json:
                _json_ok("note", {"notes": notes})
            else:
                for n in notes:
                    scope = " | ".join(x for x in (n["team_short"], n["player_name"]) if x)
                    print(f"  #{n['id']} [{scope}] {n['note']}")
        elif args.note_command == "rm":
            if not repository.deactivate_speculation_note(conn, args.id):
                return _json_err("note", "E_NOT_FOUND", f"note {args.id} not found",
                                 "note ids come from 'note list'")
            repository.log_activity(conn, decision_type="speculation", mode="manual",
                                    action_taken=f"note rm: id {args.id}", executed=True,
                                    inputs={"note_id": args.id})
            if args.json:
                _json_ok("note", {"removed": True})
            else:
                print(f"  note #{args.id} removed")
    finally:
        if owns:
            conn.close()
```

Dispatch (in `main()`, near the speculate branch ~line 1566):

```python
    elif args.command == "note":
        _cmd_note_cli(args)
```

Agent-safe lists: add `"note"` to the two lists (`resume` output ~line 111 and the agent_safe_commands output ~line 230).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_cli_note.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_note.py
git commit -m "feat(cli): note add/list/rm — speculation insights (agent-safe)"
```

---

### Task 5: Theses cross-check + spikes prompt integration

**Files:**
- Create: `src/ai/squad/theses.py`
- Modify: `src/ai/squad/spikes.py` (`build_spikes_prompt` + `generate_spike_signals` insights; cache hash includes insights)
- Modify: `src/cli.py` (`_cmd_speculate_cli` adds `theses`)
- Create: `tests/test_theses.py`
- Modify: `tests/test_ai_squad_spikes.py` (insight prompt + grounding tests)

**Interfaces:**
- Consumes: `repository.list_speculation_notes` (Task 2).
- Produces: `theses.build_theses(conn) -> list[dict]` `{note_id, note, team_short, player_name, verdict, checks}`; `spikes.build_spikes_prompt(digest, insights=None) -> str`; `generate_spike_signals` loads notes + includes them in the cache hash.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_theses.py`:

```python
"""v0.26: deterministic theses cross-check for user speculation insights."""
from src.ai.squad import theses
from src.data import repository


def _seed(db):
    db.executemany("INSERT INTO teams (id, name, short_name) VALUES (?,?,?)",
                   [(1, "Chelsea", "CHE"), (2, "Newcastle", "NEW")])
    db.executemany("INSERT INTO players (id, name, web_name, team_id, position, status) "
                   "VALUES (?,?,?,?,?, 'a')",
                   [(7, "Morgan Rogers", "Rogers", 1, "MID"),
                    (9, "Wissa", "Wissa", 2, "FWD")])
    db.execute("INSERT INTO gameweeks (id, name, finished) VALUES (1,'GW1',1),(2,'GW2',0)")
    db.execute("INSERT INTO fixtures (id, gw, home_team_id, away_team_id, finished, "
               "home_score, away_score) VALUES (1,1,2,1,1,2,3)")
    db.execute("INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at) "
               "VALUES (1,2,3,3,'t'),(2,2,2,4,'t')")
    db.executemany(
        """INSERT INTO player_gw_stats (player_id, gw, fixture_id, minutes,
           goals_scored, assists, clean_sheets, bonus, total_points, starts, saves,
           bps, expected_goals, expected_assists, expected_goals_conceded,
           defensive_contribution, yellow_cards, red_cards, settled_at)
           VALUES (?,1,1,?,0,0,0,0,?,1,0,20,?,0.1,1.4,2,0,0,'t')""",
        [(7, 90, 8, 0.597), (9, 90, 4, 1.0)])
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, "
               "xassists, xcs, computed_at) VALUES (7,2,'v2',4.0,70,0.5,0.2,0,'t')")
    db.commit()


def test_theses_player_checks(db):
    _seed(db)
    repository.add_speculation_note(db, "rogers takes long shots", team_id=1, player_id=7)
    t = theses.build_theses(db)[0]
    assert t["player_name"] == "Rogers" and t["team_short"] == "CHE"
    c = t["checks"]["player"]
    assert c["gw1_minutes"] == 90 and c["gw1_xg"] == 0.597
    assert c["xp_next"] == 4.0


def test_theses_team_checks_use_db_clubs(db):
    """Club data comes from the DB — a note scoped to NEW resolves to Newcastle."""
    _seed(db)
    repository.add_speculation_note(db, "newcastle incredibly good", team_id=2)
    t = theses.build_theses(db)[0]
    assert t["team_short"] == "NEW"
    assert t["checks"]["team"]["last_result"] == "NEW 2-2 CHE"
    assert len(t["checks"]["team"]["next3"]) == 1  # one upcoming fixture seeded


def test_theses_verdict_contradicts_zero_live_starts(db):
    _seed(db)
    db.execute("UPDATE player_gw_stats SET starts=0, minutes=0 WHERE player_id=9")
    db.commit()
    repository.add_speculation_note(db, "wissa starts every week", team_id=2, player_id=9)
    t = theses.build_theses(db)[0]
    assert t["verdict"] == "contradicts"


def test_theses_verdict_neutral_without_player(db):
    _seed(db)
    repository.add_speculation_note(db, "xabi alonso is pretty good", team_id=1)
    assert theses.build_theses(db)[0]["verdict"] == "neutral"
```

Modify `tests/test_ai_squad_spikes.py` — append:

```python
def test_spikes_prompt_includes_insights(db, monkeypatch):
    from src.ai.squad import spikes as spikes_mod
    notes = [{"note": "xabi alonso is pretty good", "team": "CHE", "player": None}]
    digest = _digest()
    prompt = spikes_mod.build_spikes_prompt(digest, insights=notes)
    assert "User insights" in prompt
    assert "xabi alonso is pretty good" in prompt


def test_spikes_grounding_still_rejects_insight_numbers(db, monkeypatch):
    """Insight text is context only: a reason citing a number NOT in the player
    digest is still rejected; citing edge numbers + mentioning the insight passes."""
    from src.ai.squad import spikes as spikes_mod
    digest = _digest()
    payload = {"spikes": [{"player_id": 0, "level": "high",
                           "reason": "10.5 long shots with the new-manager thesis"}],
               "drops": [], "market_read": "m"}
    problems = spikes_mod.validate_signals(payload, _pool(), digest)
    assert any("not in player data" in p for p in problems)
    payload["spikes"][0]["reason"] = "in 48.1 transfers with the new-manager thesis"
    problems = spikes_mod.validate_signals(payload, _pool(), digest)
    assert problems == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_theses.py tests/test_ai_squad_spikes.py`
Expected: FAIL — `theses` module missing; prompt lacks insights.

- [ ] **Step 3: Implement**

Create `src/ai/squad/theses.py`:

```python
"""Deterministic cross-check for user speculation insights (v0.26).

The user's qualitative reads (new manager, cohesion, player traits) are verified
against the system's own numbers. Club data ALWAYS comes from the DB — real-world
priors are never assumed (observed 2026-08-26: Wissa mis-attributed to Brentford
by contamination; the DB says Newcastle).
"""
from src.data import repository
from src.analytics import ratings


def _player_checks(conn, player_id):
    g1 = conn.execute(
        """SELECT minutes, starts, expected_goals, total_points
           FROM player_gw_stats WHERE player_id=? AND gw=1""", (player_id,)).fetchone()
    live = conn.execute(
        """SELECT COUNT(DISTINCT gw) AS gws, COALESCE(SUM(starts),0) AS st
           FROM player_gw_stats WHERE player_id=? AND starts IS NOT NULL""",
        (player_id,)).fetchone()
    xp = conn.execute(
        """SELECT xp FROM xp WHERE player_id=? AND model_version='v2'
           AND gw=(SELECT MIN(id) FROM gameweeks WHERE finished=0)""",
        (player_id,)).fetchone()
    us = conn.execute("SELECT xg_per_90 FROM understat_players WHERE fpl_player_id=?",
                      (player_id,)).fetchone()
    return {
        "gw1_minutes": g1["minutes"] if g1 else None,
        "gw1_starts": g1["starts"] if g1 else None,
        "gw1_xg": g1["expected_goals"] if g1 else None,
        "gw1_points": g1["total_points"] if g1 else None,
        "live_gws": live["gws"] if live else 0,
        "live_starts": live["st"] if live else 0,
        "xp_next": xp["xp"] if xp else None,
        "xg_per_90": us["xg_per_90"] if us else None,
    }


def _team_checks(conn, team_id):
    last = conn.execute(
        """SELECT th.short_name h, ta.short_name a, f.home_score, f.away_score
           FROM fixtures f JOIN teams th ON th.id=f.home_team_id
           JOIN teams ta ON ta.id=f.away_team_id
           WHERE f.finished=1 AND (f.home_team_id=? OR f.away_team_id=?)
           ORDER BY f.gw DESC LIMIT 1""", (team_id, team_id)).fetchone()
    result = None
    if last is not None:
        result = (f"{last['h']} {last['home_score']}-{last['away_score']} {last['a']}"
                  if last["home_score"] is not None else f"{last['h']} v {last['a']}")
    next3 = [dict(r) for r in conn.execute(
        """SELECT f.gw, th.short_name h, ta.short_name a
           FROM fixtures f JOIN teams th ON th.id=f.home_team_id
           JOIN teams ta ON ta.id=f.away_team_id
           WHERE f.gw >= (SELECT MIN(id) FROM gameweeks WHERE finished=0)
             AND (f.home_team_id=? OR f.away_team_id=?)
           ORDER BY f.gw LIMIT 3""", (team_id, team_id))]
    tr, _la = ratings.compute_team_ratings(conn)
    team = tr.get(team_id)
    return {"last_result": result,
            "next3": [f"{r['h']} v {r['a']}" for r in next3],
            "xg90": team.xg90 if team else None}


def _verdict(checks):
    """Heuristic, deterministic, no AI: a player scoped note contradicts data when
    the player has live GWs but zero starts; everything else is neutral."""
    c = checks.get("player")
    if c and c.get("live_gws") and c.get("live_starts") == 0:
        return "contradicts"
    return "neutral"


def build_theses(conn):
    """{note_id, note, team_short, player_name, verdict, checks} per active note."""
    out = []
    for n in repository.list_speculation_notes(conn):
        checks = {}
        if n["player_id"]:
            checks["player"] = _player_checks(conn, n["player_id"])
        elif n["team_id"]:
            checks["team"] = _team_checks(conn, n["team_id"])
        out.append({"note_id": n["id"], "note": n["note"],
                    "team_short": n["team_short"], "player_name": n["player_name"],
                    "verdict": _verdict(checks), "checks": checks})
    return out
```

Modify `src/ai/squad/spikes.py`:
- Add `from src.data import repository` to the imports.
- `build_spikes_prompt`:

```python
def build_spikes_prompt(digest: dict, insights=None) -> str:
    template = (_PROMPTS_DIR / "spikes.md").read_text()
    prompt = template.replace("<DIGEST_JSON>", json.dumps(digest, sort_keys=True, indent=2))
    if insights:
        prompt += ("\n\n## User insights (qualitative context only)\n"
                   "The user watches the matches and may hold manager/cohesion/trait "
                   "reads. You MAY reference an insight qualitatively in a reason, but "
                   "every number you cite must still come from the DIGEST JSON above "
                   "(copy verbatim).\n"
                   + json.dumps(insights, sort_keys=True, indent=2))
    return prompt
```

- `generate_spike_signals` (after the digest/gw guard, before the cache hash):

```python
    notes = repository.list_speculation_notes(conn)
    insights = [{"note": n["note"], "team": n["team_short"], "player": n["player_name"]}
                for n in notes]
    rec_hash = cache.recommendation_hash({"digest": digest, "insights": insights})
    ...
    prompt = build_spikes_prompt(digest, insights=insights)
```

(Replace the existing `rec_hash = cache.recommendation_hash(digest)` and `prompt = build_spikes_prompt(digest)` lines.)

Modify `src/cli.py` `_cmd_speculate_cli` — after computing `differentials`, add `theses`:

```python
        from .ai.squad.theses import build_theses
        _json_ok("speculate", {"gw": gw, "signals": signals,
                               "differentials": differentials,
                               "theses": build_theses(conn),
                               "data_basis": _data_basis(conn, cfg)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_theses.py tests/test_ai_squad_spikes.py tests/test_cli_agent.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai/squad/theses.py src/ai/squad/spikes.py src/cli.py tests/test_theses.py tests/test_ai_squad_spikes.py
git commit -m "feat(speculation): theses cross-check + insight context in spike signals"
```

---

### Task 6: Dashboard — /speculation page

**Files:**
- Modify: `frontend/src/lib/types.ts` (SpeculationNote)
- Modify: `frontend/src/lib/api/client.ts` (fetchNotes/postNote/deleteNote)
- Create: `frontend/src/routes/speculation/+page.svelte`, `frontend/src/routes/speculation/page.svelte.test.ts`
- Modify: `frontend/src/routes/speculation/+page.ts` (empty data loader, mirroring squad-builder)

**Interfaces:**
- Consumes: API endpoints (Task 3).
- Produces: `/speculation` page — note form (text + team select + player select filtered by team) + active notes list with delete.

- [ ] **Step 1: Write the failing vitest**

Create `frontend/src/routes/speculation/page.svelte.test.ts`:

```ts
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import Page from './+page.svelte';

afterEach(() => vi.unstubAllGlobals());

describe('speculation page', () => {
	it('lists notes and submits a new one', async () => {
		const notes = [
			{ id: 1, note: 'xabi alonso is pretty good', team_id: 1, player_id: null,
			  team_short: 'CHE', player_name: null, created_at: 't', active: true }
		];
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ note: notes[0] }) }) // POST
			.mockResolvedValue({ ok: true, json: async () => ({ notes }) });               // GETs
		vi.stubGlobal('fetch', fetchMock);
		render(Page, { props: { data: {} } });
		await waitFor(() => screen.getByText(/xabi alonso is pretty good/));
		await fireEvent.input(screen.getByLabelText(/insight/i), { target: { value: 'new note' } });
		await fireEvent.click(screen.getByRole('button', { name: /add/i }));
		await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'POST')).toBe(true));
	});
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test` in `frontend/`
Expected: FAIL — `speculation/+page.svelte` not found.

- [ ] **Step 3: Implement**

`frontend/src/lib/types.ts` — append:

```ts
export interface SpeculationNote {
	id: number;
	note: string;
	team_id: number | null;
	player_id: number | null;
	team_short: string | null;
	player_name: string | null;
	created_at: string;
	active: boolean;
}
```

`frontend/src/lib/api/client.ts` — append (mirror the existing error handling style of `getJson`):

```ts
export async function fetchNotes(fetchFn: Fetch = fetch): Promise<SpeculationNote[]> {
	const data = await getJson<{ notes: SpeculationNote[] }>('/api/speculation/notes', fetchFn);
	return data.notes;
}

export async function postNote(
	payload: { note: string; team_id: number | null; player_id: number | null },
	fetchFn: Fetch = fetch
): Promise<SpeculationNote> {
	const res = await fetchFn(`${API_BASE}/api/speculation/notes`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(payload)
	});
	if (!res.ok) throw new Error(`POST /api/speculation/notes failed: ${res.status}`);
	return (await res.json()).note as SpeculationNote;
}

export async function deleteNote(id: number, fetchFn: Fetch = fetch): Promise<void> {
	const res = await fetchFn(`${API_BASE}/api/speculation/notes/${id}`, { method: 'DELETE' });
	if (!res.ok) throw new Error(`DELETE /api/speculation/notes/${id} failed: ${res.status}`);
}
```

(Add `SpeculationNote` to the types import at the top of client.ts.)

Create `frontend/src/routes/speculation/+page.svelte`:

```svelte
<script lang="ts">
	import { onMount } from 'svelte';
	import { deleteNote, fetchNotes, postNote } from '$lib/api/client';
	import type { SpeculationNote } from '$lib/types';

	let notes = $state<SpeculationNote[]>([]);
	let noteText = $state('');
	let teamId = $state<number | null>(null);
	let playerId = $state<number | null>(null);
	let teams = $state<{ id: number; short_name: string }[]>([]);
	let players = $state<{ id: number; web_name: string }[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			[notes, teams] = await Promise.all([fetchNotes(), fetchTeams()]);
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});

	async function fetchTeams() {
		const res = await fetch('/api/speculation/teams');
		return res.ok ? (await res.json()).teams : [];
	}

	async function onTeamChange() {
		playerId = null;
		players = teamId ? await fetchPlayers(teamId) : [];
	}

	async function fetchPlayers(tid: number) {
		const res = await fetch(`/api/speculation/players?team_id=${tid}`);
		return res.ok ? (await res.json()).players : [];
	}

	async function submit() {
		if (!noteText.trim()) return;
		await postNote({ note: noteText.trim(), team_id: teamId, player_id: playerId });
		noteText = '';
		notes = await fetchNotes();
	}

	async function remove(id: number) {
		await deleteNote(id);
		notes = await fetchNotes();
	}
</script>

<svelte:head><title>Speculation — FPL Autopilot</title></svelte:head>

<div class="speculation-page">
	<h1>Speculation Insights</h1>
	<p class="muted">Your match-watching reads (managers, cohesion, traits) — the system
		cross-checks them against its own stats in <code>speculate --json</code> (theses).</p>

	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<p class="muted">Could not load notes.</p>
	{:else}
		<form onsubmit={(e) => { e.preventDefault(); submit(); }}>
			<label>
				Insight
				<textarea bind:value={noteText} rows="2" placeholder="e.g. xabi alonso is pretty good"></textarea>
			</label>
			<label>
				Team
				<select bind:value={teamId} onchange={onTeamChange}>
					<option value={null}>— none —</option>
					{#each teams as t}
						<option value={t.id}>{t.short_name}</option>
					{/each}
				</select>
			</label>
			{#if teamId}
				<label>
					Player
					<select bind:value={playerId}>
						<option value={null}>— none —</option>
						{#each players as p}
							<option value={p.id}>{p.web_name}</option>
						{/each}
					</select>
				</label>
			{/if}
			<button type="submit" disabled={!noteText.trim()}>Add</button>
		</form>

		<h2>Active insights</h2>
		{#if notes.length === 0}
			<p class="muted">No insights yet — add your first read above.</p>
		{/if}
		<ul>
			{#each notes as n (n.id)}
				<li>
					<span class="scope">{[n.team_short, n.player_name].filter(Boolean).join(' · ')}</span>
					{n.note}
					<button onclick={() => remove(n.id)} aria-label={`remove note ${n.id}`}>✕</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
```

Create `frontend/src/routes/speculation/+page.ts`:

```ts
export const prerender = false;
```

Also add the two read endpoints used by the form to `src/interface/api.py` (Task 3 pattern):

```python
@app.get("/api/speculation/teams")
def speculation_teams(conn=Depends(get_db)):
    return {"teams": [dict(r) for r in conn.execute(
        "SELECT id, short_name FROM teams ORDER BY short_name")]}


@app.get("/api/speculation/players")
def speculation_players(team_id: int, conn=Depends(get_db)):
    return {"players": [dict(r) for r in conn.execute(
        "SELECT id, web_name FROM players WHERE team_id=? ORDER BY web_name", (team_id,))]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` in `frontend/`; `.venv/bin/pytest -q tests/test_speculation_notes_api.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api/client.ts \
        frontend/src/routes/speculation/ src/interface/api.py
git commit -m "feat(dashboard): speculation insights page (form + list + delete)"
```

---

### Task 7: Seed the user's insights + smoke + full suite

**Files:**
- Create: `docs/research/calibration/seed_speculation_notes.py`

- [ ] **Step 1: Write the seed script**

Create `docs/research/calibration/seed_speculation_notes.py`:

```python
"""Seed the user's GW1-26/27 speculation insights (spec §3.1, v0.26).

Idempotent: a note whose text already exists (exact match) is not re-added.
Usage: .venv/bin/python docs/research/calibration/seed_speculation_notes.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.data.db import connect, init_db          # noqa: E402
from src.data import repository                    # noqa: E402

SEEDS = [
    ("newcastle are incredibly good at playing and scoring goals.. from newcastle "
     "there are some players that really good like wissa", "NEW", None),
    ("from chelsea there's a lot including morgan rogers, joao pedro, neto, palmer.. "
     "but the most promising one is morgan rogers", "CHE", None),
    ("chelsea and newcastle had new manager, chelsea had xabi alonso and that's pretty good",
     "CHE", None),
    ("that new good manager combined with good player transfers like morgan rogers "
     "create really good cohesion and good performance", "CHE", None),
    ("this manager had really good track record, chelsea appointed him for new season, "
     "we speculate that chelsea under that manager will perform really well, so we "
     "choose players from chelsea", "CHE", None),
    ("morgan rogers is really good when playing in previous season and oftenly take "
     "long shot so he had really high chances of scoring and expected goals, plus "
     "under xabi alonso there'll be really good performance of him", "CHE", "Rogers"),
]


def seed(conn):
    added = 0
    for note, team_short, player_name in SEEDS:
        if conn.execute("SELECT 1 FROM speculation_notes WHERE note=? LIMIT 1",
                        (note,)).fetchone():
            continue
        team_id = player_id = None
        if team_short:
            team_id = conn.execute("SELECT id FROM teams WHERE short_name=?",
                                   (team_short,)).fetchone()["id"]
        if player_name:
            player_id = conn.execute(
                "SELECT id FROM players WHERE web_name=? AND team_id=?",
                (player_name, team_id)).fetchone()["id"]
        repository.add_speculation_note(conn, note, team_id=team_id, player_id=player_id)
        added += 1
    return added


if __name__ == "__main__":
    conn = connect("data/fpl_autopilot.db")
    init_db(conn)
    print("seeded:", seed(conn))
```

- [ ] **Step 2: Run the seed locally + smoke**

```bash
.venv/bin/python docs/research/calibration/seed_speculation_notes.py
.venv/bin/fpl-autopilot note list --json
.venv/bin/fpl-autopilot speculate --json | python3 -c "import json,sys; d=json.load(sys.stdin)['data']; print('theses:', len(d['theses'])); [print(' ', t['team_short'], t['player_name'], t['verdict'], t['note'][:60]) for t in d['theses']]"
```

Expected: 6 notes seeded; `note list` shows them; `speculate` shows 6 theses with team/player scopes and verdicts (Rogers + Wissa player checks; CHE/NEW team checks with `last_result` like "FUL 2-3 CHE").

(If `speculate` needs an AI provider and one is unavailable locally, the smoke instead asserts `theses` via a direct call: `.venv/bin/python -c "from src.ai.squad.theses import build_theses; from src.data.db import connect, init_db; c=connect('data/fpl_autopilot.db'); init_db(c); print(build_theses(c))"`.)

- [ ] **Step 3: Full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

Run: `npm test` in `frontend/`
Expected: 77 + new vitest pass.

- [ ] **Step 4: Commit + push**

```bash
git add docs/research/calibration/seed_speculation_notes.py
git commit -m "feat(speculation): seed the user's GW1 insights (CHE/NEW theses)"
git push origin main
```

- [ ] **Step 5: Deploy the seed on jumbo**

After CI deploys:

```bash
ssh jumbo 'docker compose --project-directory /opt/fpl-autopilot run --rm -T app python3 /app/docs/research/calibration/seed_speculation_notes.py'
ssh jumbo 'docker compose --project-directory /opt/fpl-autopilot run --rm -T app note list --json'
```

(If the container image doesn't include docs/, copy the script in with `docker cp` or run its logic via a python3 one-liner using the same SEEDS.)

- [ ] **Step 6: Report**

Summarize: the store, the API/CLI/dashboard surfaces, the theses cross-check output for the user's 6 insights (Rogers 0.597 xG/90 ✓, CHE 3-2 win ✓, Wissa 1.0 xG ✓), and that `speculate` now includes their reads as verified context.
