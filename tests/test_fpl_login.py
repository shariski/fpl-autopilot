import pytest
import requests
from src.auth import fpl_login


class _Resp:
    def __init__(self, url="", status_code=200, payload=None):
        self.url = url
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Stand-in for requests.Session: canned login POST + /me GET, real cookie jar."""

    def __init__(self, *, me_payload, post_url="https://fantasy.premierleague.com/a/login",
                 me_status=200, cookies=None):
        self.headers = {}
        self._me_payload = me_payload
        self._post_url = post_url
        self._me_status = me_status
        self.cookies = requests.cookies.RequestsCookieJar()
        for k, v in (cookies or {}).items():
            self.cookies.set(k, v)

    def post(self, url, data=None, timeout=None):
        return _Resp(url=self._post_url)

    def get(self, url, timeout=None):
        return _Resp(status_code=self._me_status, payload=self._me_payload)


def test_login_success():
    sess = _FakeSession(
        me_payload={"player": {"entry": 3122849}},
        cookies={"pl_profile": "abc", "csrftoken": "tok"},
    )
    res = fpl_login.login("me@example.com", "throwaway-pw",
                          expected_team_id=3122849, session=sess)
    assert res.entry_id == 3122849
    assert res.cookies["pl_profile"] == "abc"
    assert res.csrf == "tok"
