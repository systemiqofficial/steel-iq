"""Row packing for the emissions viewer (``emissions.html``).

The viewer offers the same five scope views as the static emissions PNGs,
every emissions boundary found in the post-processed table, stacking by
technology or region, and the shell's geography and technology filters.
"""

from typing import Any

import pandas as pd

from .post_processed import aggregate_furnace_groups

EMISSIONS_PREFIX = "emissions_"
# Longest first so the suffix match on ``direct_with_biomass_ghg`` never hits ``direct_ghg``.
SCOPES = ("direct_with_biomass_ghg", "direct_ghg", "indirect_ghg")


def emission_boundaries(columns: list[str]) -> list[str]:
    """Boundaries present as ``emissions_<boundary>_<scope>`` columns, in first-seen order.

    Args:
        columns: Column names of the post-processed table.

    Returns:
        Boundary names such as ``rs-inspired`` or ``worldsteel_opt_credits``. A boundary
        counts as present as soon as any of its scope columns exists.
    """
    boundaries: list[str] = []
    for column in columns:
        if not column.startswith(EMISSIONS_PREFIX):
            continue
        for scope in SCOPES:
            suffix = f"_{scope}"
            if column.endswith(suffix):
                boundary = column[len(EMISSIONS_PREFIX) : -len(suffix)]
                if boundary not in boundaries:
                    boundaries.append(boundary)
                break
    return boundaries


def aggregate_emissions(post_processed: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """Sum emissions and production per year, geography, technology and product.

    Args:
        post_processed: The post-processed furnace-group table.

    Returns:
        ``(emission_keys, aggregated)`` where ``emission_keys`` are ``"<boundary>|<scope>"``
        strings naming the summed emissions columns in order, and ``aggregated`` has the
        columns ``year, geo, technology, product, n``, ``production_mt`` and one column per
        emission key, all in Mt (see :func:`~.post_processed.aggregate_furnace_groups`).

    Raises:
        ValueError: If the table carries no emissions columns.
    """
    boundaries = emission_boundaries(list(post_processed.columns))
    if not boundaries:
        raise ValueError("The post-processed table has no emissions_<boundary>_<scope> columns")

    emission_keys: list[str] = []
    value_columns: dict[str, str] = {}
    for boundary in boundaries:
        for scope in SCOPES:
            column = f"{EMISSIONS_PREFIX}{boundary}_{scope}"
            if column in post_processed.columns:
                key = f"{boundary}|{scope}"
                emission_keys.append(key)
                value_columns[column] = key
    value_columns["production"] = "production_mt"
    return emission_keys, aggregate_furnace_groups(post_processed, value_columns)


def pack_rows(aggregated: pd.DataFrame, emission_keys: list[str]) -> list[dict[str, Any]]:
    """Compact aggregated rows for embedding in the viewer.

    Args:
        aggregated: Output of :func:`aggregate_emissions`.
        emission_keys: The emission key order, matching the ``e`` list of every row.

    Returns:
        One short-keyed record per row: ``y`` year, ``g`` geo, ``t`` technology, ``p``
        product, ``n`` furnace groups, ``pr`` production (Mt) and ``e`` emissions (Mt,
        four decimals) in ``emission_keys`` order.
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
            "pr": mt(row["production_mt"]),
            "e": [mt(row[key]) for key in emission_keys],
        }
        for row in aggregated.to_dict("records")
    ]
