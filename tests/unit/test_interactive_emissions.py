"""Tests for the emissions viewer's row packing (steelo.utilities.interactive.emissions)."""

import pandas as pd
import pytest

from steelo.utilities.interactive import emissions

BOUNDARY = "worldsteel_opt_credits"


def sample_post_processed() -> pd.DataFrame:
    """A small post-processed table: one furnace group repeated over two feedstock rows."""
    rows = [
        # Same furnace group and year, two feedstock rows → emissions must count once
        [2025, "China", "CHN", "CHN:CN-HE", "P1_0", "BF", "iron", 2_000_000.0, "io_high", 5_000_000.0, 1_000_000.0],
        [2025, "China", "CHN", "CHN:CN-HE", "P1_0", "BF", "iron", 2_000_000.0, "io_mid", 5_000_000.0, 1_000_000.0],
        [2025, "Europe", "DEU", "DEU", "P2_0", "EAF", "steel", 1_000_000.0, "scrap", 100_000.0, 300_000.0],
        [2026, "Europe", "DEU", "DEU", "P2_0", "EAF", "steel", 1_100_000.0, "scrap", 110_000.0, 330_000.0],
    ]
    columns = [
        "year",
        "region",
        "iso3",
        "geo_key",
        "furnace_group_id",
        "technology",
        "product",
        "production",
        "feedstock",
        f"emissions_{BOUNDARY}_direct_ghg",
        f"emissions_{BOUNDARY}_indirect_ghg",
    ]
    return pd.DataFrame(rows, columns=columns)


def test_emission_boundaries_parsed_from_scope_suffixes() -> None:
    """Boundary names keep their own underscores and hyphens; scopes are stripped."""
    columns = [
        "year",
        "emissions_rs-inspired_direct_ghg",
        "emissions_rs-inspired_direct_with_biomass_ghg",
        "emissions_worldsteel_no_opt_credits_indirect_ghg",
        "emissions_rs-inspired_indirect_ghg",
    ]

    assert emissions.emission_boundaries(columns) == ["rs-inspired", "worldsteel_no_opt_credits"]


def test_aggregate_counts_each_furnace_group_year_once() -> None:
    """Feedstock rows repeat a furnace group's emissions; the aggregate must not double them."""
    keys, aggregated = emissions.aggregate_emissions(sample_post_processed())

    assert keys == [f"{BOUNDARY}|direct_ghg", f"{BOUNDARY}|indirect_ghg"]
    chn = aggregated[(aggregated["year"] == 2025) & (aggregated["geo"] == "CHN:CN-HE")].iloc[0]
    assert chn[f"{BOUNDARY}|direct_ghg"] == pytest.approx(5.0)
    assert chn["production_mt"] == pytest.approx(2.0)
    assert chn["n"] == 1
    assert set(aggregated["geo"]) == {"CHN:CN-HE", "DEU"}


def test_aggregate_falls_back_to_iso3_without_geo_key() -> None:
    """Tables from runs that predate the geo_key column are keyed by country."""
    keys, aggregated = emissions.aggregate_emissions(sample_post_processed().drop(columns=["geo_key"]))

    assert set(aggregated["geo"]) == {"CHN", "DEU"}


def test_aggregate_rejects_table_without_emissions() -> None:
    """A table with no emissions columns fails loudly rather than producing an empty chart."""
    with pytest.raises(ValueError, match="no emissions"):
        emissions.aggregate_emissions(pd.DataFrame({"year": [2025], "furnace_group_id": ["P1_0"]}))


def test_pack_rows_compacts_aggregates() -> None:
    """Rows carry short keys and emissions in emission-key order, in Mt to four decimals."""
    keys, aggregated = emissions.aggregate_emissions(sample_post_processed())
    packed = emissions.pack_rows(aggregated, keys)

    deu_2026 = next(row for row in packed if row["g"] == "DEU" and row["y"] == 2026)
    assert deu_2026 == {"y": 2026, "g": "DEU", "t": "EAF", "p": "steel", "n": 1, "pr": 1.1, "e": [0.11, 0.33]}
