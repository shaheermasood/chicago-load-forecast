# 0005 — Key everything on UTC; local time is a derived column

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Load depends on local clock time: people wake, commute and switch on air conditioning by the
Central-time clock. But Central time isn't a valid key. On the spring-forward day the hour
02:00 doesn't exist, and on the fall-back day 01:00 happens twice. A join or `GROUP BY` on
local time silently drops or merges hours twice a year. EIA-930's `hourly` route and our
Open-Meteo requests are both in UTC.

The plan's conventions say local time should appear "only in marts". Its Phase 2 step asks
staging to "convert UTC to America/Chicago".

## Decision

- `period_utc` (TIMESTAMPTZ) is the key in every model. All joins and deduplication use it.
- Staging adds `period_local`, the naive wall-clock time in America/Chicago, via the
  `to_local()` macro. Downstream models use it to derive hour-of-day, day-of-week and holiday
  features. It is never used as a key.
- The DuckDB session runs with `TimeZone = UTC`, so results don't depend on the machine's zone.

This follows the Phase 2 step and relaxes the "only in marts" convention: computing local time
once, in staging, is less error-prone than having every downstream model convert it.

## Alternatives considered

- **Local time only in marts.** This is stricter, but intermediate models need local hour for
  DST handling and the weather join, so the conversion would be repeated.
- **Use EIA's `local-hourly` route.** It returns local timestamps with offsets, which moves the
  DST problem into ingestion instead of solving it.

## Consequences

- Tests check both DST transitions: no local 02:00 on 2024-03-10, and local 01:00 twice on
  2024-11-03.
- A target "day" in the mart is a local date, which has 23, 24 or 25 hours. Feature code must
  not assume 24.
