# syntax=docker/dockerfile:1
#
# TailCam container image. Multi-stage: build the web UI with Node, then ship a
# slim Python runtime with Tailscale, OpenCV/ffmpeg libs, and the app installed.
# See docs/docker.md (or the in-app Docs → "Docker" page) for usage.

# ---- Stage 1: build the React dashboard ----
FROM node:22-slim AS web
WORKDIR /app/web-ui
COPY web-ui/package.json web-ui/package-lock.json ./
RUN npm ci
COPY web-ui/ ./
# Vite emits to ../src/tailcam/web/spa (OUT_DIR in vite.config.ts).
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TAILCAM_DATA_DIR=/data \
    TAILCAM_CONFIG_DIR=/config

# Runtime libraries: OpenCV (headless) needs libglib2.0-0; ffmpeg for timelapse
# encoding; curl/ca-certs to install Tailscale; iptables/iproute2 for tailscaled
# kernel networking.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        ffmpeg \
        curl \
        ca-certificates \
        iptables \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Tailscale (tailscale CLI + tailscaled). TailCam shells out to `tailscale serve`.
RUN curl -fsSL https://tailscale.com/install.sh | sh

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
# Use the freshly built dashboard from the web stage so the image always matches
# source (overwrites the committed copy under src/tailcam/web/spa).
COPY --from=web /app/src/tailcam/web/spa ./src/tailcam/web/spa
RUN pip install .

COPY docker/entrypoint.sh /usr/local/bin/tailcam-entrypoint
RUN chmod +x /usr/local/bin/tailcam-entrypoint

# Persist the database/media, the config, and Tailscale node state.
VOLUME ["/data", "/config", "/var/lib/tailscale"]

# Local/LAN HTTP port. Tailnet access goes over tailscaled, not a published port.
EXPOSE 8088

ENTRYPOINT ["/usr/local/bin/tailcam-entrypoint"]
