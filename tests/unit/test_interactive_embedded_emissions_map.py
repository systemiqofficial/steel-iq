"""Tests for the embedded emissions map (steelo.utilities.interactive.embedded_emissions_map)."""

from pathlib import Path

import pandas as pd
import pytest

from steelo.utilities.interactive import embedded_emissions_map, trade_matrix

from test_interactive_supply_chain import DC, FG, SUPPLIER, edge
from test_interactive_trade_matrix import HEADER, location, mine_location, write_allocations

BOUNDARY = "rs-inspired"
DIRECT, INDIRECT = f"emissions_{BOUNDARY}_direct_ghg", f"emissions_{BOUNDARY}_indirect_ghg"


def chain_rows() -> list[list]:
    """A Chinese BF feeding a Chinese BOF (3/4) and a German EAF (1/4); a Brazilian EAF has no recorded emissions."""
    return [
        edge("io_high", SUPPLIER, "sup_a", mine_location("Australia"), "N/A", FG, "P1_0", location("CHN"), 6e6),
        edge("hot_metal", FG, "P1_0", location("CHN"), "BF", FG, "P1_1", location("CHN"), 3e6),
        edge("pig_iron", FG, "P1_0", location("CHN"), "BF", FG, "P2_0", location("DEU"), 1e6),
        edge("steel", FG, "P1_1", location("CHN"), "BOF", DC, "India", location("IND"), 2e6),
        edge("steel", FG, "P1_1", location("CHN"), "BOF", DC, "China", location("CHN"), 2e6),
        edge("steel", FG, "P2_0", location("DEU"), "EAF", DC, "Germany", location("DEU"), 1e6),
        edge("steel", FG, "P9_0", location("BRA"), "EAF", DC, "Brazil", location("BRA"), 5e5),
    ]


def post_processed_table() -> pd.DataFrame:
    """2025 emissions of the chain's furnace groups; the BF is repeated per feedstock as the real table does."""
    rows = [
        (2025, "P1_0", "io_high", 2e6, 2e5),
        (2025, "P1_0", "io_mid", 2e6, 2e5),
        (2025, "P1_1", "hot_metal", 0.0, 1e5),
        (2025, "P2_0", "pig_iron", 4e4, None),
    ]
    return pd.DataFrame(rows, columns=["year", "furnace_group_id", "feedstock", DIRECT, INDIRECT])


def allocations_table(rows: list[list]) -> pd.DataFrame:
    """The rows as the viewer reads one year's allocation file."""
    return pd.DataFrame(rows, columns=HEADER)[embedded_emissions_map.COLUMNS]


def test_furnace_group_emissions_keeps_one_row_per_furnace_group() -> None:
    """The per-feedstock repeats collapse to one row, the boundary picks the columns and gaps count as zero."""
    by_year = embedded_emissions_map.furnace_group_emissions(post_processed_table(), BOUNDARY)

    assert list(by_year) == [2025]
    table = by_year[2025]
    assert list(table.index) == ["P1_0", "P1_1", "P2_0"]
    assert table.loc["P1_0", "direct"] == 2e6
    assert table.loc["P2_0", "indirect"] == 0.0


def test_furnace_group_emissions_rejects_a_boundary_without_columns() -> None:
    """A boundary the table does not carry is an error naming the missing columns."""
    with pytest.raises(ValueError, match="emissions_worldsteel_direct_ghg"):
        embedded_emissions_map.furnace_group_emissions(post_processed_table(), "worldsteel")


def test_embedded_matrix_carries_iron_emissions_to_the_consuming_country() -> None:
    """The BF's emissions follow its iron into both steel plants and on to the demand centres, pro rata."""
    emissions = embedded_emissions_map.furnace_group_emissions(post_processed_table(), BOUNDARY)[2025]

    matrices = embedded_emissions_map.embedded_matrix(allocations_table(chain_rows()), emissions)

    assert matrices["direct"] == {
        ("CHN", "IND"): 750_000.0,
        ("CHN", "CHN"): 750_000.0,
        ("CHN", "DEU"): 500_000.0,
        ("DEU", "DEU"): 40_000.0,
    }
    assert matrices["indirect"] == {("CHN", "IND"): 125_000.0, ("CHN", "CHN"): 125_000.0, ("CHN", "DEU"): 50_000.0}
    for scope in embedded_emissions_map.SCOPES:
        assert sum(matrices[scope].values()) == pytest.approx(emissions[scope].sum())


def test_embedded_matrix_rejects_a_cycle_between_furnace_groups() -> None:
    """Two furnace groups shipping to each other cannot be ordered, so the matrix is refused."""
    rows = chain_rows() + [edge("pig_iron", FG, "P1_1", location("CHN"), "BOF", FG, "P1_0", location("CHN"), 1e5)]
    emissions = embedded_emissions_map.furnace_group_emissions(post_processed_table(), BOUNDARY)[2025]

    with pytest.raises(ValueError, match="cycle"):
        embedded_emissions_map.embedded_matrix(allocations_table(rows), emissions)


def test_steel_flows_sums_deliveries_by_producer_and_consumer() -> None:
    """Only steel reaching demand centres counts, keyed by producing and consuming country."""
    assert embedded_emissions_map.steel_flows(allocations_table(chain_rows())) == {
        ("BRA", "BRA"): 5e5,
        ("CHN", "CHN"): 2e6,
        ("CHN", "IND"): 2e6,
        ("DEU", "DEU"): 1e6,
    }


def test_pack_years_packs_kilotonnes_against_one_country_table(tmp_path: Path) -> None:
    """Matrices are packed in kt against one country table; a year without post-processed rows is left out."""
    tm_dir = tmp_path / "TM"
    write_allocations(tm_dir, 2025, chain_rows())
    write_allocations(tm_dir, 2026, chain_rows())

    payload = embedded_emissions_map.pack_years(
        post_processed_table(),
        trade_matrix.allocation_files(tm_dir),
        BOUNDARY,
    )

    assert payload["years"] == [2025]
    assert payload["scopes"] == ["direct", "indirect"]
    assert payload["countries"] == ["CHN", "DEU", "IND", "BRA"]
    assert payload["m"][2025]["direct"] == {"s": [0, 0, 0, 1], "d": [0, 1, 2, 1], "t": [750, 500, 750, 40]}
    assert payload["m"][2025]["indirect"] == {"s": [0, 0, 0], "d": [0, 1, 2], "t": [125, 50, 125]}
    assert payload["steel"][2025] == {"s": [3, 0, 0, 1], "d": [3, 0, 2, 1], "t": [500, 2000, 2000, 1000]}


def test_country_features_key_polygons_by_the_model_iso3() -> None:
    """Natural Earth's own code for South Sudan is replaced by the ISO3 the model uses."""
    codes = {feature["properties"]["iso3"] for feature in embedded_emissions_map.country_features()["features"]}

    assert {"CHN", "IND", "SSD"} <= codes
    assert "SDS" not in codes
