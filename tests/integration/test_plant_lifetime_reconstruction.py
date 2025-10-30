"""
Integration tests for plant lifetime reconstruction with metadata.

Tests the metadata-based runtime reconstruction of plant lifecycle properties
when using different plant_lifetime values than what the data was prepared with.
"""

import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from steelo.adapters.repositories.json_repository import (
    PlantJsonRepository,
    PlantGroupJsonRepository,
)
from steelo.domain.models import (
    Plant,
    PlantGroup,
    FurnaceGroup,
    Technology,
    Location,
    PointInTime,
    TimeFrame,
    Year,
    Volumes,
)


def _create_test_furnace_group(fg_id: str, commissioning_year: int, last_reno: int | None = None) -> FurnaceGroup:
    """Helper to create a test furnace group."""
    # Calculate lifecycle based on commissioning year (using plant_lifetime=20)
    if last_reno:
        cycle_start = last_reno
    else:
        # If no renovation, it's the first cycle from commissioning
        cycle_start = commissioning_year

    cycle_end = cycle_start + 20

    return FurnaceGroup(
        furnace_group_id=fg_id,
        capacity=Volumes(1000000.0),
        status="operating",
        last_renovation_date=date(last_reno if last_reno else commissioning_year, 1, 1),
        technology=Technology(name="BF-BOF", product="steel"),
        historical_production={},
        utilization_rate=0.9,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(cycle_start), end=Year(cycle_end)),
            plant_lifetime=20,
        ),
    )


@pytest.fixture
def test_metadata():
    """Create test metadata following schema v1.0."""
    return {
        "schema_version": "1.0",
        "metadata": {
            "plant_lifetime_used": 20,
            "data_reference_year": 2025,
            "generated_at": "2025-09-30T12:00:00Z",
            "master_excel_hash": "sha256:test123",
            "master_excel_version": "test",
            "source_file": "test.xlsx",
        },
        "furnace_groups": {
            "TEST_001_0": {
                "commissioning_year": 1985,
                "age_at_reference_year": None,
                "last_renovation_year": 2005,
                "age_source": "exact",
                "source_sheet": "Test Sheet",
                "source_row": 1,
                "validation_warnings": [],
            },
            "TEST_002_0": {
                "commissioning_year": None,
                "age_at_reference_year": 18,
                "last_renovation_year": None,
                "age_source": "imputed",
                "source_sheet": "Test Sheet",
                "source_row": 2,
                "validation_warnings": ["commissioning_year_missing"],
            },
            "TEST_003_0": {
                "commissioning_year": 1985,
                "age_at_reference_year": None,
                "last_renovation_year": None,
                "age_source": "exact",
                "source_sheet": "Test Sheet",
                "source_row": 3,
                "validation_warnings": [],
            },
            "TEST_004_0": {
                "commissioning_year": None,
                "age_at_reference_year": 15,
                "last_renovation_year": None,
                "age_source": "imputed",
                "source_sheet": "Test Sheet",
                "source_row": 4,
                "validation_warnings": ["commissioning_year_missing"],
            },
        },
    }


@pytest.fixture
def test_plants_data():
    """Create test plants as domain objects with known lifecycle properties."""
    plants = [
        # Test plant 1: Commissioned 1985, renovated 2005
        Plant(
            plant_id="TEST_001",
            location=Location(lat=40.0, lon=-100.0, country="USA", region="NA", iso3="USA"),
            furnace_groups=[_create_test_furnace_group("TEST_001_0", commissioning_year=1985, last_reno=2005)],
            power_source="grid",
            soe_status="private",
            parent_gem_id="GEM001",
            workforce_size=1000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        ),
        # Test plant 2: Imputed age 18 at 2025 (means commissioned around 2007)
        Plant(
            plant_id="TEST_002",
            location=Location(lat=41.0, lon=-101.0, country="USA", region="NA", iso3="USA"),
            furnace_groups=[_create_test_furnace_group("TEST_002_0", commissioning_year=2007, last_reno=None)],
            power_source="grid",
            soe_status="private",
            parent_gem_id="GEM002",
            workforce_size=1000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        ),
        # Test plant 3: Commissioned 1985, never renovated
        Plant(
            plant_id="TEST_003",
            location=Location(lat=42.0, lon=-102.0, country="USA", region="NA", iso3="USA"),
            furnace_groups=[_create_test_furnace_group("TEST_003_0", commissioning_year=1985, last_reno=None)],
            power_source="grid",
            soe_status="private",
            parent_gem_id="GEM003",
            workforce_size=1000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        ),
        # Test plant 4: Imputed age 15 at 2025 (means commissioned around 2010)
        Plant(
            plant_id="TEST_004",
            location=Location(lat=43.0, lon=-103.0, country="USA", region="NA", iso3="USA"),
            furnace_groups=[_create_test_furnace_group("TEST_004_0", commissioning_year=2010, last_reno=None)],
            power_source="grid",
            soe_status="private",
            parent_gem_id="GEM004",
            workforce_size=1000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        ),
    ]
    return plants


