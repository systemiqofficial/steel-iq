# we need some furnace groups to test the environment
import json
import tempfile
from datetime import date
from pathlib import Path
from steelo.domain.models import (
    Environment,
    BiomassAvailability,
    ProductionThreshold,
    Plant,
    PlantGroup,
    Technology,
    FurnaceGroup,
    get_new_plant_id,
)
from steelo.devdata import get_furnace_group, get_plant, get_demand_center, PointInTime, TimeFrame, Location, Year
from steelo.domain import calculate_costs
import pytest
from steelo.domain.constants import Volumes
from steelo.simulation import SimulationConfig


def create_test_environment(tech_switches_csv=None):
    """Helper to create Environment for tests without changing test logic."""
    from steelo.simulation_types import TechnologySettings

    # Default technology settings for tests - all technologies enabled from 2025
    default_tech_settings = {
        "BF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "BOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRI": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRING": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRIH2": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "ESF": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "MOE": TechnologySettings(allowed=False, from_year=2025, to_year=None),
        "BFBOF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRINGAEAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "DRIH2EAF": TechnologySettings(allowed=True, from_year=2025, to_year=None),
        "ESFEAF": TechnologySettings(allowed=False, from_year=2025, to_year=None),
    }

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=default_tech_settings,
    )
    return Environment(config=config, tech_switches_csv=tech_switches_csv)


@pytest.fixture
def mock_cost_of_x_file():
    """Create a temporary cost_of_x.json file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        data = {
            "Country code": {"0": "USA", "1": "CHN", "2": "DEU", "3": "JPN"},
            "Cost of equity - industrial assets": {"0": 0.25, "1": 0.30, "2": 0.20, "3": 0.22},
        }
        json.dump(data, f)
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


@pytest.fixture
def mock_tech_switches_file():
    """Create a temporary tech_switches_allowed.csv file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("Origin,BF,BOF,DRI,EAF\n")
        f.write("BF,NO,NO,NO,NO\n")
        f.write("BOF,NO,NO,YES,YES\n")
        f.write("DRI,NO,NO,NO,NO\n")
        f.write("EAF,NO,NO,NO,NO\n")
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink()  # Clean up


# -------------------------------------------- Test Unique Plant and Furnace Group ID Generation --------------------------------------------
class MockPlant(Plant):
    def __init__(self):
        self.plant_id = "P000000000001"


class MockFurnaceGroup:
    def __init__(self, furnace_group_id):
        self.furnace_group_id = furnace_group_id


def test_no_furnace_groups():
    plant = MockPlant()
    plant.furnace_groups = []
    assert plant.get_new_furnance_id_number() == "P000000000001"


def test_one_furnace_group():
    plant = MockPlant()
    plant.furnace_groups = [MockFurnaceGroup(furnace_group_id="P000000000001")]
    assert plant.get_new_furnance_id_number() == "P000000000001_2"


def test_multiple_furnace_groups_sorted():
    plant = MockPlant()
    plant.furnace_groups = [
        MockFurnaceGroup(furnace_group_id="P000000000001"),
        MockFurnaceGroup(furnace_group_id="P000000000001_2"),
        MockFurnaceGroup(furnace_group_id="P000000000001_3"),
        MockFurnaceGroup(furnace_group_id="P000000000001_4"),
        MockFurnaceGroup(furnace_group_id="P000000000001_5"),
        MockFurnaceGroup(furnace_group_id="P000000000001_6"),
        MockFurnaceGroup(furnace_group_id="P000000000001_7"),
        MockFurnaceGroup(furnace_group_id="P000000000001_8"),
        MockFurnaceGroup(furnace_group_id="P000000000001_9"),
        MockFurnaceGroup(furnace_group_id="P000000000001_10"),
    ]
    assert plant.get_new_furnance_id_number() == "P000000000001_11"


