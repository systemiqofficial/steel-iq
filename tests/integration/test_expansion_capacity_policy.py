"""Tests for the ③ INCREASE expansion gate in ``PlantGroup.evaluate_expansion``.

The hook parameter defaults to None, leaving the decision path byte-identical.
A bound adapter withdraws the planned capacity from the pool at the point of
commitment: a non-intense grant builds what it withdrew, an emission-intense
grant builds the planned amount divided by the penalty divisor with equity
following the built capacity, and a blocked withdrawal drops the expansion for
the year. The region, product and owner rules live in the pool and are
exercised end-to-end through the gate. Capacities flow in model tonnes.
"""

import logging
from unittest.mock import MagicMock

import pytest

from steelo.capacity_policy import (
    CapacityPolicyConfig,
    CapacityPolicyRecorder,
    CapacityPool,
    Credit,
    TreeEvaluator,
)
from steelo.capacity_policy import handlers as cp_handlers
from steelo.capacity_policy.inputs import RegionRow, TechnologyRow
from steelo.devdata import get_furnace_group, get_plant
from steelo.domain import calculate_costs as cc
from steelo.domain import PointInTime, TimeFrame, Volumes, Year
from steelo.domain.commands import AddFurnaceGroup
from steelo.domain.models import Location, PlantGroup

CAPEX = {"EAF": 400.0, "DRI": 600.0, "MOE": 800.0}
DEBT_RATES = {"EAF": 0.04, "DRI": 0.04, "MOE": 0.04}
EQUITY_RATES = {"EAF": 0.08, "DRI": 0.08, "MOE": 0.08}
TECH_TO_PRODUCT = {"EAF": "steel", "DRI": "iron", "MOE": "iron"}
EQUITY_SHARE = 0.2

# Synthetic policy rows: EAF is authored emission-intense so a steel expansion is
# penalised; DRI and MOE are clean iron builds.
REGIONS = [
    RegionRow(geo_key="CHN:CN-HE", region_name="Jing-Jin-Ji", type="key"),
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
        is_emission_intense=False,
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


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()


def bind_policy(pool: CapacityPool | None = None) -> CapacityPool:
    """Bind a real evaluator and pool; return the pool for asserts."""
    recorder = CapacityPolicyRecorder()
    evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(), recorder=recorder)
    pool = pool if pool is not None else CapacityPool()
    cp_handlers.bind_capacity_policy(evaluator, pool, recorder)
    return pool


def credit(amount: float, vintage: int, *, tag: str | None = None, owner: str | None = None, product: str = "iron"):
    return Credit(amount_mt=amount, vintage_year=vintage, region_tag=tag, owner_id=owner, product=product)


def make_group(*, iso3: str = "CHN", geo_unit: str | None = "CN-HE", group_id: str = "gem_cpool_exp"):
    """Build a one-plant group at the given location with an ample treasury."""
    fg = get_furnace_group(
        fg_id=f"{group_id}_fg",
        utilization_rate=0.7,
        lifetime=PointInTime(
            current=Year(2025),
            time_frame=TimeFrame(start=Year(2015), end=Year(2045)),
            plant_lifetime=20,
        ),
        capacity=Volumes(3_000_000.0),
        tech_name="EAF",
    )
    plant = get_plant(
        furnace_groups=[fg],
        plant_id=f"{group_id}_plant",
        location=Location(lat=39.0, lon=116.0, country="China", region="Asia", iso3=iso3, geo_unit=geo_unit),
    )
    plant.parent_gem_id = group_id
    group = PlantGroup(plant_group_id=group_id, plants=[plant])
    group.balance = 1e9
    return group


PAIRED = object()


