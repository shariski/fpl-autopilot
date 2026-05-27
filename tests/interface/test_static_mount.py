from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.interface.api import _mount_frontend


def _make_static_dir(tmp_path: Path) -> Path:
    """Create a fake built-frontend directory with an index.html."""
    d = tmp_path / "frontend_build"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><title>fpl-autopilot</title>")
    return d


def test_static_mount_present_when_directory_exists(tmp_path):
    """When the build dir exists, GET / returns the SPA's index.html."""
    test_app = FastAPI()
    _mount_frontend(test_app, build_dir=_make_static_dir(tmp_path))
    client = TestClient(test_app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "fpl-autopilot" in resp.text


def test_static_mount_absent_when_directory_missing(tmp_path):
    """When the build dir is missing, no mount is registered — local dev is unaffected."""
    test_app = FastAPI()
    _mount_frontend(test_app, build_dir=tmp_path / "does-not-exist")
    client = TestClient(test_app)
    resp = client.get("/")
    assert resp.status_code == 404


def test_api_routes_not_shadowed_by_mount(tmp_path):
    """A /api/* route registered BEFORE the mount must still respond."""
    test_app = FastAPI()

    @test_app.get("/api/ping")
    def _ping():
        return {"ok": True}

    _mount_frontend(test_app, build_dir=_make_static_dir(tmp_path))
    client = TestClient(test_app)
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"ok": True}
