"""S-G T6: /api/audit/{gw} endpoint tests."""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.audit.audit import AuditReport
from src.audit import reports
from src.data.db import connect, init_db
from src.interface.api import app
from src.interface.deps import get_db


@pytest.fixture
def client_and_audit_dir(monkeypatch):
    """A TestClient + a temp data/audit directory that the endpoint reads from."""
    conn = connect(":memory:")
    init_db(conn)
    app.dependency_overrides[get_db] = lambda: conn

    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(reports, "DEFAULT_DIR", Path(tmp))

    yield TestClient(app), Path(tmp)

    app.dependency_overrides.clear()
    conn.close()


def _persist_minimal_report(audit_dir, *, gw_lo, gw_hi, generated_at):
    """Drop a real audit JSON in the dir using reports.persist for fidelity."""
    report = AuditReport(
        gw_range=(gw_lo, gw_hi),
        generated_at=generated_at,
        model_version="v1",
        residuals=[], cluster_counts={"unclassified": 0}, aggregate_trends={},
        proposals=[], narrative=None, narrative_provider=None,
    )
    reports.persist(report, output_dir=audit_dir)


def test_audit_endpoint_returns_404_when_no_audit_persisted(client_and_audit_dir):
    client, _audit_dir = client_and_audit_dir
    resp = client.get("/api/audit/5")
    assert resp.status_code == 404


def test_audit_endpoint_returns_persisted_report(client_and_audit_dir):
    client, audit_dir = client_and_audit_dir
    _persist_minimal_report(
        audit_dir, gw_lo=3, gw_hi=5,
        generated_at=datetime(2026, 5, 27, 10, 30, tzinfo=timezone.utc))

    resp = client.get("/api/audit/5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gw_range"] == [3, 5]
    assert body["model_version"] == "v1"


def test_audit_endpoint_returns_most_recent_match(client_and_audit_dir):
    """Multiple audits for gw=5 → endpoint returns the most recently generated."""
    client, audit_dir = client_and_audit_dir
    _persist_minimal_report(audit_dir, gw_lo=3, gw_hi=5,
                            generated_at=datetime(2026, 5, 20, tzinfo=timezone.utc))
    _persist_minimal_report(audit_dir, gw_lo=4, gw_hi=5,
                            generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc))

    resp = client.get("/api/audit/5")
    assert resp.status_code == 200
    body = resp.json()
    # The more recent one has gw_lo=4
    assert body["gw_range"] == [4, 5]


def test_audit_endpoint_ignores_audits_for_different_gw(client_and_audit_dir):
    """An audit for gw=5 is not returned for /api/audit/6."""
    client, audit_dir = client_and_audit_dir
    _persist_minimal_report(audit_dir, gw_lo=3, gw_hi=5,
                            generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc))

    resp = client.get("/api/audit/6")
    assert resp.status_code == 404