def run_expansion(
    mocker,
    group: PlantGroup,
    *,
    tech: str = "DRI",
    reductant: str = "Natural gas",
    year: int = 2027,
    capacity: float = 2_500_000.0,
    hook=None,
    sizing_query=PAIRED,
):
    """Drive evaluate_expansion with the NPV fan-out mocked to one winning option.

    The mocked option carries the capacity its NPV was taken at, which the sizing
    query decides — so the driver sizes the winner exactly as the real fan-out
    would. Production binds gate and query from one holder, hence PAIRED; pass
    ``sizing_query=None`` alongside a gate to drive the mismatch deliberately.
    """
    plant_id = group.plants[0].plant_id
    if sizing_query is PAIRED:
        sizing_query = None if hook is None else cp_handlers.increase_sizing_hook()
    npv_capacity = (
        capacity
        if sizing_query is None
        else sizing_query(
            iso3=group.plants[0].location.iso3,
            technology=tech,
            reductant=reductant,
            capacity=capacity,
        )
    )
    mocker.patch.object(
        group,
        "evaluate_expansion_options",
        return_value={plant_id: (5_000_000.0, tech, CAPEX[tech], reductant, Volumes(npv_capacity))},
    )
    extra = {}
    if hook is not None:
        extra["permitted_expansion_capacity"] = hook
    if sizing_query is not None:
        extra["increase_sizing_query"] = sizing_query
    return group.evaluate_expansion(
        price_series={"steel": [600.0] * 22, "iron": [400.0] * 22},
        capacity=Volumes(capacity),
        region_capex={"Asia": CAPEX},
        dynamic_feedstocks={},
        fopex_for_iso3={"CHN": {}, "IND": {}},
        iso3_to_region_map={"CHN": "Asia", "IND": "Asia"},
        probabilistic_agents=False,
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=[],
        global_risk_free_rate=0.02,
        equity_share=EQUITY_SHARE,
        tech_to_product=TECH_TO_PRODUCT,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(year),
        allowed_techs={Year(y): ["EAF", "DRI", "MOE"] for y in range(2020, 2041)},
        cost_of_debt_dict={"CHN": DEBT_RATES, "IND": DEBT_RATES},
        cost_of_equity_dict={"CHN": EQUITY_RATES, "IND": EQUITY_RATES},
        get_bom_from_avg_boms=MagicMock(),
        reductant_score_series=MagicMock(),
        capacity_limit_steel=Volumes(1e12),
        capacity_limit_iron=Volumes(1e12),
        installed_capacity_in_year=lambda product: Volumes(0),
        new_plant_capacity_in_year=lambda product: Volumes(0),
        new_capacity_share_from_new_plants=0.5,
        active_statuses=["operating"],
        **extra,
    )


def capture_npv_full(monkeypatch) -> list[dict]:
    """Replace calculate_npv_full with a recorder; the options loop resolves it from the module."""
    captured: list[dict] = []

    def fake(**kwargs):
        captured.append(kwargs)
        return 1_000_000.0

    monkeypatch.setattr(cc, "calculate_npv_full", fake)
    return captured


def stub_bom(_energy_costs, _tech, _capacity, _reductant=None):
    return ({"materials": {"iron_ore": {"unit_cost": 100.0, "demand": 1.0}}, "energy": {}}, 0.9, "natural_gas", {})


def stub_score_series(*_args, **_kwargs):
    return cc.ReductantScoreSeries(scores=[0.0] * 20, picks=["natural_gas"] * 20)


def run_expansion_options(
    group: PlantGroup,
    *,
    techs: tuple[str, ...] = ("DRI",),
    year: int = 2027,
    capacity: float = 3_000_000.0,
    sizing_query=None,
):
    """Drive the real per-candidate fan-out, which is where sizing happens."""
    fopex = {tech.lower(): 10.0 for tech in CAPEX}
    return group.evaluate_expansion_options(
        price_series={"steel": [600.0] * 30, "iron": [400.0] * 30},
        capacity=Volumes(capacity),
        region_capex={"Asia": CAPEX},
        cost_of_debt_dict={"CHN": DEBT_RATES, "IND": DEBT_RATES},
        cost_of_equity_dict={"CHN": EQUITY_RATES, "IND": EQUITY_RATES},
        get_bom_from_avg_boms=stub_bom,
        reductant_score_series=stub_score_series,
        dynamic_feedstocks={},
        fopex_for_iso3={"CHN": fopex, "IND": fopex},
        iso3_to_region_map={"CHN": "Asia", "IND": "Asia"},
        chosen_emissions_boundary_for_carbon_costs="scope_1",
        technology_emission_factors=[],
        global_risk_free_rate=0.02,
        equity_share=EQUITY_SHARE,
        tech_to_product=TECH_TO_PRODUCT,
        plant_lifetime=20,
        construction_time=2,
        current_year=Year(year),
        allowed_techs={Year(y): list(techs) for y in range(2020, 2041)},
        active_statuses=["operating"],
        increase_sizing_query=sizing_query,
    )


