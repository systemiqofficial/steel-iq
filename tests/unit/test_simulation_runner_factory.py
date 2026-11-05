"""
Tests for the simulation runner factory.
"""

from unittest.mock import MagicMock

from steelo.bootstrap import bootstrap_simulation


def test_factory_configures_environment_from_config(minimal_simulation_config):
    """
    Tests that the runner factory correctly initializes the Environment
    using parameters from the SimulationConfig object.
    """
    # Set a non-default value to verify it's used
    config = minimal_simulation_config
    config.capacity_limit = 0.98

    # Mock the repository to avoid loading real data
    mock_repository = MagicMock()
    mock_repository.plants.list.return_value = []
    mock_repository.demand_centers.list.return_value = []
    mock_repository.suppliers.list.return_value = []
    mock_repository.plant_groups.list.return_value = []

    config._repository = mock_repository

    # Mock JSON repository with country mappings
    from steelo.domain.models import CountryMapping

    mock_json_repository = MagicMock()
    mock_json_repository.country_mappings = MagicMock()
    mock_json_repository.country_mappings.get_all.return_value = [
        CountryMapping(
            country="Germany",
            iso2="DE",
            iso3="DEU",
            irena_name="Germany",
            region_for_outputs="Europe",
            ssp_region="EUR",
            gem_country="Germany",
            ws_region="Europe",
            tiam_ucl_region="Western Europe",
            eu_region="EU",
        )
    ]
    mock_json_repository.capex = MagicMock()
    mock_json_repository.capex.list.return_value = []

    config._json_repository = mock_json_repository

    # Create the runner
    runner = bootstrap_simulation(config)

    # Assert that the environment inside the bus was configured correctly
    assert runner.bus.env.config == config
    assert runner.bus.env.config.capacity_limit == 0.98
