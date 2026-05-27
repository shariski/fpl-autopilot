import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_DB_PATH = str(ROOT / "data" / "fpl_autopilot.db")


def load_config(path=None):
    path = pathlib.Path(path) if path else CONFIG_PATH
    with open(path) as f:
        return yaml.safe_load(f)


def team_id(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg["fpl"]["team_id"]


def db_path(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    raw = cfg.get("storage", {}).get("db_path", DEFAULT_DB_PATH)
    if raw == ":memory:":
        return raw
    p = pathlib.Path(raw).expanduser()
    return str(p if p.is_absolute() else ROOT / p)


def mode(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("mode", {}).get("current", "manual")


def confidence_floor(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("thresholds", {}).get("confidence_floor", 70)


def unattended_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("unattended", {}).get("enabled", False))


def unattended_hours_before(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("unattended", {}).get("hours_before_deadline", 2)


def telegram_interactive_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("telegram", {}).get("interactive", False))


def deadguard_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("deadguard", {}).get("enabled", False))


def deadguard_warning_minutes(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("warning_window_minutes", 120)


def deadguard_trigger_minutes(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("trigger_window_minutes", 30)


def deadguard_reeval_enabled(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return bool(cfg.get("deadguard", {}).get("reeval_if_late_news", True))


def deadguard_reeval_lockout_minutes(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("reeval_lockout_minutes", 15)


def _deadguard_scope(cfg):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("deadguard", {}).get("scope", {})


def deadguard_transfer_if_flagged(cfg=None):
    return bool(_deadguard_scope(cfg).get("transfer_if_flagged", True))


def deadguard_min_ep_delta(cfg=None):
    return _deadguard_scope(cfg).get("min_ep_delta_for_transfer", 3.0)


def deadguard_confidence_floor(cfg=None):
    return _deadguard_scope(cfg).get("confidence_floor", 75)


def _ai(cfg):
    cfg = cfg if cfg is not None else load_config()
    return cfg.get("ai", {})


def _ai_ollama(cfg):
    return _ai(cfg).get("ollama", {})


def ai_enabled(cfg=None):
    return bool(_ai(cfg).get("enabled", True))


def ai_provider(cfg=None):
    return _ai(cfg).get("provider", "ollama")


def ai_ollama_host(cfg=None):
    return _ai_ollama(cfg).get("host", "http://localhost:11434")


def ai_ollama_model(cfg=None):
    return _ai_ollama(cfg).get("model", "qwen2.5:7b-instruct-q4_K_M")


def ai_timeout_seconds(cfg=None):
    return _ai(cfg).get("timeout_seconds", 15)


def ai_consecutive_failure_backoff(cfg=None):
    return _ai(cfg).get("consecutive_failure_backoff", 3)


def ai_temperature(cfg=None):
    return _ai(cfg).get("temperature", 0.2)


def ai_max_tokens_per_pane(cfg=None):
    return _ai(cfg).get("max_tokens_per_pane", 200)
