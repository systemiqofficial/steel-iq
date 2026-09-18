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


def _switch_command(plant_id, fg_id, old_tech, new_tech, competing_npvs, capacity=80000.0):
    """Build a ChangeFurnaceGroupTechnology command as evaluate_furnace_group_strategy returns it."""
    from steelo.domain import commands

    return commands.ChangeFurnaceGroupTechnology(
        plant_id=plant_id,
        furnace_group_id=fg_id,
        technology_name=new_tech,
        old_technology_name=old_tech,
        npv=competing_npvs[new_tech],
        cosa=50.0,
        utilisation=0.7,
        capex=100.0,
        capex_no_subsidy=100.0,
        capacity=capacity,
        remaining_lifetime=5,
        bom={},
        chosen_reductant="hydrogen",
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        capex_subsidies=[],
        debt_subsidies=[],
        competing_npvs=competing_npvs,
    )


def _switch_decisions_collector(tmp_dir, mock_tech_switches_file, probabilistic_agents=True):
    """Build a DataCollector over one brownfield BF plant and one indi-origin BF plant, each in its own group."""
    from steelo.domain.models import Volumes

    brownfield_plant = get_plant(
        plant_id="plant_brownfield",
        furnace_groups=[get_furnace_group(fg_id="brownfield_fg", tech_name="BF", capacity=Volumes(100000))],
        location=Location(iso3="CHN", country="", region="China", lat=31.0, lon=111.0),
    )
    geo_plant = get_plant(
        plant_id="plant_geo_chn",
        furnace_groups=[get_furnace_group(fg_id="geo_fg", tech_name="BF", capacity=Volumes(200000))],
        location=Location(iso3="CHN", country="", region="China", lat=30.0, lon=110.0),
    )
    geo_plant.parent_gem_id = "indi_CHN"
    plant_groups = [
        PlantGroup(plant_group_id="E100000000123", plants=[brownfield_plant]),
        PlantGroup(plant_group_id="indi_CHN", plants=[geo_plant]),
    ]

    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
        probabilistic_agents=probabilistic_agents,
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)
    return DataCollector(plant_groups, env, output_dir=tmp_dir)


def test_collect_switch_decisions_records_a_decision_once(tmp_path, mock_tech_switches_file):
    """
    Record a scheduled switch once, in its decision year, with the decision-time fields.

    Origin follows parent_gem_id, old capacity is the group's own and new capacity the
    command's, and later sightings of the same pending switch add no further rows.
    """
    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    brownfield_plant = data_collector.plant_groups[0].plants[0]
    geo_plant = data_collector.plant_groups[1].plants[0]
    npvs = {"BF": 100.0, "DRI": 300.0, "ESF": -50.0}
    brownfield_plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", npvs),
    )
    geo_plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="geo_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_geo_chn", "geo_fg", "BF", "DRI", {"DRI": 300.0}),
    )

    data_collector.collect_switch_decisions(Year(2025))
    data_collector.collect_switch_decisions(Year(2026))

    assert list(data_collector.switch_decisions) == [("brownfield_fg", 2029), ("geo_fg", 2029)]
    assert data_collector.switch_decisions[("brownfield_fg", 2029)] == {
        "decision_year": 2025,
        "switch_year": 2029,
        "construction_start_year": None,
        "executed": False,
        "origin": "brownfield",
        "plant_id": "plant_brownfield",
        "furnace_group_id": "brownfield_fg",
        "plant_group_id": "E100000000123",
        "geo_key": "CHN",
        "product": "iron",
        "old_technology": "BF",
        "new_technology": "DRI",
        "old_capacity_t": 100000.0,
        "new_capacity_t": 80000.0,
        "reductant": "hydrogen",
        "winning_npv": 300.0,
        "cosa": 50.0,
        "incumbent_npv": 100.0,
        "competing_npvs": json.dumps(npvs),
        "selection_probabilities": json.dumps({"BF": 0.25, "DRI": 0.75, "ESF": 0.0}),
    }
    geo_record = data_collector.switch_decisions[("geo_fg", 2029)]
    assert geo_record["origin"] == "greenfield"
    assert geo_record["plant_group_id"] == "indi_CHN"
    # The incumbent did not take part in the draw.
    assert geo_record["incumbent_npv"] is None


