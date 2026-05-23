from src.execution import override


def test_not_frozen_by_default(db):
    assert override.is_frozen(db) is False
    assert override.status(db) is None


def test_freeze_sets_state_and_logs(db):
    override.freeze(db, reason="manual stop", source="user")
    assert override.is_frozen(db) is True
    st = override.status(db)
    assert st["reason"] == "manual stop" and st["source"] == "user" and "since" in st
    rows = db.execute(
        "SELECT decision_type, mode, action_taken, executed FROM activity_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["decision_type"] == "override" and rows[0]["mode"] == "override"
    assert "frozen (user): manual stop" == rows[0]["action_taken"]


def test_freeze_idempotent_no_double_log(db):
    override.freeze(db, reason="first", source="user")
    override.freeze(db, reason="second", source="user")   # no-op while frozen
    assert override.status(db)["reason"] == "first"        # original kept
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 1


def test_unfreeze_clears_and_logs(db):
    override.freeze(db, reason="x", source="user")
    override.unfreeze(db, source="user")
    assert override.is_frozen(db) is False
    actions = [r["action_taken"] for r in db.execute("SELECT action_taken FROM activity_log")]
    assert actions == ["frozen (user): x", "unfrozen (user)"]


def test_unfreeze_idempotent_when_not_frozen(db):
    override.unfreeze(db, source="user")                  # no-op, no error, no log
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0
