# 0006 — Every feature must have been available at 10:00 the day before

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

The forecast for local day D+1 is issued at 10:00 Central on day D. The forecast is honest
only if every input existed at that moment. Two inputs make this easy to get wrong.

**Load.** EIA-930 publishes with a delay, so at 10:00 the most recent hours aren't out yet.
A "same hour yesterday" lag is the most obvious leak: for a 15:00 target, yesterday's 15:00
is after the issue time.

**Weather.** In the Previous Runs API, a `previous_day1` value for hour t comes from the run
started about 24 hours before t. For a 23:00 target on D+1, that run starts around 23:00 on D,
13 hours after we issue. Only the first few hours of D+1 can honestly use lead 1.

## Decision

`fct_hourly_features` applies these rules, set by dbt vars in `dbt/dbt_project.yml`:

- **Load cutoff.** An hour starting at h is usable once `h + 1h + load_latency_hours <=
  issue_time`, with `load_latency_hours: 6` for now. Each lag (48, 72, 168, 336 h) is NULL if
  its hour is past the cutoff. Rolling stats (last known, 24 h and 168 h mean, 24 h max) only
  use hours up to the cutoff.
- **Weather lead per hour.** Use lead 1 if `target - 1 day <= issue - weather_run_latency_hours`
  (6 h), otherwise lead 2. In practice that means lead 1 for about the first 4–5 local hours of
  D+1 and lead 2 for the rest.
- **Tests enforce it.** `assert_no_load_leakage` and `assert_no_weather_leakage` fail the build
  if any row breaks either rule.
- **Observed weather is kept apart.** ERA5 is in `fct_hourly_weather_oracle`, not the feature
  table, so it can't end up in a feature list by accident.

## Alternatives considered

- **No latency (assume instant publication).** Simpler, but it would quietly overstate accuracy.
- **Always lead 2.** Safe, but it throws away fresher forecasts for the early hours.
- **Store true run timestamps.** Exact, but Open-Meteo doesn't expose run times through this
  API. It would need raw model archives (see ADR 0003).

## Consequences

- The Phase 4 baseline "same hour yesterday" can't be used as written. Use the 48 h lag or
  `load_last_known_mw`. "Same hour last week" (`load_lag_168h_mw`) is always available.
- Both latencies are guesses. Once the Phase 7 pipeline has run for a while,
  `fetched_at_utc - period_utc` from incremental runs shows the real EIA publication lag. Set
  `load_latency_hours` from it.
- The `previous_dayN` run timing still needs checking against Open-Meteo's documentation (ADR
  0003). If lead N turns out to mean something else, only the `weather_lead_days` CASE changes.