def test_collect_switch_decisions_fills_construction_start_and_executed(tmp_path, mock_tech_switches_file):
    """Fill construction_start_year when the rebuild is first observed and executed once the new technology runs."""
    from steelo.domain.models import Technology

    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    plant = data_collector.plant_groups[0].plants[0]
    fg = plant.furnace_groups[0]
    plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", {"BF": 100.0, "DRI": 300.0}),
    )
    record_key = ("brownfield_fg", 2029)

    data_collector.collect_switch_decisions(Year(2025))
    assert data_collector.switch_decisions[record_key]["construction_start_year"] is None

    fg.status = "construction switching technology"
    data_collector.collect_switch_decisions(Year(2027))
    data_collector.collect_switch_decisions(Year(2028))
    assert data_collector.switch_decisions[record_key]["construction_start_year"] == 2027
    assert data_collector.switch_decisions[record_key]["executed"] is False

    fg.technology = Technology(name="DRI", product="iron")
    fg.status = "operating"
    data_collector.collect_switch_decisions(Year(2029))
    assert data_collector.switch_decisions[record_key]["construction_start_year"] == 2027
    assert data_collector.switch_decisions[record_key]["executed"] is True


def test_collect_switch_decisions_second_switch_makes_second_row(tmp_path, mock_tech_switches_file):
    """Key decisions on (furnace_group_id, switch_year): a group that switches again gets a second row."""
    from steelo.domain.models import Technology

    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    plant = data_collector.plant_groups[0].plants[0]
    fg = plant.furnace_groups[0]
    plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", {"DRI": 300.0}),
    )
    data_collector.collect_switch_decisions(Year(2025))
    fg.technology = Technology(name="DRI", product="iron")
    fg.status = "operating"
    data_collector.collect_switch_decisions(Year(2029))

    plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2044,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "DRI", "ESF", {"DRI": 10.0, "ESF": 90.0}),
    )
    data_collector.collect_switch_decisions(Year(2040))

    first, second = data_collector.switch_decisions.values()
    assert (first["decision_year"], first["switch_year"], first["new_technology"]) == (2025, 2029, "DRI")
    assert first["executed"] is True
    assert (second["decision_year"], second["switch_year"], second["new_technology"]) == (2040, 2044, "ESF")
    assert second["old_technology"] == "DRI"
    assert second["executed"] is False


def test_collect_switch_decisions_probabilities_sum_to_one(tmp_path, mock_tech_switches_file):
    """Weight each technology by max(npv, 0) so the selection probabilities sum to 1."""
    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    data_collector.plant_groups[0].plants[0].change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command(
            "plant_brownfield", "brownfield_fg", "BF", "DRI", {"BF": 123.4, "DRI": 567.8, "ESF": -9.0, "MOE": 0.1}
        ),
    )
    data_collector.collect_switch_decisions(Year(2025))

    record = data_collector.switch_decisions[("brownfield_fg", 2029)]
    probabilities = json.loads(record["selection_probabilities"])
    assert set(probabilities) == {"BF", "DRI", "ESF", "MOE"}
    assert probabilities["ESF"] == 0.0
    assert sum(probabilities.values()) == pytest.approx(1.0)


def test_collect_switch_decisions_probabilities_blank_for_deterministic_agents(tmp_path, mock_tech_switches_file):
    """Leave selection_probabilities blank when agents are deterministic, because nothing is drawn then."""
    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file, probabilistic_agents=False)
    data_collector.plant_groups[0].plants[0].change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", {"BF": 100.0, "DRI": 300.0}),
    )
    data_collector.collect_switch_decisions(Year(2025))

    record = data_collector.switch_decisions[("brownfield_fg", 2029)]
    assert record["selection_probabilities"] is None
    assert json.loads(record["competing_npvs"]) == {"BF": 100.0, "DRI": 300.0}