def test_multiple_furnace_groups_non_sorted():
    plant = MockPlant()
    plant.furnace_groups = [
        MockFurnaceGroup(furnace_group_id="P000000000001"),
        MockFurnaceGroup(furnace_group_id="P000000000001_3"),
        MockFurnaceGroup(furnace_group_id="P000000000001_2"),
        MockFurnaceGroup(furnace_group_id="P000000000001_10"),
        MockFurnaceGroup(furnace_group_id="P000000000001_7"),
        MockFurnaceGroup(furnace_group_id="P000000000001_8"),
        MockFurnaceGroup(furnace_group_id="P000000000001_9"),
        MockFurnaceGroup(furnace_group_id="P000000000001_5"),
        MockFurnaceGroup(furnace_group_id="P000000000001_11"),
        MockFurnaceGroup(furnace_group_id="P000000000001_6"),
        MockFurnaceGroup(furnace_group_id="P000000000001_4"),
    ]
    assert plant.get_new_furnance_id_number() == "P000000000001_12"


def test_sintering_furnace_groups():
    plant = MockPlant()
    plant.furnace_groups = [
        MockFurnaceGroup(furnace_group_id="P000000000001"),
        MockFurnaceGroup(furnace_group_id="P000000000001_Sintering"),
        MockFurnaceGroup(furnace_group_id="P000000000001_2"),
    ]
    assert plant.get_new_furnance_id_number() == "P000000000001_3"


def test_no_existing_ids():
    assert get_new_plant_id([]) == "P000000000001"


def test_single_existing_id():
    assert get_new_plant_id(["P000000000001"]) == "P000000000002"


def test_multiple_sequential_ids():
    plant_ids = ["P000000000001", "P000000000002", "P000000000003"]
    assert get_new_plant_id(plant_ids) == "P000000000004"


def test_unsorted_ids():
    plant_ids = ["P000000000002", "P000000000001", "P000000000003"]
    assert get_new_plant_id(plant_ids) == "P000000000004"


# Should still increment from highest, ignoring the gap
def test_gaps_in_ids():
    plant_ids = ["P000000000001", "P000000000005"]
    assert get_new_plant_id(plant_ids) == "P000000000006"


def test_large_number_id():
    plant_ids = ["P000000000999"]
    assert get_new_plant_id(plant_ids) == "P000000001000"


# ----------------------------------------------------------------------------------------------------------------------------------------------------------


class FakeTechnology:
    product = "steel"


class FakeFurnaceGroup:
    # FIXME jochen - use real furnace group and technology
    def __init__(self, furnace_group_id, UR, capacity, unit_production_cost):
        self.furnace_group_id = furnace_group_id
        self.UR = UR
        self.utilization_rate = UR
        self.capacity = capacity
        self.unit_production_cost = unit_production_cost
        self.production = self.UR * self.capacity
        self.status = "operating"
        self.technology = FakeTechnology()


# def test_plant_agent_repr(plant):
#     assert str(plant) == f"Plant: <{plant.plant_id}>"


def test_update_dynamic_costs_power_price_not_scaled():
    class DummyArray:
        def __init__(self, value: float):
            self.value = value

        def sel(self, **unused):
            return self

        @property
        def values(self):
            return self.value

    location = Location(lat=10.0, lon=20.0, country="USA", region="NAM", iso3="USA")
    technology = Technology(name="EAF", product="steel")
    furnace_group = FurnaceGroup(
        furnace_group_id="P000000000001",
        capacity=Volumes(1000.0),
        status="considered",
        last_renovation_date=date(2024, 1, 1),
        technology=technology,
        historical_production={},
        utilization_rate=0.0,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2025), end=Year(2045)),
            plant_lifetime=20,
        ),
        energy_cost_dict={"electricity": 0.06, "hydrogen": 5.0},
        bill_of_materials=None,
    )

    plant = Plant(
        plant_id="P000000000001",
        location=location,
        furnace_groups=[furnace_group],
        power_source="grid",
        soe_status="private",
        parent_gem_id="",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        technology_unit_fopex={"eaf": 50.0},
        average_steel_cost=None,
        steel_capacity=None,
    )

    plant_group = PlantGroup(plant_group_id="PG_TEST", plants=[plant])
    custom_energy_costs = {
        "power_price": DummyArray(0.05),
        "capped_lcoh": DummyArray(1.2),
    }

    update_cmds = plant_group.update_dynamic_costs_for_business_opportunities(
        current_year=Year(2025),
        consideration_time=1,
        custom_energy_costs=custom_energy_costs,
        capex_dict_all_locs={"NAM": {"EAF": 100.0}},
        cost_debt_all_locs={"USA": 0.05},
        iso3_to_region_map={"USA": "NAM"},
        global_risk_free_rate=0.03,
    )

    assert update_cmds, "Expected an UpdateDynamicCosts command to be enqueued."
    cmd = update_cmds[0]
    assert cmd.new_electricity_cost == pytest.approx(0.05)
    assert cmd.new_hydrogen_cost == pytest.approx(1.2)


