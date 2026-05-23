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
    cfg = cfg or load_config()
    return cfg["fpl"]["team_id"]


def db_path(cfg=None):
    cfg = cfg or load_config()
    raw = cfg.get("storage", {}).get("db_path", DEFAULT_DB_PATH)
    if raw == ":memory:":
        return raw
    p = pathlib.Path(raw).expanduser()
    return str(p if p.is_absolute() else ROOT / p)


def mode(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("mode", {}).get("current", "manual")


def confidence_floor(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("thresholds", {}).get("confidence_floor", 70)


def unattended_enabled(cfg=None):
    cfg = cfg or load_config()
    return bool(cfg.get("unattended", {}).get("enabled", False))


def unattended_hours_before(cfg=None):
    cfg = cfg or load_config()
    return cfg.get("unattended", {}).get("hours_before_deadline", 2)
