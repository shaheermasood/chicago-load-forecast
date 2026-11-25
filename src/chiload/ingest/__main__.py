"""Command-line entry point for raw-data ingestion.

    python -m chiload.ingest backfill      # every source from its configured start to today
    python -m chiload.ingest incremental   # only recent days (plus a lookback for revisions)

Options:
    --sources eia,previous_runs,era5   limit to some sources (default: all configured)
    --start / --end YYYY-MM-DD         override the date range (UTC days, inclusive)

Both commands are idempotent: re-running rewrites only partitions whose data changed.
Exit code is 1 if any source failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from chiload import config
from chiload.ingest.eia import EIAClient, EIAError, load_demand
from chiload.ingest.store import WriteResult, existing_days
from chiload.ingest.weather import Point, WeatherError, load_weather

log = logging.getLogger("chiload.ingest")


class ConfigError(RuntimeError):
    pass


def eia_root() -> Path:
    return config.RAW_DIR / "eia"


def weather_root(source: str) -> Path:
    return config.RAW_DIR / "weather" / f"source={source}"


def incremental_start(root: Path, default_start: date, lookback_days: int) -> date:
    """Start from the latest stored day minus a lookback, or the default if nothing is stored."""
    stored = existing_days(root)
    if not stored:
        return default_start
    return max(default_start, stored[-1] - timedelta(days=lookback_days))


def build_jobs(model_cfg: dict, points_cfg: dict) -> dict[str, tuple[Path, date, Callable]]:
    """Map source name -> (root dir, configured start, function(start, end) -> results)."""
    jobs: dict[str, tuple[Path, date, Callable[[date, date], list[WriteResult]]]] = {}

    eia = model_cfg["eia"]

    def run_eia(start: date, end: date) -> list[WriteResult]:
        if not eia.get("subregion"):
            raise ConfigError(
                "configs/model.yaml eia.subregion is not set. Run `make find-comed` and record "
                "the code (see docs/decisions/0002-comed-subregion-code.md)."
            )
        client = EIAClient(config.eia_api_key())
        return load_demand(
            client,
            route=eia["route"],
            parent=eia["parent_ba"],
            subregion=eia["subregion"],
            start=start,
            end=end,
            root=eia_root(),
        )

    jobs["eia"] = (eia_root(), eia["start"], run_eia)

    weather = model_cfg["weather"]
    points = [Point(p["name"], p["lat"], p["lon"]) for p in points_cfg["points"]]
    for name, src in weather["sources"].items():

        def run_weather(start: date, end: date, name=name, src=src) -> list[WriteResult]:
            return load_weather(
                name,
                points=points,
                variables=weather["variables"],
                start=start,
                end=end,
                root=weather_root(name),
                lead_days=src.get("lead_days", ()),
            )

        jobs[name] = (weather_root(name), src["start"], run_weather)

    return jobs


def summarize(name: str, results: list[WriteResult]) -> str:
    counts = Counter(r.status for r in results)
    revised = sum(r.changed for r in results)
    parts = [f"{k}={counts[k]}" for k in ("created", "updated", "unchanged", "missing")]
    return f"{name:>14}: " + " ".join(parts) + f" revised_rows={revised}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chiload.ingest")
    parser.add_argument("mode", choices=["backfill", "incremental"])
    parser.add_argument("--sources", help="comma-separated subset of sources")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    model_cfg = config.load_yaml("model")
    jobs = build_jobs(model_cfg, config.load_yaml("weather_points"))
    wanted = args.sources.split(",") if args.sources else list(jobs)
    unknown = set(wanted) - set(jobs)
    if unknown:
        parser.error(f"unknown sources {sorted(unknown)}; choose from {sorted(jobs)}")

    end = args.end or datetime.now(UTC).date()
    lookback = model_cfg["ingest"]["incremental_lookback_days"]

    failed = False
    for name in wanted:
        root, default_start, run = jobs[name]
        if args.start:
            start = args.start
        elif args.mode == "incremental":
            start = incremental_start(root, default_start, lookback)
        else:
            start = default_start
        print(f"{name}: {start} .. {end}", flush=True)
        try:
            results = run(start, end)
        except (ConfigError, config.MissingSecretError, EIAError, WeatherError) as exc:
            print(f"{name}: FAILED: {exc}", file=sys.stderr)
            failed = True
            continue
        print(summarize(name, results), flush=True)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
