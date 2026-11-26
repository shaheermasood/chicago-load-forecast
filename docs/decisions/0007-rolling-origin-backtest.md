# 0007 — Monthly rolling-origin backtest, with the last year held out

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Load is a time series with strong seasonality, so a random train/test split would test on hours
surrounded by training data and overstate accuracy. The success criteria also call for a
"full held-out year" that model choices must not be tuned on.

## Decision

- **Holdout.** The last 12 calendar months that have observed load are never trained on or
  scored before Phase 6. `make_folds` computes them from the data, and the backtest asserts no
  fold reaches them. Future rows with no load yet don't move the holdout.
- **Folds.** Expanding window, one fold per month. Fold k tests on month k and trains on
  everything from `train_start` (2022-01-01, when archived forecasts begin) up to a cutoff. The
  first test month comes after 12 months of training, so every fold sees each season at least
  once.
- **Training cutoff.** Training ends at the first test day's `load_known_through_utc`, the latest
  load available when that day's forecast was issued. Ending at the end of the previous month
  would let the model train on about 14 hours of targets that were unknown at issue time.
- **What gets scored.** Only real observations: hours EIA reported that weren't flagged anomalous
  or imputed. Models are compared on the same hours (those every model forecast), and each
  model's coverage is reported separately.
- **Metrics.** MAPE (the headline, used for the 30% target), RMSE, bias, and daily-peak MAPE:
  the error in each day's maximum load, skipping days with fewer than 20 scored hours.
- **"Same hour yesterday."** This baseline from the plan is replaced by `naive_48h`: for any
  target hour after 10:00, yesterday's value is unknown at issue time (ADR 0006).

## Alternatives considered

- **Sliding window.** It adapts faster to load growth (data centres, EVs), but has less
  data for rare events. It's worth trying in Phase 5 as an option.
- **Daily refits.** These match production, but cost about 30 times more compute for little
  information at the baseline stage.
- **Scoring imputed hours.** That would grade models partly against our own linear
  interpolation.

## Consequences

- Hyperparameter tuning in Phase 5 uses only the backtest folds. The holdout gets one scoring
  run in Phase 6, and that run should be logged as such.
- With data from 2022 to late 2026, the holdout is about the last year, which leaves roughly
  two years of folds.
