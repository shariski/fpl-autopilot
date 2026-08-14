import json

from src.ai.squad.prompt import build_squad_prompt


def test_prompt_shape():
    digest = {"next_gw": 1, "budget": 100,
              "players": [{"player_id": 1, "web_name": "X", "team": "NEW",
                           "position": "DEF", "price": 5.0, "xp_next": 4.0,
                           "xp_6gw": 20.0, "xg90": 0.1, "xa90": 0.2,
                           "ownership_pct": 10.0, "form": 3.0, "fixtures_3": []}]}
    p = build_squad_prompt(digest)
    assert "## system" in p and "## user" in p
    assert "GKP1" in p and "FWD3" in p
    assert '"player_id": 1' in p
    parsed = p.split("```json")[-1].split("```")[0]
    assert json.loads(parsed) == digest
