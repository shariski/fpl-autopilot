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

# ca-certificates for outbound TLS (FPL / Understat / Telegram / Anthropic).
# cryptography ships prebuilt manylinux wheels for slim, so no rust toolchain needed.
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

# ENTRYPOINT is the CLI so `docker compose run --rm app init-master-password`
# (and similar one-shot subcommands) work without re-typing fpl-autopilot.
# --host 0.0.0.0 is safe inside the container; the host-side port mapping
# in compose binds to 127.0.0.1 (then tailscale serve forwards from there).
ENTRYPOINT ["fpl-autopilot"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
