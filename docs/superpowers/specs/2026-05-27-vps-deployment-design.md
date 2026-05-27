# VPS deployment — design

First-time deployment of FPL Autopilot to a long-lived host so the scheduler,
deadguard, and Telegram interactive can run unattended through the season.

## Target environment

- Host: `jumbo` (Ubuntu 24.04.4 LTS, 2 vCPU, 5.8 GiB RAM, single 48 GB disk).
- Docker 29.2.1 + Compose v5.1.0, daemon enabled at boot.
- Existing tenants on the host that we must not disturb: `kerf`, `ig-pulse`,
  `sawermaz-*`, the shared `nginx-proxy` + `acme-companion` stack, redis,
  postgres. Apps live in `/opt/<project>/` owned by user `deploy`.
- Disk pre-conditions: the host previously ran an LLM stack (`ollama`,
  `ollama-proxy`, `ollama-cloudflared`) that consumed ~12 GB of images
  and a 1.3 GB volume. That stack has been torn down as part of this work
  (see "Pre-flight" below); disk free went from 2.5 GB to ~14 GB.

## What we deploy

One container, one image, one named volume.

```
ghcr.io/shariski/fpl-autopilot:<sha>
  └─ entrypoint: fpl-autopilot serve --host 0.0.0.0 --port 8000
     ├─ FastAPI app (dashboard API at /api/*)
     ├─ Static frontend mounted at / (SvelteKit adapter-static build)
     └─ APScheduler (embedded; the `serve` command already starts it
        by default — see src/cli.py:522. `--no-scheduler` opts out
        and is not used in production.)

volume: fpl-autopilot-data → /app/data
  ├─ fpl_autopilot.db        # SQLite, WAL mode
  ├─ .salt                   # Argon2id salt for master-key derivation
  ├─ .verify                 # encrypted verify-blob ("did you type the right password?")
  └─ logs/                   # structured app logs, rotated
```

The `scheduler` CLI subcommand exists for headless operation but is not used
here. Running them as separate processes would force us to coordinate SQLite
writers across containers — `serve` embedding the scheduler keeps everything
in one process, which is the only writer, and the WAL mode read concurrency
remains unaffected.

## Why this shape and not the others

### Why not the kerf / ig-pulse pattern (nginx-proxy + VIRTUAL_HOST)?

Both `kerf` and `ig-pulse` join the external `deploy_web` network and let
`nginx-proxy` route by `VIRTUAL_HOST`. They are public products with
app-layer auth (`/login`, user accounts). The fpl-autopilot dashboard has
**no authentication** — every endpoint is callable without credentials.
Designed for `127.0.0.1` per the existing HANDOFF.

The bounded-blast-radius observation is that an attacker cannot make any
FPL change without `MASTER_PASSWORD` (which the dashboard never holds), so
no real money / no real account compromise. But they can read squad state
and toggle freeze / deadguard-keep — enough to grief the autopilot during
a deadline.

Adding app-layer auth is a real feature, not a small thing, and CLAUDE.md
§B9 already says Telegram is the primary interface. We prefer a network-
layer gate over an HTTP-layer gate for this tool. See next section.

### Why Tailscale over Cloudflare Access?

Both gate access. Tailscale wins for this tool:

1. **Network-layer, not HTTP-layer.** CF Access protects HTTP requests
   only; an open port on `0.0.0.0:443` is still discoverable. Tailscale
   makes the dashboard unroutable from the public internet at all —
   port 8000 is bound to `127.0.0.1` on jumbo, and Tailscale serves it
   to authenticated tailnet peers via the `tailscale0` interface.
2. **No third party in the request path.** Direct WireGuard peer-to-peer
   between laptop/phone and jumbo. End-to-end encrypted with keys that
   Tailscale itself can't read.
3. **Mobile is first-class.** Tailscale iOS/Android apps sign in once;
   the dashboard URL just works.
4. **Free moving parts** (no cert renewal, no nginx-proxy entry, no
   public DNS record needed).

Trade-off: we break the kerf / ig-pulse pattern. For a personal-use tool
that's the right call — the proxy pattern exists to serve external users,
which we don't have.

### Why not localhost-only + SSH tunnel?

The runner-up. Strongest possible isolation. Rejected because (a) the user
has an existing tailnet anyway, and (b) we want the dashboard reachable
from mobile during a gameweek without setting up SSH on the phone.

### Why one container instead of two?

