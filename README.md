# Chicagoland Electricity Demand Forecasting

![CI](https://github.com/shaheermasood/chicago-load-forecast/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Day-ahead hourly electricity demand forecasts for the **ComEd zone** (Chicago and northern
Illinois), built as an end-to-end pipeline: ingestion → warehouse → model → API → monitoring.

> **Status:** Phase 1 — ingestion. Results will go here once the model exists.

## The problem

Every morning at 10:00 Central, forecast each hour of tomorrow's ComEd load (MW), using only
information that would actually be available at 10:00: past load, weather *forecasts* as they
were issued, and the calendar.

**Success criteria**

1. Beat a seasonal-naive baseline (same hour last week) by ≥ 30% MAPE on a held-out year.
2. 80% prediction intervals with ~80% empirical coverage.
3. Daily pipeline runs unattended for 30 days with data-quality checks passing.

## Data

| Source | Use |
| --- | --- |
| [EIA-930](https://www.eia.gov/electricity/930-content/about.html) via EIA API v2 | Hourly ComEd subregion demand (target) |
| [Open-Meteo](https://open-meteo.com/) Previous Runs API | Weather forecasts as issued 1–2 days ahead (training features, [ADR 0003](docs/decisions/0003-weather-from-previous-runs-api.md)) |
| [Open-Meteo](https://open-meteo.com/) Historical Weather API (ERA5) | Observed weather for the oracle ablation |

No data is committed to this repo. `make backfill` rebuilds it from the sources into
`data/raw/<source>/date=YYYY-MM-DD/part.parquet`. Re-running it is a no-op, and `make ingest`
fetches only recent days ([ADR 0004](docs/decisions/0004-idempotent-raw-partitions.md)).

## Quick start

```bash
git clone https://github.com/shaheermasood/chicago-load-forecast.git
cd chicago-load-forecast
cp .env.example .env          # add your free EIA API key
make setup                    # installs deps (uv) + pre-commit hooks
make check                    # lint + tests
make find-comed               # confirm ComEd's EIA subregion code, then set it in configs/model.yaml
make backfill                 # fetch 2018-07 → today (EIA + weather); safe to re-run
```

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

## Repo layout

```
src/chiload/      Python package: ingest, features, models, monitoring, api
dbt/              SQL transformations (staging → intermediate → marts)
orchestration/    Dagster jobs and schedules
dashboard/        Streamlit app
notebooks/        Numbered, exploration only — final logic lives in src/
configs/          Weather points, model settings
reports/          EDA write-up, model card, figures
docs/decisions/   Architecture decision records
tests/            Mirrors src/
```

## Design decisions

See [`docs/decisions/`](docs/decisions/) for why things are the way they are.

## Roadmap

- [x] Phase 0 — Repo, tooling, CI, ComEd code discovery
- [ ] Phase 1 — Idempotent ingestion + backfill (loaders done; full backfill pending)
- [ ] Phase 2 — dbt warehouse + data-quality tests
- [ ] Phase 3 — EDA
- [ ] Phase 4 — Baselines + rolling-origin backtest
- [ ] Phase 5 — Features, models, prediction intervals
- [ ] Phase 6 — Holdout evaluation, SHAP, model card
- [ ] Phase 7 — Scheduled pipeline, API, dashboard, monitoring
- [ ] Phase 8 — 30-day live run, polish

## Attribution

Load data: U.S. Energy Information Administration, Form EIA-930.
Weather data by [Open-Meteo.com](https://open-meteo.com/).
