from datetime import date

import pytest

from chiload import config
from chiload.ingest import __main__ as cli
from chiload.ingest.store import partition_path


def test_incremental_start_uses_lookback(tmp_path):
    assert cli.incremental_start(tmp_path, date(2018, 7, 1), 7) == date(2018, 7, 1)
    p = partition_path(tmp_path, date(2024, 7, 10))
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    assert cli.incremental_start(tmp_path, date(2018, 7, 1), 7) == date(2024, 7, 3)
    assert cli.incremental_start(tmp_path, date(2024, 7, 8), 7) == date(2024, 7, 8)


def test_jobs_cover_eia_and_configured_weather_sources():
    jobs = cli.build_jobs(config.load_yaml("model"), config.load_yaml("weather_points"))
    assert {"eia", "previous_runs", "era5"} <= set(jobs)


def test_missing_subregion_fails_with_instructions(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    cfg = config.load_yaml("model")
    cfg["eia"]["subregion"] = None
    monkeypatch.setattr(cli.config, "load_yaml", lambda n: cfg if n == "model" else _points())

    code = cli.main(
        ["backfill", "--sources", "eia", "--start", "2024-07-01", "--end", "2024-07-01"]
    )

    assert code == 1
    assert "make find-comed" in capsys.readouterr().err


def test_unknown_source_is_rejected():
    with pytest.raises(SystemExit):
        cli.main(["backfill", "--sources", "nope"])


def _points():
    return {"points": [{"name": "a", "lat": 41.9, "lon": -87.9, "weight": 1.0}]}
