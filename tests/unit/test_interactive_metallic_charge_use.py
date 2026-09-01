"""Tests for the metallic-charge viewer's row packing (steelo.utilities.interactive.metallic_charge_use)."""

import pandas as pd
import pytest

from steelo.domain.models import CountryMapping, Location, PrimaryFeedstock, Supplier
from steelo.utilities.interactive import metallic_charge_use


def sample_post_processed() -> pd.DataFrame:
    """A BF charging two ore grades at shares, an EAF on scrap + pig iron, and a BOF on hot metal."""
    columns = ["year", "geo_key", "furnace_group_id", "technology", "product", "feedstock", "demand"]
    rows = [
        # Same furnace group and year, two charges at different shares → each counts its own demand
        [2025, "CHN:CN-HE", "P1_0", "BF", "iron", "io_low", 1_500_000.0],
        [2025, "CHN:CN-HE", "P1_0", "BF", "iron", "io_high", 700_000.0],
        # Procurement rows are no technology's metallic charge → dropped silently
        [2025, "CHN:CN-HE", "P1_0", "BF", "iron", "bio_pci", 50_000.0],
        [2025, "DEU", "P2_0", "EAF", "steel", "scrap", 600_000.0],
        [2025, "DEU", "P2_0", "EAF", "steel", "pig_iron", 500_000.0],
        [2025, "DEU", "P3_0", "BOF", "steel", "hot_metal", 1_000_000.0],
        # Zero demand and missing feedstock rows are excluded
        [2025, "DEU", "P2_0", "EAF", "steel", "dri_high", 0.0],
        [2025, "DEU", "P3_0", "BOF", "steel", None, None],
    ]
    return pd.DataFrame(rows, columns=columns)


def primary_feedstock(technology: str, metallic_charge: str, reductant: str = "") -> PrimaryFeedstock:
    """A minimal Bill of Materials entry naming a metallic charge."""
    return PrimaryFeedstock(metallic_charge=metallic_charge, reductant=reductant, technology=technology)


def sample_bom() -> list[PrimaryFeedstock]:
    """BOM entries matching the sample table; technologies lower-cased as the repository loads them."""
    return [
        primary_feedstock("bf", "io_low", "coke+pci"),
        primary_feedstock("bf", "io_high", "coke+pci"),
        primary_feedstock("eaf", "scrap"),
        primary_feedstock("eaf", "pig_iron"),
        primary_feedstock("eaf", "dri_high"),
        primary_feedstock("bof", "hot_metal"),
    ]


def sample_country_mappings() -> list[CountryMapping]:
    """Two countries, for resolving suppliers placed by country name."""
    common = {"irena_name": "", "ssp_region": "", "tiam_ucl_region": ""}
    return [
        CountryMapping(country="Germany", iso2="DE", iso3="DEU", region_for_outputs="Europe", **common),
        CountryMapping(country="China", iso2="CN", iso3="CHN", region_for_outputs="China", **common),
    ]


def test_aggregate_sums_each_charge_separately() -> None:
    """A furnace group's charges keep their own shares; procurement rows drop out silently."""
    aggregated = metallic_charge_use.aggregate_charge_use(sample_post_processed(), sample_bom())

    rows = {(r.technology, r.charge): (r.product, r.use_mt, r.n) for r in aggregated.itertuples()}
    assert rows == {
        ("BF", "io_low"): ("iron", 1.5, 1),
        ("BF", "io_high"): ("iron", 0.7, 1),
        ("EAF", "scrap"): ("steel", 0.6, 1),
        ("EAF", "pig_iron"): ("steel", 0.5, 1),
        ("BOF", "hot_metal"): ("steel", 1.0, 1),
    }


def test_aggregate_counts_duplicate_feedstock_rows_once() -> None:
    """A repeated (furnace group, year, feedstock) row must not double its demand."""
    table = sample_post_processed()
    table = pd.concat([table, table.iloc[[0]]], ignore_index=True)

    aggregated = metallic_charge_use.aggregate_charge_use(table, sample_bom())

    io_low = aggregated[aggregated["charge"] == "io_low"].iloc[0]
    assert io_low["use_mt"] == pytest.approx(1.5)
    assert io_low["n"] == 1


