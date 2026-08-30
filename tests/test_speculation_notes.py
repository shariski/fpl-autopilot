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
    assert notes[0]["team_short"] is None        # "loose note" added second -> newest first
    assert notes[1]["team_short"] == "NEW"


def test_note_deactivate(db):
    nid = repository.add_speculation_note(db, "rogers takes long shots")
    assert repository.deactivate_speculation_note(db, nid) is True
    assert repository.list_speculation_notes(db) == []
    assert len(repository.list_speculation_notes(db, active_only=False)) == 1
    assert repository.deactivate_speculation_note(db, nid) is False  # already off
    assert repository.deactivate_speculation_note(db, 9999) is False  # unknown
