"""
Tests for the simulation runner factory.
"""

from unittest.mock import MagicMock

from steelo.simulation import SimulationConfig
from steelo.bootstrap import bootstrap_simulation
from steelo.domain import Year
from steelo.simulation_types import get_default_technology_settings


def test_factory_configures_environment_from_config(tmp_path):
    """
    Tests that the runner factory correctly initializes the Environment
    using parameters from the SimulationConfig object.
    """
    # Create test data directory structure
    data_dir = tmp_path / "data"
    fixtures_dir = data_dir / "fixtures"
    fixtures_dir.mkdir(parents=True)

    # Create minimal required files
    (fixtures_dir / "tech_switches_allowed.csv").write_text(
        "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
    )
    (fixtures_dir / "cost_of_x.json").write_text("[]")
    # Create empty JSON files for all required repositories with proper structure
    (fixtures_dir / "plants.json").write_text('{"root": []}')
    (fixtures_dir / "demand_centers.json").write_text('{"root": []}')
    (fixtures_dir / "suppliers.json").write_text('{"root": []}')
    (fixtures_dir / "plant_groups.json").write_text('{"root": []}')
    (fixtures_dir / "tariffs.json").write_text('{"root": []}')
    (fixtures_dir / "subsidies.json").write_text('{"root": []}')
    (fixtures_dir / "carbon_costs.json").write_text('{"root": []}')
    (fixtures_dir / "primary_feedstocks.json").write_text('{"root": []}')
    (fixtures_dir / "input_costs.json").write_text('{"root": []}')
    (fixtures_dir / "region_emissivity.json").write_text('{"root": []}')
    (fixtures_dir / "capex.json").write_text('{"root": []}')
    (fixtures_dir / "cost_of_capital.json").write_text('{"root": []}')
    (fixtures_dir / "legal_process_connectors.json").write_text("[]")
    (fixtures_dir / "country_mappings.json").write_text("[]")
    (fixtures_dir / "hydrogen_efficiency.json").write_text("[]")
    (fixtures_dir / "hydrogen_capex_opex.json").write_text("[]")
    (fixtures_dir / "transport_emissions.json").write_text("[]")
    (fixtures_dir / "biomass_availability.json").write_text("[]")
    (data_dir / "railway_costs.json").write_text('{"root": []}')
    # Add minimal fallback material costs to satisfy validation
    (fixtures_dir / "fallback_material_costs.json").write_text(
        '[{"iso3": "DEU", "technology": "BF", "metric": "Unit material cost", "unit": "USD/t HM", "costs_by_year": {"2025": 100.0}}]'
    )

    # Create a SimulationConfig with a non-default value to verify it's used
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2026),
        master_excel_path=tmp_path / "test.xlsx",
        output_dir=tmp_path / "output",
        technology_settings=get_default_technology_settings(),
        data_dir=data_dir,  # Required for environment initialization
        capacity_limit=0.98,  # A non-default value
    )

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
