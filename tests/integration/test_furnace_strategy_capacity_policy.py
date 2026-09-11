"""Tests for the ② REPLACE pre-NPV capacity-policy hook in the furnace-group strategy.

The hook parameter defaults to None, leaving the decision path byte-identical.
A bound adapter evaluates every candidate at its permitted capacity: penalised
transitions shrink, non-intense targets and exempt-province cells stay 1:1, a
utilisation-gated group loses its replace candidates while continue and close
survive, and the executed command deposits the freed delta into the pool via
the real handlers. A same-technology renovation is a full REPLACE under the
shipped default (``renovation_counts_as_replace``); explicit False exempts it
from the ratio while the utilisation gate still applies.
"""

import logging
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from steelo.capacity_policy import CapacityPolicyConfig, CapacityPolicyRecorder, CapacityPool, TreeEvaluator
from steelo.capacity_policy import handlers as cp_handlers
from steelo.capacity_policy.inputs import RegionRow, TechnologyRow
from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import PointInTime, TimeFrame, Volumes, Year
from steelo.domain.calculate_costs import ReductantScoreSeries
from steelo.domain.commands import (
    ChangeFurnaceGroupTechnology,
    CloseFurnaceGroup,
    RenovateFurnaceGroup,
)
from steelo.domain.models import Location, PlantGroup

REGION_CAPEX = {"EAF": 400.0, "DRI": 600.0, "MOE": 800.0}
RENOVATION_SHARE = {"EAF": 0.7, "DRI": 0.7, "MOE": 0.7}
ALLOWED_TECHS = {Year(year): ["EAF", "DRI", "MOE"] for year in range(2020, 2031)}
TRANSITIONS = {"EAF": ["EAF", "DRI", "MOE"]}
DEBT_RATES = {"EAF": 0.04, "DRI": 0.04, "MOE": 0.04}
EQUITY_RATES = {"EAF": 0.08, "DRI": 0.08, "MOE": 0.08}
# The fleet modal and the candidates' own operating-start picks deliberately disagree on
# DRI, so every ② classification below shows which of the two the gate actually consulted.
FLEET_REDUCTANTS = {"DRI": "Natural gas", "MOE": "Electricity"}
CANDIDATE_PICKS = {"EAF": "Electricity", "DRI": "Hydrogen", "MOE": "Electricity"}

# Synthetic policy rows: the incumbent EAF is authored emission-intense so a
# same-technology renovation reads as a penalised REPLACE under the shipped default;
# DRI is the penalised switch target, MOE the non-intense one.
REGIONS = [
    RegionRow(geo_key="CHN:CN-HE", region_name="Jing-Jin-Ji", type="key"),
    RegionRow(geo_key="CHN:CN-QH", region_name=None, type="exempt"),
    RegionRow(geo_key="CHN:CN-GD", region_name=None, type=None),
]
TECHNOLOGIES = [
    TechnologyRow(
        technology="EAF",
        product="steel",
        reductant=None,
        is_emission_intense=True,
        switching_to=None,
        swap_ratio=None,
    ),
    TechnologyRow(
        technology="DRI",
        product="iron",
        reductant=None,
        is_emission_intense=True,
        switching_to=None,
        swap_ratio=None,
    ),
    TechnologyRow(
        technology="MOE",
        product="iron",
        reductant=None,
        is_emission_intense=False,
        switching_to=None,
        swap_ratio=None,
    ),
]

# DRI split by reductant: the candidate's pick, not the fleet's, decides which row classifies it.
SPLIT_TECHNOLOGIES = [
    TECHNOLOGIES[0],
    TechnologyRow(
        technology="DRI", product="iron", reductant=None, is_emission_intense=None, switching_to=None, swap_ratio=None
    ),
    TechnologyRow(
        technology="DRI", product="iron", reductant="Coal", is_emission_intense=True, switching_to=None, swap_ratio=None
    ),
    TechnologyRow(
        technology="DRI",
        product="iron",
        reductant="Hydrogen",
        is_emission_intense=False,
        switching_to=None,
        swap_ratio=None,
    ),
    TECHNOLOGIES[2],
]

BOM = {
    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
}


def score_series_stub(picks_by_tech: dict[str, str] | None = None):
    """A real score-series provider; the ② gate classifies the new side by ``picks[0]``.

    A ``MagicMock`` would leak a truthy mock into the classification, so the
    stub returns the one-year series the gate actually asks for.
    """
    picks = CANDIDATE_PICKS if picks_by_tech is None else picks_by_tech

    def provider(_location, tech_name, _output_shares, _start, _end, **_kwargs):
        pick = picks.get(tech_name, "")
        return ReductantScoreSeries(scores=[0.0], picks=[pick] if pick else [])

    return provider