`fpl-autopilot serve` already starts the embedded scheduler (src/cli.py:522,
`scheduler=True` default). Splitting into two containers would mean two
SQLite writers on the same volume. SQLite handles that via WAL but the
write coordination is the kind of thing that breaks subtly during DGW or
late-news cascades. One process, one writer is simpler and matches the
project's existing single-process assumption.

The separate `fpl-autopilot scheduler` command stays in the CLI for
headless ops (e.g., a one-shot data refresh from cron); it is not used
in this deployment.

## Auth and access

```
Internet  ──X── (no public binding; port 8000 is on 127.0.0.1 only)

Tailnet:
  laptop, phone  ─── WireGuard ───  jumbo  ──── tailscale serve ────  127.0.0.1:8000
                                    (tailscale0)
```

- `tailscale serve --bg https / http://localhost:8000` runs on the host
  (not in a container). It binds to `100.x.x.x:443` on `tailscale0` and
  reverse-proxies into the dashboard's localhost port.
- Cert: automatic Let's Encrypt for `<machine>.<tailnet>.ts.net` via
  Tailscale's built-in machinery. Renews silently.
- Optional: `fpl.shariski.com` Split-DNS pointing to jumbo's tailnet IP,
  resolvable only when the client is on the tailnet. Defer; the `.ts.net`
  hostname works fine to start.
- ACL: standard tailnet ACL is "all your devices can reach all your
  devices." If we later want to share with someone else, narrow with
  Tailscale ACL groups.

## Image build (CI/CD)

GitHub Actions on push to `main`:

1. Checkout repo.
2. `docker buildx build` against `Dockerfile` at the repo root.
3. Push two tags to `ghcr.io/shariski/fpl-autopilot`:
   - `:<commit-sha>` (immutable; what compose pins via `IMAGE_TAG`)
   - `:latest` (mutable convenience for ad-hoc pulls)

Uses the workflow's built-in `GITHUB_TOKEN` with `packages: write` — no
PAT or secret juggling. The image is private under the `shariski` GHCR
namespace; the VPS pulls with a deploy token (`echo $GHCR_TOKEN | docker
login ghcr.io -u shariski --password-stdin`).

The Dockerfile is multi-stage:

- **Stage 1** (`node:22-alpine`): copy `frontend/`, run `npm ci && npm
  run build`. Output lands in `frontend/build/`.
- **Stage 2** (`python:3.13-slim`): install runtime deps from
  `pyproject.toml`, copy `src/` and `config.yaml`, copy stage 1's
  `frontend/build/` to `/app/frontend_build/`. Set workdir, drop to a
  non-root user, declare `VOLUME /app/data`, set `ENTRYPOINT ["fpl-
  autopilot"]` and `CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]`.

Targets: image size < 250 MB, build time < 3 min on GHA standard runner.

## Small code changes required

Two surgical additions; both are doc-mandated by §B13 if we want this
spec to match reality.

1. **Mount the static frontend in FastAPI.** Add to the bottom of
   `src/interface/api.py`:

   ```python
   from fastapi.staticfiles import StaticFiles
   from pathlib import Path

   _static = Path("/app/frontend_build")
   if _static.is_dir():
       app.mount("/", StaticFiles(directory=_static, html=True),
                 name="frontend")
   ```

   Conditional on directory existence so local dev (no built frontend)
   is unaffected. The `html=True` flag makes a request for `/` return
   `index.html`, and the SvelteKit `200.html` SPA fallback handles
   deep links.

2. **`data/` path needs to live under `/app/data/` inside the
   container** so the named volume picks it up. The Dockerfile sets
   `WORKDIR /app`; the existing `src/config.py` reads
   `storage.db_path` from `config.yaml` (currently
   `data/fpl_autopilot.db`). Either we keep that relative path and
   ensure the container's CWD is `/app` (so `data/` resolves to
   `/app/data/`), or we override via env var. Prefer the former —
   smaller change, matches the existing config.

No CLI changes. `serve` already accepts `--host`/`--port`
(src/cli.py:546-547), and the embedded scheduler is the default.

## Secrets and bootstrap

- `.env` on jumbo at `/opt/fpl-autopilot/.env`, `chmod 600`, owned by
  `deploy`, gitignored. Same scheme as Mac, with one important
  exception: on a $5 VPS the encryption-at-rest of the refresh token
  defends mainly against backup leaks and a future tenant on the same
  hypervisor. Don't include `.env` in the same backup tarball as
  `data/.salt` + `data/fpl_autopilot.db`.