def test_collect_switch_decisions_keeps_the_row_of_a_command_without_competing_npvs(tmp_path, mock_tech_switches_file):
    """A command built without competing NPVs leaves the NPV columns blank instead of stopping the run."""
    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    cmd = _switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", {"DRI": 300.0})
    cmd.competing_npvs = None
    data_collector.plant_groups[0].plants[0].change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=cmd,
    )

    data_collector.collect_switch_decisions(Year(2025))

    record = data_collector.switch_decisions[("brownfield_fg", 2029)]
    assert (record["new_technology"], record["winning_npv"]) == ("DRI", 300.0)
    assert record["incumbent_npv"] is None
    assert record["competing_npvs"] is None
    assert record["selection_probabilities"] is None


def test_collect_switch_decisions_marks_a_switch_executed_when_the_group_is_re_decided_in_its_switch_year(
    tmp_path, mock_tech_switches_file
):
    """The PAM runs before the collector, so in the switch year the group can already carry its next command."""
    from steelo.domain.models import Technology

    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    plant = data_collector.plant_groups[0].plants[0]
    fg = plant.furnace_groups[0]
    plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", {"DRI": 300.0}),
    )
    data_collector.collect_switch_decisions(Year(2025))
    fg.status = "construction switching technology"
    data_collector.collect_switch_decisions(Year(2028))

    # 2029: the switch executes, then the PAM schedules the next one before the collector looks
    fg.technology = Technology(name="DRI", product="iron")
    plant.change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2033,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "DRI", "ESF", {"DRI": 10.0, "ESF": 90.0}),
    )
    data_collector.collect_switch_decisions(Year(2029))

    first, second = data_collector.switch_decisions.values()
    assert (first["switch_year"], first["construction_start_year"], first["executed"]) == (2029, 2028, True)
    assert (second["switch_year"], second["construction_start_year"], second["executed"]) == (2033, None, False)

    # the second rebuild's construction years never leak into the first record
    fg.status = "construction switching technology"
    data_collector.collect_switch_decisions(Year(2031))
    assert first["construction_start_year"] == 2028
    assert second["construction_start_year"] == 2031


def test_write_switch_decisions_csv_round_trips_rows(tmp_path, mock_tech_switches_file):
    """Write the recorded decisions to data/pam_switch_decisions.csv with blanks for unset fields."""
    import csv

    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file, probabilistic_agents=False)
    data_collector.plant_groups[0].plants[0].change_furnace_group_status_to_switching_technology(
        furnace_group_id="brownfield_fg",
        year_of_switch=2029,
        cmd=_switch_command("plant_brownfield", "brownfield_fg", "BF", "DRI", {"DRI": 300.0}),
    )
    data_collector.collect_switch_decisions(Year(2025))

    path = data_collector.write_switch_decisions_csv(tmp_path / "data")
    assert path == tmp_path / "data" / "pam_switch_decisions.csv"

    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["decision_year"] == "2025"
    assert rows[0]["executed"] == "False"
    assert rows[0]["construction_start_year"] == ""
    assert rows[0]["incumbent_npv"] == ""
    assert rows[0]["selection_probabilities"] == ""
    assert json.loads(rows[0]["competing_npvs"]) == {"DRI": 300.0}


def test_write_switch_decisions_csv_without_decisions_writes_header(tmp_path, mock_tech_switches_file):
    """Write the header alone when no switch was decided."""
    from steelo.domain import datacollector

    data_collector = _switch_decisions_collector(tmp_path, mock_tech_switches_file)

    path = data_collector.write_switch_decisions_csv(tmp_path / "data")

    assert path.read_text().splitlines() == [",".join(datacollector.SWITCH_DECISION_COLUMNS)]


