import json
import tempfile
from pathlib import Path

from steelo.simulation_types import get_default_technology_settings

from steelo.simulation import SimulationConfig
from steelo.domain.models import Environment, PlantGroup
from steelo.devdata import get_furnace_group, get_plant, PointInTime, TimeFrame, Location, Year
import pytest

from steelo.domain.datacollector import DataCollector


@pytest.fixture
def country_mappings():
    """Create mock country mappings for testing."""
    from steelo.domain.models import CountryMapping

    # Create mock mappings for test ISO3 codes
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
        CountryMapping(
            country="Namibia",
            iso2="NA",
            iso3="NAM",
            irena_name="Namibia",
            region_for_outputs="Subsaharan Africa",
            ssp_region="AFR",
            gem_country="Namibia",
            ws_region="Africa",
            tiam_ucl_region="Africa",
            eu_region=None,
        ),
        CountryMapping(
            country="China",
            iso2="CN",
            iso3="CHN",
            irena_name="China",
            region_for_outputs="China",
            ssp_region="CHA",
            gem_country="China",
            ws_region="China",
            tiam_ucl_region="China",
            eu_region=None,
        ),
        CountryMapping(
            country="Australia",
            iso2="AU",
            iso3="AUS",
            irena_name="Australia",
            region_for_outputs="Oceania",
            ssp_region="ANZ",
            gem_country="Australia",
            ws_region="Oceania",
            tiam_ucl_region="Australia",
            eu_region=None,
        ),
        CountryMapping(
            country="United States",
            iso2="US",
            iso3="USA",
            irena_name="United States",
            region_for_outputs="North America",
            ssp_region="USA",
            gem_country="United States",
            ws_region="North America",
            tiam_ucl_region="United States",
            eu_region=None,
        ),
    ]

    return mappings