class FakePlantsRepo:
    def __init__(self, plants):
        self._plants = plants

    def list(self):
        return list(self._plants)


class FakePlantGroupsRepo:
    """Membership, the canonical credit owner: a plant outside the group raises."""

    def __init__(self, plant_group):
        self._plant_group = plant_group

    def get_by_plant_id(self, plant_id: str):
        if not any(plant.plant_id == plant_id for plant in self._plant_group.plants):
            raise ValueError(f"No plant group found for plant ID: {plant_id}")
        return self._plant_group


class FakeUoW:
    def __init__(self, plant, plant_group):
        self.plants = FakePlantsRepo([plant])
        self.plant_groups = FakePlantGroupsRepo(plant_group)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@dataclass
class FakeEnv:
    year: float = 2025.0


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()


def bind_policy(
    *, renovation_counts_as_replace: bool = True, technologies: list[TechnologyRow] | None = None
) -> tuple[TreeEvaluator, CapacityPool]:
    """Bind a real evaluator and pool; return both for spying and pool asserts."""
    recorder = CapacityPolicyRecorder()
    evaluator = TreeEvaluator(
        REGIONS,
        TECHNOLOGIES if technologies is None else technologies,
        CapacityPolicyConfig(renovation_counts_as_replace=renovation_counts_as_replace),
        recorder=recorder,
    )
    pool = CapacityPool()
    cp_handlers.bind_capacity_policy(evaluator, pool, recorder)
    return evaluator, pool


def make_plant_and_group(
    *,
    iso3: str = "CHN",
    geo_unit: str | None = "CN-HE",
    expired: bool = False,
    balance: float = 1_000_000.0,
):
    """Build a 3.0-capacity EAF plant plus owning group at the given location."""
    fg = get_furnace_group(
        fg_id="fg_cpool_test",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2005) if expired else Year(2015), end=Year(2045)),
            plant_lifetime=20,
        ),
        capacity=Volumes(3.0),
        tech_name="EAF",
    )
    fg.chosen_reductant = "Electricity"
    plant = get_plant(
        furnace_groups=[fg],
        plant_id="plant_cpool_test",
        location=Location(lat=39.0, lon=116.0, country="China", region="Asia", iso3=iso3, geo_unit=geo_unit),
    )
    plant.parent_gem_id = "E_cpool_owner"
    plant.technology_unit_fopex = {"eaf": 50.0, "dri": 70.0, "moe": 90.0}
    plant.carbon_cost_series = [0.0] * 22
    plant_group = PlantGroup(plant_group_id="gem_cpool_test", plants=[plant])
    plant_group.balance = balance
    return plant, plant_group


def mock_npvs(mocker, furnace_group, tech_npv_dict):
    """Patch optimal_technology_name to price only candidates still on the menu."""

    def respecting_menu(**kwargs):
        menu = kwargs["allowed_furnace_transitions"].get("EAF", [])
        offered = {tech: npv for tech, npv in tech_npv_dict.items() if tech in menu}
        return (
            offered,
            {tech: REGION_CAPEX[tech] for tech in offered},
            10_000.0,
            {tech: BOM for tech in offered},
            {tech: "scrap" for tech in offered},
        )

    return mocker.patch.object(furnace_group, "optimal_technology_name", side_effect=respecting_menu)


def evaluate(
    plant,
    plant_group,
    *,
    hook=None,
    probabilistic_agents: bool = False,
    score_series=None,
    fleet_reductants=None,
):
    """Call evaluate_furnace_group_strategy; omit the hook parameter when hook is None."""
    furnace_group = plant.furnace_groups[0]
    extra = {} if hook is None else {"permitted_replace_capacity": hook}
    return plant.evaluate_furnace_group_strategy(
        furnace_group_id=furnace_group.furnace_group_id,
        plant_group=plant_group,
        market_price_series={"steel": [600.0] * 22, "iron": [400.0] * 22},
        region_capex=REGION_CAPEX,
        capex_renovation_share=RENOVATION_SHARE,
        cost_of_debt_by_tech=DEBT_RATES,
        cost_of_equity_by_tech=EQUITY_RATES,
        get_bom_from_avg_boms=MagicMock(),
        reductant_score_series=score_series_stub() if score_series is None else score_series,
        probabilistic_agents=probabilistic_agents,
        dynamic_business_cases={"EAF": [], "DRI": [], "MOE": []},
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=[],
        tech_to_product={"EAF": "steel", "DRI": "iron", "MOE": "iron"},
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(2025),
        allowed_techs=ALLOWED_TECHS,
        risk_free_rate=0.02,
        allowed_furnace_transitions=TRANSITIONS,
        capacity_limit_steel=Volumes(10_000),
        capacity_limit_iron=Volumes(10_000),
        installed_capacity_in_year=lambda product: Volumes(1_000),
        new_plant_capacity_in_year=lambda product: Volumes(0),
        most_common_reductant_by_tech=FLEET_REDUCTANTS if fleet_reductants is None else fleet_reductants,
        **extra,
    )


