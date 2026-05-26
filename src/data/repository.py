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


def snapshot_my_team_authed(conn, gw, payload):
    """Write an authed /api/my-team payload to my_team. Includes free_transfers (transfers.limit).

    Stored under gw=next_gw (the upcoming GW that this team is FOR), so that readers doing
    ORDER BY gw DESC LIMIT 1 prefer the authed row over the public-picks row from the prior GW.

    Raises KeyError on schema drift (missing transfers / missing limit) per B6.
    """
    picks = payload["picks"]
    transfers = payload["transfers"]  # raises KeyError if absent — B6
    free_transfers = transfers["limit"]  # raises KeyError if absent — B6
    bank = transfers.get("bank", 0) / 10.0
    team_value = transfers.get("value", 0) / 10.0
    chips = payload.get("chips")
    chips_json = json.dumps(chips) if chips is not None else None
    conn.execute(
        """INSERT INTO my_team (gw, picks_json, bank, team_value, free_transfers,
                                chips_used_json, snapshot_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(gw) DO UPDATE SET picks_json=excluded.picks_json, bank=excluded.bank,
             team_value=excluded.team_value, free_transfers=excluded.free_transfers,
             chips_used_json=excluded.chips_used_json, snapshot_at=excluded.snapshot_at""",
        (gw, json.dumps(picks), bank, team_value, free_transfers, chips_json, _now()),
    )
    conn.commit()


def upsert_fdr(conn, rows):
    now = _now()
    conn.executemany(
        """INSERT INTO fdr (team_id, gw, fdr_attack, fdr_defense, computed_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(team_id, gw) DO UPDATE SET
             fdr_attack=excluded.fdr_attack, fdr_defense=excluded.fdr_defense,
             computed_at=excluded.computed_at""",
        [(r["team_id"], r["gw"], r["fdr_attack"], r["fdr_defense"], now) for r in rows],
    )
    conn.commit()


def _per90(value, minutes):
    return round(value / (minutes / 90.0), 4) if minutes else 0.0


def upsert_understat_players(conn, understat_players, resolution, season):
    now = _now()
    rows = [
        (up.id, resolution.matched.get(up.id), season, up.player_name, up.team_title,
         up.games, up.time, up.goals, up.assists, up.xG, up.xA, up.npg, up.npxG,
         _per90(up.xG, up.time), _per90(up.xA, up.time), now)
        for up in understat_players
    ]
    conn.executemany(
        """INSERT INTO understat_players (understat_id, fpl_player_id, season, player_name,
             team_title, games, minutes, goals, assists, xg, xa, npg, npxg,
             xg_per_90, xa_per_90, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(understat_id) DO UPDATE SET
             fpl_player_id=excluded.fpl_player_id, season=excluded.season,
             player_name=excluded.player_name, team_title=excluded.team_title,
             games=excluded.games, minutes=excluded.minutes, goals=excluded.goals,
             assists=excluded.assists, xg=excluded.xg, xa=excluded.xa, npg=excluded.npg,
             npxg=excluded.npxg, xg_per_90=excluded.xg_per_90, xa_per_90=excluded.xa_per_90,
             updated_at=excluded.updated_at""",
        rows,
    )
    conn.commit()


def upsert_xp(conn, rows):
    now = _now()
    conn.executemany(
        """INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs, computed_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(player_id, gw, model_version) DO UPDATE SET
             xp=excluded.xp, xminutes=excluded.xminutes, xgoals=excluded.xgoals,
             xassists=excluded.xassists, xcs=excluded.xcs, computed_at=excluded.computed_at""",
        [(r["player_id"], r["gw"], r["model_version"], r["xp"], r["xminutes"],
          r["xgoals"], r["xassists"], r["xcs"], now) for r in rows],
    )
    conn.commit()


_CRED_COLUMNS = {
    "fpl_email_encrypted", "fpl_password_encrypted",
    "session_cookie_encrypted", "csrf_token_encrypted",
    "refresh_token_encrypted", "access_token_encrypted",
}


def set_encrypted(conn, column, token):
    if column not in _CRED_COLUMNS:
        raise ValueError(f"unknown credential column: {column!r}")
    conn.execute(
        f"INSERT INTO credentials (id, {column}) VALUES (1, ?) "
        f"ON CONFLICT(id) DO UPDATE SET {column}=excluded.{column}",
        (token,),
    )
    conn.commit()


def get_encrypted(conn, column):
    if column not in _CRED_COLUMNS:
        raise ValueError(f"unknown credential column: {column!r}")
    row = conn.execute(f"SELECT {column} FROM credentials WHERE id=1").fetchone()
    return row[column] if row else None


