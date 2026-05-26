from src.interface import telegram


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.is_configured() is False


def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "tok")
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "123")
    assert telegram.is_configured() is True


def test_is_configured_false_when_only_token(monkeypatch):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, "tok")
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.is_configured() is False


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def json(self):
        return self._payload


class _FakeSession:
    """Mimics requests.Session.post (json=, timeout=)."""
    def __init__(self, resp=None, boom=False):
        self._resp = resp or _Resp()
        self._boom = boom
        self.posted = None

    def post(self, url, json=None, timeout=None):
        if self._boom:
            import requests
            raise requests.RequestException("boom")
        self.posted = {"url": url, "json": json, "timeout": timeout}
        return self._resp


def _configure(monkeypatch, token="TOK", chat="42"):
    monkeypatch.setenv(telegram.BOT_TOKEN_ENV, token)
    monkeypatch.setenv(telegram.CHAT_ID_ENV, chat)


def test_send_message_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    sess = _FakeSession()
    assert telegram.send_message("hi", session=sess) is False
    assert sess.posted is None


def test_send_message_posts_and_returns_true(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    assert telegram.send_message("hello", session=sess) is True
    assert sess.posted["url"] == "https://api.telegram.org/botTOK/sendMessage"
    assert sess.posted["json"] == {"chat_id": "42", "text": "hello"}


def test_send_message_includes_buttons(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    btns = [[{"text": "Yes", "callback_data": "y"}]]
    telegram.send_message("q", buttons=btns, session=sess)
    assert sess.posted["json"]["reply_markup"] == {"inline_keyboard": btns}


def test_send_message_false_on_non_200(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(500, {}))) is False


def test_send_message_false_on_ok_false(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(200, {"ok": False}))) is False


def test_send_message_false_on_network_error(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(boom=True)) is False


def test_format_executed():
    assert telegram._format("executed", "Captain: X") == "✅ Executed\nCaptain: X"


def test_format_info_has_review_suffix():
    out = telegram._format("info", "Captain pending: X")
    assert out == "📊 Decision pending\nCaptain pending: X\nReview before the deadline."


def test_format_alert():
    assert telegram._format("alert", "session expired") == "❌ Autopilot blocked\nsession expired"


def test_notify_noop_unconfigured_no_log(db, monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: sent.append(1) or True)
    assert telegram.notify(db, kind="info", decision_type="captain", mode="manual", summary="s") is False
    assert sent == []
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_notify_success_no_failure_log(db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: True)
    assert telegram.notify(db, kind="executed", decision_type="captain", mode="auto",
                           summary="Captain: X") is True
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_notify_failure_logs_one_row_without_token(db, monkeypatch):
    _configure(monkeypatch, token="SECRET_TOKEN")
    monkeypatch.setattr(telegram, "send_message", lambda text, **k: False)
    assert telegram.notify(db, kind="info", decision_type="transfer", mode="hybrid",
                           summary="OUT A IN B") is False
    rows = db.execute(
        "SELECT decision_type, action_taken, inputs_json, executed FROM activity_log").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["decision_type"] == "notification"
    assert r["executed"] == 0
    assert "SECRET_TOKEN" not in (r["action_taken"] + (r["inputs_json"] or ""))


def test_notify_plan_noop_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    calls = []
    monkeypatch.setattr(telegram, "notify", lambda *a, **k: calls.append(k))
    telegram.notify_plan(None, [{"decision": "captain", "executed": True, "summary": "x"}], mode="auto")
    assert calls == []


def test_notify_plan_maps_kinds(monkeypatch):
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(telegram, "notify", lambda conn, **k: calls.append(k))
    plan = [{"decision": "captain", "executed": True, "summary": "Cap: X"},
            {"decision": "transfer", "executed": False, "summary": "OUT A IN B"}]
    telegram.notify_plan("CONN", plan, mode="hybrid")
    assert [c["kind"] for c in calls] == ["executed", "info"]
    assert [c["decision_type"] for c in calls] == ["captain", "transfer"]
    assert [c["summary"] for c in calls] == ["Cap: X", "OUT A IN B"]


def test_send_message_false_on_non_dict_json(monkeypatch):
    _configure(monkeypatch)
    assert telegram.send_message("x", session=_FakeSession(_Resp(200, True))) is False