def test_env_init(mock_cost_of_x_file, mock_tech_switches_file):
    repo = []
    # Scale capacities by 1000x to exceed MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE (50k tpa)
    # Original capacities: 100, 75, 133, 50, 67, 67 → production: 70k, 45k, 80k, 30k, 40k, 40k
    # Scaled to ensure all productions > 50k threshold
    for i, furnace_group in enumerate(
        [
            (0.7, 100000, 62.6),
            (0.6, 125000, 70),
            (0.6, 133000, 30),
            (0.6, 100000, 80),
            (0.6, 112000, 60),
            (0.6, 112000, 50),
        ]
    ):
        fg = FakeFurnaceGroup(
            furnace_group_id=f"Plant_{i}_0",
            UR=furnace_group[0],
            capacity=furnace_group[1],
            unit_production_cost=furnace_group[2],
        )
        repo.append(fg)
    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)
    # assert that the list of world furnace groups is the same as the repository
    assert type(env.name_to_capex) is dict


def test_cost_curve_and_price_extraction(mock_cost_of_x_file, mock_tech_switches_file):
    repo = []
    # Scale capacities to ensure production > 50k tpa threshold
    # Adjusted capacities to ensure min production: 60k (100k * 0.6), max: 140k * 0.6 = 84k
    for i, furnace_group in enumerate(
        [
            (0.7, 100000, 62.6),  # production: 70k
            (0.6, 125000, 70),  # production: 75k (was 75k→45k, now 125k→75k)
            (0.6, 140000, 30),  # production: 84k
            (0.6, 100000, 80),  # production: 60k (was 50k→30k, now 100k→60k)
            (0.6, 125000, 60),  # production: 75k (was 75k→45k, now 125k→75k)
            (0.6, 125000, 50),  # production: 75k (was 75k→45k, now 125k→75k)
        ]
    ):
        fg = FakeFurnaceGroup(
            furnace_group_id=str(i),
            UR=furnace_group[0],
            capacity=furnace_group[1],
            unit_production_cost=furnace_group[2],
        )
        repo.append(fg)
    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)
    env.generate_cost_curve(repo, lag=0)
    print(fg.capacity)
    # # assert that the cost curve is generated correctly
    # Note: capacities are multiplied by capacity_limit (0.95) in generate_cost_curve
    # Sorted by cost: 30 (140k), 50 (125k), 60 (125k), 62.6 (100k), 70 (125k), 80 (100k)
    # Cumulative capacities with 0.95 factor:
    expected_costcurve = [
        {"cumulative_capacity": 140000.0 * 0.95, "production_cost": 30.0},  # 133k
        {"cumulative_capacity": 265000.0 * 0.95, "production_cost": 50.0},  # 251.75k
        {"cumulative_capacity": 390000.0 * 0.95, "production_cost": 60.0},  # 370.5k
        {"cumulative_capacity": 490000.0 * 0.95, "production_cost": 62.6},  # 465.5k
        {"cumulative_capacity": 615000.0 * 0.95, "production_cost": 70.0},  # 584.25k
        {"cumulative_capacity": 715000.0 * 0.95, "production_cost": 80.0},  # 679.25k
    ]

    assert env.cost_curve["steel"] == expected_costcurve

    # Test extract price with scaled demand values
    # Cumulative capacities: 133k, 251.75k, 370.5k, 465.5k, 584.25k, 679.25k
    assert env.extract_price_from_costcurve(demand=400000, product="steel") == 62.6  # Between 370.5k and 465.5k
    assert env.extract_price_from_costcurve(demand=200000, product="steel") == 50.0  # Between 133k and 251.75k


