"""Scenario selection in the demand-centre and scrap-supplier Excel readers."""

import pickle

import pandas as pd
import pytest

from steelo.adapters.dataprocessing import excel_reader
from steelo.domain.models import Year

SHEET = "Demand and scrap availability"
KT_TO_T = excel_reader.KT_TO_T


@pytest.fixture
def scenario_workbook(tmp_path):
    """Two-scenario demand/scrap sheet plus the location and gravity inputs the readers need."""
    rows = []
    for iso3, country in [("AUT", "Austria"), ("PRT", "Portugal")]:
        for scenario, demand, scrap in [("BAU", 100, 10), ("China-high", 200, 20)]:
            rows.append([country, iso3, "Crude steel consumption for forming [kt]", scenario, "kt", demand, demand + 1])
            rows.append([country, iso3, "Total available scrap", scenario, "kt", scrap, scrap + 1])
    df = pd.DataFrame(rows, columns=["Country", "ISO-3 code", "Metric", "Scenario", "Unit", 2025, 2030])
    excel_path = tmp_path / "master.xlsx"
    df.to_excel(excel_path, sheet_name=SHEET, index=False)

    location_csv = tmp_path / "countries.csv"
    pd.DataFrame(
        {"COUNTRY": ["Austria", "Portugal"], "ISO": ["AT", "PT"], "latitude": [47.5, 39.5], "longitude": [14.5, -8.0]},
    ).to_csv(location_csv, index=False)

    gravity_path = tmp_path / "gravity_distances_dict.pkl"
    gravity_path.write_bytes(pickle.dumps({}))
    return excel_path, location_csv, gravity_path


def _demand_2025(scenario_workbook, scenario):
    excel_path, location_csv, gravity_path = scenario_workbook
    centres = excel_reader.read_demand_centers(
        gravity_distances_path=gravity_path,
        demand_excel_path=excel_path,
        demand_sheet_name=SHEET,
        location_csv=location_csv,
        demand_scenario=scenario,
    )
    return {dc.center_of_gravity.iso3: dc.demand_by_year[Year(2025)] for dc in centres}


def _scrap_2025(scenario_workbook, scenario):
    excel_path, location_csv, gravity_path = scenario_workbook
    suppliers = excel_reader.read_scrap_as_suppliers(
        str(excel_path),
        SHEET,
        str(location_csv),
        gravity_distances_pkl_path=gravity_path,
        scrap_scenario=scenario,
    )
    return {s.location.iso3: s.capacity_by_year[Year(2025)] for s in suppliers}


def test_read_demand_centers_selects_the_requested_scenario(scenario_workbook):
    """Each scenario name yields that scenario's demand volumes, not the other's."""
    assert _demand_2025(scenario_workbook, "BAU") == {"AUT": 100 * KT_TO_T, "PRT": 100 * KT_TO_T}
    assert _demand_2025(scenario_workbook, "China-high") == {"AUT": 200 * KT_TO_T, "PRT": 200 * KT_TO_T}


def test_read_scrap_as_suppliers_selects_the_requested_scenario(scenario_workbook):
    """Scrap availability follows its own scenario argument."""
    assert _scrap_2025(scenario_workbook, "BAU") == {"AUT": 10 * KT_TO_T, "PRT": 10 * KT_TO_T}
    assert _scrap_2025(scenario_workbook, "China-high") == {"AUT": 20 * KT_TO_T, "PRT": 20 * KT_TO_T}


def test_unknown_scenario_fails_listing_available_names(scenario_workbook):
    """A scenario absent from the sheet raises instead of silently producing no centres."""
    expected = (
        r"No rows for scenario 'Nope' in sheet 'Demand and scrap availability'; available: \['BAU', 'China-high'\]"
    )
    with pytest.raises(ValueError, match=expected):
        _demand_2025(scenario_workbook, "Nope")
    with pytest.raises(ValueError, match=expected):
        _scrap_2025(scenario_workbook, "Nope")