class TestDefaultPath:
    def test_no_hook_commands_planned_capacity(self, mocker):
        """Without the parameter the command carries the planned capacity and equity."""
        group = make_group()

        command = run_expansion(mocker, group)

        assert isinstance(command, AddFurnaceGroup)
        assert command.capacity == pytest.approx(2_500_000.0)
        assert command.equity_needed == pytest.approx(2_500_000.0 * CAPEX["DRI"] * EQUITY_SHARE)

    def test_no_hook_never_consults_a_bound_pool(self, mocker):
        """The gate rides the threaded parameter, not the binding: an unthreaded call
        leaves even a bound pool untouched."""
        pool = bind_policy()
        pool.deposit(credit(5_000_000.0, 2020, tag="Jing-Jin-Ji"))
        group = make_group()

        command = run_expansion(mocker, group)

        assert isinstance(command, AddFurnaceGroup)
        assert pool.total() == pytest.approx(5_000_000.0)


class TestBoundExpansionGate:
    def test_granted_non_intense_builds_full_and_consumes_fifo(self, mocker, caplog):
        """A clean grant builds what it withdrew; consumption is FIFO with the
        partial remainder keeping its original vintage."""
        pool = bind_policy()
        pool.deposit(credit(1_500_000.0, 2019, tag="Jing-Jin-Ji", owner="E_a"))
        pool.deposit(credit(2_000_000.0, 2022, tag="Jing-Jin-Ji", owner="E_b"))
        group = make_group()

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            command = run_expansion(mocker, group, hook=cp_handlers.expansion_capacity_hook())

        assert isinstance(command, AddFurnaceGroup)
        assert command.capacity == pytest.approx(2_500_000.0)
        assert command.equity_needed == pytest.approx(2_500_000.0 * CAPEX["DRI"] * EQUITY_SHARE)
        (remainder,) = pool.snapshot()
        assert remainder.amount_mt == pytest.approx(1_000_000.0)
        assert remainder.vintage_year == 2022
        assert remainder.owner_id == "E_b"
        assert "gate=expansion decision=granted" in caplog.text
        assert "credits_consumed=" in caplog.text

    def test_granted_intense_withdraws_planned_and_builds_penalised(self, mocker):
        """An emission-intense expansion spends the planned capacity but the command —
        and its equity — carry the penalised build."""
        pool = bind_policy()
        pool.deposit(credit(3_000_000.0, 2020, tag="Jing-Jin-Ji", product="steel"))
        group = make_group()

        command = run_expansion(
            mocker,
            group,
            tech="EAF",
            reductant="Electricity",
            capacity=3_000_000.0,
            hook=cp_handlers.expansion_capacity_hook(),
        )

        assert isinstance(command, AddFurnaceGroup)
        assert command.capacity == pytest.approx(2_000_000.0)
        assert command.equity_needed == pytest.approx(2_000_000.0 * CAPEX["EAF"] * EQUITY_SHARE)
        assert pool.total() == 0.0

    def test_blocked_short_pool_drops_the_expansion(self, mocker, caplog):
        pool = bind_policy()
        pool.deposit(credit(1_000_000.0, 2020, tag="Jing-Jin-Ji"))
        group = make_group()

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            command = run_expansion(mocker, group, hook=cp_handlers.expansion_capacity_hook())

        assert command is None
        assert pool.total() == pytest.approx(1_000_000.0)
        assert "reason=insufficient_applicable_pool" in caplog.text

    def test_key_province_build_cannot_spend_untagged_credit(self, mocker):
        """A build in a key province may spend only its own cluster's credits."""
        pool = bind_policy()
        pool.deposit(credit(5_000_000.0, 2020, tag=None))
        group = make_group(geo_unit="CN-HE")

        command = run_expansion(mocker, group, hook=cp_handlers.expansion_capacity_hook())

        assert command is None
        assert pool.total() == pytest.approx(5_000_000.0)

    def test_non_key_build_spends_any_credit(self, mocker):
        """Capacity bleeds out of key regions, never in: a non-key build may consume a
        key-region-tagged credit."""
        pool = bind_policy()
        pool.deposit(credit(2_500_000.0, 2020, tag="Jing-Jin-Ji"))
        group = make_group(geo_unit="CN-GD")

        command = run_expansion(mocker, group, hook=cp_handlers.expansion_capacity_hook())

        assert isinstance(command, AddFurnaceGroup)
        assert pool.total() == 0.0

    def test_iron_credit_cannot_fund_steel_expansion(self, mocker):
        """Product separation: iron and steel credits are separate stocks."""
        pool = bind_policy()
        pool.deposit(credit(5_000_000.0, 2020, tag="Jing-Jin-Ji", product="iron"))
        group = make_group()

        command = run_expansion(
            mocker,
            group,
            tech="EAF",
            reductant="Electricity",
            capacity=3_000_000.0,
            hook=cp_handlers.expansion_capacity_hook(),
        )

        assert command is None
        assert pool.total() == pytest.approx(5_000_000.0)


