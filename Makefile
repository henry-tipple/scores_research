# Dev entry points. Only Docker (with compose v2) and make are needed on the host.
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
TOOLS   := $(COMPOSE) run --rm tools

.PHONY: help up down reset seed migrate test test-local fake-events fleet feeds logs psql

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

up: .env ## Build and start Postgres, Mailpit, ingest and Metabase
	$(COMPOSE) up -d --build --wait postgres mailpit ingest
	$(COMPOSE) up -d metabase
	@echo "ingest   http://localhost:8000/docs"
	@echo "mailpit  http://localhost:8025"
	@echo "metabase http://localhost:3000  (first start takes about a minute)"

down: .env ## Stop the stack (keeps data)
	$(COMPOSE) --profile feeds --profile tools down

reset: .env ## Stop the stack and delete all data volumes
	$(COMPOSE) --profile feeds --profile tools down -v

migrate: .env ## Apply schema + migrations
	$(TOOLS) python db/migrate.py

seed: .env ## Migrate, then load 12 cohorts and 40 instances
	$(TOOLS) python db/seed.py

test: .env ## Run the pytest suite in the tools container (uses the study_test DB)
	$(TOOLS) pytest

test-local: ## Run pytest on the host (.venv; DB tests need TEST_DATABASE_URL)
	.venv/bin/pytest

fake-events: .env ## Post a realistic fake day of pushes, live updates and emails
	$(TOOLS) python -m ingest.fake_events

fleet: .env ## Print the fleet summary and write generated files to fleet/out/
	$(TOOLS) python -m fleet.generate summary --out fleet/out

feeds: .env ## Run the fake match-feed poller in the background
	$(COMPOSE) --profile feeds up -d feeds

logs: .env ## Tail ingest and Mailpit logs
	$(COMPOSE) logs -f --tail=100 ingest mailpit

psql: .env ## Open psql on the study database
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" "$$POSTGRES_DB"'
