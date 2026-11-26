"""Generate the dbt seed CSVs that come from Python config or packages.

Usage:  make seeds   (or: uv run python scripts/make_seeds.py)

* dbt/seeds/weather_points.csv: point name and population weight, from
  configs/weather_points.yaml. The YAML stays the single source of truth.
* dbt/seeds/holidays.csv: US federal and Illinois holidays (observed dates) from the
  `holidays` package, plus the days around them when load behaves like a holiday.

tests/test_seeds.py fails if the committed CSVs drift from what this script produces.
"""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import holidays

from chiload.config import PROJECT_ROOT, load_yaml

SEED_DIR = PROJECT_ROOT / "dbt" / "seeds"
YEARS = range(2018, 2031)

#: Holidays where most businesses close, so load drops like a Sunday. The rest (Columbus Day,
#: Lincoln's Birthday, Election Day...) are mostly government and bank closures, and are kept
#: as "minor" so the model can decide whether they matter.
MAJOR = {
    "New Year's Day",
    "Memorial Day",
    "Independence Day",
    "Labor Day",
    "Thanksgiving Day",
    "Christmas Day",
}


def holiday_rows(years=YEARS) -> list[dict[str, str]]:
    cal = holidays.country_holidays("US", subdiv="IL", years=list(years), observed=True)
    rows = {}
    for day, name in sorted(cal.items()):
        tier = "major" if any(m in name for m in MAJOR) else "minor"
        rows[day] = {"holiday_date": day.isoformat(), "holiday_name": name, "holiday_tier": tier}

    # Days that aren't official holidays but where many people are off.
    for y in years:
        thanksgiving = next(d for d, n in cal.items() if d.year == y and n == "Thanksgiving Day")
        extras = {
            thanksgiving + timedelta(days=1): "Day after Thanksgiving",
            date(y, 12, 24): "Christmas Eve",
            date(y, 12, 31): "New Year's Eve",
        }
        for day, name in extras.items():
            rows.setdefault(
                day,
                {"holiday_date": day.isoformat(), "holiday_name": name, "holiday_tier": "bridge"},
            )
    return [rows[d] for d in sorted(rows)]


def weather_point_rows() -> list[dict[str, str]]:
    return [
        {"point": p["name"], "weight": str(p["weight"])}
        for p in load_yaml("weather_points")["points"]
    ]


def render(rows: list[dict[str, str]]) -> str:
    from io import StringIO

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


SEEDS = {"holidays.csv": holiday_rows, "weather_points.csv": weather_point_rows}


def main() -> int:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in SEEDS.items():
        path: Path = SEED_DIR / name
        path.write_text(render(build()))
        print(f"wrote {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
