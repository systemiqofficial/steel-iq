"""
Integration tests for transport emissions functionality.
"""

import pytest
import pandas as pd
import json

from steelo.adapters.repositories import JsonRepository
from steelo.data.recreation_functions import recreate_transport_emissions_data


@pytest.fixture
def integration_test_dir(tmp_path):
    """Create a complete test environment with all necessary files."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    # Create transport emissions Excel
    excel_path = fixtures_dir / "master_input.xlsx"

    # Emissions data
    emissions_data = {
        "reporterISO": ["USA", "CHN", "DEU"],
        "partnerISO": ["CHN", "USA", "FRA"],
        "commodity": ["iron_ore", "steel", "coal"],
        "ghg_factor_weighted": [0.025, 0.030, 0.015],
        "updated_on": ["2024-01-01", "2024-01-01", "2024-01-02"],
    }
    emissions_df = pd.DataFrame(emissions_data)

    # Transportation costs data
    costs_data = {
        "reporterISO": ["USA", "CHN", "DEU"],
        "partnerISO": ["CHN", "USA", "FRA"],
        "commodity": ["iron_ore", "steel", "coal"],
        "transportation_cost": [50.0, 55.0, 30.0],
        "updated_on": ["2024-01-01", "2024-01-01", "2024-01-02"],
    }
    costs_df = pd.DataFrame(costs_data)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        emissions_df.to_excel(writer, sheet_name="Transport emissions", index=False)
        costs_df.to_excel(writer, sheet_name="Transportation costs", index=False)

    # Create other required files (minimal versions)
    # Plants
    plants_data = {
        "root": [
            {
                "plant_id": "P001",
                "plant_name": "Test Plant",
                "location": {
                    "iso3": "USA",
                    "country": "United States",
                    "region": "North America",
                    "lat": 40.0,
                    "lon": -100.0,
                },
                "furnace_groups": [],
            }
        ]
    }
    with open(fixtures_dir / "plants.json", "w") as f:
        json.dump(plants_data, f)

    # Demand centers
    demand_data = {"root": []}
    with open(fixtures_dir / "demand_centers.json", "w") as f:
        json.dump(demand_data, f)

    # Other required JSON files (empty) - most use root format
    for filename in [
        "suppliers.json",
        "plant_groups.json",
        "tariffs.json",
        "subsidies.json",
        "carbon_costs.json",
        "region_emissivity.json",
        "primary_feedstocks.json",
        "input_costs.json",
        "capex.json",
        "cost_of_capital.json",
        "railway_costs.json",
        "country_mappings.json",
    ]:
        with open(fixtures_dir / filename, "w") as f:
            json.dump({"root": []}, f)

    # These files expect list format
    for filename in ["hydrogen_efficiency.json", "hydrogen_capex_opex.json"]:
        with open(fixtures_dir / filename, "w") as f:
            json.dump([], f)

    # Legal process connectors has a different format
    with open(fixtures_dir / "legal_process_connectors.json", "w") as f:
        json.dump([], f)

    # Cost of X file
    cost_data = {"Country code": {"0": "USA"}, "Cost of equity - industrial assets": {"0": 0.25}}
    with open(fixtures_dir / "cost_of_x.json", "w") as f:
        json.dump(cost_data, f)

    # Tech switches CSV
    with open(fixtures_dir / "tech_switches_allowed.csv", "w") as f:
        f.write("Origin,BF,BOF,DRI,EAF\nBF,NO,NO,NO,NO\n")

    return fixtures_dir, excel_path


def test_transport_emissions_end_to_end(integration_test_dir):
    """Test complete transport emissions flow from Excel to Environment."""
    fixtures_dir, excel_path = integration_test_dir

    # Step 1: Recreate transport emissions from Excel
    transport_emissions_path = fixtures_dir / "transport_emissions.json"
    repo = recreate_transport_emissions_data(
        transport_emissions_json_path=transport_emissions_path, excel_path=excel_path
    )

    assert transport_emissions_path.exists()
    assert len(repo.list()) == 3  # All 3 entries have both emissions and costs

    # Step 2: Verify the JSON file content
    with open(transport_emissions_path, "r") as f:
        data = json.load(f)

    assert isinstance(data, list)
    assert len(data) == 3  # All 3 entries have both emissions and costs

    # Find the USA->CHN iron_ore entry
    usa_chn_entry = next(d for d in data if d["reporter_iso"] == "USA" and d["partner_iso"] == "CHN")
    assert usa_chn_entry["commodity"] == "iron_ore"
    assert usa_chn_entry["ghg_factor"] == 0.025
    assert usa_chn_entry["transportation_cost"] == 50.0

    # Step 3: Test loading into repository
    from steelo.adapters.repositories.json_repository import TransportKPIJsonRepository

    repo2 = TransportKPIJsonRepository(transport_emissions_path)
    emissions_dict = repo2.get_as_dict()

    assert len(emissions_dict) == 3
    assert ("USA", "CHN", "iron_ore") in emissions_dict
    assert emissions_dict[("USA", "CHN", "iron_ore")] == 0.025
    assert ("CHN", "USA", "steel") in emissions_dict
    assert emissions_dict[("CHN", "USA", "steel")] == 0.030
    assert ("DEU", "FRA", "coal") in emissions_dict
    assert emissions_dict[("DEU", "FRA", "coal")] == 0.015


def test_transport_emissions_optional_path(integration_test_dir):
    """Test that transport emissions work with optional path (uses default)."""
    fixtures_dir, _ = integration_test_dir

    # Create JsonRepository without specifying transport_emissions_path
    json_repo = JsonRepository(
        plant_lifetime=20,
        plants_path=fixtures_dir / "plants.json",
        demand_centers_path=fixtures_dir / "demand_centers.json",
        suppliers_path=fixtures_dir / "suppliers.json",
        plant_groups_path=fixtures_dir / "plant_groups.json",
        trade_tariffs_path=fixtures_dir / "tariffs.json",
        subsidies_path=fixtures_dir / "subsidies.json",
        carbon_costs_path=fixtures_dir / "carbon_costs.json",
        primary_feedstocks_path=fixtures_dir / "primary_feedstocks.json",
        input_costs_path=fixtures_dir / "input_costs.json",
        region_emissivity_path=fixtures_dir / "region_emissivity.json",
        capex_path=fixtures_dir / "capex.json",
        cost_of_capital_path=fixtures_dir / "cost_of_capital.json",
        legal_process_connectors_path=fixtures_dir / "legal_process_connectors.json",
        country_mappings_path=fixtures_dir / "country_mappings.json",
        hydrogen_efficiency_path=fixtures_dir / "hydrogen_efficiency.json",
        hydrogen_capex_opex_path=fixtures_dir / "hydrogen_capex_opex.json",
        railway_costs_path=fixtures_dir / "railway_costs.json",
        # transport_emissions_path not specified - should use default
    )

    # Should still have transport_emissions attribute
    assert hasattr(json_repo, "transport_emissions")
    # Will be empty since default file doesn't exist in test env
    assert json_repo.transport_emissions.list() == []
