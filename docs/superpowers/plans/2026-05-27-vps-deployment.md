# VPS Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Subagent caveat:** Tasks T7–T10 are operational (SSH to jumbo, interactive `tailscale up`, browser confirmations). Subagents cannot drive these — inline execution is required for the VPS-side half.

**Goal:** Ship fpl-autopilot to `jumbo` (Ubuntu 24.04 VPS) as a single Docker container, image built by GitHub Actions and pushed to GHCR, dashboard reachable only over Tailscale.

**Architecture:** One container running `fpl-autopilot serve` (FastAPI + embedded APScheduler) on port 8000 bound to `127.0.0.1` of the host. A named volume holds the SQLite DB and master-key material. `tailscale serve` on the host forwards `https://server1.<tailnet>.ts.net/` to the container's port. No public DNS, no Cloudflare cert, no nginx-proxy entry.

**Tech Stack:** Python 3.13-slim, FastAPI, APScheduler, SvelteKit (adapter-static), Docker, GitHub Actions, GHCR, Tailscale.

**Reference:** [`docs/superpowers/specs/2026-05-27-vps-deployment-design.md`](../specs/2026-05-27-vps-deployment-design.md)

---

## File Structure

```
fpl-autopilot/
├── Dockerfile                              # NEW — multi-stage build (frontend → python)
├── .dockerignore                           # NEW — exclude .venv, .git, data/, frontend/node_modules, tests/, etc.
├── docker-compose.yml.example              # NEW — template; the live file lives on jumbo at /opt/fpl-autopilot/docker-compose.yml
├── .github/
│   └── workflows/
│       └── build-image.yml                 # NEW — build + push to ghcr.io/shariski/fpl-autopilot on push to main
├── src/
│   └── interface/
│       └── api.py                          # MODIFY — append a StaticFiles mount for the SvelteKit build
└── tests/
    └── interface/
        └── test_static_mount.py            # NEW — verify the mount is conditional + serves index.html
```

On jumbo (operational, not in git):
```
/opt/fpl-autopilot/
├── docker-compose.yml                      # copied from docker-compose.yml.example
└── .env                                    # MASTER_PASSWORD, TELEGRAM_*, IMAGE_TAG (chmod 600)
```

---

## Task T1 — Mount the SvelteKit build inside FastAPI

The only code change. TDD because it's testable: the mount must be present when the directory exists and absent when it doesn't (so local dev without a built frontend still works).

**Files:**
- Create: `tests/interface/test_static_mount.py`
- Modify: `src/interface/api.py` (append at end)

- [ ] **Step 1.1: Write the failing tests**

Create `tests/interface/test_static_mount.py`:

```python
from pathlib import Path
from fastapi.testclient import TestClient


def _make_static_dir(tmp_path: Path) -> Path:
    """Create a fake built-frontend directory with an index.html."""
    d = tmp_path / "frontend_build"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><title>fpl-autopilot</title>")
    return d


def test_static_mount_present_when_directory_exists(tmp_path, monkeypatch):
    """When /app/frontend_build exists, GET / returns the SPA's index.html."""
    monkeypatch.setattr("src.interface.api._FRONTEND_BUILD",
                        _make_static_dir(tmp_path))
    # Re-import the app so the conditional mount evaluates against the patched path.
    import importlib
    from src.interface import api as api_mod
    importlib.reload(api_mod)
    client = TestClient(api_mod.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "fpl-autopilot" in resp.text


def test_static_mount_absent_when_directory_missing(tmp_path, monkeypatch):
    """When the build directory is missing, GET / returns 404 — local dev is unaffected."""
    monkeypatch.setattr("src.interface.api._FRONTEND_BUILD",
                        tmp_path / "does-not-exist")
    import importlib
    from src.interface import api as api_mod
    importlib.reload(api_mod)
    client = TestClient(api_mod.app)
    resp = client.get("/")
    assert resp.status_code == 404


def test_api_routes_still_work_with_mount(tmp_path, monkeypatch):
    """The static mount at / must not shadow /api/* routes."""
    monkeypatch.setattr("src.interface.api._FRONTEND_BUILD",
                        _make_static_dir(tmp_path))
    import importlib
    from src.interface import api as api_mod
    importlib.reload(api_mod)
    client = TestClient(api_mod.app)
    # /api/status exists; it should respond with JSON (whatever the body),
    # not the SPA HTML.
    resp = client.get("/api/status")
    assert resp.headers["content-type"].startswith("application/json")
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
.venv/bin/pytest tests/interface/test_static_mount.py -v
```

