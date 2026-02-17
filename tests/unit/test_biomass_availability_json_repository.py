"""Tests for BiomassAvailability JSON repository."""

import pytest
import json
from pathlib import Path
import tempfile
from steelo.adapters.repositories.json_repository import (
    BiomassAvailabilityJsonRepository,
    BiomassAvailabilityInDb,
)
from steelo.domain.models import BiomassAvailability, Year


@pytest.fixture
def temp_json_path():
    """Create a temporary JSON file path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        temp_path = Path(f.name)
    yield temp_path
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def sample_biomass_data():
    """Create sample biomass availability data."""
    return [
        BiomassAvailability(
            region="Western Europe",
            country="Germany",
            metric="Available biomass",
            scenario="High",
            unit="Mt",
            year=Year(2030),
            availability=100.5,
        ),
        BiomassAvailability(
            region="Western Europe",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2030),
            availability=500.0,
        ),
        BiomassAvailability(
            region="USA",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2030),
            availability=200.0,
        ),
        BiomassAvailability(
            region="USA",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2040),
            availability=250.0,
        ),
    ]


@pytest.fixture
def mock_country_mapping():
    """Create a mock country mapping that matches CountryMappingService interface."""

    class MockCountryMapping:
        def __init__(self, country: str, iso3: str, tiam_ucl_region: str = ""):
            self.country = country
            self.iso3 = iso3
            self.tiam_ucl_region = tiam_ucl_region

    class MockCountryMappingService:
        def __init__(self):
            self._mappings = {
                "Germany": MockCountryMapping("Germany", "DEU", "Western Europe"),
                "France": MockCountryMapping("France", "FRA", "Western Europe"),
                "United States": MockCountryMapping("United States", "USA", "USA"),
            }

    return MockCountryMappingService()


def test_repository_initialization(temp_json_path):
    """Test repository initialization with empty file."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    assert repo.path == temp_json_path
    assert repo.list() == []


def test_add_list_and_save(temp_json_path, sample_biomass_data):
    """Test adding a list of biomass availability data and saving."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_biomass_data)

    # Check data was added
    loaded_data = repo.list()
    assert len(loaded_data) == 4

    # Check file was written
    assert temp_json_path.exists()

    # Load from file and verify structure
    with open(temp_json_path, "r") as f:
        json_data = json.load(f)
    assert len(json_data) == 4
    assert json_data[0]["region"] == "Western Europe"
    assert json_data[0]["country"] == "Germany"
    assert json_data[0]["year"] == 2030
    assert json_data[0]["availability"] == 100.5


def test_load_existing_file(temp_json_path, sample_biomass_data):
    """Test loading from existing file."""
    # First save data
    repo1 = BiomassAvailabilityJsonRepository(temp_json_path)
    repo1.add_list(sample_biomass_data)

    # Create new repo instance and load
    repo2 = BiomassAvailabilityJsonRepository(temp_json_path)
    loaded_data = repo2.list()

    assert len(loaded_data) == 4
    assert all(isinstance(item, BiomassAvailability) for item in loaded_data)
    assert loaded_data[0].region == "Western Europe"
    assert loaded_data[0].country == "Germany"


def test_get_constraints_for_year_basic(temp_json_path, sample_biomass_data, mock_country_mapping):
    """Test basic constraint generation for a specific year."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_biomass_data)

    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    assert "bio_pci" in constraints
    assert len(constraints["bio_pci"]) > 0

    # Check USA constraint
    usa_tuple = ("USA",)
    assert usa_tuple in constraints["bio_pci"]
    assert 2030 in constraints["bio_pci"][usa_tuple]
    assert constraints["bio_pci"][usa_tuple][2030] == 200.0


def test_get_constraints_for_year_with_country(temp_json_path, mock_country_mapping):
    """Test constraint generation with country-specific data."""
    data = [
        BiomassAvailability(
            region="Western Europe",
            country="Germany",
            metric="Available biomass",
            scenario="High",
            unit="Mt",
            year=Year(2030),
            availability=100.0,
        )
    ]

    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(data)

    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    # Should map Germany to DEU
    deu_tuple = ("DEU",)
    assert deu_tuple in constraints["bio_pci"]
    assert 2030 in constraints["bio_pci"][deu_tuple]
    assert constraints["bio_pci"][deu_tuple][2030] == 100.0


def test_get_constraints_aggregation(temp_json_path, mock_country_mapping):
    """Test that multiple entries for same region are aggregated."""
    data = [
        BiomassAvailability(
            region="USA",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2030),
            availability=100.0,
        ),
        BiomassAvailability(
            region="USA",
            country=None,
            metric="Available biomass",
            scenario="High",
            unit="Mt",
            year=Year(2030),
            availability=50.0,
        ),
    ]

    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(data)

    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    usa_tuple = ("USA",)
    assert usa_tuple in constraints["bio_pci"]
    assert 2030 in constraints["bio_pci"][usa_tuple]
    assert constraints["bio_pci"][usa_tuple][2030] == 150.0  # 100 + 50


