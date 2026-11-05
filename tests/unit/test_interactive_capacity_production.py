"""Tests for the capacity and production viewer's row packing (steelo.utilities.interactive.capacity_production)."""

import pandas as pd
import pytest

from steelo.domain.models import DemandCenter, Location
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


def test_steel_demand_rows_sums_centres_by_country_within_years() -> None:
    """Demand centres sum per country; years outside the table's range are excluded."""
    beijing = Location(lat=1.0, lon=2.0, country="China", region="R", iso3="CHN")
    centres = [
        DemandCenter("China_1", beijing, {2025: 1_000_000, 2100: 9e9}),
        DemandCenter("China_2", beijing, {2025: 500_000}),
    ]

    demand = capacity_production.steel_demand_rows(centres, {2025})

    assert [(r.year, r.geo, r.volume_mt) for r in demand.itertuples()] == [(2025, "CHN", 1.5)]


def test_pack_demand_drops_zero_rows() -> None:
    """Demand rows carry short keys; rows that round to zero are dropped."""
    steel_demand = pd.DataFrame([(2025, "CHN", 2.0), (2025, "ABW", 0.00001)], columns=["year", "geo", "volume_mt"])
    assert capacity_production.pack_demand(steel_demand) == [{"y": 2025, "g": "CHN", "v": 2.0}]
