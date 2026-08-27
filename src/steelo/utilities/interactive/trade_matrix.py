"""Row packing for the trade-matrix viewer (``trade_matrix.html``).

The viewer shows, for one year, how much steel each geography shipped to each
other geography — the diagonal is steel consumed where it was made — grouped
by country, region or trade bloc, from the trade model's per-year allocation
files (``TM/steel_trade_allocations_<year>.csv``, one row per plant →
demand-centre allocation). Origins and destinations are kept at country grain:
plants carry a sub-national geo_unit but demand centres do not, so a finer
diagonal is not definable.
"""

import re
from pathlib import Path
from typing import Any

import pandas as pd

ALLOCATIONS_PATTERN = re.compile(r"^steel_trade_allocations_(\d{4})\.csv$")
COMMODITY = "steel"
COLUMNS = ["commodity", "source_location", "source_tech", "destination_location", "allocated_volume"]
# The location columns hold Location reprs; the country is their iso3='XXX' field.
ISO3_PATTERN = re.compile(r"\biso3='([A-Z]{3})'")
FLOW_KEYS = ["year", "origin", "destination", "technology"]


def allocation_files(tm_dir: Path) -> dict[int, Path]:
    """The run's per-year allocation files, keyed by year.

    Args:
        tm_dir: The run's ``TM`` output directory.

    Returns:
        ``{year: path}`` for every ``steel_trade_allocations_<year>.csv`` found, in year
        order; empty when the directory does not exist or holds none.
    """
    if not tm_dir.is_dir():
        return {}
    files: dict[int, Path] = {}
    for path in tm_dir.iterdir():
        match = ALLOCATIONS_PATTERN.match(path.name)
        if match:
            files[int(match.group(1))] = path
    return dict(sorted(files.items()))


def iso3_of(location: str) -> str:
    """The ISO3 code of a Location repr.

    Args:
        location: A ``Location(...)`` repr as written to the allocation files.

    Returns:
        The three-letter code of its ``iso3`` field.

    Raises:
        ValueError: If the repr carries no ISO3.
    """
    match = ISO3_PATTERN.search(location)
    if match is None:
        raise ValueError(f"No iso3 in location {location!r}")
    return match.group(1)


def read_flows(files: dict[int, Path]) -> pd.DataFrame:
    """Steel flows per year, origin country, destination country and technology.

    Args:
        files: Output of :func:`allocation_files`.

    Returns:
        Columns ``year, origin, destination, technology, volume_mt``: the allocated steel
        summed over the plants of the origin country and the demand centres of the
        destination country. A year whose file holds no steel allocations (a failed trade
        LP writes a header-only file) contributes no rows.

    Raises:
        ValueError: If a file lacks the allocation columns or a location carries no ISO3.
    """
    frames = []
    for year, path in files.items():
        table = pd.read_csv(path, usecols=COLUMNS)
        steel = table[table["commodity"] == COMMODITY]
        if steel.empty:
            continue
        flows = pd.DataFrame(
            {
                "year": year,
                "origin": steel["source_location"].map(iso3_of),
                "destination": steel["destination_location"].map(iso3_of),
                "technology": steel["source_tech"],
                "volume_mt": steel["allocated_volume"] / 1e6,
            }
        )
        frames.append(flows.groupby(FLOW_KEYS, as_index=False)["volume_mt"].sum())
    if not frames:
        return pd.DataFrame(columns=FLOW_KEYS + ["volume_mt"])
    return pd.concat(frames, ignore_index=True)


def pack_rows(flows: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact flows for embedding in the viewer.

    Args:
        flows: Output of :func:`read_flows`.

    Returns:
        One short-keyed record per flow: ``y`` year, ``o`` origin, ``d`` destination,
        ``t`` technology and ``v`` volume (Mt, four decimals). Flows that round to zero
        are dropped.
    """
    rows = []
    for row in flows.to_dict("records"):
        volume = round(float(row["volume_mt"]), 4)
        if volume > 0:
            rows.append(
                {
                    "y": int(row["year"]),
                    "o": row["origin"],
                    "d": row["destination"],
                    "t": row["technology"],
                    "v": volume,
                }
            )
    return rows