@pytest.fixture
def new_furnace():
    # Scaled production to exceed MINIMUM_PRODUCTION_VOLUME_FOR_COST_CURVE (50k tpa)
    return get_furnace_group(utilization_rate=0.7, fg_id="fg_new", production=75000)


# mocker for the .unit_production_price


def test_update_and_extract(new_furnace, mocker, mock_cost_of_x_file, mock_tech_switches_file):
    furnace_groups = [new_furnace]
    # Scale capacities to ensure production > 50k tpa threshold
    for i, furnace_group in enumerate(
        [
            (0.7, 100000, 62.6),  # production: 70k
            (0.6, 125000, 70),  # production: 75k
            (0.6, 140000, 30),  # production: 84k
            (0.6, 100000, 80),  # production: 60k
            (0.6, 125000, 60),  # production: 75k
            (0.6, 125000, 50),  # production: 75k
        ]
    ):
        fg = FakeFurnaceGroup(
            furnace_group_id=f"Plant_{i}_0",
            UR=furnace_group[0],
            capacity=furnace_group[1],
            unit_production_cost=furnace_group[2],
        )
        print(fg.technology)
        furnace_groups.append(fg)
    print(fg.capacity)
    mocker.patch.object(calculate_costs, "calculate_unit_production_cost", return_value=50)

    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)
    env.generate_cost_curve(furnace_groups, lag=0)

    print("Before update: ", [fg.technology for fg in furnace_groups])
    env.update_cost_curve(furnace_groups, lag=0)

    # After adding new_furnace (~107k capacity @ mocked cost 50), cumulative capacities shift:
    # 133k @ 30, ~353k @ 50, ~472k @ 60, ...
    # Demand of 400k falls between 353k and 472k, so price should be 60
    assert env.extract_price_from_costcurve(demand=400000, product="steel") == 60


def test_predict_new_market_price(new_furnace, mocker, mock_cost_of_x_file, mock_tech_switches_file):
    repo = []
    # Scale capacities to ensure production > 50k tpa threshold
    for i, furnace_group in enumerate(
        [
            (0.7, 100000, 62.6),  # production: 70k
            (0.6, 125000, 70),  # production: 75k
            (0.6, 140000, 30),  # production: 84k
            (0.6, 100000, 80),  # production: 60k
            (0.6, 125000, 60),  # production: 75k
            (0.6, 125000, 50),  # production: 75k
        ]
    ):
        fg = FakeFurnaceGroup(
            furnace_group_id=f"Plant_{i}_0_1",
            UR=furnace_group[0],
            capacity=furnace_group[1],
            unit_production_cost=furnace_group[2],
        )

        repo.append(fg)
    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)
    env.generate_cost_curve(repo, lag=0)

    # Set cost_dict to the steel-specific part for _predict_new_market_price to work
    env.cost_dict = env.cost_dict["steel"]

    mocker.patch.object(calculate_costs, "calculate_unit_production_cost", return_value=50)

    print(env.cost_curve["steel"])

    # After adding new_furnace (~107k capacity @ mocked cost 50), cumulative capacities are:
    # 140k @ 30, 372k @ 50 (includes new_furnace), 497k @ 60, ...
    # Demand of 450k falls between 372k and 497k, so price should be 60
    assert env._predict_new_market_price(new_furnace_group=new_furnace, demand=450000) == 60