def _pipeline_status_collector(tmp_dir, mock_tech_switches_file):
    """Build a DataCollector over a brownfield plant with four groups in different statuses and an indi-origin plant."""
    from steelo.domain.models import Volumes

    collector = _switch_decisions_collector(tmp_dir, mock_tech_switches_file)
    brownfield_plant, geo_plant = (pg.plants[0] for pg in collector.plant_groups)
    start_2028 = PointInTime(
        current=Year(2025), time_frame=TimeFrame(start=Year(2028), end=Year(2048)), plant_lifetime=20
    )
    for fg_id, tech, status, by_pam in (
        ("input_construction", "DRI", "construction", False),
        ("input_announced", "EAF", "announced", False),
        ("pam_expansion", "EAF", "construction", True),
    ):
        fg = get_furnace_group(fg_id=fg_id, tech_name=tech, capacity=Volumes(500000), lifetime=start_2028)
        fg.status = status
        fg.created_by_PAM = by_pam
        brownfield_plant.furnace_groups.append(fg)
    geo_plant.furnace_groups[0].status = "construction"
    return collector


def test_collect_pipeline_status_records_the_existing_fleets_groups_that_are_not_operating_yet(
    tmp_path, mock_tech_switches_file
):
    """Announced and construction groups of non-greenfield plants get a row a year; operating and indi groups none."""
    data_collector = _pipeline_status_collector(tmp_path, mock_tech_switches_file)

    data_collector.collect_pipeline_status(Year(2025))
    data_collector.collect_pipeline_status(Year(2026))

    rows = data_collector.pipeline_status_rows
    assert [(row["year"], row["furnace_group_id"]) for row in rows] == [
        (year, fg_id) for year in (2025, 2026) for fg_id in ("input_construction", "input_announced", "pam_expansion")
    ]
    first = rows[0]
    assert (first["plant_id"], first["plant_group_id"], first["geo_key"]) == (
        "plant_brownfield",
        "E100000000123",
        "CHN",
    )
    assert (first["technology"], first["product"], first["status"]) == ("DRI", "iron", "construction")
    assert (first["capacity"], first["start_year"], first["created_by_pam"]) == (500000.0, 2028, False)
    assert rows[2]["created_by_pam"] is True

    # once a group operates it has a post-processed row instead
    data_collector.plant_groups[0].plants[0].furnace_groups[1].status = "operating"
    data_collector.collect_pipeline_status(Year(2028))
    assert [row["furnace_group_id"] for row in rows if row["year"] == 2028] == ["input_announced", "pam_expansion"]


def test_write_pipeline_status_csv_round_trips_rows_and_writes_a_header_without_rows(tmp_path, mock_tech_switches_file):
    """The CSV holds one line per collected row in the documented column order; an empty run gets the header."""
    import pandas as pd

    from steelo.domain.datacollector import PIPELINE_STATUS_COLUMNS

    data_collector = _pipeline_status_collector(tmp_path, mock_tech_switches_file)
    data_collector.collect_pipeline_status(Year(2025))
    written = pd.read_csv(data_collector.write_pipeline_status_csv(tmp_path / "data"))
    assert list(written.columns) == PIPELINE_STATUS_COLUMNS
    assert written["furnace_group_id"].tolist() == ["input_construction", "input_announced", "pam_expansion"]
    assert written["created_by_pam"].tolist() == [False, False, True]

    empty = _switch_decisions_collector(tmp_path, mock_tech_switches_file)
    path = empty.write_pipeline_status_csv(tmp_path / "empty")
    assert path.read_text().strip() == ",".join(PIPELINE_STATUS_COLUMNS)


def test_mark_announced_input_units_as_construction_leaves_model_built_and_greenfield_groups(
    tmp_path, mock_tech_switches_file
):
    """Only the input data's announced groups turn into construction, keeping their start year."""
    from steelo import simulation

    data_collector = _pipeline_status_collector(tmp_path, mock_tech_switches_file)
    brownfield_plant, geo_plant = (pg.plants[0] for pg in data_collector.plant_groups)
    geo_plant.furnace_groups[0].status = "announced"
    pam_announced = brownfield_plant.furnace_groups[3]
    pam_announced.status = "announced"

    renamed = simulation._mark_announced_input_units_as_construction([brownfield_plant, geo_plant])

    assert renamed == 1
    statuses = {fg.furnace_group_id: fg.status for fg in brownfield_plant.furnace_groups}
    assert statuses["input_announced"] == "construction"
    assert statuses["pam_expansion"] == "announced"  # created by the PAM: not input data
    assert statuses["brownfield_fg"] == "operating"
    assert geo_plant.furnace_groups[0].status == "announced"  # a greenfield opportunity can still be discarded
    assert brownfield_plant.furnace_groups[2].lifetime.time_frame.start == 2028


