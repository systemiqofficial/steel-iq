"""Row packing for the greenfield status viewer (``greenfield_status.html``).

The viewer is the static ``greenfield/<product>_greenfield_status.png`` charts
made interactive: greenfield (GEO-origin) furnace groups per year stacked by
lifecycle status or by technology, as plant count, capacity or production,
showing either the stock in each status or the flow of groups entering it,
with a status filter and the shell's geography and technology filters.
"""

from typing import Any

import pandas as pd

# Lifecycle order doubles as the stacking order (bottom to top); the colours are
# the static status chart's, so both renderings read the same.
STATUS_COLOURS = {
    "considered": "#a6cee3",
    "announced": "#1f78b4",
    "construction": "#f1dc1e",
    "construction switching technology": "#c9a227",
    "operating": "#24851b",
    "operating switching technology": "#74c476",
    "operating pre-retirement": "#084302",
    "discarded": "#e31a1c",
    "closed": "#882626",
}

REQUIRED_COLUMNS = ["year", "furnace_group_id", "product", "technology", "status", "geo_key", "capacity", "production"]


def aggregate_status(status_timeseries: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the per-group status snapshots per year, geography, technology, product and status.

    Each input row is one greenfield furnace group's snapshot in one year
    (``data/greenfield_status_timeseries.csv``). Besides the stock in each
    status, the aggregate carries the flow into it: a group counts as entering
    a status in its first snapshot year and in any year its status differs
    from the previous year's.

    Args:
        status_timeseries: The greenfield status timeseries table.

    Returns:
        Columns ``year, geo, technology, product, status, n, capacity_mt,
        production_mt, n_new, capacity_new_mt`` — group count, capacity and
        production (Mt) in the status, plus the count and capacity that entered
        it that year.

    Raises:
        ValueError: If the table lacks a required column.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in status_timeseries.columns]
    if missing:
        raise ValueError(f"The greenfield status timeseries has no {', '.join(missing)} column")
    frame = status_timeseries.sort_values(["furnace_group_id", "year"]).rename(columns={"geo_key": "geo"})
    previous_status = frame.groupby("furnace_group_id")["status"].shift()
    entered = previous_status.isna() | (previous_status != frame["status"])
    frame = frame.assign(entered=entered, capacity_entered=frame["capacity"].where(entered, 0.0))
    grouped = frame.groupby(["year", "geo", "technology", "product", "status"], dropna=False)
    aggregated = grouped[["capacity", "production", "capacity_entered"]].sum() / 1e6
    aggregated.columns = ["capacity_mt", "production_mt", "capacity_new_mt"]
    aggregated["n"] = grouped.size()
    aggregated["n_new"] = grouped["entered"].sum().astype(int)
    return aggregated[["n", "capacity_mt", "production_mt", "n_new", "capacity_new_mt"]].reset_index()


def pack_rows(aggregated: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact aggregated rows for embedding in the viewer.

    Args:
        aggregated: Output of :func:`aggregate_status`.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` geo, ``t`` technology,
        ``p`` product, ``s`` status, ``n`` groups in the status, ``cap`` capacity
        and ``pr`` production (Mt, four decimals), ``nn`` groups entering the
        status that year and ``ncap`` their capacity.
    """
    return [
        {
            "y": int(row["year"]),
            "g": row["geo"],
            "t": row["technology"],
            "p": row["product"],
            "s": row["status"],
            "n": int(row["n"]),
            "cap": round(float(row["capacity_mt"]), 4),
            "pr": round(float(row["production_mt"]), 4),
            "nn": int(row["n_new"]),
            "ncap": round(float(row["capacity_new_mt"]), 4),
        }
        for row in aggregated.to_dict("records")
    ]
