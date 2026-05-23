from src.data.models import BootstrapStatic, EntryPicks, Fixture
from src.data import repository


def _bootstrap(load):
    return BootstrapStatic.model_validate(load("bootstrap-static.json"))


def test_upsert_players_maps_fields(db, load):
    bs = _bootstrap(load)
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    count = db.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    assert count == len(bs.elements)
    el = bs.elements[0]
    row = db.execute("SELECT price, position, team_id FROM players WHERE id=?", (el.id,)).fetchone()
    assert row["price"] == el.now_cost / 10.0
    assert row["position"] in {"GKP", "DEF", "MID", "FWD"}
    assert row["team_id"] == el.team


def test_upsert_players_idempotent(db, load):
    bs = _bootstrap(load)
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    repository.upsert_players(db, bs.elements, bs.element_types)
    count = db.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    assert count == len(bs.elements)


def test_upsert_gameweeks_preserves_state(db, load):
    bs = _bootstrap(load)
    repository.upsert_gameweeks(db, bs.events)
    db.execute("UPDATE gameweeks SET state='USER_ACTED' WHERE id=?", (bs.events[0].id,))
    db.commit()
    repository.upsert_gameweeks(db, bs.events)  # second refresh must not reset state
    row = db.execute("SELECT state FROM gameweeks WHERE id=?", (bs.events[0].id,)).fetchone()
    assert row["state"] == "USER_ACTED"


def test_upsert_fixtures(db, load):
    fixtures = [Fixture.model_validate(f) for f in load("fixtures.json")]
    repository.upsert_fixtures(db, fixtures)
    count = db.execute("SELECT COUNT(*) c FROM fixtures").fetchone()["c"]
    assert count == len(fixtures)


def test_snapshot_my_team(db, load):
    picks = EntryPicks.model_validate(load("picks.json"))
    repository.snapshot_my_team(db, 38, picks)
    row = db.execute("SELECT picks_json, bank, free_transfers FROM my_team WHERE gw=38").fetchone()
    import json
    parsed = json.loads(row["picks_json"])
    assert len(parsed) == 15
    assert row["free_transfers"] is None  # public-API limitation (spec §6)
    assert row["bank"] == picks.entry_history.bank / 10.0


from src.auth import crypto


def test_set_get_encrypted_roundtrip(db):
    key = crypto.derive_key("throwaway", b"0123456789abcdef")
    token = crypto.encrypt(key, "you@example.com")
    repository.set_encrypted(db, "fpl_email_encrypted", token)
    back = repository.get_encrypted(db, "fpl_email_encrypted")
    assert crypto.decrypt(key, back) == "you@example.com"


def test_set_encrypted_updates_same_row(db):
    key = crypto.derive_key("throwaway", b"0123456789abcdef")
    repository.set_encrypted(db, "fpl_password_encrypted", crypto.encrypt(key, "a"))
    repository.set_encrypted(db, "fpl_password_encrypted", crypto.encrypt(key, "b"))
    rows = db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"]
    assert rows == 1  # single id=1 row, updated in place
    assert crypto.decrypt(key, repository.get_encrypted(db, "fpl_password_encrypted")) == "b"


def test_get_encrypted_missing_returns_none(db):
    assert repository.get_encrypted(db, "session_cookie_encrypted") is None


def test_encrypted_unknown_column_rejected(db):
    import pytest
    with pytest.raises(ValueError):
        repository.set_encrypted(db, "id; DROP TABLE credentials", b"x")
    with pytest.raises(ValueError):
        repository.get_encrypted(db, "not_a_column")


def test_touch_session_refreshed(db):
    from src.data import repository
    repository.touch_session_refreshed(db)
    row = db.execute("SELECT session_last_refreshed FROM credentials WHERE id=1").fetchone()
    assert row["session_last_refreshed"] is not None
    # idempotent: still one row after a second call
    repository.touch_session_refreshed(db)
    assert db.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"] == 1


def test_auth_state_get_set(db):
    from src.data import repository
    assert repository.get_auth_state(db) is None  # no row yet
    repository.set_auth_state(db, "frozen")
    assert repository.get_auth_state(db) == "frozen"


def test_increment_relogin_failures(db):
    from src.data import repository
    assert repository.increment_relogin_failures(db) == 1
    assert repository.increment_relogin_failures(db) == 2
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 2


def test_mark_session_ok_resets(db):
    from src.data import repository
    repository.set_auth_state(db, "frozen")
    repository.increment_relogin_failures(db)
    repository.mark_session_ok(db)
    assert repository.get_auth_state(db) == "active"
    row = db.execute("SELECT relogin_failures FROM credentials WHERE id=1").fetchone()
    assert row["relogin_failures"] == 0


def test_token_columns_whitelisted(db):
    from src.data import repository
    repository.set_encrypted(db, "refresh_token_encrypted", b"rt")
    repository.set_encrypted(db, "access_token_encrypted", b"at")
    assert repository.get_encrypted(db, "refresh_token_encrypted") == b"rt"
    assert repository.get_encrypted(db, "access_token_encrypted") == b"at"


def test_access_expiry_get_set(db):
    from src.data import repository
    assert repository.get_access_expiry(db) is None
    repository.set_access_expiry(db, "2026-05-23T12:00:00+00:00")
    assert repository.get_access_expiry(db) == "2026-05-23T12:00:00+00:00"
