from dataclasses import dataclass
import requests
from src.data.fpl_client import USER_AGENT

LOGIN_URL = "https://users.premierleague.com/accounts/login/"
ME_URL = "https://fantasy.premierleague.com/api/me/"
REDIRECT_URI = "https://fantasy.premierleague.com/a/login"
TIMEOUT = 10


class FPLLoginError(Exception):
    """Login or validation failure. Never carries the password or cookie values."""


@dataclass
class LoginResult:
    cookies: dict
    csrf: str | None
    entry_id: int


def login(email, password, *, expected_team_id, session=None):
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = session.post(
        LOGIN_URL,
        data={"login": email, "password": password,
              "app": "plfpl-web", "redirect_uri": REDIRECT_URI},
        timeout=TIMEOUT,
    )
    # Best-effort early failure. The /me check below is authoritative.
    if "state=fail" in (resp.url or ""):
        raise FPLLoginError("login failed — check FPL email/password")

    me_resp = session.get(ME_URL, timeout=TIMEOUT)
    if me_resp.status_code != 200:
        raise FPLLoginError("login appeared to succeed but session is not authenticated")
    me = me_resp.json()
    player = me.get("player")
    if not player or "entry" not in player:
        raise FPLLoginError("login appeared to succeed but session is not authenticated")
    entry_id = player["entry"]
    if entry_id != expected_team_id:
        raise FPLLoginError(
            f"authenticated as entry {entry_id} but config team_id is {expected_team_id}")

    cookies = {c.name: c.value for c in session.cookies}
    return LoginResult(cookies=cookies, csrf=session.cookies.get("csrftoken"), entry_id=entry_id)
