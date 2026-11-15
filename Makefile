SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help setup api web seed seed-demo test test-backend test-frontend lint verify smoke smoke-online docker-up docker-down data-earth data-earth-dry-run data-worldcover data-moon data-ingest-earth data-ingest-moon data-analyze-aoi data-demo data-validate data-verify data-snapshot data-index data-index-dry-run data-publish data-sync data-fetch data-rebuild-index db-audit db-benchmark-coarse db-benchmark-typed db-backup db-backup-drill

WEST ?= -6.617
SOUTH ?= 37.682
EAST ?= -6.600
NORTH ?= 37.699
SNAPSHOT ?= samples/snapshot/analog-demo.snapshot.json
SNAPSHOT_ID ?=
DESTINATION ?= data/snapshots
DB ?= var/analog.db
ROWS ?= 100000
BACKUP ?= /tmp/analog-backup.db

help: ## Show the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	uv sync
	cd apps/web && npm ci

api: ## Run the API on http://127.0.0.1:8000
	uv run uvicorn analog_api.main:create_app --factory --reload --port 8000

web: ## Run the Vite dev server on http://localhost:5173
	cd apps/web && npm run dev

seed: ## Load committed real-data manifests into ./var/analog.db
	ANALOG_SEED_INCLUDE_SYNTHETIC=false uv run python -m analog_api.cli seed

seed-demo: ## Load the clearly-labelled synthetic fixture into ./var/demo.db
	ANALOG_DATABASE_URL=sqlite+pysqlite:///./var/demo.db ANALOG_SEED_INCLUDE_SYNTHETIC=true uv run python -m analog_api.cli seed

test: ## Run backend and frontend tests
	uv run pytest -q
	cd apps/web && npm run test -- --run

test-backend: ## Run backend + pipeline tests
	uv run pytest -q

test-frontend: ## Run frontend tests
	cd apps/web && npm run test -- --run

lint: ## Lint and format-check Python; typecheck and lint the web app
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npm run typecheck && npm run lint

verify: ## Run the full P0 quality gate
	bash scripts/verify.sh

smoke: ## Run the offline API integration smoke test
	bash scripts/smoke.sh

smoke-online: ## Opt-in real provider AOI smoke (network required)
	bash scripts/online_smoke.sh

docker-up: ## Build and start web + API with Docker Compose
	docker compose up --build

docker-down: ## Stop the Compose stack
	docker compose down

data-earth: ## Re-run the real Copernicus DEM ingestion (network)
	uv run python pipelines/earth/ingest_copernicus.py

data-moon: ## Attempt the real NASA LOLA reference import (network)
	uv run python pipelines/planetary/lola_import.py

data-earth-dry-run: ## Show the Copernicus tile URLs without downloading
	uv run python pipelines/earth/ingest_copernicus.py --dry-run

data-worldcover: ## Re-run ESA WorldCover context ingestion (network)
	uv run python pipelines/worldcover/ingest_worldcover.py

data-ingest-earth: data-earth ## Alias: online real bounded Earth demo AOIs

data-ingest-moon: data-moon ## Alias: online real lunar reference ROI

data-analyze-aoi: ## Analyze a new bounded AOI (WEST/SOUTH/EAST/NORTH; network)
	uv run python -m analog_api.cli analyze-aoi --west $(WEST) --south $(SOUTH) --east $(EAST) --north $(NORTH)

data-demo: ## Rebuild the offline demo index from the committed pinned snapshot
	uv run python pipelines/build_index.py --snapshot $(SNAPSHOT) --database-url sqlite+pysqlite:///./var/demo-index.db --reset

data-validate: ## Verify snapshot identity, manifest checksums, and artifacts
	uv run python -m analog_pipelines.snapshot verify --snapshot $(SNAPSHOT)

data-verify: data-validate ## Alias for data-validate

data-snapshot: ## Rebuild the pinned snapshot from committed manifests
	uv run python -m analog_pipelines.snapshot build

data-index: data-demo ## Alias: rebuild the offline demo index

data-index-dry-run: ## Verify the pinned snapshot and count without writing
	uv run python pipelines/build_index.py --snapshot $(SNAPSHOT) --database-url sqlite+pysqlite:///./var/demo-index.db --dry-run

data-publish: ## Publish the pinned snapshot to configured object storage (no prompts)
	uv run --extra storage python -m analog_pipelines.snapshot publish --snapshot $(SNAPSHOT)

data-sync: ## Fetch a pinned snapshot (requires SNAPSHOT_ID=... and configured store)
	@test -n "$(SNAPSHOT_ID)" || (echo "ERROR: set SNAPSHOT_ID=<id>" >&2; exit 2)
	uv run --extra storage python -m analog_pipelines.snapshot fetch --snapshot-id $(SNAPSHOT_ID) --destination $(DESTINATION)

data-fetch: data-sync ## Alias for data-sync (requires SNAPSHOT_ID)

data-rebuild-index: data-validate ## Rebuild SQLite from the pinned snapshot after checksum verification
	uv run python pipelines/build_index.py --snapshot $(SNAPSHOT) --database-url sqlite+pysqlite:///./var/analog.db --reset

db-audit: ## Report SQLite/Parquet/raw/cache/R2 storage separately
	uv run python scripts/db_audit.py --database $(DB)

db-benchmark-coarse: ## Synthetic coarse-index benchmark (ROWS=100000; not real coverage)
	uv run python scripts/benchmark_coarse_index.py --rows $(ROWS) --json-out /tmp/coarse-$(ROWS).json

db-benchmark-typed: ## Synthetic typed-observation index measurement (ROWS=100000)
	uv run python scripts/benchmark_typed_observations.py --rows $(ROWS)

db-backup: ## Create a verified online SQLite backup (BACKUP=/path/to/backup.db)
	uv run python scripts/db_backup.py backup --source $(DB) --output $(BACKUP)

db-backup-drill: ## Backup -> restore -> rebuild on copies; never resets DB
	uv run python scripts/db_backup.py drill --source $(DB)

public-up: ## Build and start api + web + Cloudflare Tunnel (one command)
	docker compose -f compose.public.yaml up -d --build

public-down: ## Stop the public demo stack
	docker compose -f compose.public.yaml down

public-logs: ## Follow public demo stack logs
	docker compose -f compose.public.yaml logs -f --tail=100

public-smoke: ## Verify the public API/web endpoint
	bash scripts/public_smoke.sh