def _closure_collector(tmp_dir, mock_tech_switches_file):
    """Build a DataCollector in 2030 over an operating group, one closed in 2030 and one closed in 2029."""
    from steelo.domain.models import Volumes

    active_fg = get_furnace_group(fg_id="fg_active", capacity=Volumes(100000), utilization_rate=0.5)
    active_fg.record_utilization(2029)
    active_fg.record_utilization(2030)
    closed_this_year_fg = get_furnace_group(fg_id="fg_closed_this_year", capacity=Volumes(200000), utilization_rate=0.8)
    closed_this_year_fg.record_utilization(2029)
    closed_this_year_fg.record_utilization(2030)
    closed_this_year_fg.status = "closed"
    # closing a group leaves its last utilisation rate, bill of materials and emissions behind
    closed_earlier_fg = get_furnace_group(fg_id="fg_closed_earlier", capacity=Volumes(300000), utilization_rate=0.9)
    closed_earlier_fg.record_utilization(2029)
    closed_earlier_fg.status = "closed"
    furnace_groups = [active_fg, closed_this_year_fg, closed_earlier_fg]
    for fg in furnace_groups:
        fg.emissions = {"worldsteel": {"direct_ghg": float(fg.production)}}
        fg.bill_of_materials = {"materials": {"io_high": {"demand": float(fg.production)}}, "energy": {}}

    plant = get_plant(
        plant_id="plant_closures",
        furnace_groups=furnace_groups,
        location=Location(iso3="CHN", country="", region="China", lat=30.0, lon=110.0),
    )
    plant_group = PlantGroup(plant_group_id="E100000000123", plants=[plant])
    config = SimulationConfig(
        start_year=Year(2025),
        end_year=Year(2060),
        master_excel_path=Path(tempfile.gettempdir()) / "master.xlsx",
        output_dir=Path(tempfile.gettempdir()),
        technology_settings=get_default_technology_settings(),
    )
    env = Environment(config=config, tech_switches_csv=mock_tech_switches_file)
    env.year = Year(2030)
    return DataCollector([plant_group], env, output_dir=tmp_dir)


def test_is_reported_this_year_covers_active_groups_and_groups_closed_after_this_years_allocation(
    tmp_path, mock_tech_switches_file
):
    """A group closed by the plant agents after producing this year is reported; one closed earlier is not."""
    data_collector = _closure_collector(tmp_path, mock_tech_switches_file)
    active_fg, closed_this_year_fg, closed_earlier_fg = data_collector.plant_groups[0].plants[0].furnace_groups

    assert data_collector.is_reported_this_year(active_fg)
    assert data_collector.is_reported_this_year(closed_this_year_fg)
    assert not data_collector.is_reported_this_year(closed_earlier_fg)

    # a group that is not operating yet was never in an allocation
    active_fg.status = "construction"
    active_fg.historical_utilization = None
    assert not data_collector.is_reported_this_year(active_fg)


def test_collect_keeps_the_final_production_year_of_a_group_the_plant_agents_closed(tmp_path, mock_tech_switches_file):
    """The stored plant records hold the group closed this year with its production, not the one closed earlier."""
    import pickle

    data_collector = _closure_collector(tmp_path, mock_tech_switches_file)
    plant_groups = data_collector.plant_groups

    data_collector.collect(world_plant_list=plant_groups[0].plants, world_plant_groups=plant_groups, year=Year(2030))

    with open(tmp_path / "TM" / "datacollection_post_allocation_2030.pkl", "rb") as f:
        records = pickle.load(f)["plant_closures"]["furnace_groups"]
    production = {record["furnace_group_id"]: record["production"] for record in records}
    assert production == {"fg_active": 50000.0, "fg_closed_this_year": 160000.0}