class TestOwnerPartition:
    """The partition lives in the pool; the gate only passes owner and year."""

    def test_pre_cutoff_draws_across_owners(self, mocker):
        pool = bind_policy()
        pool.deposit(credit(2_500_000.0, 2026, owner="E_other"))
        group = make_group(geo_unit="CN-GD")

        command = run_expansion(mocker, group, year=2027, hook=cp_handlers.expansion_capacity_hook())

        assert isinstance(command, AddFurnaceGroup)
        assert pool.total() == 0.0

    def test_from_cutoff_only_own_credits_apply(self, mocker):
        pool = bind_policy()
        pool.deposit(credit(2_500_000.0, 2026, owner="E_other"))
        group = make_group(geo_unit="CN-GD")

        command = run_expansion(mocker, group, year=2028, hook=cp_handlers.expansion_capacity_hook())

        assert command is None
        assert pool.total() == pytest.approx(2_500_000.0)

    def test_from_cutoff_own_credits_still_grant(self, mocker):
        pool = bind_policy()
        pool.deposit(credit(2_500_000.0, 2026, owner="gem_cpool_exp"))
        group = make_group(geo_unit="CN-GD")

        command = run_expansion(mocker, group, year=2028, hook=cp_handlers.expansion_capacity_hook())

        assert isinstance(command, AddFurnaceGroup)
        assert pool.total() == 0.0

    def test_none_cutoff_dissolves_the_partition(self, mocker):
        pool = bind_policy(CapacityPool(inter_company_swap_cutoff_year=None))
        pool.deposit(credit(2_500_000.0, 2026, owner="E_other"))
        group = make_group(geo_unit="CN-GD")

        command = run_expansion(mocker, group, year=2030, hook=cp_handlers.expansion_capacity_hook())

        assert isinstance(command, AddFurnaceGroup)
        assert pool.total() == 0.0


