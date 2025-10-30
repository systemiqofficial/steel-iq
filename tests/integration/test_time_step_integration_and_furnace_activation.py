# Test that the time step integration is working and that furnaces go from announced to operating when timestep is reached
import pytest


from steelo.simulation_types import get_default_technology_settings

from steelo.devdata import get_furnace_group, get_plant, furnace_announcement_dates
from steelo.domain import PointInTime, Year, TimeFrame
from steelo.simulation import Simulation
from steelo.economic_models import PlantAgentsModel
from steelo.domain.events import IterationOver
from steelo.domain.models import CountryMappingService, CountryMapping


# restore_iso3_mapping fixture removed - now handled by conftest.py's preserve_iso3_to_region


@pytest.fixture
def country_mappings_for_test(bus):
    """Create mock country mappings for bus environment."""
    # Only need Germany (DEU) for this test
    mappings = [
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
        ),
    ]

    bus.env.country_mappings = CountryMappingService(mappings)
    return bus.env.country_mappings


@pytest.fixture
def mutliple_plants():
    ps = []
    for i, row in enumerate(furnace_announcement_dates):
        fg = get_furnace_group(
            fg_id=f"plant_{i}_fg_group_{i}",
            utilization_rate=0.7,
            lifetime=PointInTime(
                current=Year(2024),
                time_frame=TimeFrame(start=Year(row[1]), end=Year(row[1] + 20)),
                plant_lifetime=20,
            ),
            capacity=100,
        )
        fg.status = row[0].capitalize()
        # Set capex_renovation_share for the furnace group
        fg.capex_renovation_share = 0.4  # Default renovation share for testing
        ps.append(
            get_plant(furnace_groups=[fg], plant_id=f"plant_{i}")
        )  # one plant per furnace group, just for the purpose of ease
        ps[-1].location.iso3 = "DEU"
    return ps


def test_announced_plants_go_active(bus, mocker, mutliple_plants, country_mappings_for_test):
    # Ensure config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(2025),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
            technology_settings=get_default_technology_settings(),
        )

    def one_time_step_simulation(bus):
        # Simulation(bus=bus, economic_model=AllocationModel()).run_simulation()
        # won't be running allocation model, since I simply want to see plants coming online

        # Transition furnace groups from construction to operating status when their start year arrives
        # This must happen BEFORE any economic models run (mimicking simulation.py behavior)
        for plant in bus.uow.plants.list():
            for fg in plant.furnace_groups:
                if (
                    bus.env.year == fg.lifetime.time_frame.start
                    and fg.status.lower() in bus.env.config.announced_statuses
                ):
                    if fg.status.lower() != "construction switching technology":
                        fg.status = "operating"

        Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()

        bus.handle(IterationOver(time_step_increment=1, iron_price=550.0))  # , dc=data_collector)

    for p in mutliple_plants:
        bus.uow.plants.add(p)

    assert sum([fg.status == "Operating" for p in bus.uow.plants.list() for fg in p.furnace_groups]) == 41
    # Mocking a lot of things
    bus.env.current_demand = 150
    mocker.patch.object(bus.env, "extract_price_from_costcurve", return_value=550)
    mocker.patch.object(bus.env, "_predict_new_market_price", return_value=550)

    # Country mappings are now provided by the country_mappings_for_test fixture
    # The fixture sets up the necessary mappings for the test plants

    # Mock capex data that PlantAgentsModel needs
    # Structure should be: name_to_capex["greenfield"][region][technology]
    capex_data = {
        "greenfield": {
            "Europe": {
                "EAF": 500.0,
                "BOF": 800.0,
                "DRI": 700.0,
                "BF": 750.0,
            }
        },
        "brownfield": {
            "Europe": {
                "EAF": 400.0,
                "BOF": 600.0,
                "DRI": 550.0,
                "BF": 580.0,
            }
        },
    }
    mocker.patch.object(bus.env, "name_to_capex", capex_data)

    # Set capex_renovation_share directly instead of mocking
    bus.env.capex_renovation_share = {"EAF": 0.8, "BOF": 0.75, "DRI": 0.78, "BF": 0.77}

    # Mock cost of debt and equity that PlantAgentsModel needs
    mocker.patch.object(bus.env, "industrial_cost_of_debt", {"DEU": 0.05})
    mocker.patch.object(bus.env, "industrial_cost_of_equity", {"DEU": 0.10})

    # Initialize technology_to_product mapping if not present
    if not hasattr(bus.env, "technology_to_product"):
        bus.env.technology_to_product = {
            "EAF": "steel",
            "BOF": "steel",
            "DRI": "iron",
            "BF": "iron",
        }

    # Initialize virgin_iron_demand for PlantAgentsModel
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks={})

    mocker.patch("steelo.domain.models.Plant.evaluate_furnace_group_strategy", return_value=None)
    mocker.patch("steelo.domain.models.PlantGroup.evaluate_expansion", return_value=None)

    # Mock the demand_dict that Environment.calculate_demand() needs
    # Need to provide demand for years ahead (as many as the plant lifetime)
    demand_years = {}
    for year in range(2025, 2065):  # Provide enough years for plant lifetime lookahead
        demand_years[year] = 100 + (year - 2025) * 2  # Simple linear growth
    mocker.patch.object(bus.env, "demand_dict", {"region1": demand_years}, create=True)
    mocker.patch.object(bus.env, "calculate_demand", return_value=None)
    start_year = bus.env.year
    for year in range(start_year, 2037):
        # Set bus.env.year to match the loop iteration (needed for transition logic inside one_time_step_simulation)
        bus.env.year = Year(year)

        one_time_step_simulation(bus)

        # After the simulation that processes year 2030, check the plants
        if year == 2030:
            assert (
                sum([fg.status.lower() == "operating" for p in bus.uow.plants.list() for fg in p.furnace_groups]) == 45
            ), (
                f"Expected 45 operating plants after year 2030, got {sum([fg.status.lower() == 'operating' for p in bus.uow.plants.list() for fg in p.furnace_groups])}"
            )

    assert sum([fg.status.lower() == "operating" for p in bus.uow.plants.list() for fg in p.furnace_groups]) == 53

    # # Operating       41
    # # announced       11
    # # construction     1
    # # highest announcement date = 2035
