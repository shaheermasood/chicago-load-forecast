# 0001 — Use EIA-930 as the load source

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

The forecast target is hourly load for the ComEd zone, which is PJM's entire Illinois
footprint. Two public sources publish it: EIA Form EIA-930 (subregion demand) and PJM Data
Miner (`hrl_load_metered`, zone COMED).

## Decision

Use EIA-930 via the EIA API v2 (`electricity/rto/region-sub-ba-data`) as the primary source.

## Alternatives considered

- **PJM Data Miner.** Metered load is the highest-quality series, but Data Miner's terms
  prohibit redistributing its data without a license, and API access needs a PJM account. Kept
  as an optional local cross-check only; never committed or published.
- **PUDL's cleaned EIA-930 Parquet.** Useful for fast backfill and for validating our own
  cleaning, but depending on it alone would hide the ingestion work this project is meant to show.

## Consequences

- Free, keyed API that anyone cloning the repo can use.
- EIA-930 data is published "as-is" and can be revised; we store fetch timestamps (Phase 1).
- Subregion data begins July 2018, which bounds the training window.
