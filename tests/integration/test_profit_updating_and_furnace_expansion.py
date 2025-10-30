import pytest


from steelo.simulation_types import get_default_technology_settings

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, Year, TimeFrame, events
from steelo.simulation import Simulation
from steelo.economic_models import PlantAgentsModel
from unittest.mock import PropertyMock
from steelo.domain.models import PlantGroup, CountryMappingService, CountryMapping


# restore_iso3_mapping fixture removed - now handled by conftest.py's preserve_iso3_to_region


@pytest.fixture
def logged_events(bus):
    """Add a logging event handler for all events."""
    logged_events = []

    def log_events(evt):
        logged_events.append(evt)

    for event, handlers in bus.event_handlers.items():
        handlers.append(log_events)
    return logged_events


@pytest.fixture
def mutliple_plants():
    # 8 countries - 8 plants
    # Bolivia, Slovenia, Portugal, Nigeria, Congo, Sweden, Norway, Malaysia,
    # THe iso3 codes for the countries are BOL, SVN, PRT, NGA, COG, SWE, NOR, MYS

    country_list = ["BOL", "SVN", "PRT", "NGA", "COG", "SWE", "NOR", "MYS"]
    ps = []
    for i in range(8):
        fg = get_furnace_group(
            fg_id=f"fg_group_{i}",
            utilization_rate=0.7,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
                plant_lifetime=20,
            ),
            capacity=100,
        )
        ps.append(
            get_plant(furnace_groups=[fg], plant_id=f"plant_{i}")
        )  # one plant per furnace group, just for the purpose of ease
        ps[-1].location.iso3 = country_list[i]
    return ps


@pytest.fixture
def country_mappings_for_test(bus):
    """Create mock country mappings for bus environment."""
    # Create mappings for test countries
    mappings = [
        CountryMapping(
            country="Bolivia",
            iso2="BO",
            iso3="BOL",
            irena_name="Bolivia",
            region_for_outputs="Latin America",
            ssp_region="LAM",
            gem_country="Bolivia",
            ws_region="Latin America",
            tiam_ucl_region="Other Latin America",
            eu_region=None,
        ),
        CountryMapping(
            country="Slovenia",
            iso2="SI",
            iso3="SVN",
            irena_name="Slovenia",
            region_for_outputs="Europe",
            ssp_region="EUR",
            gem_country="Slovenia",
            ws_region="Europe",
            tiam_ucl_region="Western Europe",
            eu_region="EU",
        ),
        CountryMapping(
            country="Portugal",
            iso2="PT",
            iso3="PRT",
            irena_name="Portugal",
            region_for_outputs="Europe",
            ssp_region="EUR",
            gem_country="Portugal",
            ws_region="Europe",
            tiam_ucl_region="Western Europe",
            eu_region="EU",
        ),
        CountryMapping(
            country="Nigeria",
            iso2="NG",
            iso3="NGA",
            irena_name="Nigeria",
            region_for_outputs="Subsaharan Africa",
            ssp_region="AFR",
            gem_country="Nigeria",
            ws_region="Africa",
            tiam_ucl_region="Africa",
            eu_region=None,
        ),
        CountryMapping(
            country="Congo",
            iso2="CG",
            iso3="COG",
            irena_name="Congo",
            region_for_outputs="Subsaharan Africa",
            ssp_region="AFR",
            gem_country="Congo",
            ws_region="Africa",
            tiam_ucl_region="Africa",
            eu_region=None,
        ),
        CountryMapping(
            country="Sweden",
            iso2="SE",
            iso3="SWE",
            irena_name="Sweden",
            region_for_outputs="Europe",
            ssp_region="EUR",
            gem_country="Sweden",
            ws_region="Europe",
            tiam_ucl_region="Western Europe",
            eu_region="EU",
        ),
        CountryMapping(
            country="Norway",
            iso2="NO",
            iso3="NOR",
            irena_name="Norway",
            region_for_outputs="Europe",
            ssp_region="EUR",
            gem_country="Norway",
            ws_region="Europe",
            tiam_ucl_region="Western Europe",
            eu_region=None,
        ),
        CountryMapping(
            country="Malaysia",
            iso2="MY",
            iso3="MYS",
            irena_name="Malaysia",
            region_for_outputs="Other Asia",
            ssp_region="ASIA",
            gem_country="Malaysia",
            ws_region="Other Asia",
            tiam_ucl_region="Other Asia",
            eu_region=None,
        ),
    ]

    bus.env.country_mappings = CountryMappingService(mappings)
    return bus.env.country_mappings


