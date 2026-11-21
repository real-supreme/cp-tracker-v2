#!/usr/bin/env bash
# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
#
# Stops whichever stack is running (production or local). Safe to run when
# nothing is running.
set -euo pipefail
cd "$(dirname "$0")"

docker compose down 2>/dev/null || true
docker compose -f docker-compose.dev.yml down 2>/dev/null || true
echo "[stop] all stacks down"