class TestSizedValuation:
    """The sizing query decides the capacity each candidate's NPV is taken at."""

    def test_intense_candidate_is_valued_at_the_penalised_capacity(self, monkeypatch):
        bind_policy()
        captured = capture_npv_full(monkeypatch)
        group = make_group()

        options = run_expansion_options(group, techs=("EAF",), sizing_query=cp_handlers.increase_sizing_hook())

        assert [call["capacity"] for call in captured] == [pytest.approx(2_000_000.0)]
        assert options[group.plants[0].plant_id][4] == pytest.approx(2_000_000.0)

    def test_clean_candidate_is_valued_at_the_planned_capacity(self, monkeypatch):
        bind_policy()
        captured = capture_npv_full(monkeypatch)
        group = make_group()

        options = run_expansion_options(group, techs=("DRI",), sizing_query=cp_handlers.increase_sizing_hook())

        assert [call["capacity"] for call in captured] == [pytest.approx(3_000_000.0)]
        assert options[group.plants[0].plant_id][4] == pytest.approx(3_000_000.0)

    def test_absent_query_values_even_an_intense_candidate_at_the_plan(self, monkeypatch):
        """The default path: no query threaded, nothing sized."""
        captured = capture_npv_full(monkeypatch)
        group = make_group()

        options = run_expansion_options(group, techs=("EAF",))

        assert [call["capacity"] for call in captured] == [pytest.approx(3_000_000.0)]
        assert options[group.plants[0].plant_id][4] == pytest.approx(3_000_000.0)

    def test_a_foreign_plant_is_never_sized(self, monkeypatch):
        bind_policy()
        captured = capture_npv_full(monkeypatch)
        group = make_group(iso3="IND", geo_unit=None, group_id="gem_foreign_opts")

        run_expansion_options(group, techs=("EAF",), sizing_query=cp_handlers.increase_sizing_hook())

        assert [call["capacity"] for call in captured] == [pytest.approx(3_000_000.0)]


class TestPenaltyReachesTheDecision:
    def test_a_balance_between_the_two_equities_now_expands(self, mocker):
        """The point of the split: equity follows the penalised build, so a group that
        could not afford the plan can afford what the policy lets it build."""
        pool = bind_policy()
        pool.deposit(credit(3_000_000.0, 2020, tag="Jing-Jin-Ji", product="steel"))
        group = make_group()
        penalised_equity = 2_000_000.0 * CAPEX["EAF"] * EQUITY_SHARE
        planned_equity = 3_000_000.0 * CAPEX["EAF"] * EQUITY_SHARE
        group.balance = (penalised_equity + planned_equity) / 2

        command = run_expansion(
            mocker,
            group,
            tech="EAF",
            reductant="Electricity",
            capacity=3_000_000.0,
            hook=cp_handlers.expansion_capacity_hook(),
        )

        assert isinstance(command, AddFurnaceGroup)
        assert command.capacity == pytest.approx(2_000_000.0)
        assert command.equity_needed == pytest.approx(penalised_equity)

    def test_a_gate_without_its_sizing_query_raises(self, mocker):
        """Production binds the pair from one holder; a gate that grants less than the
        NPV was taken at is a wiring fault, not something to correct silently."""
        pool = bind_policy()
        pool.deposit(credit(3_000_000.0, 2020, tag="Jing-Jin-Ji", product="steel"))
        group = make_group()

        with pytest.raises(ValueError, match="sizing query and the gate must agree"):
            run_expansion(
                mocker,
                group,
                tech="EAF",
                reductant="Electricity",
                capacity=3_000_000.0,
                hook=cp_handlers.expansion_capacity_hook(),
                sizing_query=None,
            )


class TestNonChinesePassthrough:
    def test_foreign_group_never_reaches_evaluator_or_pool(self, mocker):
        """A bound gate leaves a non-Chinese expansion untouched without consulting
        the evaluator or the pool."""
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig())
        pool = CapacityPool()
        cp_handlers.bind_capacity_policy(evaluator, pool, CapacityPolicyRecorder())
        on_increase_spy = mocker.spy(evaluator, "on_increase")
        try_withdraw_spy = mocker.spy(pool, "try_withdraw")
        group = make_group(iso3="IND", geo_unit=None, group_id="gem_foreign_exp")

        command = run_expansion(mocker, group, hook=cp_handlers.expansion_capacity_hook())

        assert isinstance(command, AddFurnaceGroup)
        assert command.capacity == pytest.approx(2_500_000.0)
        assert on_increase_spy.call_count == 0
        assert try_withdraw_spy.call_count == 0