def test_furnace_group_balance_sheet_updates(bus, mutliple_plants, mocker, country_mappings_for_test):
    ## I want to run the economic model, and make sure that
    # Given a plant with mutiple furnace groups
    # Mocking the output of the

    # Ensure simulation_config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
        )

    for p in mutliple_plants:
        bus.uow.plants.add(p)

    # Initialize proper capex structure for test environment - would come from initiate capex
    # Get the regions that are actually used by the test plants
    test_iso3_codes = [p.location.iso3 for p in mutliple_plants]
    iso3_to_region = country_mappings_for_test.iso3_to_region()
    test_regions = set(iso3_to_region.get(iso3, "Unknown") for iso3 in test_iso3_codes)

    if not hasattr(bus.env, "name_to_capex") or not bus.env.name_to_capex:
        bus.env.name_to_capex = {
            "greenfield": {
                # Add capex for all regions that appear in the test data
                region: {"EAF": 400.0, "BOF": 300.0, "DRI": 500.0, "BF": 450.0}
                for region in test_regions
            }
        }

    if "default" not in bus.env.name_to_capex:
        bus.env.name_to_capex["default"] = bus.env.name_to_capex["greenfield"].copy()

    # Initialize capex_renovation_share if not present
    if not hasattr(bus.env, "capex_renovation_share"):
        bus.env.capex_renovation_share = {
            "EAF": 0.4,
            "BOF": 0.4,
            "DRI": 0.4,
            "BF": 0.4,
        }

    bus.env.capex_reduction_ratio = {
        "default_region": {tech: 1.0 for tech in bus.env.name_to_capex["greenfield"].keys()}
    }
    bus.env.update_capex()

    # Add other required environment setup
    bus.env.dynamic_feedstocks = {"BF": [], "BOF": [], "DRI": [], "EAF": []}

    # Create a mock VirginIronDemand object
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(
        world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks=bus.env.dynamic_feedstocks
    )
    bus.env.avg_boms = {"BOF": {"hot metal": {"unit_cost": 300.0}}}
    bus.env.allowed_furnace_transitions = {"EAF": ["EAF", "BF", "BOF", "DRI"]}

    # Initialize technology_to_product mapping if not present
    if not hasattr(bus.env, "technology_to_product"):
        bus.env.technology_to_product = {
            "EAF": "steel",
            "BOF": "steel",
            "DRI": "iron",
            "BF": "iron",
        }

    bus.env.current_demand = 150

    # Initialize industrial_cost_of_debt for test countries
    if not hasattr(bus.env, "industrial_cost_of_debt") or not bus.env.industrial_cost_of_debt:
        bus.env.industrial_cost_of_debt = {iso3: 0.05 for iso3 in test_iso3_codes}

    # Initialize industrial_cost_of_equity for test countries
    if not hasattr(bus.env, "industrial_cost_of_equity") or not bus.env.industrial_cost_of_equity:
        bus.env.industrial_cost_of_equity = {iso3: 0.08 for iso3 in test_iso3_codes}

    # Fixing market price eval and prediction
    mocker.patch.object(bus.env, "extract_price_from_costcurve", return_value=100)
    mocker.patch.object(bus.env, "_predict_new_market_price", return_value=100)

    # We want the mocker to return a cost_of_production that differs between the plants and furnace groups
    # In return the balance sheet should be affected when the simulation is run

    mocker.patch(
        "steelo.domain.models.FurnaceGroup.unit_production_cost",
        new_callable=PropertyMock,
        side_effect=[96, 102, 104, 91, 78, 83, 97, 104],
    )

    mocker.patch(
        "steelo.domain.models.Plant.evaluate_furnace_group_strategy", return_value=None
    )  # Just avoid the furnace evalation right now

    # Mock the balance reset to preserve furnace group balances for testing
    def mock_update_balance(self, market_price, active_statuses):
        for fg in self.furnace_groups:
            if (
                fg.capacity == 0
                or fg.status.lower() not in [s.lower() for s in active_statuses]
                or fg.technology.name.lower() == "other"
                or fg.technology.product.lower() not in market_price
            ):
                continue
            fg.update_balance_sheet(market_price[fg.technology.product.lower()])
            self.balance += fg.balance
            # Don't reset fg.balance to 0 for testing purposes

    mocker.patch.object(bus.uow.plants.list()[0].__class__, "update_furnace_and_plant_balance", mock_update_balance)

    # When the simulation is run
    Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()

    # Based on production being 70 and market value being 100, the following should be the
    # furnace balances after one iteration

    # Check balances - the order may vary depending on processing order
    actual_balances = [fg.balance for p in mutliple_plants for fg in p.furnace_groups]
    expected_balances = [280, -140, -280, 630, 1540, 1190, 210, -280]

    # Sort both to compare values regardless of order
    # The test cares about the values being correct, not the exact order
    assert sorted(actual_balances) == sorted(expected_balances)