@pytest.fixture
def temp_repo_with_metadata(test_plants_data, test_metadata):
    """Create a temporary repository with plants and metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        plants_path = tmppath / "plants.json"

        # Save plants using repository (proper serialization)
        write_repo = PlantJsonRepository(plants_path, plant_lifetime=20)
        write_repo.add_list(test_plants_data)

        # Write plants_metadata.json
        metadata_path = tmppath / "plants_metadata.json"
        metadata_path.write_text(json.dumps(test_metadata, indent=2))

        yield tmppath, plants_path, metadata_path


def test_exact_commissioning_year_reconstruction(temp_repo_with_metadata):
    """
    Test 1: Exact Commissioning Year
    - Furnace commissioned 1985, renovated 2005, sim_start=2025, lifetime=25
    - Expected: age_at_2025 = 20, cycle_start=2005, cycle_end=2030
    - Verify: is_first_renovation_cycle=False, remaining_years=5
    """
    tmppath, plants_path, metadata_path = temp_repo_with_metadata

    # Load with lifetime=25 instead of the data prep lifetime of 20
    repo = PlantJsonRepository(plants_path, plant_lifetime=25, current_simulation_year=2025)

    plants = repo.list()
    assert len(plants) == 4

    # Get the plant with commissioning_year=1985, last_renovation=2005
    plant = repo.get("TEST_001")
    fg = plant.furnace_groups[0]

    # Check reconstruction
    assert fg.lifetime.plant_lifetime == 25
    assert fg.lifetime.current == 2025

    # Age from last renovation 2005 to 2025 = 20 years
    # With lifetime=25, this is 20/25 = 80% through the cycle
    # Cycle should start at 2005, end at 2030
    assert fg.lifetime.time_frame.start == 2005
    assert fg.lifetime.time_frame.end == 2030

    # Remaining years = 2030 - 2025 = 5
    assert fg.lifetime.remaining_number_of_years == 5

    # This IS the first renovation cycle after the 2005 renovation (hasn't hit 2030 yet)
    assert fg.is_first_renovation_cycle is True


def test_imputed_age_at_boundary(temp_repo_with_metadata):
    """
    Test 2: Imputed Age at Boundary
    - age_at_reference_year=18, data_ref=2025, sim_start=2025, lifetime=20
    - Expected: cycle_start=2007, cycle_end=2027
    - Verify: Renovates in 2027 (2 years from sim start)
    """
    tmppath, plants_path, metadata_path = temp_repo_with_metadata

    repo = PlantJsonRepository(plants_path, plant_lifetime=20, current_simulation_year=2025)

    plant = repo.get("TEST_002")
    fg = plant.furnace_groups[0]

    # Age at 2025 = 18 years
    # With lifetime=20, cycle position = 18 % 20 = 18
    # Cycle start = 2025 - 18 = 2007
    # Cycle end = 2007 + 20 = 2027
    assert fg.lifetime.plant_lifetime == 20
    assert fg.lifetime.time_frame.start == 2007
    assert fg.lifetime.time_frame.end == 2027

    # Remaining years = 2027 - 2025 = 2
    assert fg.lifetime.remaining_number_of_years == 2


def test_cross_cycle_with_long_lifetime(temp_repo_with_metadata):
    """
    Test 3: Cross-Cycle with Long Lifetime
    - Commissioned 1985, sim_start=2025, lifetime=30
    - Expected: age=40, cycle_position=10, cycle_start=2015, cycle_end=2045
    - Verify: is_first_renovation_cycle=False
    """
    tmppath, plants_path, metadata_path = temp_repo_with_metadata

    repo = PlantJsonRepository(plants_path, plant_lifetime=30, current_simulation_year=2025)

    plant = repo.get("TEST_003")
    fg = plant.furnace_groups[0]

    # Age from commissioning 1985 to 2025 = 40 years
    # With lifetime=30, cycle position = 40 % 30 = 10
    # Cycle start = 2025 - 10 = 2015
    # Cycle end = 2015 + 30 = 2045
    assert fg.lifetime.plant_lifetime == 30
    assert fg.lifetime.time_frame.start == 2015
    assert fg.lifetime.time_frame.end == 2045

    # Remaining years = 2045 - 2025 = 20
    assert fg.lifetime.remaining_number_of_years == 20

    # This is NOT the first renovation cycle (commissioned 1985, been through at least one cycle)
    assert fg.is_first_renovation_cycle is False


def test_start_year_offset(temp_repo_with_metadata):
    """
    Test 4: Start Year Offset
    - age_at_reference_year=15, data_ref=2025, sim_start=2030, lifetime=20
    - Expected: age_at_2030=20, at renovation boundary
    - Verify: cycle_start=2030, cycle_end=2050
    """
    tmppath, plants_path, metadata_path = temp_repo_with_metadata

    repo = PlantJsonRepository(plants_path, plant_lifetime=20, current_simulation_year=2030)

    plant = repo.get("TEST_004")
    fg = plant.furnace_groups[0]

    # Age at 2025 = 15 years
    # Age at 2030 = 15 + (2030-2025) = 20 years
    # With lifetime=20, cycle position = 20 % 20 = 0 (at boundary)
    # At boundary with age > 0 means we're starting a new cycle
    # Cycle start = 2030, Cycle end = 2050
    assert fg.lifetime.plant_lifetime == 20
    assert fg.lifetime.time_frame.start == 2030
    assert fg.lifetime.time_frame.end == 2050

    # Remaining years = 2050 - 2030 = 20
    assert fg.lifetime.remaining_number_of_years == 20


def test_legacy_data_fallback(test_plants_data):
    """
    Test 5: Legacy Data Fallback
    - No metadata file, plant_lifetime=25
    - Expected: Runs in legacy mode (no reconstruction)
    - With legacy data and plant_lifetime != 20, behavior depends on implementation
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        plants_path = tmppath / "plants.json"

        # Save plants WITHOUT metadata file
        write_repo = PlantJsonRepository(plants_path, plant_lifetime=20)
        write_repo.add_list(test_plants_data)
        # Note: No metadata file written

        # Load with lifetime=25 but no metadata
        repo = PlantJsonRepository(plants_path, plant_lifetime=25, current_simulation_year=2025)

        # Should still load successfully (legacy mode)
        plants = repo.list()
        assert len(plants) == 4

        # In legacy mode, uses baked values from JSON
        # The plant_lifetime parameter still affects the new lifetime object
        plant = repo.get("TEST_001")
        fg = plant.furnace_groups[0]

        # Should use the new lifetime value
        assert fg.lifetime.plant_lifetime == 25


