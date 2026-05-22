import pytest
from pydantic import ValidationError
from src.data.models import BootstrapStatic, Fixture, EntryPicks, Entry


def test_bootstrap_parses(load):
    bs = BootstrapStatic.model_validate(load("bootstrap-static.json"))
    assert len(bs.elements) > 500
    assert len(bs.teams) == 20
    assert {et.singular_name_short for et in bs.element_types} >= {"GKP", "DEF", "MID", "FWD"}


def test_fixtures_parse(load):
    fixtures = [Fixture.model_validate(f) for f in load("fixtures.json")]
    assert len(fixtures) > 300


def test_entry_parses(load):
    entry = Entry.model_validate(load("entry.json"))
    assert entry.id == 3122849


def test_picks_parse(load):
    picks = EntryPicks.model_validate(load("picks.json"))
    assert len(picks.picks) == 15
    assert picks.entry_history.bank >= 0


def test_schema_drift_fails_loudly(load):
    data = load("bootstrap-static.json")
    del data["elements"][0]["id"]  # required field removed -> must raise
    with pytest.raises(ValidationError):
        BootstrapStatic.model_validate(data)