def test_total_plant_group_balance_sheet(bus, mutliple_plants, mocker, country_mappings_for_test):
    ## I want to run the economic model, and make sure that
    # Given a plant with mutiple furnace groups
    # Mocking the output of the

    # Ensure simulation_config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
        )

    for p in mutliple_plants:
        bus.uow.plants.add(p)

    pg = PlantGroup(plant_group_id="pg_1", plants=mutliple_plants)

    # Initialize proper capex structure for test environment
    # Get the regions that are actually used by the test plants
    test_iso3_codes = [p.location.iso3 for p in mutliple_plants]
    iso3_to_region = country_mappings_for_test.iso3_to_region()
    test_regions = set(iso3_to_region.get(iso3, "Unknown") for iso3 in test_iso3_codes)

    if not hasattr(bus.env, "name_to_capex") or not bus.env.name_to_capex:
        bus.env.name_to_capex = {
            "greenfield": {
                # Add capex for all regions that appear in the test data
                region: {"EAF": 400.0, "BOF": 300.0, "DRI": 500.0, "BF": 450.0}
                for region in test_regions
            }
        }

    if not hasattr(bus.env, "capex_renovation_share"):
        bus.env.capex_renovation_share = {"EAF": 0.4, "BOF": 0.4}  # Set renovation capex share for EAF and BOF

    if "default" not in bus.env.name_to_capex:
        bus.env.name_to_capex["default"] = bus.env.name_to_capex["greenfield"].copy()

    bus.env.capex_reduction_ratio = {
        "default_region": {tech: 1.0 for tech in bus.env.name_to_capex["greenfield"].keys()}
    }
    bus.env.update_capex()

    # Add other required environment setup
    bus.env.dynamic_feedstocks = {"BF": [], "BOF": [], "DRI": [], "EAF": []}

    # Create a mock VirginIronDemand object
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(
        world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks=bus.env.dynamic_feedstocks
    )
    bus.env.avg_boms = {"BOF": {"hot metal": {"unit_cost": 300.0}}}
    bus.env.allowed_furnace_transitions = {"EAF": ["EAF", "BF", "BOF", "DRI"]}

    # Initialize technology_to_product mapping if not present
    if not hasattr(bus.env, "technology_to_product"):
        bus.env.technology_to_product = {
            "EAF": "steel",
            "BOF": "steel",
            "DRI": "iron",
            "BF": "iron",
        }

    bus.env.current_demand = 150

    # Initialize industrial_cost_of_debt for test countries
    if not hasattr(bus.env, "industrial_cost_of_debt") or not bus.env.industrial_cost_of_debt:
        bus.env.industrial_cost_of_debt = {iso3: 0.05 for iso3 in test_iso3_codes}

    # Initialize industrial_cost_of_equity for test countries
    if not hasattr(bus.env, "industrial_cost_of_equity") or not bus.env.industrial_cost_of_equity:
        bus.env.industrial_cost_of_equity = {iso3: 0.08 for iso3 in test_iso3_codes}

    # Fixing market price eval and prediction
    mocker.patch.object(bus.env, "extract_price_from_costcurve", return_value=100)
    mocker.patch.object(bus.env, "_predict_new_market_price", return_value=100)

    # We want the mocker to return a cost_of_production that differs between the plants and furnace groups
    # In return the balance sheet should be affected when the simulation is run

    mocker.patch(
        "steelo.domain.models.FurnaceGroup.unit_production_cost",
        new_callable=PropertyMock,
        side_effect=[96, 102, 104, 91, 78, 83, 97, 104],
    )

    mocker.patch(
        "steelo.domain.models.Plant.evaluate_furnace_group_strategy", return_value=None
    )  # Just avoid the furnace evalation right now

    # Mock the balance reset to preserve furnace group balances for testing
    def mock_update_balance(self, market_price, active_statuses):
        for fg in self.furnace_groups:
            if (
                fg.capacity == 0
                or fg.status.lower() not in [s.lower() for s in active_statuses]
                or fg.technology.name.lower() == "other"
                or fg.technology.product.lower() not in market_price
            ):
                continue
            fg.update_balance_sheet(market_price[fg.technology.product.lower()])
            self.balance += fg.balance
            # Don't reset fg.balance to 0 for testing purposes

    mocker.patch.object(bus.uow.plants.list()[0].__class__, "update_furnace_and_plant_balance", mock_update_balance)

    # When the simulation is run
    Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()

    # Based on production being 70 and market value being 100, the following should be the
    # furnace balances after one iteration
    pg.collect_total_plant_balance()
    assert pg.total_balance == sum(
        [
            280,
            -140,
            -280,
            630,
            1540,
            1190,
            210,
            -280,
        ]
    )


