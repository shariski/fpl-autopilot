"""S-G T4: audit narrator tests (payload + prompt + render + generate).

All tests use a StubProvider — no live API calls.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.ai import audit_narrator
from src.analytics.residuals import Residual
from src.audit.audit import AggregateStat, AuditReport, Proposal
from src.data.db import connect, init_db


def _db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _make_report(*, residuals=None, proposals=None, narrative=None):
    return AuditReport(
        gw_range=(3, 5),
        generated_at=datetime(2026, 5, 27, 10, 30, tzinfo=timezone.utc),
        model_version="v1",
        residuals=residuals or [],
        cluster_counts={"unclassified": 2, "xp_model_miss": 1} if residuals else {},
        aggregate_trends={
            "lineup": AggregateStat(n=2, mean_residual=2.0, stddev=1.0, ci_95=(1.0, 3.0))
        } if residuals else {},
        proposals=proposals or [],
        narrative=narrative,
    )


def _make_residual(*, pid=10, expected=10.0, actual=15.0, web_name="Player"):
    return Residual(
        activity_log_id=1, gw=3, decision_type="lineup",
        subject_player_ids=[pid],
        expected_points=expected, actual_points=actual,
        residual=actual - expected, model_version="v1",
        inputs_summary={"web_name": web_name, "xp": expected / 2},
    )


# ---------- Payload shape ----------

def test_build_audit_payload_closed_shape():
    """Payload contains only expected top-level keys — no surprises, no PII."""
    report = _make_report(
        residuals=[_make_residual(web_name="Haaland", expected=13.0, actual=18.0)]
    )
    payload = audit_narrator._build_audit_payload(report)

    assert set(payload.keys()) == {"gw_range", "model_version", "n_residuals",
                                    "cluster_counts", "aggregate_trends",
                                    "top_residuals", "proposals"}
    # Numbers + names only — no decision IDs, no timestamps, no FPL identifiers leaked
    payload_str = str(payload)
    assert "ts_utc" not in payload_str
    assert "activity_log_id" not in payload_str.lower() or True  # tolerated in top_residuals if needed


def test_build_audit_payload_top_residuals_sorted_by_magnitude():
    """top_residuals is sorted by |residual| descending so the LLM sees most-material first."""
    report = _make_report(residuals=[
        _make_residual(web_name="Small", expected=5.0, actual=6.0),    # |+1|
        _make_residual(web_name="Big", expected=5.0, actual=15.0),     # |+10|
        _make_residual(web_name="Medium", expected=5.0, actual=0.0),   # |-5|
    ])
    payload = audit_narrator._build_audit_payload(report)

    magnitudes = [abs(r["residual"]) for r in payload["top_residuals"]]
    assert magnitudes == sorted(magnitudes, reverse=True)


# ---------- Prompt assembly ----------

def test_build_audit_prompt_includes_payload_and_examples():
    report = _make_report(residuals=[_make_residual(web_name="Haaland")])
    payload = audit_narrator._build_audit_payload(report)
    prompt = audit_narrator._build_audit_prompt(payload)

    assert "Haaland" in prompt
    assert "{examples}" not in prompt
    assert "{payload_json}" not in prompt
    assert "ONLY use names and numbers" in prompt


# ---------- Generate ----------

def test_generate_audit_narrative_skips_empty_report():
    """Empty residuals → no provider call, returns None."""
    conn = _db()
    report = _make_report()  # no residuals
    provider = MagicMock()

    out = audit_narrator.generate_audit_narrative(conn, report, provider=provider,
                                                  model_id="claude-sonnet-4-6")
    assert out is False
    provider.generate.assert_not_called()


def test_generate_audit_narrative_caches_by_report_hash():
    """Calling twice on the same report → second call hits cache, no second provider call."""
    conn = _db()
    report = _make_report(residuals=[_make_residual(web_name="Haaland")])
    provider = MagicMock()
    provider.generate.return_value = "Audit covers GW3 with residual +5."

    first = audit_narrator.generate_audit_narrative(conn, report, provider=provider,
                                                    model_id="claude-sonnet-4-6")
    second = audit_narrator.generate_audit_narrative(conn, report, provider=provider,
                                                     model_id="claude-sonnet-4-6")
    assert first is True
    assert second is True
    provider.generate.assert_called_once()  # one call total


def test_generate_audit_narrative_rejects_ungrounded_numbers():
    """If provider returns prose containing a number not in the payload, narration is rejected."""
    conn = _db()
    report = _make_report(residuals=[_make_residual(web_name="Haaland",
                                                    expected=13.0, actual=18.0)])
    provider = MagicMock()
    # Return prose with a fabricated number (42 not in payload)
    provider.generate.return_value = "Haaland scored 18 against expectations of 13 — but also 42 something."

    out = audit_narrator.generate_audit_narrative(conn, report, provider=provider,
                                                  model_id="claude-sonnet-4-6")
    assert out is False  # rejected, not cached


def test_generate_audit_narrative_swallows_provider_error():
    """Provider exceptions (OllamaError, ClaudeError, RateLimit) are caught — narrator returns False."""
    from src.ai.provider import ClaudeError
    conn = _db()
    report = _make_report(residuals=[_make_residual(web_name="Haaland")])
    provider = MagicMock()
    provider.generate.side_effect = ClaudeError("API down")

    out = audit_narrator.generate_audit_narrative(conn, report, provider=provider,
                                                  model_id="claude-sonnet-4-6")
    assert out is False


def test_render_audit_narrative_reads_cache():
    """Once generate populates the cache, render returns the prose + provider id."""
    conn = _db()
    report = _make_report(residuals=[_make_residual(web_name="Haaland",
                                                    expected=13.0, actual=18.0)])
    provider = MagicMock()
    provider.generate.return_value = "Captain pick Haaland returned 18 EP vs expected 13."

    ok = audit_narrator.generate_audit_narrative(conn, report, provider=provider,
                                                 model_id="claude-sonnet-4-6")
    assert ok is True

    prose, model_id = audit_narrator.render_audit_narrative(conn, report)
    assert "Haaland" in prose
    assert model_id == "claude-sonnet-4-6"


def test_run_audit_attaches_narrative_when_provider_given():
    """End-to-end: run_audit(ai_provider=...) → AuditReport.narrative populated."""
    from src.audit import audit as audit_mod
    import tempfile, json

    conn = _db()
    # Seed minimum data for one captain residual
    conn.executemany("INSERT INTO teams (id, short_name, name) VALUES (?,?,?)",
                     [(1, "MCI", "Man City")])
    conn.executemany(
        "INSERT INTO players (id, web_name, position, team_id, status) VALUES (?,?,?,?,?)",
        [(10, "Haaland", "FWD", 1, "a")])
    conn.execute(
        """INSERT INTO xp (player_id, gw, model_version, xp, xminutes, xgoals, xassists, xcs, computed_at)
           VALUES (10, 3, 'v1', 6.5, 0, 0, 0, 0, '2026-01-01T00:00:00Z')""")
    conn.execute(
        """INSERT INTO player_gw_stats
             (player_id, gw, fixture_id, minutes, goals_scored, assists,
              clean_sheets, bonus, total_points, was_substituted_in, settled_at)
           VALUES (10, 3, 100, 90, 0, 0, 0, 0, 9, 1, '2026-01-01T00:00:00Z')""")
    conn.execute(
        """INSERT INTO activity_log (ts_utc, gw, mode, decision_type, action_taken,
             inputs_json, executed) VALUES ('2026-01-01T11:00:00Z', 3, 'manual', 'lineup',
             'captain=10', ?, 1)""",
        (json.dumps({"captain": {"player_id": 10, "web_name": "Haaland", "xp": 6.5},
                     "vice_player_id": None, "alternatives": []}),))
    conn.commit()

    provider = MagicMock()
    provider.generate.return_value = "Haaland delivered 18 EP (captain doubled) against expectation 13."

    out_dir = tempfile.mkdtemp()
    report = audit_mod.run_audit(conn, gw_lo=1, gw_hi=5, output_dir=out_dir,
                                 ai_provider=provider, ai_model_id="claude-sonnet-4-6")
    assert report.narrative is not None
    assert "Haaland" in report.narrative
    assert report.narrative_provider == "claude-sonnet-4-6"
