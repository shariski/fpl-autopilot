import pytest
import json
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


def test_mark_session_ok_resets(db):
    from src.data import repository
    repository.set_auth_state(db, "expired")
    repository.mark_session_ok(db)
    assert repository.get_auth_state(db) == "active"


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


def test_log_activity_roundtrip(db):
    import json as _json
    from src.data import repository
    repository.log_activity(db, decision_type="lineup", mode="manual",
                            action_taken="captain=5, vice=6",
                            inputs={"xp": 7.1}, executed=True,
                            exec_outcome={"status": 200}, gw=38)
    row = db.execute("SELECT * FROM activity_log").fetchone()
    assert row["decision_type"] == "lineup"
    assert row["mode"] == "manual"
    assert row["executed"] == 1
    assert row["gw"] == 38
    assert _json.loads(row["inputs_json"])["xp"] == 7.1
    assert _json.loads(row["exec_outcome_json"])["status"] == 200


def test_system_state_round_trip(db):
    from src.data import repository
    assert repository.get_system_state(db, "freeze") is None
    repository.set_system_state(db, "freeze", '{"a": 1}')
    assert repository.get_system_state(db, "freeze") == '{"a": 1}'
    repository.set_system_state(db, "freeze", '{"a": 2}')          # upsert in place
    assert repository.get_system_state(db, "freeze") == '{"a": 2}'
    assert db.execute("SELECT COUNT(*) c FROM system_state").fetchone()["c"] == 1


def test_clear_system_state(db):
    from src.data import repository
    repository.set_system_state(db, "freeze", "x")
    repository.clear_system_state(db, "freeze")
    assert repository.get_system_state(db, "freeze") is None
    repository.clear_system_state(db, "freeze")                    # idempotent: no error when absent


def test_relogin_failures_increment_and_get(db):
    from src.data import repository
    assert repository.get_relogin_failures(db) == 0               # no row yet
    assert repository.increment_relogin_failures(db) == 1
    assert repository.increment_relogin_failures(db) == 2
    assert repository.get_relogin_failures(db) == 2


def test_mark_session_ok_resets_relogin_failures(db):
    from src.data import repository
    repository.increment_relogin_failures(db)
    repository.increment_relogin_failures(db)
    repository.mark_session_ok(db)                                # existing helper resets to 0
    assert repository.get_relogin_failures(db) == 0


def test_snapshot_my_team_authed_extracts_all_fields(db):
    payload = {
        "picks": [{"element": e, "position": e, "is_captain": e == 1,
                   "is_vice_captain": e == 2, "selling_price": 50,
                   "purchase_price": 50, "multiplier": 1} for e in range(1, 16)],
        "transfers": {"bank": 23, "value": 1004, "limit": 2, "cost": 4, "status": "cost", "made": 0},
        "chips": [{"name": "wildcard", "status_for_entry": "available"},
                  {"name": "bboost", "status_for_entry": "played", "played_by_entry": [38]}],
    }
    repository.snapshot_my_team_authed(db, 38, payload)
    row = db.execute(
        "SELECT picks_json, bank, team_value, free_transfers, chips_used_json FROM my_team WHERE gw=38"
    ).fetchone()
    assert row is not None
    picks = json.loads(row["picks_json"])
    assert len(picks) == 15 and picks[0]["element"] == 1
    assert row["bank"] == 2.3       # /10 to convert tenths to whole units (existing convention)
    assert row["team_value"] == 100.4
    assert row["free_transfers"] == 2
    # chips_used_json should be the raw chips list (caller decides format downstream)
    assert json.loads(row["chips_used_json"]) == payload["chips"]


def test_snapshot_my_team_authed_idempotent(db):
    payload = {
        "picks": [{"element": 1, "position": 1, "is_captain": True, "is_vice_captain": False,
                   "selling_price": 50, "purchase_price": 50, "multiplier": 2}],
        "transfers": {"bank": 0, "value": 1000, "limit": 1, "cost": 0, "status": "cost", "made": 0},
        "chips": [],
    }
    repository.snapshot_my_team_authed(db, 5, payload)
    repository.snapshot_my_team_authed(db, 5, payload)
    rows = db.execute("SELECT COUNT(*) c FROM my_team WHERE gw=5").fetchone()
    assert rows["c"] == 1  # INSERT OR REPLACE


def test_snapshot_my_team_authed_raises_on_missing_transfers(db):
    payload = {"picks": [], "chips": []}  # transfers key absent
    with pytest.raises(KeyError):
        repository.snapshot_my_team_authed(db, 7, payload)


def test_snapshot_my_team_authed_raises_on_missing_limit(db):
    payload = {"picks": [], "transfers": {"bank": 0, "value": 1000}, "chips": []}  # no limit
    with pytest.raises(KeyError):
        repository.snapshot_my_team_authed(db, 7, payload)


def test_snapshot_my_team_authed_chips_null_when_absent(db):
    payload = {
        "picks": [],
        "transfers": {"bank": 0, "value": 1000, "limit": 1},
    }  # chips key absent — allowed, stored as NULL
    repository.snapshot_my_team_authed(db, 9, payload)
    row = db.execute("SELECT chips_used_json FROM my_team WHERE gw=9").fetchone()
    assert row["chips_used_json"] is None
