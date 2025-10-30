"""Tests for reading biomass availability from Excel."""

import pytest
import pandas as pd
from pathlib import Path
import tempfile

from steelo.adapters.dataprocessing.excel_reader import read_biomass_availability
from steelo.domain.models import BiomassAvailability, Year


@pytest.fixture
def biomass_excel_file():
    """Create a temporary Excel file with biomass availability data."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False) as f:
        # Create test data
        data = {
            "tiam-ucl_region": ["Western Europe", "USA", "China", "India", "North America"],
            "Country": ["Germany", None, None, None, "Canada"],
            "Metric": [
                "Available biomass",
                "Available biomass",
                "Available biomass",
                "Available biomass",
                "Available biomass",
            ],
            "Scenario": ["High", "Base", "Low", "Base", "High"],
            "Unit": ["Mt", "Mt", "Mt", "Mt", "Mt"],
            2024: [10.5, 20.0, 30.0, 15.0, 5.0],
            2025: [11.0, 21.0, 32.0, 16.0, 5.5],
            2030: [15.0, 25.0, 40.0, 20.0, 8.0],
            2040: [20.0, 30.0, 50.0, 25.0, 10.0],
            2050: [25.0, 35.0, 60.0, 30.0, 12.0],
        }
        df = pd.DataFrame(data)
        df.to_excel(f.name, sheet_name="Biomass availability", index=False)
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


@pytest.fixture
def biomass_excel_file_with_nan():
    """Create a temporary Excel file with NaN values."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False) as f:
        # Create test data with some NaN values
        data = {
            "tiam-ucl_region": ["Western Europe", "USA", None, "India"],
            "Country": [None, None, None, None],
            "Metric": ["Available biomass", "Available biomass", "Available biomass", "Available biomass"],
            "Scenario": ["High", "Base", "Low", "Base"],
            "Unit": ["Mt", "Mt", "Mt", "Mt"],
            2024: [10.5, None, 30.0, 15.0],
            2025: [11.0, 21.0, None, None],
            2030: [None, 25.0, 40.0, 20.0],
        }
        df = pd.DataFrame(data)
        df.to_excel(f.name, sheet_name="Biomass availability", index=False)
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


def test_read_biomass_availability_basic(biomass_excel_file):
    """Test reading basic biomass availability data from Excel."""
    availabilities = read_biomass_availability(biomass_excel_file)

    # Should have 5 regions * 5 years = 25 entries
    assert len(availabilities) == 25

    # Check first entry
    first = availabilities[0]
    assert isinstance(first, BiomassAvailability)
    assert first.region == "Western Europe"
    assert first.country == "Germany"
    assert first.metric == "Available biomass"
    assert first.scenario == "High"
    assert first.unit == "Mt"
    assert first.year == Year(2024)
    assert first.availability == 10.5

    # Check entry with no country
    usa_entries = [a for a in availabilities if a.region == "USA"]
    assert len(usa_entries) == 5  # One for each year
    assert all(a.country is None for a in usa_entries)

    # Check all years are present
    years = {a.year for a in availabilities}
    assert years == {Year(2024), Year(2025), Year(2030), Year(2040), Year(2050)}


def test_read_biomass_availability_with_nan(biomass_excel_file_with_nan):
    """Test reading biomass availability with NaN values."""
    availabilities = read_biomass_availability(biomass_excel_file_with_nan)

    # Should skip entries with NaN values in year columns
    # Also should skip rows with NaN region
    assert len(availabilities) > 0

    # Check that no availability has NaN values
    for avail in availabilities:
        assert avail.region is not None and avail.region != ""
        assert isinstance(avail.availability, float)
        assert avail.availability == avail.availability  # Not NaN

    # Check that rows with NaN region are skipped
    regions = {a.region for a in availabilities}
    assert None not in regions
    assert "" not in regions


def test_read_biomass_availability_custom_sheet_name(biomass_excel_file):
    """Test reading from a custom sheet name."""
    # This should raise an error since the sheet doesn't exist
    with pytest.raises(Exception):  # Could be ValueError or pd.errors.EmptyDataError
        read_biomass_availability(biomass_excel_file, sheet_name="NonExistentSheet")


def test_read_biomass_availability_empty_file():
    """Test reading from an empty Excel file."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False) as f:
        # Create empty Excel file with correct sheet name but no data
        df = pd.DataFrame()
        df.to_excel(f.name, sheet_name="Biomass availability", index=False)
        temp_path = Path(f.name)

    try:
        availabilities = read_biomass_availability(temp_path)
        assert len(availabilities) == 0
    finally:
        temp_path.unlink()


def test_read_biomass_availability_all_nan_years():
    """Test reading when all year values for a row are NaN."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False) as f:
        data = {
            "tiam-ucl_region": ["Western Europe", "USA"],
            "Country": [None, None],
            "Metric": ["Available biomass", "Available biomass"],
            "Scenario": ["High", "Base"],
            "Unit": ["Mt", "Mt"],
            2024: [10.5, None],
            2025: [11.0, None],
            2030: [15.0, None],
        }
        df = pd.DataFrame(data)
        df.to_excel(f.name, sheet_name="Biomass availability", index=False)
        temp_path = Path(f.name)

    try:
        availabilities = read_biomass_availability(temp_path)
        # Should only have entries for Western Europe (3 years)
        assert len(availabilities) == 3
        assert all(a.region == "Western Europe" for a in availabilities)
    finally:
        temp_path.unlink()