def touch_session_refreshed(conn):
    """Set credentials.session_last_refreshed to the current UTC time (row id=1)."""
    conn.execute(
        "INSERT INTO credentials (id, session_last_refreshed) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET session_last_refreshed=excluded.session_last_refreshed",
        (_now(),),
    )
    conn.commit()


def set_access_expiry(conn, expires_at_iso):
    conn.execute(
        "INSERT INTO credentials (id, access_token_expires_at) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET access_token_expires_at=excluded.access_token_expires_at",
        (expires_at_iso,),
    )
    conn.commit()


def get_access_expiry(conn):
    row = conn.execute("SELECT access_token_expires_at FROM credentials WHERE id=1").fetchone()
    return row["access_token_expires_at"] if row else None


def get_auth_state(conn):
    row = conn.execute("SELECT auth_state FROM credentials WHERE id=1").fetchone()
    return row["auth_state"] if row else None


def set_auth_state(conn, state):
    conn.execute(
        "INSERT INTO credentials (id, auth_state) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET auth_state=excluded.auth_state",
        (state,),
    )
    conn.commit()


def mark_session_ok(conn):
    conn.execute(
        "INSERT INTO credentials (id, auth_state, relogin_failures) VALUES (1, 'active', 0) "
        "ON CONFLICT(id) DO UPDATE SET auth_state='active', relogin_failures=0"
    )
    conn.commit()


def increment_relogin_failures(conn):
    conn.execute(
        "INSERT INTO credentials (id, relogin_failures) VALUES (1, 1) "
        "ON CONFLICT(id) DO UPDATE SET relogin_failures = relogin_failures + 1"
    )
    conn.commit()
    return get_relogin_failures(conn)


def get_relogin_failures(conn):
    row = conn.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    return row["relogin_failures"] if row else 0


def log_activity(conn, *, decision_type, mode, action_taken, inputs=None,
                 executed=False, exec_outcome=None, gw=None, alternatives=None):
    conn.execute(
        "INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken, "
        "inputs_json, alternatives_json, executed, exec_outcome_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_now(), gw, mode, decision_type, action_taken,
         json.dumps(inputs) if inputs is not None else None,
         json.dumps(alternatives) if alternatives is not None else None,
         executed,
         json.dumps(exec_outcome) if exec_outcome is not None else None),
    )
    conn.commit()


def create_pending_decision(conn, *, gw, decision_type, identity, summary):
    cur = conn.execute(
        "INSERT INTO pending_decisions (gw, decision_type, identity_json, summary, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (gw, decision_type, json.dumps(identity), summary, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_decision(conn, pid):
    return conn.execute("SELECT * FROM pending_decisions WHERE id=?", (pid,)).fetchone()


def set_pending_status(conn, pid, status):
    conn.execute("UPDATE pending_decisions SET status=?, resolved_at=? WHERE id=?",
                 (status, _now(), pid))
    conn.commit()


def get_telegram_state(conn, key):
    row = conn.execute("SELECT value FROM telegram_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_telegram_state(conn, key, value):
    conn.execute(
        "INSERT INTO telegram_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_system_state(conn, key):
    row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_system_state(conn, key, value):
    conn.execute(
        "INSERT INTO system_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def clear_system_state(conn, key):
    conn.execute("DELETE FROM system_state WHERE key=?", (key,))
    conn.commit()


def set_gameweek_state(conn, gw, state):
    conn.execute("UPDATE gameweeks SET state=? WHERE id=?", (state, gw))
    conn.commit()


def mark_deadguard_warned(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_warned_at=? WHERE id=?", (_now(), gw))
    conn.commit()


def mark_deadguard_reeval_alerted(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_reeval_alerted_at=? WHERE id=?", (_now(), gw))
    conn.commit()


def mark_deadguard_triggered(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_triggered_at=? WHERE id=?", (_now(), gw))
    conn.commit()


def set_deadguard_transfer(conn, gw, out_id, in_id):
    conn.execute("UPDATE gameweeks SET deadguard_transfer_json=? WHERE id=?",
                 (json.dumps({"out_id": out_id, "in_id": in_id}), gw))
    conn.commit()


def get_deadguard_transfer(conn, gw):
    row = conn.execute("SELECT deadguard_transfer_json FROM gameweeks WHERE id=?", (gw,)).fetchone()
    return json.loads(row["deadguard_transfer_json"]) if row and row["deadguard_transfer_json"] else None


def mark_deadguard_transfer_undone(conn, gw):
    conn.execute("UPDATE gameweeks SET deadguard_transfer_undone_at=? WHERE id=?", (_now(), gw))
    conn.commit()


def touch_user_action(conn, gw):
    conn.execute("UPDATE gameweeks SET last_user_action_at=?, state='USER_ACTED' WHERE id=?", (_now(), gw))
    conn.commit()
