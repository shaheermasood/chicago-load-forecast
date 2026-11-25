import pytest

from chiload import config


def test_model_config_loads():
    cfg = config.load_yaml("model")
    assert cfg["eia"]["parent_ba"] == "PJM"
    assert cfg["forecast"]["min_lag_hours"] >= cfg["forecast"]["horizon_hours"]


def test_weather_weights_sum_to_one():
    points = config.load_yaml("weather_points")["points"]
    assert sum(p["weight"] for p in points) == pytest.approx(1.0)


def test_missing_key_raises_helpful_error(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    with pytest.raises(config.MissingSecretError, match="register"):
        config.eia_api_key()
