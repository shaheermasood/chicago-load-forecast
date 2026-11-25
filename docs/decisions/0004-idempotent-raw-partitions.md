# 0004 — Raw data stored as one Parquet file per UTC day, with revision history

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

The plan's "done when" for ingestion says `make backfill` rebuilds raw data from scratch and a
second run changes nothing. EIA-930 values are also revised after first publication, and the
plan asks us to keep "what was known when".

## Decision

Every source writes `data/raw/<source>/date=YYYY-MM-DD/part.parquet`, keyed by UTC day
(`chiload.ingest.store`):

- Writing a day **replaces** its partition (write to a temp file, then atomic rename). It never
  appends, so rows can't duplicate.
- If the new rows equal what's stored (NaN counts as equal to NaN), **nothing is written**.
  That's what makes a second run a no-op, down to file bytes and modification times.
- `fetched_at_utc` on each row is when that value was **first seen**. Unchanged rows keep their
  timestamp, and revised rows get the new one.
- The superseded version of any changed or removed row is written to
  `_revisions/date=YYYY-MM-DD/superseded_at=<ts>.parquet`.
- A day that comes back empty from the API is reported as `missing` and never deletes stored
  data.
- Incremental mode re-fetches from the latest stored day minus `incremental_lookback_days` (7).
  That picks up EIA's late revisions and ERA5's roughly 5-day publication lag.

## Alternatives considered

- **Append-only log of every fetch.** This keeps full history, but every reader then has to
  deduplicate, and a second run doubles the data.
- **Write straight into DuckDB.** That's simpler at first, but Parquet partitions are easier to
  inspect, diff and rebuild per day, and dbt-duckdb reads them directly in Phase 2.
- **UTC vs. local-day partitions.** EIA's hourly route and our Open-Meteo requests are in UTC.
  Local-time days have 23 or 25 hours at DST changes. Conversion to America/Chicago happens in
  dbt staging.

## Consequences

- Rebuilding a point-in-time view means combining `part.parquet` with `_revisions/`. Phase 2
  staging reads only `part.parquet`, the latest values.
- Partitions are small (about 24 EIA rows, or 24 × 5 points × 2 leads of weather per day).
  That's thousands of files over 2018–today, which DuckDB scans in well under a second.