def test_is_configured_false_when_only_chat(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(telegram.CHAT_ID_ENV, "123")
    assert telegram.is_configured() is False


def test_notify_alert_sends_formatted_text(db, monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _rec(text, **kwargs):
        captured["text"] = text
        return True

    monkeypatch.setattr(telegram, "send_message", _rec)
    assert telegram.notify(db, kind="alert", decision_type="auth", mode="auto",
                           summary="FPL session expired") is True
    assert captured["text"] == "❌ Autopilot blocked\nFPL session expired"
    assert db.execute("SELECT COUNT(*) c FROM activity_log").fetchone()["c"] == 0


def test_get_updates_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.get_updates(None, session=_FakeSession()) == []


def test_get_updates_returns_result_and_passes_offset(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True, "result": [{"update_id": 5}]}))
    out = telegram.get_updates(7, session=sess)
    assert out == [{"update_id": 5}]
    assert sess.posted["url"].endswith("/getUpdates")
    assert sess.posted["json"] == {"offset": 7, "timeout": 0}


def test_get_updates_empty_on_error(monkeypatch):
    _configure(monkeypatch)
    assert telegram.get_updates(None, session=_FakeSession(boom=True)) == []
    assert telegram.get_updates(None, session=_FakeSession(_Resp(500, {}))) == []


def test_answer_callback_query_posts_when_configured(monkeypatch):
    _configure(monkeypatch)
    sess = _FakeSession(_Resp(200, {"ok": True}))
    assert telegram.answer_callback_query("cbid", text="ok", session=sess) is True
    assert sess.posted["url"].endswith("/answerCallbackQuery")
    assert sess.posted["json"]["callback_query_id"] == "cbid"
    assert sess.posted["json"]["text"] == "ok"


def test_answer_callback_query_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv(telegram.BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(telegram.CHAT_ID_ENV, raising=False)
    assert telegram.answer_callback_query("cbid", session=_FakeSession()) is False


# ---------------------------------------------------------------------------
# T12: notify_plan captain swap (S-A.1)
# ---------------------------------------------------------------------------

def _seed_captain_db(conn):
    """Minimum schema rows so captain.get_captain_picks(conn) returns >=1 pick."""
    import json as _json
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-05-20T11:00:00Z', 0, 1, 0, 'PENDING')")
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), (2, 'Brentford', 'BRE')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a')")
    conn.execute("INSERT INTO my_team(gw, picks_json) VALUES (38, ?)",
                 (_json.dumps([{"element": 10, "position": 1, "multiplier": 2,
                                "is_captain": True, "is_vice_captain": False}]),))
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-05-20T14:00:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) "
                 "VALUES (1, 38, 2, 2, '2026-05-19T00:00:00Z')")
    conn.execute("INSERT INTO xp(player_id, gw, model_version, xp, xminutes, computed_at) "
                 "VALUES (10, 38, 'v1', 7.2, 90, '2026-05-19T00:00:00Z')")
    conn.commit()


def test_notify_plan_swaps_captain_summary_when_ai_cache_populated(monkeypatch, tmp_path):
    """If cached AI captain prose exists for the next gw, notify_plan uses it
    as the captain entry's summary instead of the plan's existing summary."""
    from src.data.db import connect, init_db
    from src.interface import telegram as tg
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import captain

    conn = connect(":memory:")
    init_db(conn)
    _seed_captain_db(conn)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    decision = captain.get_captain_picks(conn)
    assert decision["picks"], "fixture seed must produce at least one captain pick"
    payload = ai_reasoning._build_captain_payload(decision)
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn.execute(
        "SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="captain", rec_hash=rec_hash,
                 prose="AI prose for captain.", model_id="m")

    sent = []

    class _LocalFakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "captain", "summary": "template summary", "executed": True}]
    tg.notify_plan(conn, plan, mode="manual", session=_LocalFakeSession())
    assert sent, "telegram.send_message should have been called"
    assert "AI prose for captain." in sent[0]["text"]
    assert "template summary" not in sent[0]["text"]


def test_notify_plan_uses_classic_summary_when_no_ai_cache(monkeypatch, tmp_path):
    from src.data.db import connect, init_db
    from src.interface import telegram as tg

    conn = connect(":memory:")
    init_db(conn)
    _seed_captain_db(conn)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    sent = []

    class _LocalFakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "captain", "summary": "template summary", "executed": True}]
    tg.notify_plan(conn, plan, mode="manual", session=_LocalFakeSession())
    assert sent
    assert "template summary" in sent[0]["text"]