def test_unit_fopex_uses_minimum_utilization_threshold():
    fg = get_furnace_group(utilization_rate=0.01, fg_id="fg_low_util", tech_name="BOF")
    fg.tech_unit_fopex = 80.0
    fg.production_threshold = ProductionThreshold(low=0.1, high=0.95)

    # Utilisation below the configured low threshold should clamp to the threshold value
    assert fg.unit_fopex == pytest.approx(80.0 / 0.1)

    # Adjusting the threshold should change the clamped result accordingly
    fg.production_threshold = ProductionThreshold(low=0.02, high=0.95)
    assert fg.unit_fopex == pytest.approx(80.0 / 0.02)

    # For utilisations above the threshold, the raw utilisation is used
    fg.utilization_rate = 0.5
    assert fg.unit_fopex == pytest.approx(80.0 / 0.5)


@pytest.fixture
def multi_furnace_groups():
    return [
        # utilization_rate below threshold -> close furnace group
        get_furnace_group(utilization_rate=0.5, fg_id="fg_group_1"),
        # technology not optimal -> change technology
        get_furnace_group(tech_name="BF", fg_id="fg_group_2", production=80),
        # end of life reached at good utilization rate -> renovate furnace group
        get_furnace_group(
            lifetime=PointInTime(
                current=Year(2025),
                time_frame=TimeFrame(start=Year(20010), end=Year(2025)),
                plant_lifetime=20,
            ),
            fg_id="fg_group_3",
        ),
        get_furnace_group(fg_id="fg_group_4"),
    ]


def test_capacity_collection(multi_furnace_groups, mock_cost_of_x_file, mock_tech_switches_file):
    # Given a plant with mutiple furnace groups
    plants = []
    for iso3 in ["DEU", "NAM", "CHN", "AUS", "USA"]:
        plant = get_plant(
            plant_id=f"plant_{iso3}",
            furnace_groups=multi_furnace_groups,
            location=Location(iso3=iso3, country="", region="", lat=49.40768, lon=8.69079),
        )
        plants.append(plant)
    # Mocking the output of the
    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)

    # Initialize country mappings for the test
    from steelo.domain.models import CountryMapping

    country_mappings = [
        CountryMapping(
            country="Germany",
            iso2="DE",
            iso3="DEU",
            irena_name="Germany",
            region_for_outputs="Europe",
            ssp_region="EUR",
            tiam_ucl_region="Europe",
        ),
        CountryMapping(
            country="Namibia",
            iso2="NA",
            iso3="NAM",
            irena_name="Namibia",
            region_for_outputs="Subsaharan Africa",
            ssp_region="AFR",
            tiam_ucl_region="Africa",
        ),
        CountryMapping(
            country="China",
            iso2="CN",
            iso3="CHN",
            irena_name="China",
            region_for_outputs="China",
            ssp_region="CHN",
            tiam_ucl_region="China",
        ),
        CountryMapping(
            country="Australia",
            iso2="AU",
            iso3="AUS",
            irena_name="Australia",
            region_for_outputs="Oceania",
            ssp_region="OCE",
            tiam_ucl_region="Oceania",
        ),
        CountryMapping(
            country="United States",
            iso2="US",
            iso3="USA",
            irena_name="United States",
            region_for_outputs="North America",
            ssp_region="NAM",
            tiam_ucl_region="North America",
        ),
    ]
    env.initiate_country_mappings(country_mappings)

    env.update_regional_capacity(plants)

    expected_regions = {"Europe", "Subsaharan Africa", "China", "Oceania", "North America"}
    assert env.regional_steel_capacity.keys() == expected_regions
    # Each plant has 3 EAF furnace groups with capacities:
    # - fg_group_1: capacity = int(45000/0.5) = 90000 tonnes
    # - fg_group_3: capacity = int(45000/0.7) = 64285 tonnes
    # - fg_group_4: capacity = int(45000/0.7) = 64285 tonnes
    # Total EAF capacity per plant = 218570 tonnes
    assert env.regional_steel_capacity == {region: {"EAF": 218570.0} for region in expected_regions}

    # BF technology produces iron, not steel
    assert env.regional_iron_capacity == {region: {"BF": 114} for region in expected_regions}


