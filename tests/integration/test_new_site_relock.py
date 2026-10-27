"""A new site's switch or renovation re-locks its power and electrolyser stack at the year the investment starts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, TimeFrame, Volumes, Year
from steelo.domain.calculate_costs import ReductantScoreSeries
from steelo.domain.commands import ChangeFurnaceGroupTechnology, RenovateFurnaceGroup
from steelo.domain.models import CaptiveEnergy, PlantGroup
from steelo.service_layer import handlers

REGION_CAPEX = {"EAF": 400.0, "DRI": 600.0}
RENOVATION_SHARE = {"EAF": 0.7, "DRI": 0.7}
ALLOWED_TECHS = {Year(year): ["EAF", "DRI"] for year in range(2035, 2046)}
TRANSITIONS = {"EAF": ["EAF", "DRI"]}
DECISION_YEAR = Year(2040)
CONSTRUCTION_TIME = 4
# The cell's LCOE (USD/kWh), the electrolyser's use (MWh/kg) and its cost (USD/kg) by lock year
LCOE = {2028: 0.050, 2040: 0.036}
ELECTROLYSER_USE = {2028: 0.050, 2040: 0.046}
ELECTROLYSER_COST = {2028: 1.3, 2040: 0.9}


def stack_of(year: int) -> CaptiveEnergy:
    """The stack a site locks in a year, with that year's LCOE and electrolyser."""
    return CaptiveEnergy(Year(year), 0.85, LCOE[year], ELECTROLYSER_USE[year], ELECTROLYSER_COST[year], None)


class RecordingStackBuilder:
    """``captive_energy_at`` that records each request and returns the stack of the year asked for."""

    def __init__(self):
        self.calls: list[tuple[object, Year]] = []

    def __call__(self, location, year):
        self.calls.append((location, year))
        return stack_of(year)


def no_stack(location, year):
    """An existing plant builds no stack, so its evaluation must never ask for one."""
    raise AssertionError("an existing plant's evaluation asked for a captive energy stack")


def make_plant(*, new_site: bool, cycle_start: int):
    """
    An EAF plant whose furnace group, locked in 2028 when it is a new site, is evaluated in 2040.

    Args:
        new_site: Whether the plant is model-built (``parent_gem_id`` starting with ``indi``).
        cycle_start: Start of the group's renovation cycle; 2020 makes 2040 its renovation year.

    Returns:
        The plant and its owning plant group.
    """
    fg = get_furnace_group(
        fg_id="fg_relock",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=DECISION_YEAR,
            time_frame=TimeFrame(start=Year(cycle_start), end=Year(cycle_start + 20)),
            plant_lifetime=20,
        ),
        capacity=100,
        tech_name="EAF",
    )
    plant = get_plant(furnace_groups=[fg], plant_id="plant_relock")
    plant.location.iso3 = "USA"
    plant.technology_unit_fopex = {"eaf": 50.0, "dri": 70.0, "EAF": 50.0, "DRI": 70.0}
    plant.carbon_cost_series = [0.0] * 22
    if new_site:
        plant.parent_gem_id = "indi_USA"
        fg.captive_energy = stack_of(2028)
    plant_group = PlantGroup(plant_group_id="relock_group", plants=[plant])
    plant_group.balance = 1_000_000.0
    return plant, plant_group


def mock_npvs(mocker, furnace_group, tech_npv_dict):
    """Patch optimal_technology_name to return fixed NPVs, keeping the call for its score-series provider."""
    bom = {
        "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
        "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
    }
    return mocker.patch.object(
        furnace_group,
        "optimal_technology_name",
        return_value=(
            tech_npv_dict,
            {tech: REGION_CAPEX[tech] for tech in tech_npv_dict},
            10_000.0,
            {tech: bom for tech in tech_npv_dict},
            {tech: "scrap" for tech in tech_npv_dict},
        ),
    )


def evaluate(plant, plant_group, captive_energy_at, reductant_score_series):
    """Evaluate the plant's furnace group in 2040 in deterministic mode."""
    return plant.evaluate_furnace_group_strategy(
        furnace_group_id=plant.furnace_groups[0].furnace_group_id,
        plant_group=plant_group,
        market_price_series={"steel": [600.0] * 22, "iron": [400.0] * 22},
        region_capex=REGION_CAPEX,
        capex_renovation_share=RENOVATION_SHARE,
        cost_of_debt_by_tech={"EAF": 0.04, "DRI": 0.04},
        cost_of_equity_by_tech={"EAF": 0.08, "DRI": 0.08},
        get_bom_from_avg_boms=MagicMock(),
        reductant_score_series=reductant_score_series,
        captive_energy_at=captive_energy_at,
        probabilistic_agents=False,
        dynamic_business_cases={"EAF": [], "DRI": []},
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=[],
        tech_to_product={"EAF": "steel", "DRI": "iron"},
        plant_lifetime=20,
        construction_time=CONSTRUCTION_TIME,
        current_year=DECISION_YEAR,
        allowed_techs=ALLOWED_TECHS,
        risk_free_rate=0.02,
        allowed_furnace_transitions=TRANSITIONS,
        capacity_limit_steel=Volumes(10_000),
        capacity_limit_iron=Volumes(10_000),
        installed_capacity_in_year=lambda product: Volumes(1_000),
        new_plant_capacity_in_year=lambda product: Volumes(0),
    )


