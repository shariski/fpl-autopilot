import json

from src.ai import reasoning


CAPTAIN_DECISION_FIXTURE = {
    "picks": [
        {"player_id": 10, "web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)",
         "reason": "Highest xP (7.2) MCI v BRE (H). Next best Salah 5.4 — gap 1.8."},
        {"player_id": 6, "web_name": "Salah", "xp": 5.4, "fixture": "LIV v EVE (A)",
         "reason": "xP 5.4 LIV v EVE (A)."},
        {"player_id": 7, "web_name": "Saka", "xp": 5.0, "fixture": "ARS v LIV (A)",
         "reason": "xP 5.0 ARS v LIV (A)."},
    ],
    "vice_player_id": 6,
    "confidence": 82,
}


def test_build_captain_payload_shape():
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    assert payload == {
        "captain": {"web_name": "Haaland", "xp": 7.2, "fixture": "MCI v BRE (H)"},
        "vice": {"web_name": "Salah", "xp": 5.4},
        "alternative_gap": 1.8,
        "confidence": 82,
    }


def test_build_captain_payload_with_single_pick():
    decision = {
        "picks": [{"player_id": 10, "web_name": "Haaland", "xp": 7.2,
                   "fixture": "MCI v BRE (H)", "reason": "..."}],
        "vice_player_id": None,
        "confidence": 60,
    }
    payload = reasoning._build_captain_payload(decision)
    assert payload["vice"] is None
    assert payload["alternative_gap"] is None
    assert payload["captain"]["web_name"] == "Haaland"


def test_build_captain_payload_returns_none_on_empty_picks():
    decision = {"picks": [], "vice_player_id": None, "confidence": None}
    assert reasoning._build_captain_payload(decision) is None


def test_build_captain_prompt_includes_payload_and_examples():
    payload = reasoning._build_captain_payload(CAPTAIN_DECISION_FIXTURE)
    prompt = reasoning._build_captain_prompt(payload)
    assert "Haaland" in prompt          # from payload
    assert "Saka" in prompt             # from the second few-shot exemplar
    assert '"web_name": "Haaland"' in prompt or json.dumps(payload, sort_keys=True, indent=2) in prompt
    assert "Do not invent" in prompt
