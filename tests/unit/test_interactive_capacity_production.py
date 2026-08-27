"""Tests for the capacity and production viewer's row packing (steelo.utilities.interactive.capacity_production)."""

import pandas as pd
import pytest

from steelo.utilities.interactive import capacity_production


def sample_post_processed() -> pd.DataFrame:
    """A small post-processed table: one furnace group repeated over two feedstock rows."""
    columns = ["year", "iso3", "geo_key", "furnace_group_id", "technology", "product", "capacity", "production"]
    rows = [
        # Same furnace group and year, two feedstock rows → counted once
        [2025, "CHN", "CHN:CN-HE", "P1_0", "BF", "iron", 3_000_000.0, 2_000_000.0],
        [2025, "CHN", "CHN:CN-HE", "P1_0", "BF", "iron", 3_000_000.0, 2_000_000.0],
        [2025, "DEU", "DEU", "P2_0", "EAF", "steel", 1_200_000.0, 1_000_000.0],
        [2026, "DEU", "DEU", "P2_0", "EAF", "steel", 1_200_000.0, 1_100_000.0],
    ]
    return pd.DataFrame(rows, columns=columns)


def test_aggregate_counts_each_furnace_group_year_once() -> None:
    """Feedstock rows repeat a furnace group's capacity and production; the aggregate must not double them."""
    aggregated = capacity_production.aggregate_capacity_production(sample_post_processed())

    chn = aggregated[(aggregated["year"] == 2025) & (aggregated["geo"] == "CHN:CN-HE")].iloc[0]
    assert chn["capacity_mt"] == pytest.approx(3.0)
    assert chn["production_mt"] == pytest.approx(2.0)
    assert chn["n"] == 1
    assert list(aggregated.columns) == ["year", "geo", "technology", "product", "capacity_mt", "production_mt", "n"]


def test_aggregate_rejects_table_without_capacity() -> None:
    """A table missing the quantity columns fails loudly rather than producing an empty chart."""
    with pytest.raises(ValueError, match="capacity"):
        capacity_production.aggregate_capacity_production(sample_post_processed().drop(columns=["capacity"]))


def test_pack_rows_compacts_aggregates() -> None:
    """Rows carry short keys with capacity and production in Mt to four decimals."""
    packed = capacity_production.pack_rows(capacity_production.aggregate_capacity_production(sample_post_processed()))

    deu_2026 = next(row for row in packed if row["g"] == "DEU" and row["y"] == 2026)
    assert deu_2026 == {"y": 2026, "g": "DEU", "t": "EAF", "p": "steel", "n": 1, "cap": 1.2, "pr": 1.1}