- Variables in `.env`:
  ```
  MASTER_PASSWORD=<from password manager>
  TELEGRAM_BOT_TOKEN=<existing, reused from Mac>
  TELEGRAM_CHAT_ID=<existing, reused from Mac>
  ANTHROPIC_API_KEY=<optional, only if AI panes are enabled>
  IMAGE_TAG=<commit sha; CI sets via deploy script; defaults to "latest">
  ```

- Bootstrap steps (one-time, manual, on the VPS):
  1. `mkdir /opt/fpl-autopilot && chmod 700 /opt/fpl-autopilot && cd /opt/fpl-autopilot`
  2. Copy `docker-compose.yml` from the repo. (Out of git on the VPS;
     fetched via scp or `curl https://raw.githubusercontent.com/...`)
  3. Create `.env` with the variables above, `chmod 600`.
  4. `docker compose pull` → fetches the image from GHCR.
  5. `docker compose run --rm app fpl-autopilot init-master-password`
     (interactive — prompts twice, writes `data/.salt` + `data/.verify`
     into the volume).
  6. `docker compose run --rm app fpl-autopilot init-fpl` (interactive —
     pastes the FPL refresh token, encrypts it into the DB).
  7. `docker compose up -d` → starts the long-running container.
  8. `tailscale serve --bg https / http://localhost:8000` on the host
     (not in a container).
  9. Smoke-test from laptop on tailnet: `curl https://jumbo.<tailnet>.ts.net/api/status`.

## Lifecycle

- **Code update:** push to `main` → GHA builds → on jumbo, `cd
  /opt/fpl-autopilot && IMAGE_TAG=<new-sha> docker compose pull &&
  docker compose up -d`. (We can wrap that in a small deploy script
  later; not needed for V1.)
- **Schema migrations:** the project uses raw SQL in
  `src/data/schema.sql` + `init_db()` on every connect. Idempotent
  by design. No separate migration runner needed.
- **Backups:** weekly cron on jumbo dumps `/opt/fpl-autopilot/data/` to
  `/opt/fpl-autopilot/backups/`. Out of scope for V1; tracked as
  follow-up. The data we care about is `fpl_autopilot.db`,
  `.salt`, and `.verify`.
- **Disaster recovery:** restore the three files into a fresh volume,
  ensure `.env` is present with the same `MASTER_PASSWORD`, `docker
  compose up -d`. Per `docs/onboarding.md` §Backup automation.

## Healthcheck and monitoring

- Compose healthcheck: `curl -fsS http://127.0.0.1:8000/api/status`
  every 30s. Container restarts on three consecutive failures (Docker
  policy `unless-stopped`).
- External: an UptimeRobot or Healthchecks.io ping against the tailnet
  URL is awkward (their workers aren't on the tailnet). Defer; if the
  scheduler stops working, the user notices on Telegram (or the
  dashboard banner from §2.5c-3).
- Telegram alerts: existing `telegram.notify(kind="alert", ...)` already
  fires on auth failures and execution errors. That's the primary
  out-of-band signal.

## Open follow-ups

- **Backup cron** on jumbo. Not blocking V1. Track separately.
- **Optional Split-DNS for `fpl.shariski.com`** to the tailnet IP, so
  the dashboard URL matches the convention used by other apps. Cosmetic.
- **Log rotation policy.** APScheduler + FastAPI both produce continuous
  logs into `/app/data/logs/`. We rely on the file rotation already set
  up in `src/__init__.py` (per `docs/plan.md` Phase 0); confirm it
  works inside the container.
- **GHCR deploy token rotation.** GHA's `GITHUB_TOKEN` is short-lived
  per workflow run, but the VPS-side `docker login` needs a PAT. Track
  rotation discipline as a follow-up.

## Pre-flight done as part of this work

- `ollama` / `ollama-proxy` / `ollama-cloudflared` torn down, including
  the 1.3 GB `ollama-stack_ollama_models` volume and the related network.
  Freed ~11.5 GB. Orphaned Cloudflare tunnel registration left on
  Cloudflare's side; user to delete at `dash.cloudflare.com → Zero
  Trust → Networks → Tunnels`.
- Disk free: 14 GB / 48 GB (was 2.5 GB before).

## Out of scope

- Public dashboard for shared access. No multi-user. No login.
- Auto-renewal of FPL refresh token. Tracked elsewhere
  (`docs/superpowers/specs/2026-05-23-session-lifecycle-design.md`).
- High-availability or failover. Single host, accept downtime risk.
- Container security hardening beyond non-root user (no AppArmor,
  no seccomp profile, no read-only rootfs). Personal-tool threat model.
