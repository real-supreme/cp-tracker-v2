#!/usr/bin/env bash
# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
#
# Production runner.
#   ./run.sh            start/update the production stack
#   ./run.sh logs       tail app logs
#   ./run.sh status     show container status
#   ./run.sh restart    restart app only
#   ./run.sh down       stop everything
#
# Requires: docker (with compose v2), bash.
set -euo pipefail

cd "$(dirname "$0")"

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
log()  { printf '%s[run]%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s[run]%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%s[run]%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }

# ---------- preflight ----------
command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
docker compose version >/dev/null 2>&1 || die "docker compose v2 not available"

[[ -f .env ]] || die ".env not found. Copy .env.example to .env and fill it in."
[[ -f config/config.yaml ]] || die "config/config.yaml not found. Copy config/config.example.yaml to config/config.yaml."

# Validate essential env vars without printing secrets.
missing=0
for var in COC_API_KEYS DATABASE_URL API_CLIENT_KEYS; do
    val=$(grep -E "^${var}=" .env | head -n1 | cut -d= -f2- || true)
    if [[ -z "${val:-}" ]]; then
        warn "missing ${var} in .env"
        missing=1
    fi
done
[[ $missing -eq 0 ]] || die "fill in the missing values in .env before starting"

# ---------- cert check ----------
mkdir -p nginx/certs
if [[ ! -f nginx/certs/fullchain.pem || ! -f nginx/certs/privkey.pem ]]; then
    warn "nginx/certs/{fullchain.pem,privkey.pem} missing."
    warn "generate a self-signed cert now (OK for testing) or drop a Let's Encrypt cert here:"
    warn "  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \\"
    warn "    -keyout nginx/certs/privkey.pem -out nginx/certs/fullchain.pem \\"
    warn "    -subj '/CN=localhost'"
    read -r -p "generate a self-signed cert now? [Y/n] " yn
    yn=${yn:-Y}
    if [[ "$yn" =~ ^[Yy]$ ]]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
            -keyout nginx/certs/privkey.pem \
            -out    nginx/certs/fullchain.pem \
            -subj '/CN=localhost' >/dev/null 2>&1
        log "self-signed cert created at nginx/certs/"
    else
        die "place a cert in nginx/certs/ and re-run"
    fi
fi

# ---------- command dispatch ----------
case "${1:-up}" in
    up)
        log "building and starting production stack"
        docker compose up -d --build
        log "waiting for app to become healthy"
        for i in $(seq 1 30); do
            if docker compose exec -T app curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
                break
            fi
            sleep 2
            if [[ $i -eq 30 ]]; then
                warn "app did not report healthy within 60s"
                warn "run './run.sh logs' to see what's happening"
                exit 1
            fi
        done
        log "stack is up"
        printf '\n%sEndpoints%s\n' "$DIM" "$RST"
        printf '  health   : https://<host>/api/v1/health\n'
        printf '  stats    : https://<host>/api/v1/stats        (Bearer token required)\n'
        printf '  clans    : https://<host>/api/v1/clans/<tag>\n'
        printf '\n%sNext%s\n' "$DIM" "$RST"
        printf '  ./run.sh logs      tail app logs\n'
        printf '  ./run.sh status    container status\n'
        printf '  ./run.sh down      stop everything\n'
        ;;
    logs)
        docker compose logs -f app
        ;;
    status)
        docker compose ps
        ;;
    restart)
        log "restarting app container"
        docker compose restart app
        ;;
    down)
        log "stopping production stack"
        docker compose down
        ;;
    *)
        die "unknown command: $1 (up|logs|status|restart|down)"
        ;;
esac