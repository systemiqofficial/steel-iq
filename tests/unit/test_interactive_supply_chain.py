"""Tests for the supply-chain viewer's edge packing (steelo.utilities.interactive.supply_chain)."""

from pathlib import Path

import pytest

from steelo.utilities.interactive import supply_chain, trade_matrix

from test_interactive_trade_matrix import HEADER, location, mine_location, write_allocations

FG, DC, SUPPLIER = "Plant-FurnaceGroup", "DemandCenter", "Supplier"


def edge(
    commodity: str,
    source_type: str,
    source_id: str,
    source: str,
    tech: str,
    destination_type: str,
    destination_id: str,
    destination: str,
    volume: float,
) -> list:
    """One allocation row with explicit endpoint types and ids."""
    return [
        commodity,
        source_type,
        source_id,
        source,
        1e6,
        tech,
        destination_type,
        destination_id,
        destination,
        volume,
        0,
        0,
        0,
    ]


def chain_tm_dir(tmp_path: Path) -> Path:
    """A two-tier chain in 2025: mine → BF → BOF → demand centre, plus scrap → EAF; 2026 header-only."""
    tm_dir = tmp_path / "TM"
    write_allocations(
        tm_dir,
        2025,
        [
            edge("steel", FG, "P1_1", location("CHN"), "BOF", DC, "India", location("IND"), 2e6),
            edge("steel", FG, "P2_0", location("DEU"), "EAF", DC, "Germany", location("DEU"), 1e6),
            edge("hot_metal", FG, "P1_0", location("CHN"), "BF", FG, "P1_1", location("CHN"), 2.2e6),
            edge("io_high", SUPPLIER, "sup_a", mine_location("Australia"), "N/A", FG, "P1_0", location("CHN"), 3e6),
            edge("scrap", SUPPLIER, "Germany_scrap", location("DEU"), "N/A", FG, "P2_0", location("DEU"), 1.1e6),
            edge("bio_pci", SUPPLIER, "bio_1", location("DEU"), "N/A", FG, "P1_0", location("CHN"), 5e5),
            edge("steel", FG, "P3_0", location("BRA"), "EAF", DC, "Brazil", location("BRA"), 0.2),
        ],
    )
    write_allocations(tm_dir, 2026, [])
    return tm_dir


def test_pack_edges_keeps_every_metal_edge_with_shared_endpoint_tables(tmp_path: Path) -> None:
    """Edges reference one endpoint table; kinds, geographies and technologies come from the rows."""
    packed = supply_chain.pack_edges(trade_matrix.allocation_files(chain_tm_dir(tmp_path)))

    assert packed["kinds"] == ["furnace_group", "demand_centre", "scrap_supplier", "mine"]
    assert list(packed["edges"]) == [2025]  # the header-only year contributes nothing
    endpoints = {row[2]: row for row in packed["endpoints"]}
    assert endpoints["P1_1"] == [0, "CHN", "P1_1"]
    assert endpoints["India"] == [1, "IND", "India"]
    assert endpoints["Germany_scrap"] == [2, "DEU", "Germany_scrap"]
    assert endpoints["sup_a"] == [3, "Australia", "sup_a"]
    assert "bio_1" not in endpoints  # reductants are not metal trade

    year = packed["edges"][2025]
    ids = [row[2] for row in packed["endpoints"]]
    edges = {
        (packed["commodities"][c], ids[s], ids[d]): (packed["techs"][t], v)
        for c, s, d, t, v in zip(year["c"], year["s"], year["d"], year["t"], year["v"])
    }
    assert edges[("steel", "P1_1", "India")] == ("BOF", 2_000_000)
    assert edges[("hot_metal", "P1_0", "P1_1")] == ("BF", 2_200_000)
    assert edges[("io_high", "sup_a", "P1_0")] == ("N/A", 3_000_000)
    assert edges[("scrap", "Germany_scrap", "P2_0")] == ("N/A", 1_100_000)
    assert ("steel", "P3_0", "Brazil") not in edges  # rounds to zero tonnes
    assert len(edges) == 5


def test_pack_edges_rejects_unmapped_commodity(tmp_path: Path) -> None:
    """A commodity in neither the product map nor the exclusions fails loudly."""
    tm_dir = tmp_path / "TM"
    write_allocations(
        tm_dir,
        2025,
        [edge("unobtainium", SUPPLIER, "s", location("DEU"), "N/A", FG, "P1_0", location("DEU"), 1.0)],
    )

    with pytest.raises(ValueError, match="unobtainium"):
        supply_chain.pack_edges(trade_matrix.allocation_files(tm_dir))


def test_pack_edges_rejects_mine_without_label(tmp_path: Path) -> None:
    """A mine whose location carries no country label cannot be placed in the chain."""
    tm_dir = tmp_path / "TM"
    bad_mine = "Location(lat=1.0, lon=2.0, country='', region='X', iso3='', distance_to_other_iso3=None, geo_unit=None)"
    write_allocations(
        tm_dir,
        2025,
        [edge("io_low", SUPPLIER, "s", bad_mine, "N/A", FG, "P1_0", location("DEU"), 1.0)],
    )

    with pytest.raises(ValueError, match="country label"):
        supply_chain.pack_edges(trade_matrix.allocation_files(tm_dir))


def test_header_is_the_export_column_set() -> None:
    """The packer reads a subset of the export's columns."""
    assert set(supply_chain.COLUMNS) <= set(HEADER)
