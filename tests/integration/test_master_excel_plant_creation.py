"""Integration test for creating plants.json directly from master_input.xlsx."""

import json

import pytest
import pandas as pd


@pytest.fixture
def test_master_excel_path(tmp_path):
    """Create test master Excel file."""
    excel_path = tmp_path / "test_master_input.xlsx"

    # Create minimal plant data
    plant_data = {
        "Plant ID": ["P100000000001", "P100000000002", "P100000000003"],
        "Plant name (English)": ["Test Steel Plant 1", "Test Steel Plant 2", "Test Steel Plant 3"],
        "Country": ["USA", "Germany", "Japan"],
        "Subnational unit (province/state)": ["Ohio", "Bavaria", "Tokyo"],
        "Coordinates": ["40.123,-82.456", "48.789,11.234", "35.678,139.901"],
        "Capacity operating status": ["operating", "operating", "construction"],
        "Plant age (years)": [15, 20, 0],
        "Start date": ["2009", "2004", "2025"],
        "Nominal crude steel capacity (ttpa)": [2000, 1500, 3000],
        "Nominal BOF steel capacity (ttpa)": [1200, 0, 2000],
        "Nominal EAF steel capacity (ttpa)": [800, 1500, 1000],
        "Nominal iron capacity (ttpa)": [1800, 0, 2500],
        "Nominal BF capacity (ttpa)": [1800, 0, 2500],
        "Nominal DRI capacity (ttpa)": [0, 0, 0],
        "Main production equipment": ["BF;BOF;EAF", "EAF", "BF;BOF;EAF"],
    }

    # Create Bill of Materials data (minimal version with required columns)
    bom_data = {
        "Business case": [
            "iron_bf",
            "iron_bf",
            "iron_bf",
            "iron_bf",
            "steel_bof",
            "steel_bof",
            "steel_bof",
            "steel_eaf",
            "steel_eaf",
            "steel_eaf",
            "iron_dri",
            "iron_dri",
            "iron_dri",
        ],
        "Metallic charge": [
            "pellets_low",
            "pellets_low",
            "pellets_low",
            "pellets_low",
            "hot_metal",
            "hot_metal",
            "hot_metal",
            "scrap",
            "scrap",
            "scrap",
            "pellets_mid",
            "pellets_mid",
            "pellets_mid",
        ],
        "Reductant": [""] * 13,
        "Side": [
            "Input",
            "Input",
            "Input",
            "Output",
            "Input",
            "Input",
            "Output",
            "Input",
            "Input",
            "Output",
            "Input",
            "Input",
            "Output",
        ],
        "Metric type": [
            "Feedstock",
            "Fuel",
            "Emissions",
            "Product",
            "Feedstock",
            "Emissions",
            "Product",
            "Feedstock",
            "Emissions",
            "Product",
            "Feedstock",
            "Fuel",
            "Product",
        ],
        "Type": [None] * 13,
        "Vector": [
            "pellets_low",
            "coal",
            "co2",
            "hot_metal",
            "hot_metal",
            "co2",
            "steel",
            "scrap",
            "co2",
            "steel",
            "pellets_mid",
            "natural gas",
            "dri",
        ],
        "Value": [
            1.6,
            0.5,
            2.1,
            1.0,
            1.05,
            0.15,
            1.0,
            1.1,
            0.4,
            1.0,
            1.5,
            0.15,
            1.0,
        ],
        "Unit": ["t/t"] * 13,
        "System boundary": ["cradle-to-gate"] * 13,
        "ghg_factor_scope_1": [0.0] * 13,
        "ghg_factor_scope_2": [0.0] * 13,
        "ghg_factor_scope_3_rest": [0.0] * 13,
    }

    # Create Excel file
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame(plant_data).to_excel(writer, sheet_name="Iron and steel plants", index=False)
        pd.DataFrame(bom_data).to_excel(writer, sheet_name="Bill of Materials", index=False)

    return excel_path


def test_create_plants_json_directly_from_master_excel(tmp_path, test_master_excel_path):
    """Test that plants.json can be created directly from master Excel file."""
    # GIVEN: A minimal valid master_input.xlsx file
    assert test_master_excel_path.exists(), f"Test Excel file not found at {test_master_excel_path}"

    # Setup output directory
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # WHEN: DataPreparationService is invoked with the test master Excel
    # Note: We'll use the specific file recreation to only create plants.json
    # to avoid errors from missing sheets for other data types
    from unittest.mock import Mock, MagicMock
    from steelo.data.recreate import DataRecreator
    from steelo.data.recreation_config import RecreationConfig
    from steelo.data.manager import DataManager

    # Mock the DataManager to avoid needing actual packages
    mock_manager = Mock(spec=DataManager)
    # Mock get_package_path to return a dummy path (won't be used for plants.json)
    mock_manager.get_package_path = MagicMock(return_value=tmp_path / "dummy_package")

    recreator = DataRecreator(mock_manager)

    # Create only plants.json
    config = RecreationConfig(files_to_recreate=["plants.json"], skip_existing=False, verbose=False)

    recreator.recreate_with_config(
        output_dir=output_dir,
        config=config,
        master_excel_path=test_master_excel_path,
        package_name="core-data",
    )

    # THEN: Assert plants.json is created correctly
    generated_plants_path = output_dir / "plants.json"
    assert generated_plants_path.exists(), "plants.json was not created"

    # Load the generated output
    with open(generated_plants_path) as f:
        generated_plants = json.load(f)

    # Check that we have the expected number of plants
    assert len(generated_plants["root"]) == 3, f"Expected 3 plants, got {len(generated_plants['root'])}"

    # Check key fields for each plant
    expected_plant_ids = ["P100000000001", "P100000000002", "P100000000003"]
    expected_countries = ["USA", "DEU", "JPN"]
    expected_statuses = ["operating", "operating", "construction"]

    for i, plant in enumerate(generated_plants["root"]):
        # Check plant ID
        assert plant["plant_id"] == expected_plant_ids[i], f"Plant {i} ID mismatch"

        # Check location
        assert plant["location"]["iso3"] == expected_countries[i], f"Plant {i} ISO3 mismatch"
        assert plant["location"]["lat"] is not None, f"Plant {i} missing latitude"
        assert plant["location"]["lon"] is not None, f"Plant {i} missing longitude"

        # Check furnace groups exist
        assert len(plant["furnace_groups"]) > 0, f"Plant {i} has no furnace groups"

        # Check each furnace group has required fields
        for j, fg in enumerate(plant["furnace_groups"]):
            assert "furnace_group_id" in fg, f"Plant {i} furnace group {j} missing ID"
            assert "capacity" in fg, f"Plant {i} furnace group {j} missing capacity"
            assert fg["status"] == expected_statuses[i], f"Plant {i} furnace group {j} status mismatch"
            assert "technology" in fg, f"Plant {i} furnace group {j} missing technology"
            assert "name" in fg["technology"], f"Plant {i} furnace group {j} missing technology name"
            assert "product" in fg["technology"], f"Plant {i} furnace group {j} missing technology product"
