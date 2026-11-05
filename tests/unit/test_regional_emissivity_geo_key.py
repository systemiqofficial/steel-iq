"""Geo-keyed Power-grid-emissivity rows split into iso3 + geo_unit with country gap-fill."""

import pandas as pd
import pytest

from steelo.adapters.dataprocessing.excel_reader import read_regional_emissivities

GRID_SHEET = "Power grid emissivity"
GAS_SHEET = "Met coal & gas emissions"


def write_master(tmp_path, grid_rows, gas_rows):
    """Write grid and gas/coke emissivity sheets and return the workbook path."""
    path = tmp_path / "master.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(grid_rows).to_excel(writer, sheet_name=GRID_SHEET, index=False)
        pd.DataFrame(gas_rows).to_excel(writer, sheet_name=GAS_SHEET, index=False)
    return path


def grid_row(iso3, year, value, scenario="projection_business_as_usual", country="China"):
    return {
        "Vector": "Electricity",
        "country": country,
        "country_iso3": iso3,
        "region": "Asia",
        "year": year,
        "projection_scenario": scenario,
        "ghg_factor_unit": "tCO2/kWh",
        "ghg_factor_scope_2": value,
    }


def gas_row(iso3, vector, scope_1):
    return {
        "Vector": vector,
        "country": "China",
        "country_iso3": iso3,
        "year": 2020,
        "ghg_factor_unit": "tCO2/t",
        "ghg_factor_scope_1": scope_1,
    }


def by_id(result):
    return {entry.id: entry for entry in result}


@pytest.fixture
def master_with_province_rows(tmp_path):
    """Country rows for 2025-2026 plus a province authoring 2026 only."""
    return write_master(
        tmp_path,
        [
            grid_row("CHN", 2025, 0.00055),
            grid_row("CHN", 2026, 0.00050),
            grid_row("CHN:CN-AH", 2026, 0.00030, country="Anhui"),
        ],
        [
            gas_row("CHN", "Coking coal", 3.1),
            gas_row("CHN", "Natural gas", 2.2),
        ],
    )


def test_province_key_splits_into_iso3_and_geo_unit(master_with_province_rows):
    """A combined ISO3:code key is split, keeps a distinct id, and composes its geo_key."""
    entries = by_id(read_regional_emissivities(master_with_province_rows, GRID_SHEET, GAS_SHEET))

    province = entries["CHN:CN-AH_business_as_usual"]
    assert province.iso3 == "CHN"
    assert province.geo_unit == "CN-AH"
    assert province.geo_key == "CHN:CN-AH"

    country = entries["CHN_business_as_usual"]
    assert country.geo_unit is None
    assert country.geo_key == "CHN"


def test_province_inherits_gas_coke_from_country(master_with_province_rows):
    """Gas/coke factors are authored per country, so the province takes the country's."""
    entries = by_id(read_regional_emissivities(master_with_province_rows, GRID_SHEET, GAS_SHEET))

    province = entries["CHN:CN-AH_business_as_usual"]
    assert province.coke_emissivity == pytest.approx({"ghg_factor_scope_1": 3.1})
    assert province.gas_emissivity == pytest.approx({"ghg_factor_scope_1": 2.2})


def test_province_years_gap_filled_from_country(master_with_province_rows):
    """Years the province does not author are filled from the country's same-scenario group."""
    entries = by_id(read_regional_emissivities(master_with_province_rows, GRID_SHEET, GAS_SHEET))

    province = entries["CHN:CN-AH_business_as_usual"]
    assert province.grid_emissivity[2025]["Electricity"] == pytest.approx(0.00055)
    assert province.grid_emissivity[2026]["Electricity"] == pytest.approx(0.00030)

    country = entries["CHN_business_as_usual"]
    assert country.grid_emissivity[2026]["Electricity"] == pytest.approx(0.00050)


def test_gap_fill_logs_warning(master_with_province_rows, caplog):
    """Filling provincial years from the country is surfaced as a warning during prep."""
    with caplog.at_level("WARNING"):
        read_regional_emissivities(master_with_province_rows, GRID_SHEET, GAS_SHEET)

    assert "CHN:CN-AH (projection_business_as_usual) missing 1 year(s); filled 2025-2025 from CHN" in caplog.text


def test_gap_fill_stays_within_scenario(tmp_path):
    """A province group only takes years from the country's group for the same scenario."""
    master = write_master(
        tmp_path,
        [
            grid_row("CHN", 2024, 0.00060, scenario="historical"),
            grid_row("CHN", 2025, 0.00055),
            grid_row("CHN:CN-AH", 2026, 0.00030, country="Anhui"),
        ],
        [
            gas_row("CHN", "Coking coal", 3.1),
            gas_row("CHN", "Natural gas", 2.2),
        ],
    )
    entries = by_id(read_regional_emissivities(master, GRID_SHEET, GAS_SHEET))

    province = entries["CHN:CN-AH_business_as_usual"]
    assert sorted(province.grid_emissivity) == [2025, 2026]


def test_plain_iso3_sheet_reads_unchanged(tmp_path):
    """A sheet without any geo_keys yields country-level entries only."""
    master = write_master(
        tmp_path,
        [
            grid_row("CHN", 2025, 0.00055),
            grid_row("CHN", 2026, 0.00050),
        ],
        [
            gas_row("CHN", "Coking coal", 3.1),
            gas_row("CHN", "Natural gas", 2.2),
        ],
    )
    entries = read_regional_emissivities(master, GRID_SHEET, GAS_SHEET)

    assert len(entries) == 1
    (country,) = entries
    assert country.geo_unit is None
    assert country.id == "CHN_business_as_usual"
    assert sorted(country.grid_emissivity) == [2025, 2026]
