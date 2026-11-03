"""Tests for the steel routes viewer's route building (steelo.utilities.interactive.steel_routes)."""

import pandas as pd
import pytest

from steelo.utilities.interactive import steel_routes

COLUMNS = ["year", "iso3", "furnace_group_id", "technology", "chosen_reductant", "product", "production", "feedstock"]
ALLOCATION_COLUMNS = ["commodity", "source_id", "destination_id", "allocated_volume"]


def sample_post_processed() -> pd.DataFrame:
    """Four iron makers and five steel furnace groups; each steel furnace group repeats on two feedstock rows."""
    rows = [
        [2025, "CHN", "B_FOSSIL", "BF", "coke+pci", "iron", 6e6, "io_high"],
        [2025, "CHN", "B_BIO", "BF", "coke+bio_pci", "iron", 6e6, "io_high"],
        [2025, "CHN", "D_H2", "DRI", "hydrogen", "iron", 3e6, "io_high"],
        [2025, "DEU", "D_GAS", "DRI", "natural_gas", "iron", 3e6, "io_high"],
        # A BOF charged with hot metal, hydrogen DRI and scrap; its scrap joins the iron routes.
        [2025, "CHN", "S_BOF", "BOF", None, "steel", 10e6, "hot_metal"],
        [2025, "CHN", "S_BOF", "BOF", None, "steel", 10e6, "scrap"],
        # An EAF charged with scrap and gas DRI.
        [2025, "DEU", "S_EAF", "EAF", None, "steel", 4e6, "scrap"],
        [2025, "DEU", "S_EAF", "EAF", None, "steel", 4e6, "dri_mid"],
        # A BOF charged with scrap only.
        [2025, "DEU", "S_SCRAP_BOF", "BOF", None, "steel", 2e6, "scrap"],
        [2025, "DEU", "S_SCRAP_BOF", "BOF", None, "steel", 2e6, "scrap"],
        # An EAF with no charge allocation.
        [2025, "DEU", "S_NONE", "EAF", None, "steel", 1e6, "scrap"],
        [2026, "DEU", "S_NONE", "EAF", None, "steel", 1e6, "scrap"],
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def write_allocations(tm_dir, year: int) -> None:
    rows = [
        ["hot_metal", "B_FOSSIL", "S_BOF", 6e6],
        ["dri_high", "D_H2", "S_BOF", 2e6],
        ["scrap", "Supplier_1", "S_BOF", 2e6],
        ["scrap", "Supplier_1", "S_EAF", 3e6],
        ["dri_mid", "D_GAS", "S_EAF", 1e6],
        ["scrap", "Supplier_1", "S_SCRAP_BOF", 2e6],
        ["io_high", "Mine", "B_FOSSIL", 9e6],  # not a metallic charge of a steel furnace: ignored
    ]
    pd.DataFrame(rows, columns=ALLOCATION_COLUMNS).to_csv(tm_dir / f"steel_trade_allocations_{year}.csv", index=False)


def route_totals(rows: pd.DataFrame) -> dict[str, float]:
    return rows.groupby("route")["production_mt"].sum().round(6).to_dict()


def test_iron_source_reads_the_technology_and_reductant() -> None:
    assert steel_routes.iron_source("DRI", "hydrogen") == "DRI-H2"
    assert steel_routes.iron_source("DRI", "natural_gas") == "DRI-FOSSIL"
    assert steel_routes.iron_source("DRI", "coal") == "DRI-FOSSIL"
    assert steel_routes.iron_source("BF", "coke+pci") == "BF-FOSSIL"
    assert steel_routes.iron_source("BF", "coke+bio_pci") == "BF-BIO"
    assert steel_routes.iron_source("BF", "coke+bio_pci+h2") == "BF-H2"
    assert steel_routes.iron_source("BF_CHARCOAL", "bio_pci") == "BF-CHARCOAL"
    assert steel_routes.iron_source("BF_CHARCOAL+CCS", "bio_pci") == "BF-CHARCOAL-CCS"
    assert steel_routes.iron_source("BF", float("nan")) == "BF-FOSSIL"


def test_routes_split_each_furnace_groups_production_by_charge_share(tmp_path) -> None:
    write_allocations(tmp_path, 2025)

    rows = steel_routes.steel_route_rows(sample_post_processed(), tmp_path)

    # BOF: hot metal 6 and DRI 2 of 10 Mt, the 2 Mt of scrap spread over them (0.75 / 0.25). Each furnace
    # group counts once however many feedstock rows repeat it.
    assert route_totals(rows) == {
        steel_routes.BF_BOF_FOSSIL: 7.5,
        steel_routes.DRI_BOF_H2: 2.5,
        steel_routes.SCRAP_EAF: 3.0,
        steel_routes.DRI_EAF_FOSSIL: 1.0,
        steel_routes.SCRAP_BOF: 2.0,
        steel_routes.UNALLOCATED: 1.0,
    }


def test_route_rows_total_the_recorded_steel_production(tmp_path) -> None:
    write_allocations(tmp_path, 2025)
    table = sample_post_processed()

    rows = steel_routes.steel_route_rows(table, tmp_path)

    expected = table[(table["year"] == 2025) & (table["product"] == "steel")].drop_duplicates("furnace_group_id")
    assert rows["production_mt"].sum() == pytest.approx(expected["production"].sum() / 1e6)
    assert set(rows["technology"]) == {"BOF", "EAF"}
    assert set(rows["geo"]) == {"CHN", "DEU"}


def test_year_without_allocations_is_left_out(tmp_path, caplog) -> None:
    write_allocations(tmp_path, 2025)

    with caplog.at_level("WARNING", logger="steelo.utilities.interactive.steel_routes"):
        rows = steel_routes.steel_route_rows(sample_post_processed(), tmp_path)

    assert set(rows["year"]) == {2025}
    assert "steel_trade_allocations_2026.csv" in caplog.text


def test_no_allocations_gives_no_rows(tmp_path) -> None:
    assert steel_routes.steel_route_rows(sample_post_processed(), tmp_path).empty


def test_missing_column_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="chosen_reductant"):
        steel_routes.steel_route_rows(sample_post_processed().drop(columns=["chosen_reductant"]), tmp_path)


def test_pack_rows_use_short_keys(tmp_path) -> None:
    write_allocations(tmp_path, 2025)
    packed = steel_routes.pack_rows(steel_routes.steel_route_rows(sample_post_processed(), tmp_path))

    assert {"y": 2025, "g": "DEU", "t": "EAF", "r": steel_routes.SCRAP_EAF, "v": 3.0} in packed
