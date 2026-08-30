"""Seed the user's GW1-26/27 speculation insights (spec §3.1, v0.26).

Idempotent: a note whose text already exists (exact match) is not re-added.
Usage: .venv/bin/python docs/research/calibration/seed_speculation_notes.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.data.db import connect, init_db          # noqa: E402
from src.data import repository                    # noqa: E402

SEEDS = [
    ("newcastle are incredibly good at playing and scoring goals.. from newcastle "
     "there are some players that really good like wissa", "NEW", None),
    ("from chelsea there's a lot including morgan rogers, joao pedro, neto, palmer.. "
     "but the most promising one is morgan rogers", "CHE", None),
    ("chelsea and newcastle had new manager, chelsea had xabi alonso and that's pretty good",
     "CHE", None),
    ("that new good manager combined with good player transfers like morgan rogers "
     "create really good cohesion and good performance", "CHE", None),
    ("this manager had really good track record, chelsea appointed him for new season, "
     "we speculate that chelsea under that manager will perform really well, so we "
     "choose players from chelsea", "CHE", None),
    ("morgan rogers is really good when playing in previous season and oftenly take "
     "long shot so he had really high chances of scoring and expected goals, plus "
     "under xabi alonso there'll be really good performance of him", "CHE", "Rogers"),
]


def seed(conn):
    added = 0
    for note, team_short, player_name in SEEDS:
        if conn.execute("SELECT 1 FROM speculation_notes WHERE note=? LIMIT 1",
                        (note,)).fetchone():
            continue
        team_id = player_id = None
        if team_short:
            team_id = conn.execute("SELECT id FROM teams WHERE short_name=?",
                                   (team_short,)).fetchone()["id"]
        if player_name:
            player_id = conn.execute(
                "SELECT id FROM players WHERE web_name=? AND team_id=?",
                (player_name, team_id)).fetchone()["id"]
        repository.add_speculation_note(conn, note, team_id=team_id, player_id=player_id)
        added += 1
    return added


if __name__ == "__main__":
    conn = connect("data/fpl_autopilot.db")
    init_db(conn)
    print("seeded:", seed(conn))
