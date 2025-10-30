"""Tests for biomass availability recreation function."""

import pytest
import pandas as pd
from pathlib import Path
import tempfile
import json

from steelo.data.recreation_functions import recreate_biomass_availability_data
from steelo.adapters.repositories.json_repository import BiomassAvailabilityJsonRepository
from steelo.domain.models import BiomassAvailability


@pytest.fixture
def temp_excel_file():
    """Create a temporary Excel file with biomass availability data."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False) as f:
        # Create test data
        data = {
            "tiam-ucl_region": ["Western Europe", "USA", "China"],
            "Country": ["Germany", None, None],
            "Metric": ["Available biomass", "Available biomass", "Available biomass"],
            "Scenario": ["High", "Base", "Low"],
            "Unit": ["Mt", "Mt", "Mt"],
            2024: [10.5, 20.0, 30.0],
            2025: [11.0, 21.0, 32.0],
            2030: [15.0, 25.0, 40.0],
        }
        df = pd.DataFrame(data)
        df.to_excel(f.name, sheet_name="Biomass availability", index=False)
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


@pytest.fixture
def temp_json_path():
    """Create a temporary JSON file path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        temp_path = Path(f.name)
    yield temp_path
    if temp_path.exists():
        temp_path.unlink()


def test_recreate_biomass_availability_data_basic(temp_excel_file, temp_json_path):
    """Test basic recreation of biomass availability data."""
    # Call the recreation function
    repo = recreate_biomass_availability_data(
        biomass_availability_json_path=temp_json_path, excel_path=temp_excel_file, sheet_name="Biomass availability"
    )

    # Verify repository was returned
    assert isinstance(repo, BiomassAvailabilityJsonRepository)
    assert repo.path == temp_json_path

    # Verify data was written
    assert temp_json_path.exists()

    # Load and verify the JSON content
    with open(temp_json_path, "r") as f:
        json_data = json.load(f)

    assert len(json_data) == 9  # 3 regions * 3 years

    # Check structure of first entry
    first = json_data[0]
    assert "region" in first
    assert "country" in first
    assert "metric" in first
    assert "scenario" in first
    assert "unit" in first
    assert "year" in first
    assert "availability" in first

    # Verify data can be loaded back
    loaded_data = repo.list()
    assert len(loaded_data) == 9
    assert all(isinstance(item, BiomassAvailability) for item in loaded_data)


def test_recreate_biomass_availability_data_custom_sheet(temp_json_path):
    """Test recreation with custom sheet name."""
    # Create Excel with custom sheet name
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False) as f:
        data = {
            "tiam-ucl_region": ["India"],
            "Country": [None],
            "Metric": ["Available biomass"],
            "Scenario": ["Base"],
            "Unit": ["Mt"],
            2030: [50.0],
        }
        df = pd.DataFrame(data)
        df.to_excel(f.name, sheet_name="CustomBiomassSheet", index=False)
        excel_path = Path(f.name)

    try:
        # Call with custom sheet name
        repo = recreate_biomass_availability_data(
            biomass_availability_json_path=temp_json_path, excel_path=excel_path, sheet_name="CustomBiomassSheet"
        )

        loaded_data = repo.list()
        assert len(loaded_data) == 1
        assert loaded_data[0].region == "India"
        assert loaded_data[0].availability == 50.0
    finally:
        excel_path.unlink()


def test_recreate_biomass_availability_data_overwrites_existing(temp_excel_file, temp_json_path):
    """Test that recreation overwrites existing JSON file."""
    # First create some existing data
    existing_data = [
        {
            "region": "Old",
            "country": None,
            "metric": "Old metric",
            "scenario": "Old scenario",
            "unit": "kg",
            "year": 2020,
            "availability": 999.0,
        }
    ]
    with open(temp_json_path, "w") as f:
        json.dump(existing_data, f)

    # Recreate with new data
    repo = recreate_biomass_availability_data(biomass_availability_json_path=temp_json_path, excel_path=temp_excel_file)

    # Verify old data is gone
    loaded_data = repo.list()
    assert len(loaded_data) == 9  # New data, not old
    assert not any(item.region == "Old" for item in loaded_data)


def test_recreate_function_signature():
    """Test that the function has the expected signature for recreation config."""
    import inspect
    from steelo.data.recreation_functions import recreate_biomass_availability_data

    sig = inspect.signature(recreate_biomass_availability_data)
    params = list(sig.parameters.keys())

    # Should have these parameters
    assert "biomass_availability_json_path" in params
    assert "excel_path" in params
    assert "sheet_name" in params

    # Check default for sheet_name
    assert sig.parameters["sheet_name"].default == "Biomass availability"
