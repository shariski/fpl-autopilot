from src.data.models import BootstrapStatic, UnderstatPlayersResponse
from src.data import repository, name_resolver


def _fpl_rows(db, load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    repository.upsert_teams(db, bs.teams)
    repository.upsert_players(db, bs.elements, bs.element_types)
    players = [dict(r) for r in db.execute("SELECT id, name, web_name, team_id FROM players")]
    teams = [dict(r) for r in db.execute("SELECT id, name, short_name FROM teams")]
    return players, teams


def _understat(load):
    return UnderstatPlayersResponse.model_validate(load("understat-players.json")).players


def test_team_title_normalizes_and_overrides(db, load):
    _, teams = _fpl_rows(db, load)
    lookup = name_resolver._team_lookup(teams)
    ids, unmapped = name_resolver._resolve_team_title("Tottenham", lookup)  # -> Spurs (override)
    assert unmapped == [] and len(ids) == 1
    ids2, unmapped2 = name_resolver._resolve_team_title("Arsenal", lookup)  # normalizes directly
    assert unmapped2 == [] and len(ids2) == 1


def test_comma_team_title_is_mid_season_transfer(db, load):
    _, teams = _fpl_rows(db, load)
    lookup = name_resolver._team_lookup(teams)
    ids, unmapped = name_resolver._resolve_team_title("Aston Villa,Crystal Palace", lookup)
    assert unmapped == [] and len(ids) == 2


def test_resolves_known_players_with_high_match_rate(db, load):
    players, teams = _fpl_rows(db, load)
    us = _understat(load)
    res = name_resolver.resolve_players(players, teams, us)
    assert res.unmapped_teams == []
    assert len(res.matched) >= int(0.97 * len(us))  # observed ~98% on the frozen fixture
    haaland_u = next(p for p in us if p.player_name == "Erling Haaland")
    fpl_haaland = next(r for r in players if r["web_name"] == "Haaland")
    assert res.matched[haaland_u.id] == fpl_haaland["id"]


def test_ambiguous_name_left_unmatched(db, load):
    players, teams = _fpl_rows(db, load)
    us = _understat(load)
    res = name_resolver.resolve_players(players, teams, us)
    gabriel = next((p for p in us if p.player_name == "Gabriel"), None)
    if gabriel is not None:  # Arsenal has multiple "Gabriel"s -> must not force a match
        assert gabriel.id not in res.matched


def test_manual_override_is_authoritative(db, load):
    players, teams = _fpl_rows(db, load)
    us = _understat(load)
    target = us[0]
    res = name_resolver.resolve_players(players, teams, us, overrides={target.id: 99999})
    assert res.matched[target.id] == 99999
