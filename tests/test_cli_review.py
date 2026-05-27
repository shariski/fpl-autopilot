"""S-G T5: CLI `review` subcommand tests."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src import cli


def _empty_report_mock(gw_range=(3, 6)):
    """A MagicMock shaped like AuditReport that works with format_text."""
    m = MagicMock(persisted_path=None, residuals=[], cluster_counts={},
                  aggregate_trends={}, proposals=[], narrative=None,
                  narrative_provider=None, gw_range=gw_range,
                  generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
                  model_version="v1")
    return m


def _seed_settled_gws(conn, gws):
    """Mark these GWs as finished=1 in the gameweeks table."""
    for gw in gws:
        conn.execute(
            "INSERT INTO gameweeks (id, deadline_utc, finished, is_next, is_current) "
            "VALUES (?,?,?,?,?)",
            (gw, f"2026-01-{gw:02d}T11:30:00Z", 1, 0, 0))
    conn.commit()


# ---------- Window resolution ----------

def test_review_default_runs_last_4_gws(db, capsys):
    """No --gw / --last → audit the last 4 settled GWs."""
    _seed_settled_gws(db, [1, 2, 3, 4, 5, 6])
    with patch("src.audit.audit.run_audit") as mock_audit:
        mock_audit.return_value = _empty_report_mock(gw_range=(3, 6))
        cli._cmd_review_cli(conn=db, ai_override="none")

    kwargs = mock_audit.call_args.kwargs
    assert kwargs["gw_lo"] == 3
    assert kwargs["gw_hi"] == 6


def test_review_gw_argument(db):
    """--gw 3 → audit only GW3."""
    _seed_settled_gws(db, [1, 2, 3, 4])
    with patch("src.audit.audit.run_audit") as mock_audit:
        mock_audit.return_value = _empty_report_mock(gw_range=(3, 3))
        cli._cmd_review_cli(conn=db, gw=3, ai_override="none")

    kwargs = mock_audit.call_args.kwargs
    assert kwargs["gw_lo"] == 3
    assert kwargs["gw_hi"] == 3


def test_review_last_argument(db):
    """--last 2 → audit the last 2 settled GWs."""
    _seed_settled_gws(db, [1, 2, 3, 4, 5])
    with patch("src.audit.audit.run_audit") as mock_audit:
        mock_audit.return_value = _empty_report_mock(gw_range=(4, 5))
        cli._cmd_review_cli(conn=db, last=2, ai_override="none")

    kwargs = mock_audit.call_args.kwargs
    assert kwargs["gw_lo"] == 4
    assert kwargs["gw_hi"] == 5


def test_review_handles_no_settled_gws(db, capsys):
    """Empty gameweeks → graceful message, not a stack trace."""
    cli._cmd_review_cli(conn=db, ai_override="none")
    out = capsys.readouterr().out
    assert "no settled" in out.lower() or "nothing to audit" in out.lower()


# ---------- Output formatting ----------

def test_review_format_text_default(db, capsys):
    """Default --format text → human-readable lines from reports.format_text."""
    _seed_settled_gws(db, [3])
    with patch("src.audit.audit.run_audit") as mock_audit, \
         patch("src.audit.reports.format_text") as mock_format:
        mock_format.return_value = "FORMATTED TEXT OUTPUT"
        mock_audit.return_value = MagicMock(persisted_path="/tmp/x.json", residuals=[],
                                             cluster_counts={}, aggregate_trends={},
                                             proposals=[], narrative=None,
                                             narrative_provider=None, gw_range=(3, 3),
                                             generated_at=None, model_version="v1")
        cli._cmd_review_cli(conn=db, gw=3, ai_override="none", format_="text")

    out = capsys.readouterr().out
    assert "FORMATTED TEXT OUTPUT" in out


def test_review_format_json(db, capsys):
    """--format json → reports._to_jsonable serialized to stdout."""
    from datetime import datetime, timezone
    from src.audit.audit import AuditReport
    _seed_settled_gws(db, [3])

    real_report = AuditReport(
        gw_range=(3, 3),
        generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        model_version="v1",
        residuals=[], cluster_counts={}, aggregate_trends={},
        proposals=[], narrative=None, narrative_provider=None,
        persisted_path="/tmp/x.json",
    )
    with patch("src.audit.audit.run_audit", return_value=real_report):
        cli._cmd_review_cli(conn=db, gw=3, ai_override="none", format_="json")

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["gw_range"] == [3, 3]
    assert parsed["model_version"] == "v1"


# ---------- AI provider selection ----------

def test_review_ai_override_none_skips_provider(db):
    """--ai none → no provider is constructed, run_audit gets ai_provider=None."""
    _seed_settled_gws(db, [3])
    with patch("src.audit.audit.run_audit") as mock_audit:
        mock_audit.return_value = _empty_report_mock(gw_range=(3, 3))
        cli._cmd_review_cli(conn=db, gw=3, ai_override="none")

    kwargs = mock_audit.call_args.kwargs
    assert kwargs.get("ai_provider") is None


def test_review_ai_override_claude_requires_api_key(db, monkeypatch, capsys):
    """--ai claude with no ANTHROPIC_API_KEY → graceful error, audit not run."""
    _seed_settled_gws(db, [3])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("src.audit.audit.run_audit") as mock_audit:
        cli._cmd_review_cli(conn=db, gw=3, ai_override="claude")

    mock_audit.assert_not_called()
    err = capsys.readouterr().out + capsys.readouterr().err
    # Some error/warning surfaced to the user about missing key
    # (we read both out + err since the implementation may print to either)
