# 0008 — LightGBM point model with conformalized quantile intervals

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Phase 5 needs a model that beats the weekly naive by at least 30% MAPE, and 80% prediction
intervals that actually contain the outcome about 80% of the time (success criterion 2).

## Decision

- **Point forecast:** LightGBM with squared-error loss on `chiload.features.build`.
  - Trees capture the U-shaped temperature response, the different weekday and weekend
    shapes, and holiday effects without hand-written interactions. They also handle missing
    features natively, which happens on days with gaps.
  - Load growth (data centers, EVs) is carried by the load-level features (`load_mean_168h`,
    lags), because trees can't extrapolate a trend.
- **Intervals:** conformalized quantile regression (CQR).
  - Quantile LightGBM models at 10% and 90% are fit on the training window minus its last 60
    days.
  - The misses on those 60 days set one additive adjustment, which widens or narrows every
    interval to reach 80% coverage on the calibration days.
  - Only real observations are scored, the same as in the backtest.
- **Settings:** hyperparameters live in `configs/model.yaml` and are tuned only on backtest folds
  (ADR 0007).

## Alternatives considered

- **Raw quantile models.** These often undercover out of sample, and nothing corrects them.
- **Split conformal around the point model.** Every interval would have the same width in
  MW. Real uncertainty is much larger on hot summer afternoons than at night, and quantile
  models capture that.
- **One model per hour of day.** The plan lists this as optional. It means 24 times more fits,
  and hour is already a feature. It's worth trying later if the per-hour error breakdown in
  Phase 6 shows a problem.

## Consequences

- Each fold fits three LightGBM models. A full backtest takes minutes, not seconds.
- The coverage guarantee holds only if the calibration window behaves like the test month.
  It isn't guaranteed in a time series, so the backtest reports `interval_coverage` per fold
  to show where it drifts, for example in seasonal transitions.
- On synthetic data the backtest gives exactly 80% coverage. Real coverage is unknown until the
  backtest runs on real data.