def apply_switch(plant, command):
    """Execute a switch command exactly as the service-layer handler does."""
    plant.change_furnace_group_technology(
        furnace_group_id=command.furnace_group_id,
        technology_name=command.technology_name,
        plant_lifetime=20,
        lag=0,
        capex=command.capex,
        capex_no_subsidy=command.capex_no_subsidy,
        cost_of_debt=command.cost_of_debt,
        cost_of_debt_no_subsidy=command.cost_of_debt_no_subsidy,
        capacity=command.capacity,
        capex_subsidies=command.capex_subsidies,
        debt_subsidies=command.debt_subsidies,
        bom=command.bom,
        chosen_reductant=command.chosen_reductant,
        legacy_years=command.remaining_lifetime,
    )
    return plant.events[-1]


def apply_renovation(plant, command):
    """Execute a renovation command exactly as the service-layer handler does."""
    plant.renovate_furnace_group(
        furnace_group_id=command.furnace_group_id,
        plant_lifetime=20,
        capacity=command.capacity,
        capex=command.capex,
        capex_no_subsidy=command.capex_no_subsidy,
        cost_of_debt=command.cost_of_debt,
        cost_of_debt_no_subsidy=command.cost_of_debt_no_subsidy,
        capex_subsidies=command.capex_subsidies,
        debt_subsidies=command.debt_subsidies,
    )
    return plant.events[-1]


class TestDefaultPath:
    def test_no_hook_passes_no_candidate_capacities_and_commands_full_capacity(self, mocker):
        """Without the parameter the NPV sees candidate_capacities=None and nothing shrinks."""
        plant, plant_group = make_plant_and_group()
        mock = mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 400.0})

        command = evaluate(plant, plant_group)

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.capacity == pytest.approx(3.0)
        assert mock.call_args.kwargs["candidate_capacities"] is None

    def test_default_renovation_commands_full_capacity(self, mocker):
        plant, plant_group = make_plant_and_group(expired=True)
        mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500.0, "MOE": 400.0})

        command = evaluate(plant, plant_group)

        assert isinstance(command, RenovateFurnaceGroup)
        assert command.capacity == pytest.approx(3.0)