def _seed_transfer_db(conn):
    """Minimal seed so transfers.get_transfer_suggestions returns >=1 row.
    Squad starts with Watkins; Haaland is a buy candidate (higher xp_5gw)."""
    import json as _json
    conn.execute("INSERT INTO gameweeks(id, name, deadline_utc, is_current, is_next, "
                 "finished, state) VALUES (38, 'GW38', '2026-06-02T18:30Z', 0, 1, 0, 'PENDING')")
    conn.execute("INSERT INTO teams(id, name, short_name) VALUES (1, 'Man City', 'MCI'), "
                 "(2, 'Brentford', 'BRE'), (3, 'Aston Villa', 'AVL')")
    conn.execute("INSERT INTO players(id, web_name, position, team_id, price, status) "
                 "VALUES (10, 'Haaland', 'FWD', 1, 14.0, 'a'), "
                 "(20, 'Watkins', 'FWD', 3, 9.0, 'a'), "
                 "(30, 'Isak', 'FWD', 2, 9.3, 'a')")
    conn.execute("INSERT INTO my_team(gw, picks_json, bank) VALUES (38, ?, 0.5)",
                 (_json.dumps([{"element": 20, "position": 11, "multiplier": 1,
                                "is_captain": False, "is_vice_captain": False}]),))
    conn.execute("INSERT INTO fixtures(id, gw, home_team_id, away_team_id, kickoff_utc, finished) "
                 "VALUES (1, 38, 1, 2, '2026-06-02T19:00Z', 0), (2, 38, 3, 2, '2026-06-02T19:00Z', 0)")
    conn.execute("INSERT INTO fdr(team_id, gw, fdr_attack, fdr_defense, computed_at) VALUES "
                 "(1, 38, 2, 2, '2026-05-19T00:00Z'), (3, 38, 4, 4, '2026-05-19T00:00Z')")
    # Haaland (buy) >> median > Watkins (sell); Isak anchors the median above 3.0
    conn.execute("INSERT INTO xp(player_id, gw, model_version, xp, xminutes, computed_at) VALUES "
                 "(10, 38, 'v1', 8.0, 90, '2026-05-19T00:00Z'), "
                 "(20, 38, 'v1', 3.0, 90, '2026-05-19T00:00Z'), "
                 "(30, 38, 'v1', 5.0, 90, '2026-05-19T00:00Z')")
    conn.commit()


def test_notify_plan_swaps_transfer_summary_when_ai_cache_populated(monkeypatch, tmp_path):
    """If cached AI transfer prose exists for the next gw, notify_plan uses it for the transfer entry."""
    from src.data.db import connect, init_db
    from src.interface import telegram
    from src.ai import cache as ai_cache, reasoning as ai_reasoning
    from src.decisions import transfers

    conn = connect(":memory:")
    init_db(conn)
    _seed_transfer_db(conn)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    decision = transfers.get_transfer_suggestions(conn)
    assert decision["suggestions"], "seed must produce >= 1 transfer suggestion"
    payload = ai_reasoning._build_transfer_payload(conn, decision)
    rec_hash = ai_cache.recommendation_hash(payload)
    nxt = conn.execute("SELECT MIN(id) AS gw FROM gameweeks WHERE finished=0").fetchone()["gw"]
    ai_cache.put(conn, gw=nxt, pane_type="transfer", rec_hash=rec_hash,
                 prose="AI prose for transfer.", model_id="m")

    sent = []
    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "transfer", "summary": "template transfer summary", "executed": True}]
    telegram.notify_plan(conn, plan, mode="manual", session=_FakeSession())
    assert sent, "telegram.send_message should have been called"
    assert "AI prose for transfer." in sent[0]["text"]
    assert "template transfer summary" not in sent[0]["text"]


def test_notify_plan_uses_classic_summary_when_no_transfer_ai_cache(monkeypatch, tmp_path):
    from src.data.db import connect, init_db
    from src.interface import telegram

    conn = connect(":memory:")
    init_db(conn)
    _seed_transfer_db(conn)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    sent = []
    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            sent.append(json)
            class R:
                status_code = 200
                def json(self): return {"ok": True}
            return R()

    plan = [{"decision": "transfer", "summary": "template transfer summary", "executed": True}]
    telegram.notify_plan(conn, plan, mode="manual", session=_FakeSession())
    assert sent
    assert "template transfer summary" in sent[0]["text"]