def test_aggregate_warns_on_charge_of_another_technology(caplog) -> None:
    """A feedstock that is a charge elsewhere but not for its own technology is warned about and omitted."""
    table = sample_post_processed()
    table.loc[len(table)] = [2025, "DEU", "P4_0", "BF", "iron", "scrap", 400_000.0]

    with caplog.at_level("WARNING", logger="steelo.utilities.interactive.metallic_charge_use"):
        aggregated = metallic_charge_use.aggregate_charge_use(table, sample_bom())

    assert "BF_scrap" in caplog.text
    assert not ((aggregated["technology"] == "BF") & (aggregated["charge"] == "scrap")).any()


def test_aggregate_rejects_table_without_required_columns() -> None:
    """A table missing the feedstock column fails loudly rather than producing an empty chart."""
    with pytest.raises(ValueError, match="feedstock"):
        metallic_charge_use.aggregate_charge_use(sample_post_processed().drop(columns=["feedstock"]), sample_bom())


def test_aggregate_rejects_bom_without_metallic_charges() -> None:
    """A Bill of Materials naming no metallic charge cannot identify any row."""
    with pytest.raises(ValueError, match="no metallic charge"):
        metallic_charge_use.aggregate_charge_use(sample_post_processed(), [])


def test_aggregate_falls_back_to_iso3_without_geo_key() -> None:
    """Tables from runs that predate the geo_key column are keyed by country."""
    table = sample_post_processed().rename(columns={"geo_key": "iso3"})
    aggregated = metallic_charge_use.aggregate_charge_use(table, sample_bom())

    assert set(aggregated["geo"]) == {"CHN:CN-HE", "DEU"}


def test_scrap_supply_rows_sums_scrap_by_country_within_years() -> None:
    """Scrap sub-centres sum per country, other commodities and years are excluded, names resolve to ISO3."""
    berlin = Location(lat=1.0, lon=2.0, country="Germany", region="R", iso3="DEU")
    unplaced = Location(lat=1.0, lon=2.0, country="Germany", region="R", iso3="")
    suppliers = [
        Supplier("DEU_scrap_1", berlin, "scrap", {2025: 1_000_000, 2030: 9e9}, {}),
        Supplier("DEU_scrap_2", unplaced, "scrap", {2025: 500_000}, {}),
        Supplier("mine", berlin, "io_mid", {2025: 400_000}, {}),
    ]

    supply = metallic_charge_use.scrap_supply_rows(suppliers, sample_country_mappings(), {2025})

    assert [(r.year, r.geo, r.supply_mt) for r in supply.itertuples()] == [(2025, "DEU", 1.5)]


def test_pack_rows_compacts_aggregates() -> None:
    """Rows carry short keys with the charge tonnage to four decimals."""
    aggregated = metallic_charge_use.aggregate_charge_use(sample_post_processed(), sample_bom())
    packed = metallic_charge_use.pack_rows(aggregated)

    assert {
        "y": 2025,
        "g": "CHN:CN-HE",
        "t": "BF",
        "p": "iron",
        "c": "io_low",
        "n": 1,
        "v": 1.5,
    } in packed


def test_pack_supply_drops_zero_rows() -> None:
    """Supply rows carry short keys; rows that round to zero are dropped."""
    supply = pd.DataFrame([(2025, "DEU", 1.5), (2025, "CHN", 0.00001)], columns=["year", "geo", "supply_mt"])
    assert metallic_charge_use.pack_supply(supply) == [{"y": 2025, "g": "DEU", "v": 1.5}]


def test_charge_colours_cover_the_house_order() -> None:
    """Every ordered charge has a house colour, so the legend never falls back mid-list."""
    assert list(metallic_charge_use.CHARGE_COLOURS) == metallic_charge_use.CHARGE_ORDER
