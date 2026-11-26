# Architecture

Planned end state (built over Phases 1–7):

```
EIA API ─┐                         ┌─> FastAPI /forecast
         ├─> ingest (Python) ─> raw Parquet ─> dbt on DuckDB ─> features ─> model ─┤
Open-Meteo┘   idempotent,          partitioned    staging → marts         (MLflow)   ├─> Streamlit dashboard
              incremental          by date        + data tests                       └─> monitoring (error, drift)
                                 orchestrated daily at 10:00 Central by Dagster
```

Replace this with a rendered diagram (e.g. Mermaid or Excalidraw PNG in `reports/figures/`)
before publishing.

## Raw layer (Phase 1)

```
data/raw/
├── eia/date=YYYY-MM-DD/part.parquet                         period_utc, parent, subba, load_mw, fetched_at_utc
├── eia/_revisions/date=YYYY-MM-DD/superseded_at=…parquet    values a later fetch replaced
└── weather/
    ├── source=previous_runs/date=…/part.parquet            period_utc, point, lead_days, temp_f, …, fetched_at_utc
    └── source=era5/date=…/part.parquet                     period_utc, point, temp_f, …, fetched_at_utc
```

Entry point: `python -m chiload.ingest {backfill|incremental}`, wrapped by `make backfill` and
`make ingest`. See ADRs 0003 (weather source) and 0004 (partitioning and revisions).
