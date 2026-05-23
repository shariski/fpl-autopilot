from src import config


def test_team_id_from_config():
    cfg = {"fpl": {"team_id": 3122849}, "storage": {"db_path": "x.db"}}
    assert config.team_id(cfg) == 3122849
    assert config.db_path(cfg) == str(config.ROOT / "x.db")


def test_loads_repo_config_yaml():
    cfg = config.load_config()
    assert cfg["fpl"]["team_id"] == 3122849


def test_db_path_memory_and_absolute_passthrough():
    assert config.db_path({"storage": {"db_path": ":memory:"}}) == ":memory:"
    assert config.db_path({"storage": {"db_path": "/tmp/abs.db"}}) == "/tmp/abs.db"


def test_mode_from_config():
    assert config.mode({"mode": {"current": "auto"}}) == "auto"
    assert config.mode({}) == "manual"  # default


def test_confidence_floor_from_config():
    assert config.confidence_floor({"thresholds": {"confidence_floor": 65}}) == 65
    assert config.confidence_floor({}) == 70  # default
