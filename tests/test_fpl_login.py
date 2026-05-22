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


def test_login_team_id_mismatch():
    sess = _FakeSession(me_payload={"player": {"entry": 999}},
                        cookies={"pl_profile": "abc"})
    with pytest.raises(fpl_login.FPLLoginError):
        fpl_login.login("me@example.com", "throwaway-pw",
                        expected_team_id=3122849, session=sess)


def test_login_not_authenticated():
    sess = _FakeSession(me_payload={}, cookies={})  # no "player" key
    with pytest.raises(fpl_login.FPLLoginError):
        fpl_login.login("me@example.com", "throwaway-pw",
                        expected_team_id=3122849, session=sess)


def test_login_bad_credentials():
    # /me would look authenticated, but the login POST signalled failure
    sess = _FakeSession(
        me_payload={"player": {"entry": 3122849}},
        post_url="https://fantasy.premierleague.com/a/login?state=fail",
        cookies={"pl_profile": "abc"},
    )
    with pytest.raises(fpl_login.FPLLoginError) as exc:
        fpl_login.login("me@example.com", "throwaway-pw",
                        expected_team_id=3122849, session=sess)
    assert "throwaway-pw" not in str(exc.value)  # password never in the message
