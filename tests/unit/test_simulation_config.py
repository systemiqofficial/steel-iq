# tests/unit/test_simulation_config.py

import json

import pytest
from pathlib import Path

from steelo.simulation_types import get_default_technology_settings

from steelo.domain import Year
from steelo.domain.constants import MT_TO_T
from steelo import simulation
from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
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
        end_year=Year(2060),
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
    assert config.carbon_border_export_rebates is False


def test_simulation_config_can_override_defaults():
    """
    Tests that default parameters can be overridden during instantiation.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
        technology_settings=get_default_technology_settings(),
        capacity_limit=0.99,  # Override default
        active_statuses=["operating"],  # Override default
    )

    assert config.capacity_limit == 0.99
    assert config.active_statuses == ["operating"]


def test_policy_output_dir_defaults_under_data_and_is_not_created(tmp_path):
    """
    The capacity-policy CSVs live in data/policy, but the flush creates the
    directory, so a policy-OFF run leaves no empty one behind.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
    )

    assert config.policy_output_dir == tmp_path / "output" / "data" / "policy"
    assert not config.policy_output_dir.exists()


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
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
        probabilistic_agents=False,
    )

    assert config.probability_of_construction == 1.0
    assert config.probability_of_announcement == 1.0


def test_deterministic_agents_force_full_npv_sampling_with_narrowed_priority_share():
    """
    Tests that probabilistic_agents=False forces full NPV sampling of candidate
    locations (calculate_npv_sites_share=1.0) while narrowing
    geo_config.pick_priority_sites_share to 0.005 to keep the cost of full sampling
    low, regardless of defaults or overrides.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
        probabilistic_agents=False,
        calculate_npv_sites_share=0.3,
    )

    assert config.calculate_npv_sites_share == 1.0
    assert config.geo_config.pick_priority_sites_share == 0.005


def test_probabilistic_agents_keep_default_announcement_and_construction_probabilities():
    """
    Tests that probabilistic_agents=True (the default) preserves the stochastic
    announcement and construction probabilities.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
    )

    assert config.probabilistic_agents is True
    assert config.probability_of_construction == 0.9
    assert config.probability_of_announcement == 0.7


def test_probabilistic_agents_keep_default_npv_sampling_and_priority_share():
    """
    Tests that probabilistic_agents=True (the default) preserves the default
    calculate_npv_sites_share and geo_config.pick_priority_sites_share values.
    """
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path("test.xlsx"),
        output_dir=Path("/tmp/output"),
    )

    assert config.probabilistic_agents is True
    assert config.calculate_npv_sites_share == 0.1
    assert config.geo_config.pick_priority_sites_share == 0.05


def test_config_as_json_dict_survives_a_json_round_trip_with_nested_settings_as_objects(tmp_path):
    """
    Tests that the dict written to simulation_config.json holds nested settings as JSON
    objects and lists, not Python repr strings, and leaves out the private fields.
    """
    config = SimulationConfig.for_testing(repository=InMemoryRepository(), output_dir=tmp_path)

    loaded = json.loads(json.dumps(simulation.config_as_json_dict(config)))

    assert loaded["geo_config"]["hydrogen_ceiling_percentile"] == 100.0
    assert loaded["capacity_policy"]["enabled"] is False
    assert loaded["technology_settings"]["BF"]["allowed"] is True
    assert loaded["disposal_cost_outputs"] == ["steelmaking_slag"]
    assert loaded["output_dir"] == str(tmp_path)
    assert not [key for key in loaded if key.startswith("_")]


def test_config_default_returns_a_plain_field_default():
    """A field with a plain default comes back as that default."""
    assert simulation.config_default("chosen_demand_scenario") == "BAU"
    assert simulation.config_default("lp_epsilon") == 1e-3


def test_config_default_calls_the_default_factory_for_a_fresh_copy(tmp_path):
    """A field with a ``default_factory`` comes back as a new object on every call, so callers cannot share it."""
    first = simulation.config_default("primary_products")
    second = simulation.config_default("primary_products")

    assert first == SimulationConfig.for_testing(repository=InMemoryRepository(), output_dir=tmp_path).primary_products
    assert first is not second


def test_config_default_reads_nested_geo_and_capacity_policy_fields():
    """``geo_config.<field>`` and ``capacity_policy.<field>`` name the defaults of the two nested configs."""
    assert simulation.config_default("geo_config.max_latitude") == 70.0
    assert simulation.config_default("geo_config.excluded_greenfield_technologies") == ["BOF"]
    assert simulation.config_default("capacity_policy.replacement_ratio") == 1.5
    assert simulation.config_default("capacity_policy.inter_company_swap_cutoff_year") == 2028


@pytest.mark.parametrize(
    "name",
    [
        "start_year",
        "no_such_field",
        "geo_config.no_such_field",
        "no_such_config.enabled",
    ],
)
def test_config_default_raises_key_error_for_a_name_without_a_default(name):
    """A required field, an unknown field and an unknown nested config all raise ``KeyError``."""
    with pytest.raises(KeyError):
        simulation.config_default(name)