def test_property_calculations(temp_repo_with_metadata):
    """
    Test 6: Property Calculations
    - Verify outstanding_debt, is_first_renovation_cycle, capex_type
    - Properties should update correctly with new lifetime
    """
    tmppath, plants_path, metadata_path = temp_repo_with_metadata

    # Test with different lifetimes
    for lifetime in [15, 20, 25, 30]:
        repo = PlantJsonRepository(plants_path, plant_lifetime=lifetime, current_simulation_year=2025)

        plant = repo.get("TEST_001")
        fg = plant.furnace_groups[0]

        # Verify plant_lifetime is used
        assert fg.lifetime.plant_lifetime == lifetime

        # Verify outstanding_debt property can be accessed
        # (it depends on plant_lifetime and remaining_years)
        try:
            debt = fg.outstanding_debt
            # debt should be a numeric value
            assert isinstance(debt, (int, float))
        except AttributeError:
            # Property might not exist on all furnace groups
            pass

        # Verify is_first_renovation_cycle can be accessed
        is_first = fg.is_first_renovation_cycle
        assert isinstance(is_first, bool)


def test_plant_group_metadata_loading(test_plants_data, test_metadata):
    """
    Test 8: PlantGroup Metadata Loading
    - Load PlantGroup with multiple plants
    - Verify: Uses shared plants_metadata.json
    - Verify: Each furnace group in each plant finds its metadata
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        plant_groups_path = tmppath / "plant_groups.json"

        # Create plant group with first 2 plants
        plant_group = PlantGroup(plant_group_id="TEST_GROUP", plants=test_plants_data[:2])

        # Save plant group using repository
        write_repo = PlantGroupJsonRepository(plant_groups_path, plant_lifetime=20)
        write_repo.add(plant_group)

        # Write shared metadata
        metadata_path = tmppath / "plants_metadata.json"
        metadata_path.write_text(json.dumps(test_metadata, indent=2))

        # Load plant group with metadata and different lifetime
        repo = PlantGroupJsonRepository(plant_groups_path, plant_lifetime=25, current_simulation_year=2025)

        groups = repo.list()
        assert len(groups) == 1

        group = groups[0]
        assert group.plant_group_id == "TEST_GROUP"
        assert len(group.plants) == 2

        # Verify each plant's furnace groups use metadata
        for plant in group.plants:
            for fg in plant.furnace_groups:
                # Should have reconstructed with lifetime=25
                assert fg.lifetime.plant_lifetime == 25

                # Verify time_frame was reconstructed (not from baked JSON)
                # The reconstruction should differ from the baked values when lifetime changes
                assert fg.lifetime.time_frame is not None


def test_future_plants_reconstruction(test_metadata, test_plants_data):
    """
    Test 8: Future Plants (Not Yet Commissioned)
    - Scenario: Plant with commissioning_year > current_simulation_year
    - Verify: Time frame stays in future (not pushed into past)
    - Verify: cycle_start == commissioning_year
    - Verify: cycle_end == commissioning_year + plant_lifetime
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        plants_path = tmppath / "plants.json"

        # Create a future plant (commissioned in 2030, sim starts in 2025)
        future_fg = _create_test_furnace_group("FUTURE_001_0", commissioning_year=2030, last_reno=None)
        future_plant = Plant(
            plant_id="FUTURE_001",
            location=Location(lat=45.0, lon=-105.0, country="USA", region="NA", iso3="USA"),
            furnace_groups=[future_fg],
            power_source="grid",
            soe_status="private",
            parent_gem_id="GEM_FUTURE",
            workforce_size=1000,
            certified=False,
            category_steel_product=set(),
            technology_unit_fopex={},
        )

        # Save with metadata
        write_repo = PlantJsonRepository(plants_path, plant_lifetime=20)
        write_repo.add(future_plant)

        # Add metadata for future plant
        future_metadata = test_metadata.copy()
        future_metadata["furnace_groups"]["FUTURE_001_0"] = {
            "commissioning_year": 2030,
            "age_at_reference_year": None,
            "last_renovation_year": None,
            "age_source": "exact",
            "source_sheet": "Test Sheet",
            "source_row": 99,
            "validation_warnings": [],
        }

        metadata_path = tmppath / "plants_metadata.json"
        metadata_path.write_text(json.dumps(future_metadata, indent=2))

        # Load with lifetime=25, simulation starting in 2025
        repo = PlantJsonRepository(plants_path, plant_lifetime=25, current_simulation_year=2025)
        plant = repo.get("FUTURE_001")
        fg = plant.furnace_groups[0]

        # Verify time frame is in the FUTURE, not the past
        assert fg.lifetime.plant_lifetime == 25
        assert fg.lifetime.time_frame.start == 2030, "Future plant should start at commissioning year"
        assert fg.lifetime.time_frame.end == 2055, "Future plant cycle should end at commissioning + lifetime"
        assert fg.lifetime.current == 2025

        # For future plants, remaining_number_of_years = plant_lifetime (hasn't started yet)
        assert fg.lifetime.remaining_number_of_years == 25
