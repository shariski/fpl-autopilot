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


def test_unattended_enabled_from_config():
    assert config.unattended_enabled({"unattended": {"enabled": True}}) is True
    assert config.unattended_enabled({}) is False  # default off


def test_unattended_hours_before_from_config():
    assert config.unattended_hours_before({"unattended": {"hours_before_deadline": 5}}) == 5
    assert config.unattended_hours_before({}) == 2  # default


def test_telegram_interactive_enabled_from_config():
    assert config.telegram_interactive_enabled({"telegram": {"interactive": True}}) is True
    assert config.telegram_interactive_enabled({"telegram": {}}) is False
    assert config.telegram_interactive_enabled({}) is False  # default off


def test_deadguard_accessors_from_config():
    assert config.deadguard_enabled({"deadguard": {"enabled": True}}) is True
    assert config.deadguard_enabled({}) is False
    assert config.deadguard_warning_minutes({"deadguard": {"warning_window_minutes": 90}}) == 90
    assert config.deadguard_warning_minutes({}) == 120     # default
    assert config.deadguard_trigger_minutes({"deadguard": {"trigger_window_minutes": 45}}) == 45
    assert config.deadguard_trigger_minutes({}) == 30      # default


def test_deadguard_scope_accessors():
    cfg = {"deadguard": {"scope": {"transfer_if_flagged": False, "min_ep_delta_for_transfer": 4.0,
                                   "confidence_floor": 80}}}
    assert config.deadguard_transfer_if_flagged(cfg) is False
    assert config.deadguard_min_ep_delta(cfg) == 4.0
    assert config.deadguard_confidence_floor(cfg) == 80
    # defaults when the block/keys are absent (explicit empty dict must NOT fall back to config.yaml)
    assert config.deadguard_transfer_if_flagged({}) is True
    assert config.deadguard_min_ep_delta({}) == 3.0
    assert config.deadguard_confidence_floor({}) == 75


def test_deadguard_reeval_accessors():
    assert config.deadguard_reeval_enabled({"deadguard": {"reeval_if_late_news": False}}) is False
    assert config.deadguard_reeval_enabled({"deadguard": {}}) is True       # default on
    assert config.deadguard_reeval_enabled({}) is True                      # explicit {} must not fall back
    assert config.deadguard_reeval_lockout_minutes({"deadguard": {"reeval_lockout_minutes": 20}}) == 20
    assert config.deadguard_reeval_lockout_minutes({}) == 15                # default


def test_ai_defaults_when_missing():
    from src import config
    cfg = {}
    assert config.ai_enabled(cfg) is True
    assert config.ai_provider(cfg) == "ollama"
    assert config.ai_ollama_host(cfg) == "http://localhost:11434"
    assert config.ai_ollama_model(cfg) == "qwen2.5:7b-instruct-q4_K_M"
    assert config.ai_timeout_seconds(cfg) == 15
    assert config.ai_consecutive_failure_backoff(cfg) == 3
    assert config.ai_temperature(cfg) == 0.2
    assert config.ai_max_tokens_per_pane(cfg) == 200


def test_ai_overrides_from_yaml():
    from src import config
    cfg = {"ai": {"enabled": False, "ollama": {"model": "llama3.1:8b"}, "timeout_seconds": 30}}
    assert config.ai_enabled(cfg) is False
    assert config.ai_ollama_model(cfg) == "llama3.1:8b"
    assert config.ai_timeout_seconds(cfg) == 30