@pytest.fixture
def mock_cost_of_x_file():
    """Create a temporary cost_of_x.json file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        data = {
            "Country code": {"0": "USA", "1": "CHN", "2": "DEU", "3": "JPN", "4": "NAM", "5": "AUS"},
            "Cost of equity - industrial assets": {"0": 0.25, "1": 0.30, "2": 0.20, "3": 0.22, "4": 0.25, "5": 0.28},
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


def test_collect_cost_breakdown(multi_furnace_groups, mocker, mock_cost_of_x_file, mock_tech_switches_file):
    # Mock the report_bill_of_materials method to return empty dict to avoid the unit_cost issue
    mocker.patch("steelo.domain.models.FurnaceGroup.report_bill_of_materials", return_value={})

    plants = []
    for iso3 in ["DEU", "NAM", "CHN", "AUS", "USA"]:
        plant = get_plant(
            plant_id=f"plant_{iso3}",
            furnace_groups=multi_furnace_groups,
            location=Location(iso3=iso3, country="", region="", lat=49.40768, lon=8.69079),
        )
        plants.append(plant)

    # Create a PlantGroup with all plants
    plant_group = PlantGroup(plant_group_id="test_group", plants=plants)
    plant_groups = [plant_group]

    # Create Environment with new API
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        data_collector = DataCollector(plant_groups, env, output_dir=output_dir)

        cost_breakdown = data_collector.collect_cost_breakdrown()

    assert cost_breakdown.keys() == {"plant_DEU", "plant_NAM", "plant_CHN", "plant_AUS", "plant_USA"}


@pytest.fixture
def furnace_group_factory():
    """
    Fixture factory to create furnace groups with a dynamic plant_name.
    """

    def _create_furnace_groups(plant_name):
        return [
            # Utilization rate below threshold -> close furnace group
            get_furnace_group(utilization_rate=0.5, fg_id=f"{plant_name}_fg_group_1"),
            # Technology not optimal -> change technology
            get_furnace_group(tech_name="BF", fg_id=f"{plant_name}_fg_group_2", production=80),
            # End of life reached at good utilization rate -> renovate furnace group
            get_furnace_group(
                lifetime=PointInTime(
                    current=Year(2025),
                    time_frame=TimeFrame(start=Year(2010), end=Year(2025)),
                    plant_lifetime=20,
                ),
                fg_id=f"{plant_name}_fg_group_3",
            ),
            # Default furnace group
            get_furnace_group(fg_id=f"{plant_name}_fg_group_4"),
        ]

    return _create_furnace_groups


def test_collect_capacity(furnace_group_factory, mock_cost_of_x_file, mock_tech_switches_file, country_mappings):
    plants = []
    for iso3 in ["DEU", "NAM", "CHN", "AUS", "USA"]:
        plant = get_plant(
            plant_id=f"plant_{iso3}",
            furnace_groups=furnace_group_factory(iso3),
            location=Location(iso3=iso3, country="", region="", lat=49.40768, lon=8.69079),
        )
        plants.append(plant)

    # Create a PlantGroup with all plants
    plant_group = PlantGroup(plant_group_id="test_group", plants=plants)
    plant_groups = [plant_group]

    # Create Environment with new API
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)
    env.current_demand = 300
    env.generate_cost_curve(world_furnace_groups=[fg for plant in plants for fg in plant.furnace_groups], lag=0)

    # Initialize country mappings before updating regional capacity
    env.initiate_country_mappings(country_mappings)
    env.update_regional_capacity(plants)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        data_collector = DataCollector(plant_groups, env, output_dir=output_dir)

        # Test collect_capacity method
        capacity_data = data_collector.collect_capacity()

    # Verify the structure
    assert "iron" in capacity_data
    assert "steel" in capacity_data
    assert isinstance(capacity_data["iron"], dict)
    assert isinstance(capacity_data["steel"], dict)

    # def test_capacity_collection(multi_furnace_groups):
    #     # Given a plant with mutiple furnace groups
    #     plants = []
    #     for iso3 in ["DEU", "NAM", "CHN", "AUS", "USA"]:
    #         plant = get_plant(
    #             furnace_groups=multi_furnace_groups,
    #             location=Location(iso3=iso3, country="", region="", lat=49.40768, lon=8.69079),
    #         )
    #         plants.append(plant)
    #     # Mocking the output of the
    #     # Create Environment with new API
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)
    #     env.update_regional_capacity(plants)

    #     assert env.regional_steel_capacity.keys() == {"DEU", "NAM", "CHN", "AUS", "USA"}
    #     assert env.regional_steel_capacity == {
    #         iso3: {"EAF": 218, "BFBOF": 114} for iso3 in ["DEU", "NAM", "CHN", "AUS", "USA"]
    #     }

    #     assert env.regional_iron_capacity == {}

    # def test_capex_reduction_ratio(multi_furnace_groups):
    #     # Given a plant with mutiple furnace groups
    #     plants = []
    #     # Create Environment with new API
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2050),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)


#     for idx, iso3 in enumerate(["DEU", "DEU"]):
#         plant = get_plant(
#             furnace_groups=multi_furnace_groups,
#             location=Location(iso3=iso3, country="", region="", lat=49.40768, lon=8.69079),
#         )
#         plants.append(plant)
#         if idx == 0:
#             env.initialise_production_capacity(plants)
#         else:
#             env.update_regional_capacity(plants)
#     assert (
#         env.steel_capex_reduction_ratio["DEU"]["EAF"] == 0.97
#     )  # with learning rate of 0.03 a doubling of capacity should lower capex by 3%
#     plants.append(
#         get_plant(
#             furnace_groups=multi_furnace_groups,
#             location=Location(iso3="DEU", country="", region="", lat=49.40768, lon=8.69079),
#         )
#     )
#     env.update_regional_capacity(plants)

#     assert env.steel_capex_reduction_ratio["DEU"]["EAF"] == 0.9528700898561145


# def test_plant_cost_breakdown_report(multi_furnace_groups):
#     # Given a plant with mutiple furnace groups
#     # for idx, iso3 in enumerate(["DEU"]):
#     plant = get_plant(furnace_groups=multi_furnace_groups)
#     cost_breakdown = plant.report_cost_breakdown()
#     assert cost_breakdown.keys() == {"EAF", "BFBOF"}
#     assert cost_breakdown["BFBOF"]["O&M"] == 1219.55 / 10
#     assert cost_breakdown["BFBOF"]["Bill of Materials"] == pytest.approx(
#         {
#             "Iron Ore": 3.0,
#             "Scrap": 0.6,
#             "Electricity": -0.75,
#             "Hydrogen": 0.0,
#             "Coal": 5.0,
#             "Gas": 2.5,
#         }
#     )
#     print(cost_breakdown["EAF"]["Bill of Materials"])
#     assert cost_breakdown["EAF"]["Bill of Materials"] == pytest.approx(
#         {
#             "Iron": 1.5,
#             "Scrap": 10.8,
#             "Electricity": 13.5,
#             "Hydrogen": 0.0,
#             "Coal": 0.0,
#         }
#     )