class TestBoundReplacePath:
    def test_penalised_switch_evaluated_commanded_and_deposited_at_permitted_capacity(self, mocker, caplog):
        """The whole thread: NPV at 2.0, command at 2.0, 1.0 lands in the pool."""
        _, pool = bind_policy()
        plant, plant_group = make_plant_and_group()
        fg = plant.furnace_groups[0]
        mock = mock_npvs(mocker, fg, {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 400.0})

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.tree"):
            command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.capacity == pytest.approx(2.0)
        passed = mock.call_args.kwargs["candidate_capacities"]
        assert passed["DRI"] == pytest.approx(2.0)
        assert passed["MOE"] == pytest.approx(3.0)
        # The incumbent's renovation candidate is itself a penalised REPLACE under the default
        assert passed["EAF"] == pytest.approx(2.0)
        # Switch equity is debited at the permitted capacity
        assert plant_group.balance == pytest.approx(1_000_000.0 - REGION_CAPEX["DRI"] * 2.0 * fg.equity_share)
        # The drift instrumentation carries both routes' reductants; the new side is the
        # candidate's own operating-start pick, not the fleet modal ("Natural gas")
        assert "old_reductant=Electricity" in caplog.text
        assert "new_reductant=Hydrogen" in caplog.text

        event = apply_switch(plant, command)
        assert fg.capacity == pytest.approx(2.0)
        assert event.old_capacity == pytest.approx(3.0)
        assert event.capacity == pytest.approx(2.0)

        cp_handlers.deposit_on_furnace_group_tech_changed(event, uow=FakeUoW(plant, plant_group), env=FakeEnv())
        (credit,) = pool.snapshot()
        assert credit.amount_mt == pytest.approx(1.0)
        assert credit.region_tag == "Jing-Jin-Ji"
        # Membership, not the event's ultimate_plant_group (parent_gem_id "E_cpool_owner")
        assert credit.owner_id == "gem_cpool_test"
        # A switch keeps the group's product (change_furnace_group_technology carries it
        # over), so the freed credit banks under what the shrunk capacity actually made
        assert credit.product == "steel"

    def test_non_intense_candidate_stays_unshrunk_with_no_deposit(self, mocker):
        _, pool = bind_policy()
        plant, plant_group = make_plant_and_group()
        mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 400.0, "MOE": 1_000_000.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.technology_name == "MOE"
        assert command.capacity == pytest.approx(3.0)

        event = apply_switch(plant, command)
        cp_handlers.deposit_on_furnace_group_tech_changed(event, uow=FakeUoW(plant, plant_group), env=FakeEnv())
        assert pool.total() == 0.0

    def test_exempt_province_replaces_one_to_one(self, mocker):
        bind_policy()
        plant, plant_group = make_plant_and_group(geo_unit="CN-QH")
        mock = mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 400.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.capacity == pytest.approx(3.0)
        assert mock.call_args.kwargs["candidate_capacities"]["DRI"] == pytest.approx(3.0)

    def test_utilisation_gated_group_loses_every_candidate_but_keeps_running(self, mocker):
        """The gate empties the replace menu — the incumbent included, under the default
        flag — and a live group with nothing to decide simply continues."""
        bind_policy()
        plant, plant_group = make_plant_and_group()
        fg = plant.furnace_groups[0]
        fg.historical_utilization = {2024: 0.20, 2025: 0.22}
        mock = mock_npvs(mocker, fg, {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 2_000_000.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert command is None
        assert mock.call_args.kwargs["allowed_furnace_transitions"]["EAF"] == []

    def test_utilisation_gated_expired_group_can_still_close(self, mocker):
        bind_policy()
        plant, plant_group = make_plant_and_group(expired=True)
        fg = plant.furnace_groups[0]
        fg.historical_utilization = {2024: 0.20, 2025: 0.22}
        mock_npvs(mocker, fg, {"EAF": -1_000.0, "DRI": 1_000_000.0, "MOE": 2_000_000.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, CloseFurnaceGroup)

    def test_non_chinese_group_never_reaches_the_evaluator(self, mocker):
        evaluator, pool = bind_policy()
        spy = mocker.spy(evaluator, "permitted_capacity")
        plant, plant_group = make_plant_and_group(iso3="USA", geo_unit=None)
        mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 400.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.capacity == pytest.approx(3.0)
        assert spy.call_count == 0

        event = apply_switch(plant, command)
        cp_handlers.deposit_on_furnace_group_tech_changed(event, uow=FakeUoW(plant, plant_group), env=FakeEnv())
        assert pool.total() == 0.0


class TestCandidatePickClassification:
    """The new side is classified by the candidate's own operating-start pick."""

    def test_the_pick_classifies_where_the_fleet_modal_would_have_penalised(self, mocker, caplog):
        """DRI+Hydrogen is deep-abating and replaces 1:1; DRI+Coal, the fleet modal here,
        would have shrunk the switch to 2.0. The permitted capacity says which one ran."""
        _, pool = bind_policy(technologies=SPLIT_TECHNOLOGIES)
        plant, plant_group = make_plant_and_group()
        mock = mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 400.0})

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.tree"):
            command = evaluate(
                plant,
                plant_group,
                hook=cp_handlers.replace_capacity_hook(),
                fleet_reductants={"DRI": "Coal", "MOE": "Electricity"},
            )

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.capacity == pytest.approx(3.0)
        assert mock.call_args.kwargs["candidate_capacities"]["DRI"] == pytest.approx(3.0)
        assert "new_reductant=Hydrogen" in caplog.text

        event = apply_switch(plant, command)
        cp_handlers.deposit_on_furnace_group_tech_changed(event, uow=FakeUoW(plant, plant_group), env=FakeEnv())
        assert pool.total() == 0.0

    def test_a_candidate_with_no_pick_lands_on_the_conservative_fallback(self, mocker, caplog):
        """An empty pick reaches the adapter as None, so a reductant-split technology with
        no blank row is classified worst-case — the pre-existing fallback, not a guess."""
        bind_policy(technologies=SPLIT_TECHNOLOGIES)
        plant, plant_group = make_plant_and_group()
        mock = mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 500.0, "DRI": 1_000_000.0, "MOE": 400.0})

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.tree"):
            command = evaluate(
                plant,
                plant_group,
                hook=cp_handlers.replace_capacity_hook(),
                score_series=score_series_stub({"EAF": "Electricity", "MOE": "Electricity"}),
            )

        assert isinstance(command, ChangeFurnaceGroupTechnology)
        assert command.capacity == pytest.approx(2.0)
        assert mock.call_args.kwargs["candidate_capacities"]["DRI"] == pytest.approx(2.0)
        assert "decision=conservative_fallback" in caplog.text
        assert "new_reductant=None" in caplog.text


