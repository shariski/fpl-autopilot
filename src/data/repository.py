import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def upsert_teams(conn, teams):
    conn.executemany(
        """INSERT INTO teams (id, name, short_name, strength_attack_home,
             strength_attack_away, strength_defence_home, strength_defence_away)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, short_name=excluded.short_name,
             strength_attack_home=excluded.strength_attack_home,
             strength_attack_away=excluded.strength_attack_away,
             strength_defence_home=excluded.strength_defence_home,
             strength_defence_away=excluded.strength_defence_away""",
        [(t.id, t.name, t.short_name, t.strength_attack_home, t.strength_attack_away,
          t.strength_defence_home, t.strength_defence_away) for t in teams],
    )
    conn.commit()


def upsert_players(conn, elements, element_types):
    pos = {et.id: et.singular_name_short for et in element_types}
    now = _now()
    conn.executemany(
        """INSERT INTO players (id, name, web_name, team_id, position, price,
             status, ownership, form, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, web_name=excluded.web_name,
             team_id=excluded.team_id, position=excluded.position,
             price=excluded.price, status=excluded.status,
             ownership=excluded.ownership, form=excluded.form,
             updated_at=excluded.updated_at""",
        [(e.id, f"{e.first_name} {e.second_name}", e.web_name, e.team,
          pos[e.element_type], e.now_cost / 10.0, e.status,
          e.selected_by_percent, e.form, now) for e in elements],
    )
    conn.commit()


def upsert_gameweeks(conn, events):
    # state column defaults to 'PENDING' on insert and is intentionally NOT
    # touched on conflict, so a refresh never clobbers the state machine.
    conn.executemany(
        """INSERT INTO gameweeks (id, name, deadline_utc, is_current, is_next, finished)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, deadline_utc=excluded.deadline_utc,
             is_current=excluded.is_current, is_next=excluded.is_next,
             finished=excluded.finished""",
        [(ev.id, ev.name, ev.deadline_time.isoformat(), ev.is_current,
          ev.is_next, ev.finished) for ev in events],
    )
    conn.commit()


def upsert_fixtures(conn, fixtures):
    conn.executemany(
        """INSERT INTO fixtures (id, gw, home_team_id, away_team_id, kickoff_utc,
             finished, home_score, away_score)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             gw=excluded.gw, home_team_id=excluded.home_team_id,
             away_team_id=excluded.away_team_id, kickoff_utc=excluded.kickoff_utc,
             finished=excluded.finished, home_score=excluded.home_score,
             away_score=excluded.away_score""",
        [(f.id, f.event, f.team_h, f.team_a,
          f.kickoff_time.isoformat() if f.kickoff_time else None,
          f.finished, f.team_h_score, f.team_a_score) for f in fixtures],
    )
    conn.commit()


def snapshot_my_team(conn, gw, picks):
    picks_json = json.dumps([
        {"element": p.element, "position": p.position, "multiplier": p.multiplier,
         "is_captain": p.is_captain, "is_vice_captain": p.is_vice_captain}
        for p in picks.picks
    ])
    chips = json.dumps([picks.active_chip] if picks.active_chip else [])
    conn.execute(
        """INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers,
             chips_used_json, snapshot_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(gw) DO UPDATE SET
             picks_json=excluded.picks_json, bank=excluded.bank,
             team_value=excluded.team_value, free_transfers=excluded.free_transfers,
             chips_used_json=excluded.chips_used_json, snapshot_at=excluded.snapshot_at""",
        (gw, picks_json, picks.entry_history.bank / 10.0,
         picks.entry_history.value / 10.0, None, chips, _now()),  # free_transfers: auth-only
    )
    conn.commit()
