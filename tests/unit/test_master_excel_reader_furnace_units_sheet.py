"""Unit tests for MasterExcelReader.read_plants_from_furnace_units_sheet()
(Phase 2 reader for the flattened 'Furnace units' sheet)."""

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


@pytest.fixture
def furnace_units_excel_path(tmp_path):
    path = tmp_path / "furnace_units.xlsx"

    furnace_units = pd.DataFrame(
        {
            "plant_id": ["P1", "P1", "P2"],
            "plant_name": ["US Plant", "US Plant", "China Plant"],
            "iso3": ["USA", "USA", "CHN"],
            "region": ["North America", "North America", "East Asia"],
            "geo_unit_or_province": [None, None, None],
            "latitude": [40.0, 40.0, 31.0],
            "longitude": [-83.0, -83.0, 121.0],
            "technology": ["BF", "BOF", "BF"],
            "capacity_ttpa": [1000.0, 900.0, 1500.0],
            "status": ["operating", "operating", "operating"],
            "start_year": [2005, 2005, 2011],
            "last_renovation_year": [2015, 2015, 2011],
            "soe_status": ["private", "private", "state-owned"],
            "power_source": ["grid", "grid", "grid"],
            "parent_gem_id": ["", "", ""],
            "workforce_size": [1000, 1000, 2000],
            "source": ["gem_unit", "gem_unit", "external"],
            "source_sheet": ["Blast furnaces", "Basic oxygen furnaces", "Units"],
            "source_row": [2, 3, 10],
            "unit_id": ["U1", "U3", "EXT_ROW10_G1"],
        }
    )

    bom_data = pd.DataFrame(
        {
            "Business case": ["iron_bf", "steel_bof"],
            "Metallic charge": ["", ""],
            "Reductant": ["", ""],
            "Side": ["Input", "Input"],
            "Metric type": ["Feedstock", "Feedstock"],
            "Type": [None, None],
            "Vector": ["", ""],
            "Value": [1.0, 1.0],
            "Unit": ["t/t", "t/t"],
            "System boundary": ["cradle-to-gate", "cradle-to-gate"],
            "ghg_factor_scope_1": [0.0, 0.0],
            "ghg_factor_scope_2": [0.0, 0.0],
            "ghg_factor_scope_3_rest": [0.0, 0.0],
        }
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        furnace_units.to_excel(writer, sheet_name="Furnace units", index=False)
        bom_data.to_excel(writer, sheet_name="Bill of Materials", index=False)

    return path


class TestReadPlantsFromFurnaceUnitsSheet:
    def test_missing_sheet_raises(self, tmp_path):
        empty_path = tmp_path / "empty.xlsx"
        pd.DataFrame({"a": [1]}).to_excel(empty_path, sheet_name="Not furnace units", index=False)
        reader = MasterExcelReader(excel_path=empty_path)
        with pytest.raises(ValueError, match="Furnace units"):
            reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

    def test_two_rows_same_plant_merge_into_one_plant(self, furnace_units_excel_path):
        reader = MasterExcelReader(excel_path=furnace_units_excel_path)
        plants, metadata, _ = reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

        by_id = {p.plant_id: p for p in plants}
        assert "P1" in by_id
        assert len(by_id["P1"].furnace_groups) == 2
        techs = {fg.technology.name for fg in by_id["P1"].furnace_groups}
        assert techs == {"BF", "BOF"}

    def test_plant_location_built_from_row(self, furnace_units_excel_path):
        reader = MasterExcelReader(excel_path=furnace_units_excel_path)
        plants, _, _ = reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

        by_id = {p.plant_id: p for p in plants}
        p2 = by_id["P2"]
        assert p2.location.iso3 == "CHN"
        assert p2.location.lat == 31.0
        assert p2.location.lon == 121.0

    def test_furnace_group_capacity_converted_to_tonnes(self, furnace_units_excel_path):
        from steelo.domain.constants import KT_TO_T

        reader = MasterExcelReader(excel_path=furnace_units_excel_path)
        plants, _, _ = reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

        by_id = {p.plant_id: p for p in plants}
        bf_fg = next(fg for fg in by_id["P1"].furnace_groups if fg.technology.name == "BF")
        assert bf_fg.capacity == KT_TO_T * 1000.0

    def test_metadata_captured_per_furnace_group(self, furnace_units_excel_path):
        reader = MasterExcelReader(excel_path=furnace_units_excel_path)
        plants, metadata, _ = reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

        total_fg = sum(len(p.furnace_groups) for p in plants)
        assert len(metadata) == total_fg
        assert all(m.commissioning_year is not None for m in metadata.values())

    def test_furnace_group_ids_are_unique(self, furnace_units_excel_path):
        reader = MasterExcelReader(excel_path=furnace_units_excel_path)
        plants, _, _ = reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

        all_ids = [fg.furnace_group_id for p in plants for fg in p.furnace_groups]
        assert len(all_ids) == len(set(all_ids))


def _write_geography_variant(base_path, tmp_path, iso3, geo_unit):
    """Copy the fixture workbook with plant P1's iso3/geo_unit_or_province replaced (both rows)."""
    df = pd.read_excel(base_path, sheet_name="Furnace units")
    bom = pd.read_excel(base_path, sheet_name="Bill of Materials")
    df.loc[df["plant_id"] == "P1", "iso3"] = iso3
    df.loc[df["plant_id"] == "P1", "geo_unit_or_province"] = geo_unit
    path = tmp_path / "furnace_units_geo.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Furnace units", index=False)
        bom.to_excel(writer, sheet_name="Bill of Materials", index=False)
    return path


def _reader_with_injected_geo_keys(excel_path):
    """A reader with deterministic geo-keys (no prepared-data dependency), as prep passes
    the in-memory geo_hierarchy."""
    return MasterExcelReader(excel_path=excel_path, valid_geo_keys={"CHN:CN-HE", "CHN:CN-SH"})


class TestReadPlantsFromFurnaceUnitsSheetGeography:
    """Prep-time enforcement of the sheet's authored geography."""

    def test_invalid_iso3_raises(self, furnace_units_excel_path, tmp_path):
        path = _write_geography_variant(furnace_units_excel_path, tmp_path, iso3="ZZZ", geo_unit=None)
        reader = _reader_with_injected_geo_keys(path)
        with pytest.raises(ValueError, match="not a valid ISO-3"):
            reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

    def test_unrecognised_geo_unit_raises(self, furnace_units_excel_path, tmp_path):
        """A raw province name where an ISO 3166-2 code is expected fails loudly at read time."""
        path = _write_geography_variant(furnace_units_excel_path, tmp_path, iso3="CHN", geo_unit="Hebei")
        reader = _reader_with_injected_geo_keys(path)
        with pytest.raises(ValueError, match="CHN:Hebei"):
            reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

    def test_declared_geo_unit_lands_on_location(self, furnace_units_excel_path, tmp_path):
        path = _write_geography_variant(furnace_units_excel_path, tmp_path, iso3="CHN", geo_unit="CN-HE")
        reader = _reader_with_injected_geo_keys(path)
        plants, _, _ = reader.read_plants_from_furnace_units_sheet(dynamic_feedstocks_dict={})

        by_id = {p.plant_id: p for p in plants}
        assert by_id["P1"].location.geo_unit == "CN-HE"
        assert by_id["P1"].location.geo_key == "CHN:CN-HE"
