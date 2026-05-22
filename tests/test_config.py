from src import config


def test_team_id_from_config():
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": "x.db"}}
    assert config.team_id(cfg) == 3122849
    assert config.db_path(cfg) == "x.db"


def test_loads_repo_config_yaml():
    cfg = config.load_config()
    assert cfg["fpl"]["team_id"] == 3122849