def test_capex_reduction_ratio(multi_furnace_groups, mock_cost_of_x_file, mock_tech_switches_file):
    # Given a plant with mutiple furnace groups
    plants = []
    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)

    # Initialize country mappings for the test
    from steelo.domain.models import CountryMapping

    country_mappings = [
        CountryMapping(
            country="Germany",
            iso2="DE",
            iso3="DEU",
            irena_name="Germany",
            region_for_outputs="Europe",
            ssp_region="EUR",
            tiam_ucl_region="Europe",
        ),
    ]
    env.initiate_country_mappings(country_mappings)

    for idx, iso3 in enumerate(["DEU", "DEU"]):
        plant = get_plant(
            furnace_groups=multi_furnace_groups,
            location=Location(iso3=iso3, country="", region="", lat=49.40768, lon=8.69079),
        )
        plants.append(plant)
        env.update_regional_capacity(plants)
    env.update_steel_capex_reduction_ratio()
    assert env.steel_capex_reduction_ratio["Europe"]["EAF"] == pytest.approx(
        0.97, 0.3
    )  # with learning rate of 0.03 a doubling of capacity should lower capex by 3%
    plants.append(
        get_plant(
            furnace_groups=multi_furnace_groups,
            location=Location(iso3="DEU", country="", region="", lat=49.40768, lon=8.69079),
        )
    )
    env.update_regional_capacity(plants)

    assert env.steel_capex_reduction_ratio["Europe"]["EAF"] == pytest.approx(0.9528700898561145, 0.3)


