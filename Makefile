
### `Makefile`

```make
SHELL := /bin/bash
COMPOSE := docker compose
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help up down logs dev test seed lint fmt reset-db zip

help:
	@echo "Targets:"
	@echo "  up       - production stack (Cockroach Cloud via .env)"
	@echo "  dev      - local dev stack (OSS Cockroach container)"
	@echo "  down     - stop everything"
	@echo "  logs     - tail app logs"
	@echo "  test     - run pytest"
	@echo "  seed     - insert fake entities (no COC calls)"
	@echo "  lint     - ruff"
	@echo "  fmt      - ruff --fix"
	@echo "  reset-db - drop and recreate the dev DB"
	@echo "  zip      - create the source archive"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f app

dev:
	$(COMPOSE_DEV) up --build

test:
	docker compose -f docker-compose.dev.yml run --rm app pytest -q

seed:
	docker compose -f docker-compose.dev.yml run --rm app python -m scripts.dev_seed

lint:
	docker compose -f docker-compose.dev.yml run --rm app ruff check app tests

fmt:
	docker compose -f docker-compose.dev.yml run --rm app ruff check --fix app tests

reset-db:
	docker compose -f docker-compose.dev.yml down -v
	$(COMPOSE_DEV) up -d crdb-dev

zip:
	rm -f clash-panda-tracker.zip
	zip -r clash-panda-tracker.zip \
	  . \
	  -x '.git/*' -x '.env' -x '*.zip' -x '.pytest_cache/*' -x '__pycache__/*' \
	         -x 'nginx/certs/*' -x 'config/config.yaml'
	@echo "created clash-panda-tracker.zip"