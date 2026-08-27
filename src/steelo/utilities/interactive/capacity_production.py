"""Row packing for the capacity and production viewer (``capacity_and_production.html``).

The viewer shows steel or iron capacity, production, or both on one chart
(solid production over hatched capacity), stacked by technology or region,
with the shell's geography and technology filters.
"""

from typing import Any

import pandas as pd

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