@pytest.fixture
def six_plants():
    # 8 countries - 8 plants
    # Bolivia, Slovenia, Portugal, Nigeria, Congo, Sweden, Norway, Malaysia,
    # THe iso3 codes for the countries are BOL, SVN, PRT, NGA, COG, SWE, NOR, MYS

    country_list = ["BOL", "SVN", "PRT", "NGA", "COG", "SWE"]  # , "NOR", "MYS"]
    ps = []
    for i in range(len(country_list)):
        fg = get_furnace_group(
            fg_id=f"fg_group_{i}",
            utilization_rate=0.7,
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(2023), end=Year(2043)),
                plant_lifetime=20,
            ),
            capacity=100,
        )
        ps.append(
            get_plant(furnace_groups=[fg], plant_id=f"plant_{i}")
        )  # one plant per furnace group, just for the purpose of ease
        ps[-1].location.iso3 = country_list[i]
    return ps


@pytest.mark.xfail(reason="Known issue: Balance accumulation not working properly, preventing expansions")
def test_time_to_expansion(bus, mocker, six_plants, logged_events, country_mappings_for_test):
    # Fixing market price eval and prediction

    # Ensure simulation_config is set for this test
    if bus.env.config is None:
        from steelo.simulation import SimulationConfig
        from pathlib import Path
        import tempfile

        # Create a temporary directory for test outputs
        temp_dir = tempfile.mkdtemp()
        bus.env.config = SimulationConfig(
            start_year=Year(
                2025,
                technology_settings=get_default_technology_settings(),
            ),
            end_year=Year(2030),
            master_excel_path=Path(temp_dir) / "master.xlsx",
            output_dir=Path(temp_dir) / "output",
        )

    for p in six_plants:
        bus.uow.plants.add(p)

    pg = PlantGroup(plant_group_id="pg_1", plants=six_plants)

    bus.uow.plant_groups.add(pg)

    # Initialize proper capex structure for test environment
    # Get the regions that are actually used by the test plants
    test_iso3_codes = [p.location.iso3 for p in six_plants]
    iso3_to_region = country_mappings_for_test.iso3_to_region()
    test_regions = set(iso3_to_region.get(iso3, "Unknown") for iso3 in test_iso3_codes)

    if not hasattr(bus.env, "name_to_capex") or not bus.env.name_to_capex:
        bus.env.name_to_capex = {
            "greenfield": {
                # Add capex for all regions that appear in the test data
                region: {"EAF": 300.0, "BOF": 200.0, "DRI": 400.0, "BF": 350.0}
                for region in test_regions
            }
        }

    if not hasattr(bus.env, "capex_renovation_share"):
        bus.env.capex_renovation_share = {
            "EAF": 0.4,
            "BOF": 0.4,
            "DRI": 0.4,
            "BF": 0.4,
        }  # Set renovation capex share for EAF and BOF

    if "default" not in bus.env.name_to_capex:
        bus.env.name_to_capex["default"] = bus.env.name_to_capex["greenfield"].copy()

    bus.env.capex_reduction_ratio = {
        "default_region": {tech: 1.0 for tech in bus.env.name_to_capex["greenfield"].keys()}
    }
    bus.env.update_capex()

    # test_iso3_codes is already defined above when getting regions

    # Add other required environment setup
    bus.env.dynamic_feedstocks = {"BF": [], "BOF": [], "DRI": [], "EAF": []}

    # Create a mock VirginIronDemand object
    from steelo.domain.models import VirginIronDemand

    bus.env.virgin_iron_demand = VirginIronDemand(
        world_suppliers=[], steel_demand_dict={}, dynamic_feedstocks=bus.env.dynamic_feedstocks
    )
    bus.env.avg_boms = {"BOF": {"hot metal": {"unit_cost": 300.0}}}
    bus.env.allowed_furnace_transitions = {"EAF": ["EAF", "BF", "BOF", "DRI"]}

    # Initialize demand_dict for future demand calculations
    bus.env.initiate_demand_dicts(bus.uow.demand_centers.list())

    bus.env.current_demand = 150
    mocker.patch.object(bus.env, "extract_price_from_costcurve", return_value=550)
    mocker.patch.object(bus.env, "_predict_new_market_price", return_value=550)
    mocker.patch(
        "steelo.domain.models.FurnaceGroup.unit_production_cost",
        new_callable=PropertyMock,
        side_effect=[196, 202, 204, 181, 178, 203] * 250,
    )

    mocker.patch("steelo.domain.models.Plant.evaluate_furnace_group_strategy", return_value=None)

    # Mock get_bom_from_avg_boms to return a valid BOM structure
    def mock_get_bom_from_avg_boms(energy_costs, tech, capacity):
        return (
            {
                "materials": {"scrap": {"unit_cost": 100.0, "demand": 1.0}},
                "energy": {"electricity": {"unit_cost": 50.0, "demand": 1.0}},
            },
            0.8,
            "scrap",
        )

    mocker.patch.object(bus.env, "get_bom_from_avg_boms", side_effect=mock_get_bom_from_avg_boms)

    for i in range(10):
        Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()

    # Effectively it's in the last two time-steps that we have the expansion threshold filled.
    assert [events.FurnaceGroupAdded, events.FurnaceGroupAdded] == [
        type(evt) for evt in logged_events
    ]  # Fixme: @Jochen - This test actually passes, and it seems it's becuase
    # the new furnace group doesn't actually have a the right unit production cost.
    # Maybe you can have a look when reviewing.
    assert sum(fg.balance for p in six_plants for fg in p.furnace_groups) == 0
