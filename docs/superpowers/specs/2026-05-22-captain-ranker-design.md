# Captain Ranker — Design Spec

- **Date:** 2026-05-22
- **Status:** Approved for planning
- **Scope:** First Decision-Layer engine. Suggest-only (Phase 1, read/derive). Ranks the squad for captaincy.
- **Slice goal:** given the squad and xP, return the top-5 captain options (with reasoning) + vice, in the `/api/captain` shape.

Consumes Analytics output (`xp`) + squad/fixtures. Built in parallel with the transfer engine; both are independent.

---

## 1. Context

Per `decision-engine.md` ("Captain ranker"): rank squad players by `xP[next_gw]` desc, tiebreak by `1 − rotation_risk`, then `fdr_attack` asc; output top 5 with a reasoning string (xP value, fixture, gap to second-best); vice = #2.

Data available (all in the DB; Decision Layer reads Analytics output + squad, never re-derives from raw FPL — B2): `xp` (per player/GW, `model_version='v1'`), `my_team` (latest picks snapshot), `players` (web_name, position, team_id, status), `fixtures` (opponent + home/away per GW), `teams` (short names), `fdr` (per team/GW).

**v1 adaptation (B4):** there is no explicit `rotation_risk` metric. v1 uses **`xminutes` (from the `xp` row) as the rotation-risk proxy** — higher expected minutes = lower rotation risk. Documented in `decision-engine.md`.

## 2. Decisions locked

| Decision | Choice |
|---|---|
| Ranking | `xp` (next GW) desc; tiebreak `xminutes` desc; then `fdr_attack` asc |
| Candidates | all 15 squad players (FPL allows captaining any squad member) |
| Output | top 5 + reasoning string + `vice_player_id` (= rank #2) |
| Persistence | none — pure read/derive feeding `/api/captain` (activity-log logging is a Phase-2/scheduler concern) |
| next GW | first unfinished GW (`MIN(id) FROM gameweeks WHERE finished=0`) — same as FDR/xP |

## 3. Scope

### In scope
- `src/decisions/captain.py` **only**. The `src/decisions/` package marker, the `pyproject.toml` `src.decisions` entry, and the `decision-engine.md` v1 note + changelog row are **already on `main`** — do NOT modify `pyproject.toml`, `src/decisions/__init__.py`, or `docs/decision-engine.md`.
- Pure `rank_captains(candidates) -> list[pick]` + a thin `get_captain_picks(conn) -> dict` reader returning the `/api/captain` shape.
- `tests/test_captain.py` — deterministic tests (pure ranking + integration reader).

### Out of scope
- Persistence / activity-log (Phase 2 / scheduler).
- The FastAPI endpoint that serves this (separate workstream — it will call `get_captain_picks`).
- Explicit `rotation_risk` modeling (v1 proxy via `xminutes`).

## 4. Behavior (precise)

Each candidate is `{player_id, web_name, position, xp, xminutes, fdr_attack, fixture}` where `fixture` is a display string like `"MCI v BOU (H)"` (player's team vs opponent, venue) for the next GW.

`rank_captains(candidates)`:
- Sort by `(-xp, -xminutes, fdr_attack)`.
- Take top 5. For each, build `reason`:
  - Rank 1: `f"Highest xP ({xp}) {fixture}. Next best {second.web_name} {second.xp} — gap {round(xp-second.xp,1)}."`
  - Ranks 2–5: `f"xP {xp} {fixture}."`
- Return list of `{player_id, web_name, xp, fixture, reason}`.

`get_captain_picks(conn)`:
- Find next GW. Read latest `my_team` snapshot → 15 element ids.
- For each, join `players` (web_name, position, team_id, status), `xp` (xp, xminutes for next GW, `model_version='v1'`), and derive `fixture` + `fdr_attack` from `fixtures`+`teams`+`fdr` for the player's team that GW. Players with no `xp` row for the GW (e.g., blank GW or unmatched) are ranked last (treat `xp` as 0, `fixture` `"—"`).
- Call `rank_captains`; return `{"picks": [...5...], "vice_player_id": picks[1].player_id if len>1 else None}`.

Output matches `docs/api-contract.md` `/api/captain`.

## 5. decision-engine.md update (B4) — ALREADY APPLIED ON main
The "Captain ranker" section already carries the v1 rotation-risk-proxy note and changelog row `v0.4`. **Do not modify `decision-engine.md`.**

## 6. Components
- `src/decisions/captain.py`: `rank_captains(candidates)` (pure), `get_captain_picks(conn)` (reader; may use a small helper for the fixture string shared conceptually with the planner — keep local for now, don't over-abstract).

## 7. Testing (B11)
- `test_rank_captains_orders_by_xp`: higher xp ranks first.
- `test_rank_captains_tiebreak_minutes_then_fdr`: equal xp → higher xminutes wins; equal xp+minutes → lower fdr_attack wins.
- `test_rank_captains_reason_includes_gap`: rank-1 reason contains the numeric gap to #2.
- `test_rank_captains_vice_is_second`: `vice_player_id` equals the #2 pick (via the reader or a top-level helper).
- `test_get_captain_picks_integration` (in-memory DB): seed players/my_team/xp/fixtures/teams/fdr for a next GW; assert 5 picks, descending xp, vice = #2, shape matches the contract.

## 8. Definition of done
1. `pytest` green incl. captain tests.
2. `get_captain_picks(conn)` returns a valid `/api/captain` payload against the live DB (top picks are the squad's premium attackers).
3. `decision-engine.md` noted + changelog (B4).

## 9. Notes
- Suggest-only; no execution (Phase 2).
- `xP_5gw` not needed here (captain is a single-GW decision).
