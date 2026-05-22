from datetime import datetime
from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    # Unknown extra fields are ignored: API *additions* must not break us,
    # but *renames/retypes/removals* of modeled fields raise loudly (R1).
    model_config = ConfigDict(extra="ignore")


class Event(_Base):
    id: int
    name: str
    deadline_time: datetime
    is_current: bool
    is_next: bool
    finished: bool


class Team(_Base):
    id: int
    name: str
    short_name: str
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int


class Element(_Base):
    id: int
    first_name: str
    second_name: str
    web_name: str
    team: int
    element_type: int
    now_cost: int
    status: str
    selected_by_percent: float
    form: float


class ElementType(_Base):
    id: int
    singular_name_short: str


class BootstrapStatic(_Base):
    events: list[Event]
    teams: list[Team]
    elements: list[Element]
    element_types: list[ElementType]


class Fixture(_Base):
    id: int
    event: int | None
    team_h: int
    team_a: int
    kickoff_time: datetime | None
    finished: bool
    team_h_score: int | None
    team_a_score: int | None


class Entry(_Base):
    id: int
    name: str
    player_first_name: str
    player_last_name: str
    summary_overall_points: int | None
    summary_overall_rank: int | None


class EntryHistory(_Base):
    event: int
    bank: int
    value: int


class Pick(_Base):
    element: int
    position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool


class EntryPicks(_Base):
    active_chip: str | None
    entry_history: EntryHistory
    picks: list[Pick]


class ElementSummary(_Base):
    # Modeled lightly; not persisted this slice (consumed by Analytics later).
    history: list[dict]
    fixtures: list[dict]


class UnderstatPlayer(_Base):
    id: str
    player_name: str
    team_title: str
    games: int
    time: int            # minutes
    goals: int
    assists: int
    xG: float
    xA: float
    npg: int
    npxG: float


class UnderstatPlayersResponse(_Base):
    success: bool
    players: list[UnderstatPlayer]