def test_plant_cost_breakdown_report(multi_furnace_groups):
    import copy

    # Given a plant with mutiple furnace groups
    # Make a deep copy to avoid affecting other tests
    copied_furnace_groups = copy.deepcopy(multi_furnace_groups)
    plant = get_plant(furnace_groups=copied_furnace_groups)

    # Fix bill of materials to match expected format
    for fg in plant.furnace_groups:
        for bom_group, items in fg.technology.bill_of_materials.items():
            for item_name, item_dict in items.items():
                if isinstance(item_dict["unit_cost"], (int, float)):
                    item_dict["unit_cost"] = {"Value": item_dict["unit_cost"]}
    expected_bf_operations_and_maintenance = sum(
        [fg.production * fg.unit_fopex for fg in plant.furnace_groups if fg.technology.name == "BF"]
    )
    cost_breakdown = plant.report_cost_breakdown()
    assert cost_breakdown.keys() == {"EAF", "BF"}
    assert cost_breakdown["BF"]["O&M"] == expected_bf_operations_and_maintenance
    # BF technology bill of materials calculation:
    # From devdata.py, BF technology has:
    #   - Iron Ore: demand=2.2 units/ton, unit_cost=$1.5/unit
    #   - Coke: demand=0.4 units/ton, unit_cost=$2.0/unit
    #   - Coal: demand=8.0 units/ton, unit_cost=$0.5/unit
    #   - Gas: demand=3.0 units/ton, unit_cost=$0.5/unit
    #
    # BF furnace group requested production=80 tons (line 312) with default utilization_rate=0.7
    # In get_furnace_group: capacity = int(80/0.7) = int(114.28) = 114
    # Actual production = capacity × utilization_rate = 114 × 0.7 = 79.8 tons
    # Total Cost = Production × Demand × Unit_Cost
    #
    # Expected values with actual production=79.8:
    #   - Iron Ore: 79.8 × 2.2 × 1.5 = 263.34
    #   - Coke: 79.8 × 0.4 × 2.0 = 63.84
    #   - Coal: 79.8 × 8.0 × 0.5 = 319.2
    #   - Gas: 79.8 × 3.0 × 0.5 = 119.7
    assert cost_breakdown["BF"]["Bill of Materials"] == pytest.approx(
        {
            "Iron Ore": 263.34,
            "Coke": 63.84,
            "Coal": 319.2,
            "Gas": 119.7,
        }
    )
    # EAF technology bill of materials calculation:
    # From devdata.py, EAF technology has:
    #   - Iron: demand=0.2 units/ton, unit_cost=$2.5/unit
    #   - Scrap: demand=1.2 units/ton, unit_cost=$3.0/unit
    #   - Electricity: demand=6.0 units/ton, unit_cost=$0.75/unit
    #   - Hydrogen: demand=0.0 units/ton, unit_cost=$2.5/unit
    #   - Coal: demand=0.0 units/ton, unit_cost=$0.5/unit
    #
    # 3 EAF furnace groups:
    #   fg_group_1: production=45000, utilization=0.5 -> capacity=90000, actual_production=45000
    #   fg_group_3: production=45000, utilization=0.7 -> capacity=64285, actual_production=44999.5
    #   fg_group_4: production=45000, utilization=0.7 -> capacity=64285, actual_production=44999.5
    # Total EAF production = 45000 + 44999.5 + 44999.5 = 134999 tonnes
    #
    # Expected values with total production=134999:
    #   - Iron: 134999 × 0.2 × 2.5 = 67499.5
    #   - Scrap: 134999 × 1.2 × 3.0 = 485996.4
    #   - Electricity: 134999 × 6.0 × 0.75 = 607495.5
    #   - Hydrogen: 134999 × 0.0 × 2.5 = 0.0
    #   - Coal: 134999 × 0.0 × 0.5 = 0.0
    assert cost_breakdown["EAF"]["Bill of Materials"] == pytest.approx(
        {
            "Iron": 67499.5,
            "Scrap": 485996.4,
            "Electricity": 607495.5,
            "Hydrogen": 0.0,
            "Coal": 0.0,
        }
    )


@pytest.fixture
def multi_demand_centres():
    return [
        get_demand_center(centre_id="DEU"),
        get_demand_center(demand={2025: 15, 2026: 25}, centre_id="NAM"),
        get_demand_center(demand={2025: 5, 2026: 30}, centre_id="CHN"),
    ]


def test_demand_dict(multi_demand_centres, mock_cost_of_x_file, mock_tech_switches_file):
    env = create_test_environment(tech_switches_csv=mock_tech_switches_file)
    env.initiate_demand_dicts(multi_demand_centres)

    print(env.year)
    env.calculate_demand()
    assert env.current_demand == 30

    env.year += 1
    env.calculate_demand()

    assert env.current_demand == 75


# -------------------------------------------- Test TransportEmission --------------------------------------------
def test_transport_emission_creation():
    """Test creating a TransportEmission instance."""
    from steelo.domain.models import TransportKPI

    emission = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="iron_ore",
        ghg_factor=0.025,
        transportation_cost=50.0,
        updated_on="2024-01-01",
    )

    assert emission.reporter_iso == "USA"
    assert emission.partner_iso == "CHN"
    assert emission.commodity == "iron_ore"
    assert emission.ghg_factor == 0.025
    assert emission.transportation_cost == 50.0
    assert emission.updated_on == "2024-01-01"


def test_transport_emission_hash():
    """Test that TransportEmission instances are hashable and hash is based on key fields."""
    from steelo.domain.models import TransportKPI

    emission1 = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="iron_ore",
        ghg_factor=0.025,
        transportation_cost=50.0,
        updated_on="2024-01-01",
    )

    emission2 = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="iron_ore",
        ghg_factor=0.030,  # Different ghg_factor
        transportation_cost=55.0,  # Different cost
        updated_on="2024-01-02",  # Different date
    )

    emission3 = TransportKPI(
        reporter_iso="USA",
        partner_iso="JPN",  # Different partner
        commodity="iron_ore",
        ghg_factor=0.025,
        transportation_cost=60.0,
        updated_on="2024-01-01",
    )

    # Same key fields should have same hash
    assert hash(emission1) == hash(emission2)
    # Different key fields should have different hash
    assert hash(emission1) != hash(emission3)

    # Should be able to use in a set
    emission_set = {emission1, emission2, emission3}
    assert len(emission_set) == 2  # emission1 and emission2 should be considered the same


