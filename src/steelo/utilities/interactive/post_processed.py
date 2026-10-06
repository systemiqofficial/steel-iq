"""Aggregation of the post-processed furnace-group table, shared by the viewers built from it.

The table carries one row per furnace group, year *and* feedstock (the
material-allocation breakdown), so furnace-group quantities such as capacity,
production and emissions repeat across those rows; the aggregation keeps a
single row per furnace group and year before summing.
"""

import pandas as pd

# Geographic grain of a row: the sub-national geo_key when the table carries one, else the country.
GEO_COLUMNS = ("geo_key", "iso3")
AGGREGATION_KEYS = ["year", "geo", "technology", "product"]


def aggregate_furnace_groups(post_processed: pd.DataFrame, value_columns: dict[str, str]) -> pd.DataFrame:
    """Sum furnace-group quantities per year, geography, technology and product.

    Args:
        post_processed: The post-processed furnace-group table.
        value_columns: ``{source column: output column}`` for the tonne quantities to sum.

    Returns:
        Columns ``year, geo, technology, product, n`` (furnace groups) and one output
        column per entry of ``value_columns``, in Mt. Each furnace group counts once per
        year regardless of how many feedstock rows repeat it.
    """
    geo_column = next(column for column in GEO_COLUMNS if column in post_processed.columns)
    per_fg = post_processed.drop_duplicates(subset=["furnace_group_id", "year"]).rename(columns={geo_column: "geo"})
    grouped = per_fg.groupby(AGGREGATION_KEYS, dropna=False)
    aggregated = grouped[list(value_columns)].sum().rename(columns=value_columns) / 1e6
    aggregated["n"] = grouped.size()
    return aggregated.reset_index()
