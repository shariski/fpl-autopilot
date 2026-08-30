# Speculation Insights (User-Curated Knowledge for the Speculation Engine) — Design

**Date:** 2026-08-26 · **Status:** approved in brainstorming · **Scope:** AI/speculation layer + interface (B2: never the deterministic decision engine — B4 boundary)

## 1. Problem

The speculation engine (`src/ai/squad/spikes.py`) generates spike/drop signals purely from stats (transfers in/out, form, xP, fixtures). It cannot express qualitative football knowledge: a new manager with a strong track record (Chelsea + Xabi Alonso), transfer-driven cohesion (Chelsea's Rogers/João Pedro/Neto/Palmer), or a player's shot-taking trait (Rogers' long shots → 0.597 xG/90). The user watches the matches and holds this knowledge; the system has no channel to receive or cross-check it.

Verified data context (2026-08-26, system's own DB):
- CHE won 3-2 at FUL (Rogers, João Pedro, Palmer scored; Palmer + João Pedro assisted); xG/90 profiles: João Pedro 0.675, Rogers 0.597, Neto 0.415.
- NEW drew 2-2 with LIV (Elanga, Willock scored; Wissa 90 min, 1.0 xG, 4 pts).
- The system has no manager data (FPL API exposes none); real-world club priors must NEVER be assumed (observed 2026-08-26: Wissa mis-attributed to Brentford by real-world contamination — the DB says Newcastle).

## 2. Decisions (approved)

1. **User-curated insight store** (`speculation_notes` table). The user is the source of qualitative knowledge; the system cross-checks with stats.
2. **Entry surface: dashboard form** (primary) + CLI `note add/list/rm` (cheap; agent-contract usable).
3. **Consumption: AI speculation prompt context** — the AI may reference insights qualitatively, but every number it cites must still come from the stats digest (existing grounding validation — no invented numbers).
4. **Boundary (B4):** insights feed the speculation/AI layer only — never xP/FDR/decision thresholds. They add context to squad-builder spice signals, not to the core math.

## 3. Data model

`speculation_notes` (new table in `src/data/schema.sql` + idempotent migration in `db.py`):

| column | type | notes |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| note | TEXT NOT NULL | free text ("Xabi Alonso new CHE manager, expect overperformance") |
| team_id | INTEGER NULL | optional team scoping |
| player_id | INTEGER NULL | optional player scoping |
| created_at | TIMESTAMP | UTC |
| active | BOOLEAN default 1 | soft-delete |

Minimal by design: no confidence ratings, no kind tags (the AI tags kinds when consuming). Insight content is the user's; verification is the system's.

### 3.1 Initial seed (the user's GW1-26/27 insights, entered by the system — no re-typing)

The user shared these during design; they are seeded on first deploy (a one-shot script + documented seed, idempotent by content hash). The notes keep the user's own wording:

1. `"newcastle are incredibly good at playing and scoring goals.. from newcastle there are some players that really good like wissa"` → team NEW
2. `"from chelsea there's a lot including morgan rogers, joao pedro, neto, palmer.. but the most promising one is morgan rogers"` → team CHE
3. `"chelsea and newcastle had new manager, chelsea had xabi alonso and that's pretty good"` → team CHE
4. `"that new good manager combined with good player transfers like morgan rogers create really good cohesion and good performance"` → team CHE
5. `"this manager had really good track record, chelsea appointed him for new season, we speculate that chelsea under that manager will perform really well, so we choose players from chelsea"` → team CHE
6. `"morgan rogers is really good when playing in previous season and oftenly take long shot so he had really high chances of scoring and expected goals, plus under xabi alonso there'll be really good performance of him"` → team CHE + player Rogers

Verified against the system's data at seed time (2026-08-26): CHE won 3-2 at FUL (Rogers, João Pedro, Palmer scored); NEW drew 2-2 LIV (Elanga, Willock scored; Wissa 90 min, 1.0 xG); Rogers xG/90 0.597, João Pedro 0.675. The theses cross-check reproduces these from the DB on every `speculate` call.

Repository functions (`src/data/repository.py`):
- `add_speculation_note(conn, note, team_id=None, player_id=None) -> id`
- `list_speculation_notes(conn, active_only=True) -> list[dict]`
- `deactivate_speculation_note(conn, note_id) -> bool`

## 4. API (src/interface/api.py)

- `POST /api/speculation/notes` `{"note": str, "team_id": int|None, "player_id": int|None}` → 400 on empty note; returns the created row.
- `GET /api/speculation/notes` → `{"notes": [{id, note, team_id, player_id, team_short, player_name, created_at, active}]}` (joined for display).
- `DELETE /api/speculation/notes/{id}` → sets active=0; 404 if unknown.

B10: note creation/deletion writes an activity_log entry (decision_type='speculation', action_taken='note add: <truncated 80 chars>' / 'note rm: id N').

## 5. Dashboard

New route `/speculation` (SvelteKit, pattern of `/squad-builder`):
- Form: note textarea (required) + team select (from teams table) + player select (filtered by team, from players).
- List of active notes with team/player chips + delete button.
- The `speculate --json`-style data (spikes/drops/differentials + theses) if cheap to surface later — NOT in this slice (keep the page form+list only; the AI pane already surfaces speculation in squad-builder).

## 6. CLI (src/cli.py)

```
fpl-autopilot note add "NEW: Wissa takes long shots → high xG" [--team NEW] [--player Wissa] [--json]
fpl-autopilot note list [--json]
fpl-autopilot note rm <id> [--json]
```
Agent-safe (local DB only, no FPL writes) — added to the agent-safe list in `docs/agent-contract.md` + `docs/runbook.md`.

## 7. AI consumption (src/ai/squad/spikes.py)

- `build_spikes_prompt` gains an **"user insights"** section: each active note rendered with its team/player context, plus that team/player's digest numbers (GW1 xG/minutes, xP, xg_per_90, fixture run) — the AI can tie the qualitative claim to the numbers.
- Grounding rule unchanged: any number in the AI's reason must appear in the digest (existing validator). Qualitative references to insights ("Chelsea under Alonso — user thesis") are allowed without a digest number.
- `speculate --json` output gains **`theses`**: one entry per active note —
  `{note_id, note, team_short, player_name, checks: {..deterministic..}}` where checks are computed in code, never by the AI:
  - player: GW1 minutes/starts/xG/points (player_gw_stats), xP next GW, xg_per_90 (understat)
  - team: last GW result (fixtures), team xG/90 (ratings), next-3 fixture opponents + FDR
  - verdict: `"matches" | "contradicts" | "neutral"` via a simple rule (e.g. note mentions a player and the player has 0 starts in live GWs → contradicts; else neutral) — heuristic, documented, no AI.
- Squad-builder speculation (`runner.py`) passes the same insight context into its prompt when generating.

## 8. Tests

- **repository:** add/list/deactivate round-trip; empty-note rejection at API layer.
- **api:** POST 400 empty note; POST + GET + DELETE lifecycle; delete unknown → 404.
- **cli:** note add/list/rm dispatch + JSON envelope; team/player flags resolve by short_name/web_name.
- **spikes:** prompt includes active insights; a reason citing ONLY a qualitative insight (no numbers) validates; a reason citing a number not in the digest is still rejected (grounding preserved).
- **theses cross-check:** deterministic unit tests (player with 0 live starts → contradicts; team GW1 win + note → neutral/matches).
- **frontend:** vitest for the form component (submit payload shape) — 77 existing tests stay green.

## 9. Docs

- `docs/agent-contract.md`: `note` commands in the agent-safe list + `speculate` output shape gains `theses`.
- `docs/runbook.md`: one-liner for `note add`.
- `docs/architecture.md`: no change required (speculation layer note optional).

## 10. Out of scope

External manager data scraper; deterministic (decision-engine) effects from insights; Telegram entry surface; speculation visualization beyond the form/list.

## 11. Definition of done (B14)

- Code implements this doc; tests pass; full suite green (pytest + vitest).
- Manual smoke: `note add` via CLI + dashboard form, `speculate --json` shows the thesis with cross-checks, activity log entries present.
- The Wissa fact-check regression is covered by a test asserting insight cross-check reads club from the DB (never hardcoded).
