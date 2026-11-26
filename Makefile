.PHONY: setup lint format test find-comed backfill ingest seeds build freshness docs check

setup:            ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install

lint:             ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format:           ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

test:             ## Run the test suite
	uv run pytest

find-comed:       ## Phase 0: discover ComEd's EIA-930 subregion code (needs EIA_API_KEY)
	uv run python scripts/find_comed_code.py

backfill:         ## Fetch all raw data from each source's start date to today (idempotent)
	uv run python -m chiload.ingest backfill

ingest:           ## Fetch only recent days, re-checking the last week for revisions
	uv run python -m chiload.ingest incremental

DBT = uv run dbt --project-dir dbt --profiles-dir dbt

seeds:            ## Regenerate dbt seed CSVs (holidays, weather-point weights) from Python/config
	uv run python scripts/make_seeds.py

build:            ## Build and test the dbt warehouse (data/warehouse.duckdb); needs a backfill first
	$(DBT) build

freshness:        ## Check that raw sources are recent enough
	$(DBT) source freshness

docs:             ## Generate dbt docs and serve the lineage graph on localhost:8080
	$(DBT) docs generate
	$(DBT) docs serve

check: lint test  ## Everything CI runs

# Later phases will add: train, serve, dashboard
