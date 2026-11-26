"""Rolling-origin backtest for day-ahead forecasters (Phase 4).

    python -m chiload.models.backtest            # or: make backtest

How folds work (expanding window, monthly):

* Fold k trains on every target hour from `train_start` up to the moment the first forecast
  of test month k is issued, and tests on all of month k.
* Training rows stop at the first test day's `load_known_through_utc`. A model refit for the
  month couldn't have known later loads. Without this, the last ~half day of training
  targets would come from after the forecast was issued.
* The last `holdout_months` of data are never trained on or scored here. They're kept for one
  look in Phase 6, and `make_folds` fails if any fold reaches them.

Scoring uses only real observations (not imputed or anomalous hours) and only hours that
every model forecast, so the models are compared on exactly the same hours. Each model's own
coverage is logged as well.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from chiload import config
from chiload.models.baselines import Forecaster, default_baselines
from chiload.models.data import DEFAULT_DB, evaluable, load_features
from chiload.models.gbm import LightGBMForecaster
from chiload.models.metrics import score

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fold:
    index: int
    test_month: date  # first day of the test month
    train_end_utc: pd.Timestamp  # last target hour usable for training (inclusive)


@dataclass(frozen=True)
class FoldPlan:
    folds: list[Fold]
    holdout_start: date  # first local day of the untouched holdout


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def make_folds(
    df: pd.DataFrame, *, train_start: date, min_train_months: int, holdout_months: int
) -> FoldPlan:
    """Plan the monthly folds. Only target days with an observed load count as 'data'."""
    observed_days = df.loc[evaluable(df), "target_date_local"]
    if observed_days.empty:
        raise ValueError("No observed load in the feature table")
    last_month = month_start(max(observed_days))
    holdout_start = add_months(last_month, -(holdout_months - 1))
    first_test = add_months(month_start(train_start), min_train_months)

    days = df[["target_date_local", "load_known_through_utc"]].drop_duplicates("target_date_local")
    known_through = dict(
        zip(days["target_date_local"], days["load_known_through_utc"], strict=True)
    )

    folds = []
    month = first_test
    while month < holdout_start:
        if month in known_through:
            folds.append(Fold(len(folds), month, pd.Timestamp(known_through[month])))
        month = add_months(month, 1)

    if not folds:
        raise ValueError(
            f"No folds: first test month {first_test} isn't before the holdout {holdout_start}. "
            "Need more data or a smaller min_train_months/holdout_months."
        )
    assert all(add_months(f.test_month, 1) <= holdout_start for f in folds)
    return FoldPlan(folds, holdout_start)


def fold_frames(
    df: pd.DataFrame, fold: Fold, *, train_start: date, holdout_start: date
) -> tuple[pd.DataFrame, pd.DataFrame]:
    day = df["target_date_local"]
    train = df[
        (day >= train_start)
        & (df["target_period_utc"] <= fold.train_end_utc)
        & df["load_mw"].notna()
    ]
    test = df[(day >= fold.test_month) & (day < add_months(fold.test_month, 1))]
    # Belt and braces: nothing from the holdout, and no training hour after the cutoff.
    assert (train["target_date_local"] < holdout_start).all()
    assert (test["target_date_local"] < holdout_start).all()
    assert train["target_period_utc"].max() < test["target_period_utc"].min()
    return train, test


def run_backtest(
    df: pd.DataFrame,
    models: list[Forecaster],
    *,
    train_start: date,
    min_train_months: int,
    holdout_months: int,
    min_eval_hours_per_day: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame, FoldPlan]:
    """Run every model over every fold.

    Returns (predictions, fold_metrics, plan). Predictions are long-format, one row per test
    hour and model. fold_metrics has one row per (model, fold), plus fold "all" for the whole
    backtest.
    """
    plan = make_folds(
        df,
        train_start=train_start,
        min_train_months=min_train_months,
        holdout_months=holdout_months,
    )
    preds = []
    for fold in plan.folds:
        train, test = fold_frames(
            df, fold, train_start=train_start, holdout_start=plan.holdout_start
        )
        base = test[["target_period_utc", "target_date_local", "load_mw"]].assign(
            fold=fold.index, evaluable=evaluable(test).to_numpy()
        )
        for model in models:
            model.fit(train)
            lower = upper = np.full(len(test), np.nan)
            if hasattr(model, "predict_interval"):
                lower, upper = model.predict_interval(test)
            preds.append(
                base.assign(
                    model=model.name,
                    prediction=model.predict(test),
                    lower=lower,
                    upper=upper,
                )
            )
        log.info(
            "fold %d (%s): train %d rows, test %d rows",
            fold.index,
            fold.test_month,
            len(train),
            len(test),
        )

    predictions = pd.concat(preds, ignore_index=True)
    return predictions, score_predictions(predictions, min_eval_hours_per_day), plan


def score_predictions(predictions: pd.DataFrame, min_eval_hours_per_day: int = 20) -> pd.DataFrame:
    # One row per test hour, one prediction column per model.
    base_cols = ["target_period_utc", "target_date_local", "load_mw", "fold", "evaluable"]
    base = (
        predictions[base_cols].drop_duplicates("target_period_utc").set_index("target_period_utc")
    )
    by_model = predictions.pivot(index="target_period_utc", columns="model", values="prediction")
    wide = base.join(by_model).reset_index()
    bounds = {
        side: predictions.pivot(index="target_period_utc", columns="model", values=side)
        .reindex(wide["target_period_utc"])
        .reset_index(drop=True)
        for side in ("lower", "upper")
    }
    models = sorted(predictions["model"].unique())
    common = wide["evaluable"] & wide[models].notna().all(axis=1)

    rows = []
    for model in models:
        own = wide["evaluable"] & wide[model].notna()
        for fold, part in [*wide[common].groupby("fold"), ("all", wide[common])]:
            if part.empty:
                continue
            metrics = score(
                part["target_date_local"],
                part["load_mw"].to_numpy(),
                part[model].to_numpy(),
                min_hours_per_day=min_eval_hours_per_day,
            )
            scope = (
                wide["evaluable"] if fold == "all" else wide["evaluable"] & (wide["fold"] == fold)
            )
            metrics["coverage"] = float((own & scope).sum() / max(scope.sum(), 1))
            lo, hi = bounds["lower"].loc[part.index, model], bounds["upper"].loc[part.index, model]
            if lo.notna().all():
                y = part["load_mw"]
                metrics["interval_coverage"] = float(((y >= lo) & (y <= hi)).mean())
                metrics["interval_width_pct"] = float(((hi - lo) / y).mean() * 100)
            rows.append({"model": model, "fold": str(fold), **metrics})
    return pd.DataFrame(rows)


def summary_table(
    fold_metrics: pd.DataFrame, reference: str = "seasonal_naive_week"
) -> pd.DataFrame:
    """Overall metrics per model, with MAPE improvement over the reference baseline."""
    overall = fold_metrics[fold_metrics["fold"] == "all"].set_index("model")
    ref = overall.loc[reference, "mape"] if reference in overall.index else np.nan
    overall["mape_improvement_vs_week_pct"] = (1 - overall["mape"] / ref) * 100
    cols = [
        "mape",
        "mape_improvement_vs_week_pct",
        "rmse",
        "daily_peak_mape",
        "bias_pct",
        "coverage",
        "n_hours",
        "interval_coverage",
        "interval_width_pct",
    ]
    return overall[[c for c in cols if c in overall.columns]].sort_values("mape")


def to_markdown(table: pd.DataFrame) -> str:
    header = ["model", *table.columns]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for name, row in table.iterrows():
        lines.append("| " + " | ".join([str(name), *(str(v) for v in row)]) + " |")
    return "\n".join(lines) + "\n"


OVERALL_METRICS = (
    "mape",
    "rmse",
    "daily_peak_mape",
    "bias_pct",
    "coverage",
    "n_hours",
    "interval_coverage",
    "interval_width_pct",
)


def available_models(cfg: dict) -> dict[str, Forecaster]:
    """Every model the backtest can run, keyed by name."""
    gbm_cfg = cfg.get("models", {}).get("lightgbm", {})
    models: list[Forecaster] = [
        *default_baselines(),
        LightGBMForecaster(
            gbm_cfg.get("params"),
            interval=gbm_cfg.get("interval", 0.8),
            calibration_days=gbm_cfg.get("calibration_days", 60),
        ),
    ]
    return {m.name: m for m in models}


def log_to_mlflow(
    predictions: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    plan: FoldPlan,
    models: list[Forecaster],
    settings: dict,
    mlflow_cfg: dict,
) -> None:
    import mlflow

    mlflow.set_tracking_uri(mlflow_cfg["tracking_uri"])
    name = mlflow_cfg["experiment"]
    if mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(
            name, artifact_location=Path(mlflow_cfg["artifact_location"]).resolve().as_uri()
        )
    mlflow.set_experiment(name)

    for model in models:
        m = fold_metrics[fold_metrics["model"] == model.name]
        with mlflow.start_run(run_name=model.name):
            mlflow.set_tags({"stage": "backtest", "model": model.name})
            mlflow.log_params({**settings, **{f"model.{k}": v for k, v in model.params().items()}})
            mlflow.log_params(
                {
                    "n_folds": len(plan.folds),
                    "first_test_month": str(plan.folds[0].test_month),
                    "last_test_month": str(plan.folds[-1].test_month),
                    "holdout_start": str(plan.holdout_start),
                }
            )
            for _, row in m[m["fold"] != "all"].iterrows():
                for k in ("mape", "rmse", "daily_peak_mape", "bias_pct", "interval_coverage"):
                    if k in row and pd.notna(row[k]):
                        mlflow.log_metric(f"fold_{k}", row[k], step=int(row["fold"]))
            overall = m[m["fold"] == "all"].iloc[0]
            mlflow.log_metrics(
                {k: overall[k] for k in OVERALL_METRICS if k in overall and pd.notna(overall[k])}
            )
            mine = predictions[predictions["model"] == model.name].drop(columns="model")
            mlflow.log_table(mine.astype({"target_date_local": str}), "predictions.json")
            if hasattr(model, "feature_importance"):
                # Importance from the last fold's model, the one trained on the most data.
                imp = model.feature_importance()
                mlflow.log_dict(imp.round(1).to_dict(), "feature_importance_gain.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chiload.models.backtest")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--no-mlflow", action="store_true", help="skip MLflow logging")
    parser.add_argument("--report", type=Path, help="write the summary table as Markdown here")
    parser.add_argument("--models", help="comma-separated subset (default: all)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = config.load_yaml("model")
    bt = cfg["backtest"]
    settings = {
        "train_start": bt["train_start"],
        "min_train_months": bt["min_train_months"],
        "holdout_months": bt["holdout_months"],
    }
    df = load_features(args.db)
    registry = available_models(cfg)
    wanted = args.models.split(",") if args.models else list(registry)
    unknown = set(wanted) - set(registry)
    if unknown:
        parser.error(f"unknown models {sorted(unknown)}; choose from {sorted(registry)}")
    if "seasonal_naive_week" not in wanted:
        wanted.insert(0, "seasonal_naive_week")  # the reference every result is compared to
    models = [registry[name] for name in wanted]
    predictions, fold_metrics, plan = run_backtest(
        df, models, min_eval_hours_per_day=bt["min_eval_hours_per_day"], **settings
    )

    table = summary_table(fold_metrics)
    first, last = plan.folds[0].test_month, plan.folds[-1].test_month
    print(
        f"\n{len(plan.folds)} monthly folds, {first} to {last}; "
        f"holdout from {plan.holdout_start} untouched.\n"
    )
    print(table.round(2).to_string())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(to_markdown(table.round(2)))
    if not args.no_mlflow:
        log_to_mlflow(predictions, fold_metrics, plan, models, settings, cfg["mlflow"])
        uri = cfg["mlflow"]["tracking_uri"]
        print(f"\nLogged {len(models)} runs to MLflow ({uri}). View them with `make mlflow`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
