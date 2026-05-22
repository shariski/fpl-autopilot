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
    session.post(
        LOGIN_URL,
        data={"login": email, "password": password,
              "app": "plfpl-web", "redirect_uri": REDIRECT_URI},
        timeout=TIMEOUT,
    )
    me = session.get(ME_URL, timeout=TIMEOUT).json()
    entry_id = me["player"]["entry"]
    cookies = {c.name: c.value for c in session.cookies}
    return LoginResult(cookies=cookies, csrf=session.cookies.get("csrftoken"), entry_id=entry_id)