def test_get_constraints_no_data_for_year(temp_json_path, sample_biomass_data, mock_country_mapping):
    """Test constraint generation when no data exists for requested year."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_biomass_data)

    # Request year with no data
    constraints = repo.get_constraints_for_year(Year(2025), mock_country_mapping)

    assert "bio_pci" in constraints
    assert len(constraints["bio_pci"]) == 0


def test_region_mapping_western_europe(temp_json_path, mock_country_mapping):
    """Test region mapping for Western Europe."""
    data = [
        BiomassAvailability(
            region="Western Europe",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2030),
            availability=1000.0,
        )
    ]

    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(data)

    # This test expects the implementation to map Western Europe to multiple countries
    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    # Should have entries for multiple European countries
    assert "bio_pci" in constraints
    # The exact mapping depends on implementation, but should have some entries
    assert len(constraints["bio_pci"]) > 0


def test_pydantic_model_conversion():
    """Test BiomassAvailabilityInDb Pydantic model conversion."""
    biomass = BiomassAvailability(
        region="USA",
        country=None,
        metric="Available biomass",
        scenario="Base",
        unit="Mt",
        year=Year(2030),
        availability=200.0,
    )

    # Convert to Pydantic model
    db_model = BiomassAvailabilityInDb.from_domain(biomass)
    assert db_model.region == "USA"
    assert db_model.country is None
    assert db_model.year == 2030
    assert db_model.availability == 200.0

    # Convert back to domain
    domain_obj = db_model.to_domain()
    assert isinstance(domain_obj, BiomassAvailability)
    assert domain_obj.region == "USA"
    assert domain_obj.country is None
    assert domain_obj.year == Year(2030)
    assert domain_obj.availability == 200.0


# --- CO2 storage constraint tests ---


@pytest.fixture
def sample_co2_storage_data():
    """Create sample CO2 storage availability data.

    CO2 storage items differ from biomass: metric contains 'co2',
    country field holds ISO3 directly.
    """
    return [
        BiomassAvailability(
            region="",
            country="DEU",
            metric="co2_stored",
            scenario="",
            unit="t/y",
            year=Year(2030),
            availability=5_000_000.0,
        ),
        BiomassAvailability(
            region="",
            country="FRA",
            metric="co2_stored",
            scenario="",
            unit="t/y",
            year=Year(2030),
            availability=3_000_000.0,
        ),
        BiomassAvailability(
            region="",
            country="DEU",
            metric="co2_stored",
            scenario="",
            unit="t/y",
            year=Year(2040),
            availability=8_000_000.0,
        ),
    ]


def test_co2_storage_constraints_use_co2_stored_key(
    temp_json_path,
    sample_co2_storage_data,
    mock_country_mapping,
):
    """CO2 storage items produce constraints under the 'co2_stored' key."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_co2_storage_data)

    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    assert "co2_stored" in constraints
    assert len(constraints["co2_stored"]) == 2  # DEU and FRA


def test_co2_storage_country_is_iso3_directly(
    temp_json_path,
    sample_co2_storage_data,
    mock_country_mapping,
):
    """CO2 storage uses country field as ISO3 directly (no region mapping)."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_co2_storage_data)

    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    deu_tuple = ("DEU",)
    fra_tuple = ("FRA",)
    assert deu_tuple in constraints["co2_stored"]
    assert fra_tuple in constraints["co2_stored"]
    assert constraints["co2_stored"][deu_tuple][2030] == 5_000_000.0
    assert constraints["co2_stored"][fra_tuple][2030] == 3_000_000.0


def test_co2_storage_different_year(
    temp_json_path,
    sample_co2_storage_data,
    mock_country_mapping,
):
    """CO2 storage constraints filter by year correctly."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_co2_storage_data)

    constraints = repo.get_constraints_for_year(Year(2040), mock_country_mapping)

    assert "co2_stored" in constraints
    deu_tuple = ("DEU",)
    assert deu_tuple in constraints["co2_stored"]
    assert constraints["co2_stored"][deu_tuple][2040] == 8_000_000.0
    # FRA has no 2040 data
    fra_tuple = ("FRA",)
    assert fra_tuple not in constraints["co2_stored"]


def test_mixed_biomass_and_co2_constraints(
    temp_json_path,
    sample_biomass_data,
    sample_co2_storage_data,
    mock_country_mapping,
):
    """Both bio_pci and co2_stored keys present when data contains both types."""
    repo = BiomassAvailabilityJsonRepository(temp_json_path)
    repo.add_list(sample_biomass_data + sample_co2_storage_data)

    constraints = repo.get_constraints_for_year(Year(2030), mock_country_mapping)

    assert "bio_pci" in constraints
    assert "co2_stored" in constraints
    assert len(constraints["bio_pci"]) > 0
    assert len(constraints["co2_stored"]) == 2
