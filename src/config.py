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
