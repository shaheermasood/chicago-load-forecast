.PHONY: setup lint format test find-comed check

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

check: lint test  ## Everything CI runs

# Later phases will add: backfill, build (dbt), train, serve, dashboard