def recording_series():
    """A score-series stub that records the keyword arguments of every call."""
    calls: list[dict] = []

    def series(location, tech, output_shares, start, end, **kwargs):
        calls.append(kwargs)
        return ReductantScoreSeries(scores=[0.0], picks=["scrap"])

    return series, calls


class FakeUnitOfWork:
    """A unit of work over one plant, as the handlers use it."""

    def __init__(self, plant):
        self.plants = SimpleNamespace(get=lambda plant_id: plant)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass


def test_new_site_renovating_in_2040_holds_the_stack_of_2040(mocker):
    """The evaluation locks one stack for 2040, prices the candidates from it, and the renovation installs it."""
    plant, plant_group = make_plant(new_site=True, cycle_start=2020)
    fg = plant.furnace_groups[0]
    optimal = mock_npvs(mocker, fg, {"EAF": 5_000.0, "DRI": 1_000.0})
    builder = RecordingStackBuilder()
    series, series_calls = recording_series()

    command = evaluate(plant, plant_group, builder, series)
    optimal.call_args.kwargs["score_series_for_tech"]("DRI", {}, Year(2044), Year(2045))

    assert isinstance(command, RenovateFurnaceGroup)
    assert builder.calls == [(plant.location, DECISION_YEAR)]
    assert series_calls == [{"captive": stack_of(2040)}]
    assert fg.captive_energy == stack_of(2028)

    handlers.renovate_furnace_group(
        command, FakeUnitOfWork(plant), SimpleNamespace(year=DECISION_YEAR, config=SimpleNamespace(plant_lifetime=20))
    )

    assert fg.captive_energy.lock_year == 2040
    assert (fg.captive_energy.lcoe, fg.captive_energy.electrolyser_use, fg.captive_energy.electrolyser_cost) == (
        LCOE[2040],
        ELECTROLYSER_USE[2040],
        ELECTROLYSER_COST[2040],
    )


def test_new_site_switching_from_2040_keeps_its_old_stack_until_the_switch_executes_in_2044(mocker):
    """The switch decided in 2040 carries the stack of 2040, which replaces the old one only with the new technology."""
    plant, plant_group = make_plant(new_site=True, cycle_start=2030)
    fg = plant.furnace_groups[0]
    mock_npvs(mocker, fg, {"EAF": 1_000.0, "DRI": 1_000_000.0})
    series, _ = recording_series()

    command = evaluate(plant, plant_group, RecordingStackBuilder(), series)
    assert isinstance(command, ChangeFurnaceGroupTechnology)
    assert command.captive_energy == stack_of(2040)

    year_of_switch = DECISION_YEAR + CONSTRUCTION_TIME
    plant.change_furnace_group_status_to_switching_technology(fg.furnace_group_id, year_of_switch, command)
    for year in range(DECISION_YEAR + 1, year_of_switch):
        fg.lifetime.current = Year(year)
        assert fg.captive_energy == stack_of(2028)
        assert fg.technology.name == "EAF"

    fg.lifetime.current = Year(year_of_switch)
    handlers.execute_scheduled_technology_switch(
        fg.future_switch_cmd,
        uow=FakeUnitOfWork(plant),
        env=SimpleNamespace(
            year=Year(year_of_switch),
            config=SimpleNamespace(plant_lifetime=20, construction_time=CONSTRUCTION_TIME),
            dynamic_feedstocks={},
        ),
    )

    assert fg.technology.name == "DRI"
    assert fg.captive_energy == stack_of(2040)


def test_existing_plants_evaluation_passes_no_stack(mocker):
    """An existing plant builds no stack, prices its candidates without one, and its commands carry none."""
    plant, plant_group = make_plant(new_site=False, cycle_start=2020)
    optimal = mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 5_000.0, "DRI": 1_000.0})
    series, series_calls = recording_series()

    command = evaluate(plant, plant_group, no_stack, series)
    optimal.call_args.kwargs["score_series_for_tech"]("DRI", {}, Year(2044), Year(2045))

    assert isinstance(command, RenovateFurnaceGroup)
    assert command.captive_energy is None
    assert series_calls == [{"captive": None}]