Expected: 3 FAIL (no `_FRONTEND_BUILD` attribute on module, no mount registered).

- [ ] **Step 1.3: Implement the mount**

Open `src/interface/api.py`. At the very bottom of the file, append:

```python
# --- Static frontend (SvelteKit adapter-static build) ---
# Mounted at "/" so the dashboard PWA is served from the same FastAPI
# process. Conditional on the directory existing so local dev (no built
# frontend) is unaffected. The mount sits AFTER all @app.get/@app.post
# decorators above so the route table for /api/* is registered first
# and is not shadowed by StaticFiles.
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_FRONTEND_BUILD = Path("/app/frontend_build")
if _FRONTEND_BUILD.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_BUILD, html=True),
              name="frontend")
```

- [ ] **Step 1.4: Run tests to verify they pass**

```
.venv/bin/pytest tests/interface/test_static_mount.py -v
```

Expected: 3 PASS.

- [ ] **Step 1.5: Re-run the full test suite to confirm nothing else regressed**

```
.venv/bin/pytest -q
```

Expected: all pre-existing tests still PASS (the prior count was 404; we've added 3, so 407 expected).

- [ ] **Step 1.6: Commit**

```bash
git add tests/interface/test_static_mount.py src/interface/api.py
git commit -m "feat(interface): mount SvelteKit static build at / (conditional)

Adds a StaticFiles mount at the FastAPI root, gated on /app/frontend_build
being present so local dev is unaffected. Required for the upcoming
single-container VPS deployment where the dashboard PWA is served from
the same process as /api/*. See docs/superpowers/specs/2026-05-27-vps-
deployment-design.md."
```

---

## Task T2 — Write the multi-stage Dockerfile

Stage 1 builds the frontend; stage 2 is the runtime python image with the built static files copied in.

**Files:**
- Create: `Dockerfile`

- [ ] **Step 2.1: Write `Dockerfile` at the repo root**

```dockerfile
# syntax=docker/dockerfile:1.7

# ===== Stage 1: build the SvelteKit static frontend =====
FROM node:22-alpine AS frontend
WORKDIR /build
# Copy only the manifest first so layer caching works on dep installs.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline --no-audit
# Now bring the rest of the frontend source and build.
COPY frontend/ ./
RUN npm run build
# Output lands in /build/build (SvelteKit adapter-static default).

# ===== Stage 2: python runtime =====
FROM python:3.13-slim AS runtime

# Minimal system deps for cryptography (rust isn't needed for the prebuilt
# wheels on slim, but we add ca-certificates for outbound TLS to FPL /
# Understat / Telegram / Anthropic).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user for the runtime layer.
RUN useradd --create-home --uid 10001 --shell /bin/bash autopilot

WORKDIR /app

# Install Python deps first so they cache across code-only edits.
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# The project itself (provides the fpl-autopilot console script).
COPY --chown=autopilot:autopilot . /app
RUN pip install --no-cache-dir --no-deps -e .

# Copy the built frontend in from stage 1.
COPY --from=frontend --chown=autopilot:autopilot /build/build /app/frontend_build

# Volume for SQLite + key material + logs. Mounted from the host in compose.
RUN mkdir -p /app/data && chown -R autopilot:autopilot /app/data
VOLUME ["/app/data"]

USER autopilot
EXPOSE 8000

# CMD must match what compose / `docker run` invoke. --host 0.0.0.0 is safe
# *inside the container*; the host port-mapping in compose binds to
# 127.0.0.1 of the host (then tailscale serve forwards from there).
ENTRYPOINT ["fpl-autopilot"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2.2: Verify the Dockerfile parses (no build yet — build happens in T4)**

```
docker buildx build --check .
```

Expected: no errors; `buildx check` validates syntax + best-practice lints. (A few warnings about pinned base-image digest are acceptable for V1.)

- [ ] **Step 2.3: Commit**

```bash
git add Dockerfile
git commit -m "feat(deploy): multi-stage Dockerfile (node frontend build → python runtime)

Two stages: node:22-alpine to build the SvelteKit static frontend, then
python:3.13-slim with deps installed, project copied in editable mode,
and the built frontend copied to /app/frontend_build (where api.py's
StaticFiles mount will find it). Non-root user, /app/data volume, port
8000. ENTRYPOINT is the fpl-autopilot CLI so commands like
\`docker compose run --rm app fpl-autopilot init-master-password\` work."
```

---

## Task T3 — Write `.dockerignore`

Keeps the build context small (faster builds, smaller image surface) and prevents accidental secret leakage into layers.

**Files:**
- Create: `.dockerignore`

- [ ] **Step 3.1: Write `.dockerignore` at the repo root**

```gitignore
# Version control + tooling
.git
.gitignore
.gitattributes

# Python virtualenv + caches
.venv
.pytest_cache
__pycache__
*.py[cod]
*$py.class
.coverage
htmlcov
.mypy_cache
.ruff_cache

# Tests + docs (not needed in the runtime image)
tests
docs

# Local data (CRITICAL — don't bake the dev SQLite + keys into the image)
data

# Frontend build artifacts that should come from stage 1, not the host
frontend/node_modules
frontend/build
frontend/.svelte-kit
frontend/.vite

# Editor / IDE
.idea
.vscode
*.swp

# Local-only files
.env
.env.*
!.env.example
config.yaml.local
*.log

# Project metadata that shouldn't end up in the image
fpl_autopilot.egg-info
.serena
```

- [ ] **Step 3.2: Verify the ignore file is picked up**

```
docker buildx build --no-cache --progress=plain --output type=local,dest=/tmp/dctx --build-arg=DUMMY=1 . 2>&1 | head -5
```

(Optional sanity check. If you want to skip, the file is exercised when T4 builds the image for real.)

- [ ] **Step 3.3: Commit**

```bash
git add .dockerignore
git commit -m "feat(deploy): .dockerignore — exclude dev artifacts and secrets

Excludes .venv, data/, .env, tests/, docs/, and the frontend host-side
node_modules / build / .svelte-kit so they don't enter the build context.
Keeps image small and prevents accidental secret leakage into layers."
```

---

## Task T4 — Local build + smoke test

Builds the image locally to confirm everything composes before we wire CI. Catches Dockerfile issues with fast feedback instead of waiting on GHA.

**Files:** (no new files — verification only)

- [ ] **Step 4.1: Build the image locally**

```
docker build -t fpl-autopilot:local .
```

Expected: completes successfully in 2-4 min on first build. Final image size reported should be < 350 MB (target was 250 MB; small overrun is OK for V1).

- [ ] **Step 4.2: Inspect image size**

```
docker images fpl-autopilot:local --format "{{.Size}}"
```

Expected: under 350 MB. If significantly larger, check for `node_modules` leaking into stage 2 (the COPY should only pull from stage 1).

- [ ] **Step 4.3: Run a one-shot to confirm the static mount picks up the built frontend**

```
docker run --rm -p 127.0.0.1:8001:8000 -v /tmp/fpl-autopilot-data:/app/data --name fpl-test fpl-autopilot:local serve --host 0.0.0.0 --port 8000 &
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/api/status
docker stop fpl-test
```

Expected:
- `GET /` → 200 (SvelteKit index.html served)
- `GET /api/status` → 200 with JSON

If the container exits because there's no `.salt` / no master password, that's fine for this smoke — we just want to see the build succeeds and the static mount is wired. The runtime container in T7+ will have those files in the volume.

- [ ] **Step 4.4: Commit only if anything changed**

Nothing should have changed in the repo from this task. If the build surfaced issues in T2 or T3, fix them and amend / re-commit those tasks. Otherwise no commit.

---

## Task T5 — Write the GitHub Actions workflow

Builds + pushes the image to `ghcr.io/shariski/fpl-autopilot` on push to main. Tags with both the commit SHA and `latest`.

**Files:**
- Create: `.github/workflows/build-image.yml`

- [ ] **Step 5.1: Write the workflow file**

```yaml
name: Build & push image

on:
  push:
    branches: [main]
    paths:
      - "src/**"
      - "frontend/**"
      - "pyproject.toml"
      - "requirements.txt"
      - "Dockerfile"
      - ".dockerignore"
      - ".github/workflows/build-image.yml"
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata (tags)
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,format=long
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 5.2: Commit**

```bash
git add .github/workflows/build-image.yml
git commit -m "ci(deploy): build + push image to GHCR on push to main

GitHub Actions workflow that builds the multi-stage Dockerfile and pushes
two tags to ghcr.io/shariski/fpl-autopilot: the full commit SHA
(reproducible — what compose pins via IMAGE_TAG) and 'latest' (mutable
convenience for ad-hoc pulls). Uses the workflow's built-in GITHUB_TOKEN
with packages: write — no PAT or secret juggling. Path filter limits CI
runs to image-relevant changes."
```

---

## Task T6 — Push and verify CI succeeds

Push the local commits and watch the first image build on GHA. This unblocks T7 (jumbo needs to pull the image).

**Files:** (none — verification only)

- [ ] **Step 6.1: Push the branch**

```bash
git push origin main
```

- [ ] **Step 6.2: Watch the workflow run**

```bash
gh run watch
```

Or, in a browser: `https://github.com/shariski/fpl-autopilot/actions`.

Expected: workflow `Build & push image` completes green in 3-6 min. The first run will be slower (no cache); subsequent runs hit the GHA build cache.

- [ ] **Step 6.3: Verify the image exists in GHCR**

```bash
gh api -X GET /user/packages/container/fpl-autopilot/versions --jq '.[0:3] | .[].metadata.container.tags'
```

Expected: two tags on the most recent version — the SHA-long form (e.g. `sha-b6bbd1f...`) and `latest`.

---

## Task T7 — Bootstrap `/opt/fpl-autopilot` on jumbo

Operational task — inline execution only. Needs you (or me driving via `ssh jumbo`) at the keyboard for interactive prompts (init-master-password, init-fpl).

**Files:**
- Create on jumbo: `/opt/fpl-autopilot/docker-compose.yml`
- Create on jumbo: `/opt/fpl-autopilot/.env`

The compose file lives on jumbo (not in git on the VPS), but we keep a copy in the repo as `docker-compose.yml.example` so future-you knows what was deployed.

- [ ] **Step 7.1: Write `docker-compose.yml.example` to the repo**

Create `docker-compose.yml.example` at the repo root:

```yaml
# fpl-autopilot — production compose. Lives on the VPS at
# /opt/fpl-autopilot/docker-compose.yml (copy of this file, not a symlink
# — secrets in .env are not committed).
#
# One service: `app` runs `fpl-autopilot serve`, which embeds the
# APScheduler in-process. See docs/superpowers/specs/2026-05-27-vps-
# deployment-design.md for why we don't split into two services.
#
# Networking: bound to 127.0.0.1:8000 on the host. The host's
# `tailscale serve` (running outside Docker) forwards HTTPS from the
# tailnet to this port. Nothing is publicly routable.

services:
  app:
    image: ghcr.io/shariski/fpl-autopilot:${IMAGE_TAG:-latest}
    container_name: fpl-autopilot
    restart: unless-stopped
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - fpl-autopilot-data:/app/data
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"
      interval: 30s
      timeout: 6s
      retries: 3
      start_period: 30s
    # Logs: rely on Docker's default json-file driver with rotation.
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"

volumes:
  fpl-autopilot-data:
```

Commit it:

```bash
git add docker-compose.yml.example
git commit -m "feat(deploy): docker-compose.yml.example template

Template for the compose file deployed at /opt/fpl-autopilot/ on jumbo.
One service (serve + embedded scheduler), bound to 127.0.0.1:8000 for
tailscale serve to forward, named volume for SQLite + key material,
healthcheck against /api/status, log rotation. Not used directly by
CI — the live file is a copy on the VPS so the .env reference resolves
to the VPS-side .env."
git push origin main
```

- [ ] **Step 7.2: Create the directory + copy the compose file to jumbo**

```bash
ssh jumbo 'mkdir -p /opt/fpl-autopilot && cd /opt/fpl-autopilot'
scp docker-compose.yml.example jumbo:/opt/fpl-autopilot/docker-compose.yml
ssh jumbo 'ls -la /opt/fpl-autopilot/'
```

Expected: `docker-compose.yml` present, owned by `deploy`, mode 644.

- [ ] **Step 7.3: Create `.env` on jumbo with the secrets**

Run this on jumbo (interactive — you type the values; nothing is echoed to the terminal). Do it via `ssh jumbo` from a real terminal (not `!`) so getpass-style entry works:

```bash
ssh jumbo
cd /opt/fpl-autopilot
umask 077                                 # next file is 600
cat > .env <<EOF
TELEGRAM_BOT_TOKEN=$(read -rs -p "Telegram bot token: " v && echo "$v")
TELEGRAM_CHAT_ID=$(read -r -p "Telegram chat id: " v && echo "$v")
MASTER_PASSWORD=$(read -rs -p "Master password: " v && echo "$v")
EOF
echo
stat -c "%a %U %G %s %n" .env             # verify 600
```

(Or just `nano .env` and paste in the lines — whichever you find less error-prone. The important thing is `chmod 600` afterwards.)

Expected: `600 deploy deploy <bytes> .env`.

- [ ] **Step 7.4: Configure GHCR pull credentials on jumbo**

Personal-access-token with `read:packages` scope on the GHCR side. Create one at
`https://github.com/settings/tokens` → "Generate new token (classic)" →
`read:packages` only. Then on jumbo:

```bash
ssh jumbo
echo "<the-PAT>" | docker login ghcr.io -u shariski --password-stdin
```

Expected: `Login Succeeded`. Credentials persist in `~/.docker/config.json`.

- [ ] **Step 7.5: Pull the image**

```bash
ssh jumbo 'cd /opt/fpl-autopilot && docker compose pull'
```

Expected: image `ghcr.io/shariski/fpl-autopilot:latest` pulled. Size matches what GHA built.

- [ ] **Step 7.6: Initialize the master password (interactive, writes .salt + .verify into the volume)**

```bash
ssh -t jumbo 'cd /opt/fpl-autopilot && docker compose run --rm app init-master-password'
```

Expected: prompts for the password twice, then confirms `Salt written to data/.salt` and `Encryption verified`. Same value as `MASTER_PASSWORD` in `.env`.

- [ ] **Step 7.7: Initialize FPL credentials (paste the refresh token)**

You'll need the refresh token from your existing Mac setup. Get it on the Mac:

```
.venv/bin/python -c "from src.auth import session; print(session.get_refresh_token_for_handoff())"
```

(If that helper doesn't exist, the value is in your DB — `sqlite3 data/fpl_autopilot.db 'SELECT * FROM auth;'` and decrypt with your master password; or re-extract from a fresh FPL browser session per the HANDOFF doc.)

Then on jumbo:

```bash
ssh -t jumbo 'cd /opt/fpl-autopilot && docker compose run --rm app init-fpl'
```

Expected: prompts for the refresh token (paste from clipboard), confirms the team_id matches `config.yaml`, and writes the encrypted credential into the DB inside the volume.

---

## Task T8 — Start the long-running container

- [ ] **Step 8.1: Start it**

```bash
ssh jumbo 'cd /opt/fpl-autopilot && docker compose up -d'
```

- [ ] **Step 8.2: Verify health**

```bash
ssh jumbo 'docker compose -f /opt/fpl-autopilot/docker-compose.yml ps; docker compose -f /opt/fpl-autopilot/docker-compose.yml logs --tail 30 app'
```

Expected: container status `Up X seconds (healthy)` after the 30s start period. Logs show APScheduler starting and the FastAPI server listening on `0.0.0.0:8000`. No Argon2 errors (would indicate `.env` MASTER_PASSWORD ≠ the password used in T7.6).

- [ ] **Step 8.3: Confirm reachable on the host (Tailscale not yet involved)**

```bash
ssh jumbo 'curl -fsS http://127.0.0.1:8000/api/status | head -c 200 && echo'
```

Expected: JSON response — current GW info, squad summary, etc.

---

## Task T9 — Expose via Tailscale

- [ ] **Step 9.1: Start `tailscale serve`**

```bash
ssh -t jumbo 'sudo tailscale serve --bg http://localhost:8000'
```

Expected: prints the tailnet URL (e.g. `Available within your tailnet: https://server1.<tailnet>.ts.net/`). Cert auto-provisioning happens silently on first request.

Note: older Tailscale docs show `tailscale serve --bg https / http://localhost:8000`
(separate scheme + path args). That syntax was removed; current CLI just takes the
upstream URL and defaults to HTTPS at /. Verified on jumbo May 2026.

- [ ] **Step 9.2: Verify from your Mac (already on the tailnet)**

```bash
curl -fsS https://server1.<tailnet>.ts.net/api/status | head -c 200 && echo
```

Replace `<tailnet>` with whatever Tailscale printed in 9.1.

Expected: same JSON response as 8.3, but via HTTPS, via Tailscale's WireGuard tunnel.

- [ ] **Step 9.3: Verify from your phone**

Open `https://server1.<tailnet>.ts.net/` in your phone's browser (with Tailscale running on the phone). Expected: dashboard renders, you see your squad / decisions / etc.

- [ ] **Step 9.4: Verify it survives a reboot of the VPS**

This is optional but worth doing to catch any cron / unit / systemd issue:

```bash
ssh jumbo 'sudo reboot'
# wait 60s
ssh jumbo 'docker ps --filter name=fpl-autopilot --format "table {{.Names}}\t{{.Status}}"; tailscale serve status'
```

Expected: container is `Up X seconds (healthy)` automatically (Docker daemon enabled, container `restart: unless-stopped`); `tailscale serve` config persists across reboots (it's stored in tailscaled state).

---

## Task T10 — Update README + close out

- [ ] **Step 10.1: Update `README.md` "Status" section**

Open `README.md`. Find the `## Status` heading near the bottom. Replace its body with:

```markdown
## Status

- **Phase 1 (Insight Engine):** ✅ complete
- **Phase 2 (Decision Automation):** ✅ complete (auth, executor, mode router, Telegram interactive, deadguard)
- **Phase 3 (AI Layer):** in progress
- **Deployed:** ✅ live on a personal VPS, dashboard reachable via Tailscale only. See [`docs/superpowers/specs/2026-05-27-vps-deployment-design.md`](docs/superpowers/specs/2026-05-27-vps-deployment-design.md) and [`docs/superpowers/plans/2026-05-27-vps-deployment.md`](docs/superpowers/plans/2026-05-27-vps-deployment.md).
```

- [ ] **Step 10.2: Commit**

```bash
git add README.md
git commit -m "docs: mark Phase 2 done + project deployed to VPS"
git push origin main
```

- [ ] **Step 10.3: Final task-list sweep**

Mark all plan tasks complete. Verify outstanding follow-ups from the spec are captured somewhere durable (a fresh GitHub issue or a `## Open follow-ups` entry in the spec):
- Backup cron on jumbo
- Optional Split-DNS for `fpl.shariski.com`
- Log rotation policy inside the container (the json-file driver handles Docker stdout; in-app logs to `data/logs/` still need a rotation strategy)
- GHCR PAT rotation discipline
- Cloudflare orphan tunnel cleanup (from pre-flight)

---

## Self-review notes

- **Spec coverage:** T1 covers static mount. T2-T4 cover the Dockerfile + image. T5-T6 cover CI. T7 covers /opt/fpl-autopilot + .env + first-time init. T8-T9 cover runtime + Tailscale. T10 closes the loop. The spec's "out of scope" items (backups, split-DNS, log rotation, multi-user) are deliberately not in the plan.
- **Placeholder scan:** none. Every step has a real command or real code.
- **One known runtime concrete detail to fill in:** the tailnet FQDN (e.g. `server1.<tailnet>.ts.net`) is unknown until T9.1 runs — `tailscale serve` prints it. Step 9.2/9.3 reference it explicitly; this is operational discovery, not a planning gap.
- **The auth refresh-token export helper** referenced in 7.7 may not exist — flagged in the step itself with a fallback path. Acceptable: a small unblocking task that doesn't justify its own T_n.
