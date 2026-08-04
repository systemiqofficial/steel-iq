# tests/unit/test_simulation_config.py

import pytest
from pathlib import Path

from steelo.simulation_types import get_default_technology_settings

from steelo.domain import Year
from steelo.domain.constants import MT_TO_T
from steelo.simulation import SimulationConfig


# Create the same fixture as in the path resolver test
@pytest.fixture
def prepared_data_dir(tmp_path):
    """Creates a temporary, valid prepared data directory structure."""
    data_dir = tmp_path / "data"
    fixtures_dir = data_dir / "fixtures"
    geo_dir = data_dir / "outputs" / "GEO"

    fixtures_dir.mkdir(parents=True)
    geo_dir.mkdir(parents=True)

    # Create some dummy files that the resolver should find
    (fixtures_dir / "plants.json").touch()
    (fixtures_dir / "demand_centers.json").touch()
    (geo_dir / "feasibility_mask.nc").touch()

    return data_dir


def test_simulation_config_has_default_parameters():
    """
    Tests that configurable parameters are fields on SimulationConfig
    with correct default values.
    """
    # This instantiation will fail until the fields are added.
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
        technology_settings=get_default_technology_settings(),
    )

    assert config.capacity_limit == 0.95
    assert config.geo_config.random_seed == 42
    assert config.active_statuses == ["operating", "operating pre-retirement", "operating switching technology"]
    assert config.capacity_limit_iron == 100 * MT_TO_T
    assert config.capacity_limit_steel == 100 * MT_TO_T
    assert config.new_capacity_share_from_new_plants == 0.4


def test_simulation_config_can_override_defaults():
    """
    Tests that default parameters can be overridden during instantiation.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
        technology_settings=get_default_technology_settings(),
        capacity_limit=0.99,  # Override default
        active_statuses=["operating"],  # Override default
    )

    assert config.capacity_limit == 0.99
    assert config.active_statuses == ["operating"]


def test_config_factory_from_data_directory(prepared_data_dir):
    """
    Tests that the from_data_directory factory correctly populates path fields.
    """
    config = SimulationConfig.from_data_directory(
        data_dir=prepared_data_dir,
        output_dir=Path("/tmp/sim_output"),
        start_year=Year(2025),
        end_year=Year(2030),
        # Override the top-level random_seed; it is propagated into geo_config in __post_init__.
        random_seed=123,
    )

    # Check that the factory correctly set the data directory
    assert config.data_dir == prepared_data_dir
    assert config.output_dir == Path("/tmp/sim_output")
    assert config.start_year == 2025
    assert config.geo_config.random_seed == 123  # Check override

    # Check that geo paths were set if they exist in the data directory
    assert config.feasibility_mask_path == prepared_data_dir / "outputs" / "GEO" / "feasibility_mask.nc"


def test_deterministic_agents_force_announcement_and_construction_probabilities_to_one():
    """
    Tests that probabilistic_agents=False makes the new-plant announcement and
    construction draws deterministic (probability 1), regardless of defaults.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
        probabilistic_agents=False,
    )

    assert config.probability_of_construction == 1.0
    assert config.probability_of_announcement == 1.0


def test_probabilistic_agents_keep_default_announcement_and_construction_probabilities():
    """
    Tests that probabilistic_agents=True (the default) preserves the stochastic
    announcement and construction probabilities.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
    )

    assert config.probabilistic_agents is True
    assert config.probability_of_construction == 0.9
    assert config.probability_of_announcement == 0.7
