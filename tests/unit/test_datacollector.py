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
        end_year=Year(2060),
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
        end_year=Year(2060),
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
        end_year=Year(2060),
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
        end_year=Year(2060),
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


def test_collect_new_plant_data_selects_by_origin_not_owner(mock_tech_switches_file):
    """
    Collect a GEO-origin plant even when it sits in a company plant group.

    Under the capacity policy a credit-funded greenfield is moved out of its
    indi_<iso3> group into the funding company's group while keeping
    parent_gem_id = "indi_<iso3>", so it must still be counted in
    status_counts and, once operating in the given year, appear in
    new_plant_locations. A brownfield plant in the same company group
    (company parent_gem_id) must not be collected.
    """
    year = Year(2025)

    geo_plant = get_plant(
        plant_id="plant_geo_chn",
        furnace_groups=[
            get_furnace_group(
                fg_id="geo_fg",
                lifetime=PointInTime(
                    current=year,
                    time_frame=TimeFrame(start=year, end=Year(2045)),
                    plant_lifetime=20,
                ),
            ),
        ],
        location=Location(iso3="CHN", country="", region="", lat=30.0, lon=110.0),
    )
    geo_plant.parent_gem_id = "indi_CHN"

    brownfield_plant = get_plant(
        plant_id="plant_brownfield",
        furnace_groups=[get_furnace_group(fg_id="brownfield_fg")],
        location=Location(iso3="CHN", country="", region="", lat=31.0, lon=111.0),
    )

    company_group = PlantGroup(plant_group_id="E100000000123", plants=[geo_plant, brownfield_plant])

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)

    with tempfile.TemporaryDirectory() as temp_dir:
        data_collector = DataCollector([company_group], env, output_dir=Path(temp_dir))
        data_collector.collect_new_plant_data(year)

    # Only the indi-origin plant's furnace group is counted, despite the company group id.
    assert data_collector.status_counts["steel"][year]["EAF"]["operating"] == 1
    # It started operating this year, so its location is on the map; the brownfield one is not.
    assert data_collector.new_plant_locations["steel"][year] == [{"lat": 30.0, "lon": 110.0}]


def _greenfield_status_collector(year, tmp_dir, mock_tech_switches_file):
    """Build a DataCollector over one indi-origin plant with operating, construction and considered furnace groups."""
    from steelo.domain.models import Volumes

    operating_fg = get_furnace_group(fg_id="geo_fg_operating", capacity=Volumes(100000), utilization_rate=0.5)
    construction_fg = get_furnace_group(fg_id="geo_fg_construction", capacity=Volumes(200000), utilization_rate=0.7)
    construction_fg.status = "construction"
    considered_fg = get_furnace_group(fg_id="geo_fg_considered", capacity=Volumes(300000), utilization_rate=0.0)
    considered_fg.status = "considered"
    considered_fg.historical_npv_business_opportunities = {int(year): 1234.5}

    geo_plant = get_plant(
        plant_id="plant_geo_chn",
        furnace_groups=[operating_fg, construction_fg, considered_fg],
        location=Location(iso3="CHN", country="", region="China", lat=30.0, lon=110.0),
    )
    geo_plant.parent_gem_id = "indi_CHN"
    plant_group = PlantGroup(plant_group_id="indi_CHN", plants=[geo_plant])

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)
    return DataCollector([plant_group], env, output_dir=tmp_dir)


def test_collect_new_plant_data_records_greenfield_status_rows(tmp_path, mock_tech_switches_file):
    """
    Record one flat snapshot row per greenfield furnace group and year.

    Production and utilisation are taken as-is for operating groups but zeroed
    for non-operating statuses, whose utilization_rate can hold a stale value.
    """
    year = Year(2025)
    data_collector = _greenfield_status_collector(year, tmp_path, mock_tech_switches_file)
    data_collector.collect_new_plant_data(year)

    rows = {row["furnace_group_id"]: row for row in data_collector.greenfield_status_rows}
    assert len(rows) == 3
    assert rows["geo_fg_operating"] == {
        "year": 2025,
        "furnace_group_id": "geo_fg_operating",
        "plant_id": "plant_geo_chn",
        "plant_group_id": "indi_CHN",
        "product": "steel",
        "technology": "EAF",
        "reductant": "",
        "status": "operating",
        "geo_key": "CHN",
        "region": "China",
        "lat": 30.0,
        "lon": 110.0,
        "capacity": 100000.0,
        "production": 50000.0,
        "utilization_rate": 0.5,
        "opportunity_npv": None,
    }
    # The stale utilisation on the construction group must not book production.
    assert rows["geo_fg_construction"]["production"] == 0.0
    assert rows["geo_fg_construction"]["utilization_rate"] == 0.0
    assert rows["geo_fg_construction"]["capacity"] == 200000.0
    # The considered group carries this year's opportunity NPV.
    assert rows["geo_fg_considered"]["status"] == "considered"
    assert rows["geo_fg_considered"]["opportunity_npv"] == 1234.5


def test_write_greenfield_status_csv_round_trips_rows(tmp_path, mock_tech_switches_file):
    """Write the collected snapshot rows to data/greenfield_status_timeseries.csv and read them back."""
    import csv

    year = Year(2025)
    data_collector = _greenfield_status_collector(year, tmp_path, mock_tech_switches_file)
    data_collector.collect_new_plant_data(year)

    path = data_collector.write_greenfield_status_csv(tmp_path / "data")
    assert path == tmp_path / "data" / "greenfield_status_timeseries.csv"

    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    by_fg = {row["furnace_group_id"]: row for row in rows}
    assert by_fg["geo_fg_operating"]["year"] == "2025"
    assert by_fg["geo_fg_operating"]["production"] == "50000.0"
    assert by_fg["geo_fg_operating"]["opportunity_npv"] == ""
    assert by_fg["geo_fg_construction"]["status"] == "construction"
    assert by_fg["geo_fg_considered"]["opportunity_npv"] == "1234.5"


def test_write_greenfield_status_csv_without_rows_writes_nothing(tmp_path, mock_tech_switches_file):
    """Return None and write no file when no greenfield rows were collected."""
    data_collector = _greenfield_status_collector(Year(2025), tmp_path, mock_tech_switches_file)

    assert data_collector.write_greenfield_status_csv(tmp_path / "data") is None
    assert not (tmp_path / "data").exists()
