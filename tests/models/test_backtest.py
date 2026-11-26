from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from chiload.models import backtest
from chiload.models.backtest import add_months, fold_frames, make_folds, run_backtest, summary_table
from chiload.models.baselines import default_baselines
from chiload.models.gbm import LightGBMForecaster

SETTINGS = {"train_start": date(2022, 1, 1), "min_train_months": 12, "holdout_months": 12}


def test_add_months():
    assert add_months(date(2023, 11, 1), 3) == date(2024, 2, 1)
    assert add_months(date(2024, 1, 15), -1) == date(2023, 12, 1)


def test_fold_plan_keeps_the_last_12_months_untouched(features):
    plan = make_folds(features, **SETTINGS)
    assert plan.holdout_start == date(2024, 1, 1)
    months = [f.test_month for f in plan.folds]
    assert months[0] == date(2023, 1, 1) and months[-1] == date(2023, 12, 1)
    assert len(months) == 12


def test_holdout_ignores_trailing_future_rows(features):
    # Live rows for tomorrow have no load yet; they must not shift the holdout.
    future = features.tail(48).copy()
    future["target_period_utc"] += pd.Timedelta(days=30)
    future["target_date_local"] = (
        pd.to_datetime(future["target_date_local"]) + pd.Timedelta(days=30)
    ).dt.date
    future["load_mw"] = np.nan
    plan = make_folds(pd.concat([features, future]), **SETTINGS)
    assert plan.holdout_start == date(2024, 1, 1)


def test_training_stops_at_what_was_known_when_the_test_month_was_issued(features):
    plan = make_folds(features, **SETTINGS)
    fold = plan.folds[3]
    train, test = fold_frames(
        features, fold, train_start=SETTINGS["train_start"], holdout_start=plan.holdout_start
    )
    first_issue = test["issue_time_utc"].min()
    assert train["target_period_utc"].max() == fold.train_end_utc
    assert fold.train_end_utc < first_issue < test["target_period_utc"].min()
    assert test["target_date_local"].min() == fold.test_month


def test_not_enough_data_is_an_error(features):
    short = features[features["target_date_local"].astype(str) < "2022-10-01"]
    with pytest.raises(ValueError, match="No folds"):
        make_folds(short, **SETTINGS)


def test_end_to_end_linear_beats_seasonal_naive(features):
    preds, metrics, plan = run_backtest(features, default_baselines(), **SETTINGS)
    table = summary_table(metrics)
    assert set(table.index) == {"seasonal_naive_week", "naive_48h", "linear_calendar_weather"}
    assert table.loc["linear_calendar_weather", "mape_improvement_vs_week_pct"] > 30
    assert (preds["target_date_local"] < plan.holdout_start).all()
    # 12 folds + "all" for each model
    assert len(metrics) == 3 * 13


def test_models_are_compared_on_the_same_hours(features):
    df = features.copy()
    df.loc[df.index[9000:9100], "load_lag_48h_mw"] = np.nan
    _, metrics, _ = run_backtest(df, default_baselines(), **SETTINGS)
    overall = metrics[metrics["fold"] == "all"].set_index("model")
    assert overall["n_hours"].nunique() == 1
    assert overall.loc["naive_48h", "coverage"] < 1.0
    assert overall.loc["seasonal_naive_week", "coverage"] == 1.0


def test_unevaluable_hours_are_not_scored(features):
    df = features.copy()
    df.loc[df.index[9000:9010], "is_load_imputed"] = True
    _, base, _ = run_backtest(features, default_baselines()[:1], **SETTINGS)
    _, flagged, _ = run_backtest(df, default_baselines()[:1], **SETTINGS)
    n = lambda m: m.loc[m["fold"] == "all", "n_hours"].item()  # noqa: E731
    assert n(base) - n(flagged) == 10


def test_cli_logs_to_mlflow(features, tmp_path, monkeypatch, capsys):
    db = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("create schema marts")
        con.register("f", features)
        con.execute("create table marts.fct_hourly_features as select * from f")

    real_load = backtest.config.load_yaml

    def fake_load_yaml(name):
        cfg = real_load(name)
        if name == "model":
            cfg["models"]["lightgbm"]["params"].update(n_estimators=40, learning_rate=0.1)
            cfg["mlflow"] = {
                "tracking_uri": f"sqlite:///{tmp_path}/mlflow.db",
                "artifact_location": str(tmp_path / "artifacts"),
                "experiment": "test",
            }
        return cfg

    monkeypatch.setattr(backtest.config, "load_yaml", fake_load_yaml)
    report = tmp_path / "report.md"
    assert backtest.main(["--db", str(db), "--report", str(report)]) == 0

    import mlflow

    runs = mlflow.search_runs(experiment_names=["test"])
    assert sorted(runs["tags.model"]) == sorted(
        ["seasonal_naive_week", "naive_48h", "linear_calendar_weather", "lightgbm"]
    )
    assert runs["metrics.mape"].notna().all()
    assert runs["params.holdout_start"].eq("2024-01-01").all()
    assert "linear_calendar_weather" in report.read_text()
    assert "holdout from 2024-01-01 untouched" in capsys.readouterr().out
    gbm = runs[runs["tags.model"] == "lightgbm"].iloc[0]
    assert 0.6 < gbm["metrics.interval_coverage"] < 0.95
    artifacts = mlflow.artifacts.list_artifacts(run_id=gbm["run_id"])
    assert "feature_importance_gain.json" in {a.path for a in artifacts}


def test_cli_rejects_unknown_models(features, tmp_path):
    db = tmp_path / "w.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("create schema marts")
        con.register("f", features.head(10))
        con.execute("create table marts.fct_hourly_features as select * from f")
    with pytest.raises(SystemExit):
        backtest.main(["--db", str(db), "--models", "nope", "--no-mlflow"])


def test_interval_metrics_only_for_models_with_intervals(features):
    models = [
        default_baselines()[0],
        LightGBMForecaster({"n_estimators": 40, "learning_rate": 0.1}),
    ]
    _, metrics, _ = run_backtest(features, models, **SETTINGS)
    overall = metrics[metrics["fold"] == "all"].set_index("model")
    assert pd.isna(overall.loc["seasonal_naive_week", "interval_coverage"])
    assert 0.6 < overall.loc["lightgbm", "interval_coverage"] < 0.95
    assert overall.loc["lightgbm", "interval_width_pct"] > 0
