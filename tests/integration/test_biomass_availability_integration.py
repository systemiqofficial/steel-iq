"""Integration tests for biomass availability with trade module."""

import pytest
from pathlib import Path
import tempfile

from steelo.simulation_types import get_default_technology_settings

from steelo.domain.models import BiomassAvailability, Year, SecondaryFeedstockConstraint
from steelo.adapters.repositories.json_repository import BiomassAvailabilityJsonRepository


@pytest.fixture
def sample_biomass_data():
    """Create sample biomass availability data."""
    return [
        BiomassAvailability(
            region="Western Europe",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2030),
            availability=1000.0,
        ),
        BiomassAvailability(
            region="USA",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2030),
            availability=500.0,
        ),
        BiomassAvailability(
            region="China",
            country=None,
            metric="Available biomass",
            scenario="Base",
            unit="Mt",
            year=Year(2040),
            availability=800.0,
        ),
    ]


@pytest.fixture
def temp_biomass_json_path(sample_biomass_data):
    """Create a temporary biomass availability JSON file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        temp_path = Path(f.name)

    # Create and populate repository
    repo = BiomassAvailabilityJsonRepository(temp_path)
    repo.add_list(sample_biomass_data)

    yield temp_path
    if temp_path.exists():
        temp_path.unlink()


@pytest.mark.skip(reason="Needs refactoring to work with new bootstrap architecture")
def test_biomass_constraints_loaded_into_environment(temp_biomass_json_path, tmp_path):
    """Test that biomass constraints are properly loaded from repository."""
    # Create a biomass repository
    repo = BiomassAvailabilityJsonRepository(temp_biomass_json_path)

    # Get constraints for year 2030
    constraints_2030 = repo.get_constraints_for_year(Year(2030), None)

    # Verify we got the expected constraints
    assert "bio-pci" in constraints_2030

    # Check that we have the correct regions
    # USA should be there
    usa_tuple = ("USA",)
    assert usa_tuple in constraints_2030["bio-pci"]
    assert constraints_2030["bio-pci"][usa_tuple] == {2030: 500.0}

    # Western Europe is mapped to individual country codes
    # Check that we have a tuple with multiple European country codes
    we_countries = None
    for region_tuple in constraints_2030["bio-pci"]:
        if len(region_tuple) > 1 and "DEU" in region_tuple:  # Germany is in Western Europe
            we_countries = region_tuple
            break

    assert we_countries is not None
    assert constraints_2030["bio-pci"][we_countries] == {2030: 1000.0}

    # Convert to SecondaryFeedstockConstraint objects
    constraint_objects = []
    for commodity, regions in constraints_2030.items():
        for region_tuple, year_constraints in regions.items():
            constraint = SecondaryFeedstockConstraint(
                secondary_feedstock_name=commodity,
                region_iso3s=list(region_tuple),
                maximum_constraint_per_year={Year(y): v for y, v in year_constraints.items()},
            )
            constraint_objects.append(constraint)

    # Verify we created the constraint objects
    assert len(constraint_objects) == 2  # USA and Western Europe
    assert all(c.secondary_feedstock_name == "bio-pci" for c in constraint_objects)

    # Check that we can filter by year using the domain model
    from steelo.domain.models import Environment
    from steelo.simulation import SimulationConfig

    # Create a minimal config and environment
    config = SimulationConfig(
        start_year=Year(2030),
        end_year=Year(2040),
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config)
    env.year = Year(2030)
    env.secondary_feedstock_constraints = constraint_objects

    # Get relevant constraints for current year
    relevant = env.relevant_secondary_feedstock_constraints(env.secondary_feedstock_constraints, env.year)
    assert relevant is not None
    assert "bio-pci" in relevant
    assert len(relevant["bio-pci"]) == 2


@pytest.mark.skip(reason="Needs refactoring to work with new bootstrap architecture")
def test_biomass_constraints_change_with_year(temp_biomass_json_path, tmp_path):
    """Test that biomass constraints change correctly when year changes."""
    # Create repository
    repo = BiomassAvailabilityJsonRepository(temp_biomass_json_path)

    # Get all constraints and merge them
    all_constraints = {}
    for year in [2030, 2040]:
        year_constraints = repo.get_constraints_for_year(Year(year), None)
        for commodity, region_data in year_constraints.items():
            if commodity not in all_constraints:
                all_constraints[commodity] = {}
            for region_tuple, year_dict in region_data.items():
                if region_tuple not in all_constraints[commodity]:
                    all_constraints[commodity][region_tuple] = {}
                all_constraints[commodity][region_tuple].update(year_dict)

    # Convert to SecondaryFeedstockConstraint objects
    constraint_objects = []
    for commodity, regions in all_constraints.items():
        for region_tuple, year_constraints in regions.items():
            constraint = SecondaryFeedstockConstraint(
                secondary_feedstock_name=commodity,
                region_iso3s=list(region_tuple),
                maximum_constraint_per_year={Year(y): v for y, v in year_constraints.items()},
            )
            constraint_objects.append(constraint)

    # Create environment
    from steelo.domain.models import Environment
    from steelo.simulation import SimulationConfig

    config = SimulationConfig(
        start_year=Year(2030),
        end_year=Year(2040),
        master_excel_path=tmp_path / "master.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config)
    env.secondary_feedstock_constraints = constraint_objects

    # Test year 2030
    env.year = Year(2030)
    relevant_2030 = env.relevant_secondary_feedstock_constraints(env.secondary_feedstock_constraints, env.year)

    assert "bio-pci" in relevant_2030
    assert len(relevant_2030["bio-pci"]) == 2  # Western Europe and USA

    # Test year 2040
    env.year = Year(2040)
    relevant_2040 = env.relevant_secondary_feedstock_constraints(env.secondary_feedstock_constraints, env.year)

    assert "bio-pci" in relevant_2040
    # In 2040 we have China data (which gets mapped to CHN)
    # Check for CHN or China
    found_china = False
    for region_tuple in relevant_2040["bio-pci"]:
        if "CHN" in region_tuple or "China" in region_tuple:
            found_china = True
            assert relevant_2040["bio-pci"][region_tuple] == 800.0
            break
    assert found_china

    # Test year with no data
    env.year = Year(2025)
    relevant_2025 = env.relevant_secondary_feedstock_constraints(env.secondary_feedstock_constraints, env.year)

    # Should have no constraints for 2025
    assert relevant_2025 == {}  # No constraints for 2025