def test_trace_collectors_count_a_group_closed_this_year_but_not_one_closed_earlier(tmp_path, mock_tech_switches_file):
    """Emissions, production by product and iron ore include the 2030 closure and skip the stale 2029 closure."""
    data_collector = _closure_collector(tmp_path, mock_tech_switches_file)

    emissions = data_collector.collect_emissions_by_technology(Year(2030))
    iron_ore = data_collector.collect_iron_ore_by_quality(Year(2030))

    assert emissions == {"worldsteel": {"EAF": {"direct_ghg": 210000.0}}}
    assert data_collector.trace_production_by_product[Year(2030)] == {"steel": 210000.0}
    assert iron_ore == {"io_high": 210000.0}


def test_greenfield_status_row_keeps_the_production_of_a_group_closed_this_year(tmp_path, mock_tech_switches_file):
    """A greenfield group closed after this year's allocation reads closed with its production; an older closure reads zero."""
    data_collector = _closure_collector(tmp_path, mock_tech_switches_file)
    data_collector.plant_groups[0].plants[0].parent_gem_id = "indi_CHN"

    data_collector.collect_new_plant_data(Year(2030))

    rows = {row["furnace_group_id"]: row for row in data_collector.greenfield_status_rows}
    assert (rows["fg_closed_this_year"]["status"], rows["fg_closed_this_year"]["production"]) == ("closed", 160000.0)
    assert rows["fg_closed_this_year"]["utilization_rate"] == 0.8
    assert (rows["fg_closed_earlier"]["production"], rows["fg_closed_earlier"]["utilization_rate"]) == (0.0, 0.0)


def _shrunk_renovation_collector(tmp_dir, mock_tech_switches_file):
    """Build the closure collector with its operating group renovated to two thirds of its capacity after the 2030 allocation."""
    from steelo.domain.models import Volumes

    data_collector = _closure_collector(tmp_dir, mock_tech_switches_file)
    data_collector.plant_groups[0].plants[0].furnace_groups[0].capacity = Volumes(100000 / 1.5)
    return data_collector


def test_collect_reports_the_capacity_and_production_of_the_allocation_for_a_group_shrunk_this_year(
    tmp_path, mock_tech_switches_file
):
    """A renovation that shrinks a group after the allocation leaves this year's capacity and production untouched."""
    import pickle

    data_collector = _shrunk_renovation_collector(tmp_path, mock_tech_switches_file)
    plant_groups = data_collector.plant_groups

    data_collector.collect(world_plant_list=plant_groups[0].plants, world_plant_groups=plant_groups, year=Year(2030))

    with open(tmp_path / "TM" / "datacollection_post_allocation_2030.pkl", "rb") as f:
        records = pickle.load(f)["plant_closures"]["furnace_groups"]
    reported = {record["furnace_group_id"]: (record["capacity"], record["production"]) for record in records}
    assert reported["fg_active"] == (100000.0, 50000.0)

    # from the next allocation on the shrunk capacity is the reported one
    data_collector.env.year = Year(2031)
    plant_groups[0].plants[0].furnace_groups[0].record_utilization(2031)
    assert data_collector.allocated_capacity(plant_groups[0].plants[0].furnace_groups[0]) == pytest.approx(100000 / 1.5)


def test_production_by_product_and_greenfield_row_use_the_capacity_of_the_allocation(tmp_path, mock_tech_switches_file):
    """The production trace and the greenfield status row of a group shrunk this year keep the allocation's tonnes."""
    data_collector = _shrunk_renovation_collector(tmp_path, mock_tech_switches_file)
    data_collector.plant_groups[0].plants[0].parent_gem_id = "indi_CHN"

    data_collector.collect_emissions_by_technology(Year(2030))
    data_collector.collect_new_plant_data(Year(2030))

    assert data_collector.trace_production_by_product[Year(2030)] == {"steel": 210000.0}
    rows = {row["furnace_group_id"]: row for row in data_collector.greenfield_status_rows}
    assert (rows["fg_active"]["capacity"], rows["fg_active"]["production"]) == (100000.0, 50000.0)
