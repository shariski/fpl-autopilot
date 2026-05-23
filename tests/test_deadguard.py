from src.data import repository


def _seed_gw(db, gw=30):
    db.execute("INSERT INTO gameweeks (id, deadline_utc, is_next) VALUES (?, '2026-05-23T18:00:00+00:00', 1)", (gw,))
    db.commit()


def test_deadguard_warned_at_column_exists(db):
    _seed_gw(db)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(gameweeks)")}
    assert "deadguard_warned_at" in cols


def test_set_gameweek_state(db):
    _seed_gw(db)
    repository.set_gameweek_state(db, 30, "DEADGUARD_ACTIVE")
    assert db.execute("SELECT state FROM gameweeks WHERE id=30").fetchone()["state"] == "DEADGUARD_ACTIVE"


def test_mark_deadguard_warned_and_triggered(db):
    _seed_gw(db)
    repository.mark_deadguard_warned(db, 30)
    repository.mark_deadguard_triggered(db, 30)
    row = db.execute("SELECT deadguard_warned_at, deadguard_triggered_at FROM gameweeks WHERE id=30").fetchone()
    assert row["deadguard_warned_at"] is not None
    assert row["deadguard_triggered_at"] is not None


def test_touch_user_action_sets_state_and_timestamp(db):
    _seed_gw(db)
    repository.touch_user_action(db, 30)
    row = db.execute("SELECT state, last_user_action_at FROM gameweeks WHERE id=30").fetchone()
    assert row["state"] == "USER_ACTED"
    assert row["last_user_action_at"] is not None
