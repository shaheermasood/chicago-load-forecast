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
