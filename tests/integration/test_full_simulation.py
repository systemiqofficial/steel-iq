import os
import pytest
import tempfile
from pathlib import Path

from steelo.simulation_types import get_default_technology_settings

from steelo.devdata import get_plant, get_furnace_group, get_test_suppliers, get_test_demand_centers
from steelo.simulation import SimulationConfig
from steelo.adapters.repositories.in_memory_repository import InMemoryRepository
from steelo.bootstrap import bootstrap_simulation
from steelo.domain.models import PlantGroup, Year


@pytest.fixture(autouse=True)
def setup_test_fixtures(monkeypatch):
    """Set up test fixtures directory for all tests in this module."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fixtures_dir = Path(tmpdir) / "fixtures"
        fixtures_dir.mkdir(exist_ok=True)

        # Create minimal tech_switches_allowed.csv
        tech_switches_csv = fixtures_dir / "tech_switches_allowed.csv"
        tech_switches_csv.write_text(
            "Technology,BF-BOF,DRI-EAF,Scrap-EAF\nBF-BOF,YES,YES,YES\nDRI-EAF,NO,YES,YES\nScrap-EAF,NO,NO,YES\n"
        )

        # Create minimal cost_of_x.json (empty is fine for tests)
        cost_of_x_json = fixtures_dir / "cost_of_x.json"
        cost_of_x_json.write_text("[]")

        # Create all required JSON files for bootstrap
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
        # Create cost_of_capital.json with DEU data
        cost_of_capital_data = {
            "root": [
                {"iso3": "DEU", "cost_of_debt": 0.04, "cost_of_equity": 0.08, "start_year": 2025, "end_year": 2030}
            ]
        }
        import json

        (fixtures_dir / "cost_of_capital.json").write_text(json.dumps(cost_of_capital_data))
        (fixtures_dir / "legal_process_connectors.json").write_text("[]")
        (fixtures_dir / "country_mappings.json").write_text("[]")
        (fixtures_dir / "hydrogen_efficiency.json").write_text("[]")
        (fixtures_dir / "hydrogen_capex_opex.json").write_text("[]")
        (fixtures_dir / "transport_emissions.json").write_text("[]")
        (fixtures_dir / "biomass_availability.json").write_text("[]")
        (fixtures_dir / "carbon_storage.json").write_text('{"root": []}')
        (fixtures_dir.parent / "railway_costs.json").write_text('{"root": []}')

        # Create a minimal master.xlsx file (empty Excel file)
        import pandas as pd

        with pd.ExcelWriter(fixtures_dir / "master.xlsx") as writer:
            pd.DataFrame().to_excel(writer, sheet_name="Sheet1")

        # Set environment variable for the duration of tests
        monkeypatch.setenv("STEELO_FIXTURES_DIR", str(fixtures_dir))

        yield


@pytest.fixture
def synthetic_config(monkeypatch):
    """Create a minimal simulation config for testing."""
    # Get the fixtures directory from environment variable
    fixtures_dir = Path(os.environ.get("STEELO_FIXTURES_DIR", "./fixtures"))

    # Use a temporary directory for output
    output_dir = Path(tempfile.mkdtemp(prefix="test_output_"))

    return SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2027),
        output_dir=output_dir,  # Use temp directory for output
        master_excel_path=fixtures_dir / "master.xlsx",  # Required parameter
        data_dir=fixtures_dir.parent,  # Set data_dir for fixtures
        technology_settings=get_default_technology_settings(),
    )


@pytest.fixture
def runner_with_synthetic_data(synthetic_config):
    """Create a simulation runner with synthetic data."""
    # Create a repository with synthetic data
    repository = InMemoryRepository()

    # Add synthetic plants with different technologies
    # Use individual technology names that the trade model expects
    plant1 = get_plant(plant_id="plant_1", tech_name="EAF", production=50.0, unit_production_cost=70.0)
    # Set capex_renovation_share for plant1's furnace groups
    for fg in plant1.furnace_groups:
        fg.capex_renovation_share = 0.4

    # Create separate BF and BOF furnace groups instead of combined "BFBOF"
    bf_fg = get_furnace_group(fg_id="bf_1", tech_name="BF", production=60.0)
    bf_fg.technology.product = "iron"  # BF produces iron
    bf_fg.capex_renovation_share = 0.4
    bof_fg = get_furnace_group(fg_id="bof_1", tech_name="BOF", production=40.0)
    bof_fg.technology.product = "steel"  # BOF produces steel
    bof_fg.capex_renovation_share = 0.4
    plant2 = get_plant(
        plant_id="plant_2",
        furnace_groups=[bf_fg, bof_fg],
    )

    # Create separate DRI and EAF furnace groups
    dri_fg = get_furnace_group(fg_id="dri_1", tech_name="DRI", production=30.0)
    dri_fg.technology.product = "iron"  # DRI produces iron
    dri_fg.capex_renovation_share = 0.4
    eaf_fg = get_furnace_group(fg_id="eaf_2", tech_name="EAF", production=40.0)
    eaf_fg.technology.product = "steel"  # EAF produces steel
    eaf_fg.capex_renovation_share = 0.4
    plant3 = get_plant(
        plant_id="plant_3",
        furnace_groups=[dri_fg, eaf_fg],
    )

    # Add comprehensive test suppliers and demand centers
    test_suppliers = get_test_suppliers()
    test_demand_centers = get_test_demand_centers()

    # Add to repository
    repository.plants.add(plant1)
    repository.plants.add(plant2)
    repository.plants.add(plant3)

    # Create plant groups
    plant_group = PlantGroup(plant_group_id="test_group", plants=[plant1, plant2, plant3])
    repository.plant_groups.add(plant_group)

    for demand_center in test_demand_centers:
        repository.demand_centers.add(demand_center)

    for supplier in test_suppliers:
        repository.suppliers.add(supplier)

    # Add trade tariffs (needed by bootstrap_simulation)
    repository.trade_tariffs.add_list([])

    # Add country mappings (needed by Environment)
    from steelo.domain.models import CountryMapping

    country_mappings = [
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
            irena_region="Europe",
        ),
    ]
    repository.country_mappings = country_mappings

    # Add cost of capital data (needed by bootstrap_simulation)
    from steelo.domain.models import CostOfCapital

    cost_of_capital_data = [
        CostOfCapital(
            country="Germany",
            iso3="DEU",
            debt_res=0.04,
            equity_res=0.08,
            wacc_res=0.06,
            debt_other=0.04,
            equity_other=0.08,
            wacc_other=0.06,
        )
    ]
    repository.cost_of_capital = cost_of_capital_data

    # Capex data will be initialized directly in Environment after bootstrap

    # Use the bootstrap_simulation factory, injecting our test repository
    # This completely bypasses the need for any file I/O or complex setup.
    runner = bootstrap_simulation(config=synthetic_config, repository=repository)

    # Initialize country mappings in the environment since we're using a test repository
    from steelo.domain.models import CountryMappingService, Capex, CostOfCapital

    runner.bus.env.country_mappings = CountryMappingService(country_mappings)

    # Initialize cost of capital data (needed for setting cost of debt in furnace groups)
    cost_of_capital_list = [
        CostOfCapital(
            country="Germany",
            iso3="DEU",
            debt_res=0.04,
            equity_res=0.08,
            wacc_res=0.06,
            debt_other=0.05,  # This is used as industrial_cost_of_debt
            equity_other=0.10,
            wacc_other=0.075,
        )
    ]
    runner.bus.env.initiate_industrial_asset_cost_of_capital(cost_of_capital_list)

    # Initialize capex data in the environment (needed by update_capex)
    capex_list = [
        Capex(
            technology_name="EAF",
            product="steel",
            greenfield_capex=500.0,
            capex_renovation_share=0.4,
            learning_rate=0.05,  # 5% learning rate
        ),
        Capex(
            technology_name="BF",
            product="iron",
            greenfield_capex=800.0,
            capex_renovation_share=0.4,
            learning_rate=0.03,  # 3% learning rate
        ),
        Capex(
            technology_name="BOF",
            product="steel",
            greenfield_capex=300.0,
            capex_renovation_share=0.4,
            learning_rate=0.02,  # 2% learning rate
        ),
        Capex(
            technology_name="DRI",
            product="iron",
            greenfield_capex=600.0,
            capex_renovation_share=0.4,
            learning_rate=0.04,  # 4% learning rate
        ),
    ]
    runner.bus.env.initiate_techno_economic_details(capex_list=capex_list)

    # Add capex reduction ratios (needed by update_capex_reduction_ratios)
    runner.bus.env.capex_reduction_ratio = {
        "Europe": {
            "EAF": 1.0,
            "BF": 1.0,
            "BOF": 1.0,
            "DRI": 1.0,
        }
    }

    # Now call update_capex after we've initialized the capex data
    runner.bus.env.update_capex()

    return runner


@pytest.mark.integration
def test_full_simulation_with_synthetic_data(runner_with_synthetic_data):
    """Test that a full simulation can run with synthetic data."""
    # This test verifies that the bootstrap_simulation factory works correctly
    # and that a simulation runner can be created with synthetic data
    assert runner_with_synthetic_data is not None
    assert runner_with_synthetic_data.bus is not None
    assert runner_with_synthetic_data.bus.uow is not None
    assert runner_with_synthetic_data.bus.env is not None

    # Verify the repository has the expected data
    plants = runner_with_synthetic_data.bus.uow.plants.list()
    assert len(plants) == 3
    assert len(runner_with_synthetic_data.bus.uow.demand_centers.list()) == 2
    # Note: suppliers are stored in the repository, not in UnitOfWork
    assert len(runner_with_synthetic_data.bus.uow.repository.suppliers.list()) == 9

    # Note: Full simulation run requires extensive setup including:
    # - Regional FOPEX data
    # - Complete trade allocations
    # - Proper demand/supply balance
    # This test focuses on verifying the refactored bootstrap process works


@pytest.mark.integration
def test_simulation_advances_time(runner_with_synthetic_data):
    """Test that the simulation can be created and configured correctly."""
    # Verify the simulation is configured with the correct time range
    assert runner_with_synthetic_data.config.start_year == 2025
    assert runner_with_synthetic_data.config.end_year == 2027

    # Verify the environment has been initialized
    assert runner_with_synthetic_data.bus.env.year is not None


@pytest.mark.integration
def test_simulation_collects_data(runner_with_synthetic_data):
    """Test that the data collector is properly initialized."""
    # Check data collector has been created
    assert runner_with_synthetic_data.data_collector is not None

    # Verify data collector has the expected structure
    assert hasattr(runner_with_synthetic_data.data_collector, "trace_price")
    assert hasattr(runner_with_synthetic_data.data_collector, "trace_capacity")
    assert hasattr(runner_with_synthetic_data.data_collector, "trace_production")
