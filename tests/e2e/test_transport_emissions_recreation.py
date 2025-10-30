"""
Tests for transport emissions recreation functionality.
"""

import pytest
import pandas as pd
import json

from steelo.data.recreation_functions import recreate_transport_emissions_data
from steelo.adapters.repositories.json_repository import TransportKPIJsonRepository


@pytest.fixture
def transport_emissions_excel(tmp_path):
    """Create a test Excel file with transport emissions data."""
    excel_path = tmp_path / "transport_emissions.xlsx"

    # Emissions data
    emissions_data = {
        "reporterISO": ["USA", "CHN", "DEU", "JPN", None, "IND"],
        "partnerISO": ["CHN", "USA", "FRA", None, "BRA", ""],
        "commodity": ["iron ore", "steel", "COAL", "iron ore", "steel", "iron ore"],
        "ghg_factor_weighted": [0.025, 0.030, 0.015, 0.020, 0.035, 0.040],
        "updated_on": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
    }
    emissions_df = pd.DataFrame(emissions_data)

    # Transportation costs data
    costs_data = {
        "reporterISO": ["USA", "CHN", "DEU", "JPN", "IND"],
        "partnerISO": ["CHN", "USA", "FRA", "BRA", "USA"],
        "commodity": ["iron ore", "steel", "coal", "iron ore", "iron ore"],
        "transportation_cost": [50.0, 55.0, 30.0, 45.0, 60.0],
        "updated_on": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-03"],
    }
    costs_df = pd.DataFrame(costs_data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        emissions_df.to_excel(writer, sheet_name="Transport emissions", index=False)
        costs_df.to_excel(writer, sheet_name="Transportation costs", index=False)

    return excel_path


def test_recreate_transport_emissions_data(transport_emissions_excel, tmp_path):
    """Test the recreate_transport_emissions_data function."""
    json_path = tmp_path / "transport_emissions.json"

    # Recreate the data
    repo = recreate_transport_emissions_data(
        transport_emissions_json_path=json_path, excel_path=transport_emissions_excel, sheet_name="Transport emissions"
    )

    # Verify the repository was returned
    assert isinstance(repo, TransportKPIJsonRepository)

    # Verify the JSON file was created
    assert json_path.exists()

    # Verify the data was loaded correctly
    emissions = repo.list()
    assert len(emissions) == 5  # Combined emissions and costs entries

    # Find specific entries to verify
    usa_chn_iron = next(e for e in emissions if e.reporter_iso == "USA" and e.partner_iso == "CHN")
    assert usa_chn_iron.commodity == "iron_ore"  # Should be normalized with underscore
    assert usa_chn_iron.ghg_factor == 0.025
    assert usa_chn_iron.transportation_cost == 50.0
    assert usa_chn_iron.updated_on == "2024-01-01"

    # Check commodity normalization
    chn_usa_steel = next(e for e in emissions if e.reporter_iso == "CHN" and e.partner_iso == "USA")
    assert chn_usa_steel.commodity == "steel"
    assert chn_usa_steel.ghg_factor == 0.030
    assert chn_usa_steel.transportation_cost == 55.0

    # Verify entries exist for all valid combinations
    commodities = {e.commodity for e in emissions}
    assert "iron_ore" in commodities
    assert "steel" in commodities
    assert "coal" in commodities


def test_recreate_transport_emissions_data_custom_sheet(transport_emissions_excel, tmp_path):
    """Test recreation with custom sheet name."""
    # Create Excel with custom sheet name
    excel_path = tmp_path / "custom_sheet.xlsx"

    emissions_data = {
        "reporterISO": ["USA"],
        "partnerISO": ["CHN"],
        "commodity": ["iron ore"],
        "ghg_factor_weighted": [0.025],
        "updated_on": ["2024-01-01"],
    }
    emissions_df = pd.DataFrame(emissions_data)

    costs_data = {
        "reporterISO": ["USA"],
        "partnerISO": ["CHN"],
        "commodity": ["iron ore"],
        "transportation_cost": [50.0],
        "updated_on": ["2024-01-01"],
    }
    costs_df = pd.DataFrame(costs_data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        emissions_df.to_excel(writer, sheet_name="Custom Transport", index=False)
        costs_df.to_excel(writer, sheet_name="Transportation costs", index=False)

    json_path = tmp_path / "transport_emissions.json"

    # Recreate with custom sheet name
    repo = recreate_transport_emissions_data(
        transport_emissions_json_path=json_path, excel_path=excel_path, sheet_name="Custom Transport"
    )

    emissions = repo.list()
    assert len(emissions) == 1
    assert emissions[0].reporter_iso == "USA"
    assert emissions[0].transportation_cost == 50.0


def test_recreate_transport_emissions_integration_with_environment(transport_emissions_excel, tmp_path):
    """Test that recreated data can be used with Environment."""
    json_path = tmp_path / "transport_emissions.json"

    # Recreate the data
    repo = recreate_transport_emissions_data(
        transport_emissions_json_path=json_path, excel_path=transport_emissions_excel
    )

    # Get as dictionary format for Environment
    transport_dict = repo.get_as_dict()

    # Verify the format is correct
    assert isinstance(transport_dict, dict)
    assert all(isinstance(k, tuple) and len(k) == 3 for k in transport_dict.keys())
    assert all(isinstance(v, float) for v in transport_dict.values())

    # Check specific entries
    assert transport_dict[("USA", "CHN", "iron_ore")] == 0.025
    assert transport_dict[("CHN", "USA", "steel")] == 0.030
    assert transport_dict[("DEU", "FRA", "coal")] == 0.015


def test_recreate_transport_emissions_json_format(transport_emissions_excel, tmp_path):
    """Test the JSON file format created by recreation."""
    json_path = tmp_path / "transport_emissions.json"

    # Recreate the data
    recreate_transport_emissions_data(transport_emissions_json_path=json_path, excel_path=transport_emissions_excel)

    # Read the JSON file directly
    with open(json_path, "r") as f:
        data = json.load(f)

    # Verify structure
    assert isinstance(data, list)
    assert len(data) == 5  # Combined emissions and costs entries

    # Check structure of all entries
    for entry in data:
        assert "reporter_iso" in entry
        assert "partner_iso" in entry
        assert "commodity" in entry
        assert "ghg_factor" in entry
        assert "transportation_cost" in entry
        assert "updated_on" in entry

    # Find and verify specific entry
    usa_chn_entry = next(d for d in data if d["reporter_iso"] == "USA" and d["partner_iso"] == "CHN")
    assert usa_chn_entry["commodity"] == "iron_ore"
    assert usa_chn_entry["ghg_factor"] == 0.025
    assert usa_chn_entry["transportation_cost"] == 50.0
    assert usa_chn_entry["updated_on"] == "2024-01-01"
