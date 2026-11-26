import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_seeds", ROOT / "scripts" / "make_seeds.py")
make_seeds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_seeds)


def test_committed_seeds_are_up_to_date():
    for name, build in make_seeds.SEEDS.items():
        committed = (make_seeds.SEED_DIR / name).read_text()
        assert committed == make_seeds.render(build()), f"{name} is stale: run `make seeds`"


def test_holiday_tiers():
    rows = {r["holiday_date"]: r for r in make_seeds.holiday_rows(range(2024, 2025))}
    assert rows["2024-07-04"]["holiday_tier"] == "major"
    assert rows["2024-11-29"] == {
        "holiday_date": "2024-11-29",
        "holiday_name": "Day after Thanksgiving",
        "holiday_tier": "bridge",
    }
    assert rows["2024-03-04"]["holiday_tier"] == "minor"  # Casimir Pulaski Day


def test_observed_dates_are_used():
    rows = {
        r["holiday_date"]: r["holiday_name"] for r in make_seeds.holiday_rows(range(2021, 2022))
    }
    # July 4, 2021 was a Sunday, so it was observed on Monday the 5th.
    assert "2021-07-05" in rows
