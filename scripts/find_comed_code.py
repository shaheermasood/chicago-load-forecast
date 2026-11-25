"""Phase 0: find ComEd's subregion code in EIA-930 and the first hour it has data.

Usage:  make find-comed   (or: uv run python scripts/find_comed_code.py)

Record the result in docs/decisions/0002-comed-subregion-code.md and
configs/model.yaml (eia.subregion).
"""

from __future__ import annotations

import sys

from chiload.config import MissingSecretError, eia_api_key, load_yaml
from chiload.ingest.eia import EIAClient, EIAError

KEYWORDS = ("comed", "commonwealth edison")


def find_candidates(facets: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return subregion facets whose name or id looks like ComEd."""
    return [
        f
        for f in facets
        if any(k in f.get("name", "").lower() for k in KEYWORDS)
        or f.get("id", "").upper() in {"COMED", "CE"}
    ]


def first_hour(client: EIAClient, route: str, parent: str, code: str) -> dict | None:
    """Return the earliest row for one subregion (sorted ascending, one row)."""
    resp = client.get(
        f"{route}/data",
        {
            "frequency": "hourly",
            "data[]": "value",
            "facets[parent][]": parent,
            "facets[subba][]": code,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 1,
        },
    )
    rows = resp.get("data", [])
    return rows[0] if rows else None


def main() -> int:
    cfg = load_yaml("model")["eia"]
    route, parent = cfg["route"], cfg["parent_ba"]

    try:
        client = EIAClient(eia_api_key())
        facets = client.facet_values(route, "subba")
    except (MissingSecretError, EIAError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{len(facets)} subregions available on {route}.\n")
    candidates = find_candidates(facets)
    if not candidates:
        print("No ComEd-like subregion found. All subregions:")
        for f in sorted(facets, key=lambda f: f["id"]):
            print(f"  {f['id']:>8}  {f.get('name', '')}")
        return 2

    for c in candidates:
        print(f"Candidate: {c['id']}  ({c.get('name', '')})")
        row = first_hour(client, route, parent, c["id"])
        if row:
            print(
                f"  parent={row.get('parent')}  first hour={row.get('period')} UTC  "
                f"value={row.get('value')} {row.get('value-units', '')}"
            )
        else:
            print(f"  no rows under parent {parent}")

    print(
        "\nNext: put the code in configs/model.yaml (eia.subregion) and "
        "docs/decisions/0002-comed-subregion-code.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