class TestRenovationRuling:
    def test_default_renovation_of_an_intense_group_shrinks_and_deposits(self, mocker):
        """Under the shipped default a same-tech renovation of an intense group is a
        penalised REPLACE."""
        _, pool = bind_policy()
        plant, plant_group = make_plant_and_group(expired=True)
        fg = plant.furnace_groups[0]
        mock = mock_npvs(mocker, fg, {"EAF": 1_000_000.0, "DRI": 500.0, "MOE": 400.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, RenovateFurnaceGroup)
        assert command.capacity == pytest.approx(2.0)
        assert mock.call_args.kwargs["candidate_capacities"]["EAF"] == pytest.approx(2.0)
        # Renovation equity is debited at the permitted capacity
        assert plant_group.balance == pytest.approx(1_000_000.0 - REGION_CAPEX["EAF"] * 2.0 * fg.equity_share)

        event = apply_renovation(plant, command)
        assert fg.capacity == pytest.approx(2.0)
        assert event.old_capacity == pytest.approx(3.0)
        assert event.capacity == pytest.approx(2.0)

        cp_handlers.deposit_on_furnace_group_renovated(event, uow=FakeUoW(plant, plant_group), env=FakeEnv())
        (credit,) = pool.snapshot()
        assert credit.amount_mt == pytest.approx(1.0)
        assert credit.region_tag == "Jing-Jin-Ji"

    def test_gate_blocked_expired_group_falls_through_to_close_under_the_default(self, mocker):
        bind_policy()
        plant, plant_group = make_plant_and_group(expired=True)
        fg = plant.furnace_groups[0]
        fg.historical_utilization = {2024: 0.20, 2025: 0.22}
        mock_npvs(mocker, fg, {"EAF": 1_000_000.0, "DRI": 1_000_000.0, "MOE": 2_000_000.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, CloseFurnaceGroup)

    def test_ratio_exempt_gate_blocked_expired_group_falls_through_to_close(self, mocker):
        """The gate reaches a ratio-exempt renovation too, so a
        low-utilisation expired group loses the incumbent option and closes."""
        bind_policy(renovation_counts_as_replace=False)
        plant, plant_group = make_plant_and_group(expired=True)
        fg = plant.furnace_groups[0]
        fg.historical_utilization = {2024: 0.1, 2025: 0.1}
        mock = mock_npvs(mocker, fg, {"EAF": 1_000_000.0, "DRI": 1_000_000.0, "MOE": 2_000_000.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, CloseFurnaceGroup)
        assert mock.call_args.kwargs["allowed_furnace_transitions"]["EAF"] == []

    def test_ratio_exempt_renovation_untouched_and_event_unshrunk(self, mocker):
        """Flag off: renovation at full capacity, event carries old_capacity == capacity."""
        _, pool = bind_policy(renovation_counts_as_replace=False)
        plant, plant_group = make_plant_and_group(expired=True)
        mock_npvs(mocker, plant.furnace_groups[0], {"EAF": 1_000_000.0, "DRI": 500.0, "MOE": 400.0})

        command = evaluate(plant, plant_group, hook=cp_handlers.replace_capacity_hook())

        assert isinstance(command, RenovateFurnaceGroup)
        assert command.capacity == pytest.approx(3.0)

        event = apply_renovation(plant, command)
        assert event.old_capacity == pytest.approx(3.0)
        assert event.capacity == pytest.approx(3.0)

        cp_handlers.deposit_on_furnace_group_renovated(event, uow=FakeUoW(plant, plant_group), env=FakeEnv())
        assert pool.total() == 0.0
