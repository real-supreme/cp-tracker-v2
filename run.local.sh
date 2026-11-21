#!/usr/bin/env bash
# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
#
# Local dev runner.
#   ./run.local.sh        build + start with hot reload, tail logs
#   ./run.local.sh down   stop
#
# Requires .env and config/config.yaml to already exist with real values.
# Nothing is created. Nothing is defaulted. If config is bad, the app will
# say so in the logs within a few seconds.
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]]              || { echo ".env missing. Copy .env.example and fill it in." >&2; exit 1; }
[[ -f config/config.yaml ]]|| { echo "config/config.yaml missing. Copy config/config.example.yaml." >&2; exit 1; }

case "${1:-up}" in
  up)
    docker compose -f docker-compose.dev.yml up --build -d
    exec docker compose -f docker-compose.dev.yml logs -f app
    ;;
  down)
    docker compose -f docker-compose.dev.yml down
    ;;
  *)
    echo "usage: $0 [up|down]" >&2
    exit 1
    ;;
esac