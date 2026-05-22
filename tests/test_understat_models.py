import pytest
from pydantic import ValidationError
from src.data.models import UnderstatPlayer, UnderstatPlayersResponse


def test_understat_response_parses(load):
    resp = UnderstatPlayersResponse.model_validate(load("understat-players.json"))
    assert resp.success is True
    assert len(resp.players) > 500
    haaland = next(p for p in resp.players if p.player_name == "Erling Haaland")
    assert haaland.xG > 0
    assert haaland.time > 0


def test_understat_numeric_strings_coerce(load):
    resp = UnderstatPlayersResponse.model_validate(load("understat-players.json"))
    p = resp.players[0]
    assert isinstance(p.games, int)
    assert isinstance(p.xG, float)


def test_understat_schema_drift_fails_loudly(load):
    data = load("understat-players.json")
    data["players"][0]["xG"] = "not-a-number"  # float field, non-coercible -> must raise
    with pytest.raises(ValidationError):
        UnderstatPlayersResponse.model_validate(data)
