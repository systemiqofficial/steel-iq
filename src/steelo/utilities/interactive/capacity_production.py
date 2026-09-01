"""Row packing for the capacity and production viewer (``capacity_and_production.html``).

The viewer shows steel or iron capacity, production, or both on one chart
(solid production over hatched capacity), stacked by technology or region,
with the shell's geography and technology filters. An optional overlay
compares the steel chart against the prepared demand centres' steel demand
— the demand the trade LP is asked to serve — which is country-level.
"""

from typing import Any, Iterable

import pandas as pd

from steelo.domain.models import DemandCenter

from .post_processed import aggregate_furnace_groups

VALUE_COLUMNS = {"capacity": "capacity_mt", "production": "production_mt"}


def aggregate_capacity_production(post_processed: pd.DataFrame) -> pd.DataFrame:
    """Sum capacity and production per year, geography, technology and product.

    Args:
        post_processed: The post-processed furnace-group table.

    Returns:
        Columns ``year, geo, technology, product, n, capacity_mt, production_mt``
        (see :func:`~.post_processed.aggregate_furnace_groups`).

    Raises:
        ValueError: If the table lacks the capacity or production column.
    """
    missing = [column for column in VALUE_COLUMNS if column not in post_processed.columns]
    if missing:
        raise ValueError(f"The post-processed table has no {', '.join(missing)} column")
    return aggregate_furnace_groups(post_processed, VALUE_COLUMNS)


def pack_rows(aggregated: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact aggregated rows for embedding in the viewer.

    Args:
        aggregated: Output of :func:`aggregate_capacity_production`.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` geo, ``t`` technology, ``p``
        product, ``n`` furnace groups, ``cap`` capacity and ``pr`` production (Mt, four
        decimals).
    """

    def mt(value: Any) -> float:
        return 0.0 if pd.isna(value) else round(float(value), 4)

    return [
        {
            "y": int(row["year"]),
            "g": row["geo"],
            "t": row["technology"],
            "p": row["product"],
            "n": int(row["n"]),
            "cap": mt(row["capacity_mt"]),
            "pr": mt(row["production_mt"]),
        }
        for row in aggregated.to_dict("records")
    ]


def steel_demand_rows(demand_centers: Iterable[DemandCenter], years: set[int]) -> pd.DataFrame:
    """Steel demand per year and country, from the prepared demand centres.

    The demand centres carry the demand the trade LP is asked to serve, so the sum
    matches the engine's steel demand even in years where the LP leaves centres
    unserved (which drop out of the allocation files).

    Args:
        demand_centers: The prepared demand centres (``fixtures/demand_centers.json``).
        years: The years to keep (the table's years).

    Returns:
        Columns ``year, geo, volume_mt``. Demand centres carry no sub-national
        geography, so ``geo`` is always a country.
    """
    rows = [
        (int(year), centre.center_of_gravity.iso3, float(volume) / 1e6)
        for centre in demand_centers
        for year, volume in centre.demand_by_year.items()
        if int(year) in years
    ]
    demand = pd.DataFrame(rows, columns=["year", "geo", "volume_mt"])
    return demand.groupby(["year", "geo"], as_index=False)[["volume_mt"]].sum()


def pack_demand(steel_demand: pd.DataFrame) -> list[dict[str, Any]]:
    """Compact steel-demand rows for embedding in the viewer.

    Args:
        steel_demand: Output of :func:`steel_demand_rows`.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` country and ``v`` demand
        (Mt, four decimals). Rows that round to zero are dropped.
    """
    rows = []
    for row in steel_demand.to_dict("records"):
        volume = round(float(row["volume_mt"]), 4)
        if volume > 0:
            rows.append({"y": int(row["year"]), "g": row["geo"], "v": volume})
    return rows