def test_transport_emission_equality():
    """Test equality comparison for TransportEmission."""
    from steelo.domain.models import TransportKPI

    emission1 = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="iron_ore",
        ghg_factor=0.025,
        transportation_cost=50.0,
        updated_on="2024-01-01",
    )

    emission2 = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="iron_ore",
        ghg_factor=0.025,
        transportation_cost=50.0,
        updated_on="2024-01-01",
    )

    emission3 = TransportKPI(
        reporter_iso="USA",
        partner_iso="CHN",
        commodity="steel",  # Different commodity
        ghg_factor=0.025,
        transportation_cost=50.0,
        updated_on="2024-01-01",
    )

    assert emission1 == emission2
    assert emission1 != emission3


# -------------------------------------------- Test BiomassAvailability --------------------------------------------
def test_biomass_availability_initialization():
    """Test BiomassAvailability initialization."""
    biomass = BiomassAvailability(
        region="Western Europe",
        country="Germany",
        metric="Available biomass",
        scenario="High availability",
        unit="Mt",
        year=Year(2030),
        availability=100.5,
    )

    assert biomass.region == "Western Europe"
    assert biomass.country == "Germany"
    assert biomass.metric == "Available biomass"
    assert biomass.scenario == "High availability"
    assert biomass.unit == "Mt"
    assert biomass.year == Year(2030)
    assert biomass.availability == 100.5


def test_biomass_availability_with_none_country():
    """Test BiomassAvailability with None country (regional data)."""
    biomass = BiomassAvailability(
        region="North America",
        country=None,
        metric="Available biomass",
        scenario="Base case",
        unit="Mt",
        year=Year(2025),
        availability=50.0,
    )

    assert biomass.region == "North America"
    assert biomass.country is None
    assert biomass.availability == 50.0


def test_biomass_availability_hash():
    """Test BiomassAvailability hash based on region, country, and year."""
    biomass1 = BiomassAvailability(
        region="Western Europe",
        country="Germany",
        metric="Available biomass",
        scenario="High availability",
        unit="Mt",
        year=Year(2030),
        availability=100.5,
    )

    biomass2 = BiomassAvailability(
        region="Western Europe",
        country="Germany",
        metric="Different metric",  # Different metric
        scenario="Different scenario",  # Different scenario
        unit="kt",  # Different unit
        year=Year(2030),
        availability=200.0,  # Different availability
    )

    biomass3 = BiomassAvailability(
        region="Western Europe",
        country="Germany",
        metric="Available biomass",
        scenario="High availability",
        unit="Mt",
        year=Year(2031),  # Different year
        availability=100.5,
    )

    # Same key fields (region, country, year) should have same hash
    assert hash(biomass1) == hash(biomass2)
    # Different key fields should have different hash
    assert hash(biomass1) != hash(biomass3)

    # Should be able to use in a set
    biomass_set = {biomass1, biomass2, biomass3}
    assert len(biomass_set) == 2  # biomass1 and biomass2 should be considered the same


def test_biomass_availability_year_type():
    """Test that year is properly typed as Year."""
    biomass = BiomassAvailability(
        region="Western Europe",
        country=None,
        metric="Available biomass",
        scenario="Base case",
        unit="Mt",
        year=Year(2030),
        availability=75.0,
    )

    # Year is a NewType, so it's actually an int at runtime
    assert isinstance(biomass.year, int)
    assert biomass.year == 2030
