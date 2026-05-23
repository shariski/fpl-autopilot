from src.data import repository


def test_create_and_get_pending_decision(db):
    pid = repository.create_pending_decision(
        db, gw=30, decision_type="transfer",
        identity={"out_id": 7, "in_id": 99}, summary="Transfer pending: OUT O IN I")
    row = repository.get_pending_decision(db, pid)
    assert row["gw"] == 30
    assert row["decision_type"] == "transfer"
    assert row["status"] == "pending"
    assert row["summary"] == "Transfer pending: OUT O IN I"
    import json
    assert json.loads(row["identity_json"]) == {"out_id": 7, "in_id": 99}
    assert row["created_at"] is not None and row["resolved_at"] is None


def test_get_pending_decision_missing_returns_none(db):
    assert repository.get_pending_decision(db, 999) is None


def test_set_pending_status_sets_status_and_resolved_at(db):
    pid = repository.create_pending_decision(
        db, gw=30, decision_type="lineup", identity={"captain_id": 5, "vice_id": 6}, summary="Captain pending: Cap")
    repository.set_pending_status(db, pid, "confirmed")
    row = repository.get_pending_decision(db, pid)
    assert row["status"] == "confirmed"
    assert row["resolved_at"] is not None


def test_telegram_state_round_trip_and_default(db):
    assert repository.get_telegram_state(db, "update_offset") is None
    repository.set_telegram_state(db, "update_offset", "42")
    assert repository.get_telegram_state(db, "update_offset") == "42"
    repository.set_telegram_state(db, "update_offset", "43")   # upsert
    assert repository.get_telegram_state(db, "update_offset") == "43"
