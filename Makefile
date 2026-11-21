# On Windows without make, use: .\make.ps1 <target>
COMPOSE ?= docker compose
API = $(COMPOSE) exec -T api

.PHONY: up down build logs migrate test test-backend test-dashboard lint seed run-cycle backup check-providers purge-demo clean-leads shell

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build

logs:
	$(COMPOSE) logs -f --tail=100

migrate:
	$(API) alembic upgrade head

test: test-backend test-dashboard

test-backend:
	$(API) pytest -q

test-dashboard:
	cd dashboard && npm test --silent

lint:
	$(API) ruff check app tests
	$(API) mypy app
	cd dashboard && npm run lint

seed:
	$(API) python -m app.cli seed

run-cycle:
	$(API) python -m app.cli run-cycle

backup:
	$(API) python -m app.cli backup

check-providers:
	$(API) python -m app.cli check-providers

purge-demo:
	$(API) python -m app.cli purge-demo

clean-leads:  # old/undated social posts and freelance leads -> Not useful
	$(API) python -m app.cli clean-leads

shell:
	$(COMPOSE) exec api bash
