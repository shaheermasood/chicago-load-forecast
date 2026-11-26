# 0003 — Training weather comes from Open-Meteo's Previous Runs API

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

The model issues its forecast at 10:00 Central for each hour of the next day, so its weather
inputs have to be *forecasts as they were issued*, made about 24–48 hours before the target
hour. Training on weather that was observed later, or forecast only a few hours ahead, would
make the backtest look better than live performance will be.

The project plan named Open-Meteo's **Historical Forecast API**. That API builds one continuous
series by joining the first hours of each successive model run. Each value is therefore a
very short-lead forecast, close to an analysis of what happened. That is much better than
reanalysis, but it still understates the error of a real day-ahead forecast.

Open-Meteo's **Previous Runs API** returns the same variables with suffixes such as
`temperature_2m_previous_day1` and `_previous_day2`. These are the values from the model run one
or two days before the target time. A probe on 2026-11-25 returned data for 2021-06-01 but
only nulls for 2021-03-01 at O'Hare, so the archive starts somewhere in spring 2021.

## Decision

- Training features come from `previous_runs` with `lead_days: [1, 2]`: lead 1 for the
  day-ahead target and lead 2 for the 48-hour stretch goal. Ingestion starts at 2022-01-01,
  as the plan's "train on 2022 onward" says.
- `era5` (reanalysis) is ingested from 2018-07-01 for the oracle ablation and climatology.
- `historical_forecast` is still supported by the loader but off by default. It can be turned
  on in `configs/model.yaml` to measure how much the forecast source matters.

## Alternatives considered

- **Historical Forecast API as the main source.** It's what the plan named, with a longer
  archive, but lead time is close to zero, so it leaks a little skill the live model won't have.
- **Store full model runs (forecast issue time × valid time).** This is the most faithful
  option but needs a different data source, such as NOAA GFS GRIB archives, and far more
  storage. It's overkill for a zone-average temperature feature.

## Consequences

- `lead_days` is part of the weather key. The Phase 2 feature mart must join `lead_days = 1`
  for the day-ahead horizon and `lead_days = 2` for hours 25–48.
- `previous_dayN` follows Open-Meteo's run timing, not exactly our 10:00 cutoff. Before
  relying on the exact alignment, check Open-Meteo's documented run-to-day mapping against the
  10:00 Central issue time. Record the result here.
- The ablation now has three tiers: ERA5 (perfect weather), Historical Forecast (very short
  lead) and Previous Runs (honest day-ahead).
