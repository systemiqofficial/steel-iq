"""Tests for the ③ INCREASE greenfield gate and attribution.

The gate sits in ``FurnaceGroup.track_business_opportunities`` at
considered→announced, beside the G2 CO2 gate whose block-and-retry shape it
copies: a blocked withdrawal returns no command, the opportunity stays
considered and retries next year, and a granted retry consumes on the retry
year. Withdrawals are single-owner — a build can be refused with an ample pool
when no one holder covers it. A grant stashes the withdrawal and the funding
owner on the furnace group; at announced→construction the
``FurnaceGroupAdded`` handler moves the plant into the funding company (group
membership only — ``parent_gem_id`` keeps the GEO pricing paths), with dormant
and unknown owners falling back to ``indi_<iso3>``. Capacities flow in model
tonnes.
"""

import logging
from collections import Counter
from types import SimpleNamespace

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
from steelo.domain import PointInTime, TimeFrame, Volumes, Year, events
from steelo.domain.commands import UpdateFurnaceGroupStatus
from steelo.domain.models import Location, PlantGroup
from steelo.service_layer.unit_of_work import UnitOfWork

# Synthetic policy rows shared with the expansion-gate tests: EAF is authored
# emission-intense so a steel greenfield is penalised; DRI is a clean iron build.
REGIONS = [
    RegionRow(geo_key="CHN:CN-HE", region_name="Jing-Jin-Ji", type="key", from_year=None),
    RegionRow(geo_key="CHN:CN-GD", region_name=None, type=None, from_year=None),
]
TECHNOLOGIES = [
    TechnologyRow(
        technology="EAF",
        product="steel",
        reductant=None,
        is_emission_intense=True,
        is_deep_abatement=False,
        switching_to=None,
        swap_ratio=None,
    ),
    TechnologyRow(
        technology="DRI",
        product="iron",
        reductant=None,
        is_emission_intense=False,
        is_deep_abatement=False,
        switching_to=None,
        swap_ratio=None,
    ),
]

BOM = {
    "materials": {"scrap": {"unit_cost": 200.0, "demand": 1.0}},
    "energy": {"electricity": {"unit_cost": 80.0, "demand": 0.5}},
}


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()


@pytest.fixture(autouse=True)
def positive_npv(mocker):
    """Pin the yearly re-valuation positive; the gate, not the NPV, is under test."""
    mocker.patch("steelo.domain.calculate_costs.calculate_npv_full", return_value=5_000_000.0)


def bind_policy(pool: CapacityPool | None = None) -> CapacityPool:
    """Bind a real evaluator and pool; return the pool for asserts."""
    recorder = CapacityPolicyRecorder()
    evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(), recorder=recorder)
    pool = pool if pool is not None else CapacityPool()
    cp_handlers.bind_capacity_policy(evaluator, pool, recorder)
    return pool


def credit(amount: float, vintage: int, *, tag: str | None = None, owner: str | None = None, product: str = "iron"):
    return Credit(amount_mt=amount, vintage_year=vintage, region_tag=tag, owner_id=owner, product=product)


def make_opportunity(*, tech_name: str = "DRI", capacity: float = 2_000_000.0, fg_id: str = "fg_gf_opp"):
    """Build a considered opportunity with two positive NPV years already recorded."""
    fg = get_furnace_group(
        fg_id=fg_id,
        tech_name=tech_name,
        capacity=Volumes(capacity),
        lifetime=PointInTime(
            current=Year(2026),
            time_frame=TimeFrame(start=Year(2030), end=Year(2050)),
            plant_lifetime=20,
        ),
    )
    fg.status = "considered"
    fg.technology.capex = 900.0
    fg.historical_npv_business_opportunities = {2024: 5_000_000.0, 2025: 5_000_000.0}
    fg.bill_of_materials = BOM
    fg.output_shares = {"scrap": 1.0}
    fg.energy_costs_no_subsidy = {"electricity": 60.0, "hydrogen": 4.0}
    fg.chosen_reductant = "Electricity" if tech_name == "EAF" else "Natural gas"
    return fg


def track(fg, *, year: int = 2026, iso3: str = "CHN", geo_unit: str | None = "CN-GD", hook=None, stats=None):
    """Drive the real considered→announced re-check for one year."""
    location = Location(lat=30.0, lon=110.0, country="China", region="Asia", iso3=iso3, geo_unit=geo_unit)
    extra = {} if hook is None else {"permitted_greenfield_capacity": hook}
    return fg.track_business_opportunities(
        year=Year(year),
        location=location,
        market_price={"steel": [600.0] * 30, "iron": [400.0] * 30},
        cost_of_equity=0.08,
        plant_lifetime=20,
        construction_time=2,
        consideration_time=3,
        probability_of_announcement=1.0,
        all_opex_subsidies=[],
        reductant_score_series=lambda *args, **kwargs: SimpleNamespace(scores=[0.0] * 20),
        status_stats=stats,
        **extra,
    )


class TestDefaultPath:
    def test_no_hook_announces_without_consulting_a_bound_pool(self):
        """The gate rides the threaded parameter, not the binding."""
        pool = bind_policy()
        pool.deposit(credit(5_000_000.0, 2020))
        fg = make_opportunity()

        command = track(fg)

        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert command.new_status == "announced"
        assert pool.total() == pytest.approx(5_000_000.0)
        assert fg.capacity_pool_granted_withdraw_mt is None
        assert fg.capacity_pool_attributed_owner_id is None


class TestBoundGreenfieldGate:
    def test_single_owner_rule_blocks_despite_ample_pool(self, caplog):
        """A 2.0 Mt build is refused when A holds 1.0 and B holds 1.5: the pool is
        ample but no single owner covers it, and the log says so distinctly."""
        pool = bind_policy()
        pool.deposit(credit(1_000_000.0, 2019, owner="E_a"))
        pool.deposit(credit(1_500_000.0, 2020, owner="E_b"))
        fg = make_opportunity()
        stats: Counter = Counter()

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            command = track(fg, hook=cp_handlers.greenfield_capacity_hook(), stats=stats)

        assert command is None
        assert stats["capacity_pool_blocked"] == 1
        assert stats["announced"] == 0
        assert pool.total() == pytest.approx(2_500_000.0)
        assert fg.status == "considered"
        assert fg.capacity_pool_granted_withdraw_mt is None
        assert "reason=no_single_owner_with_sufficient_credits" in caplog.text

    def test_blocked_opportunity_retries_and_consumes_on_the_retry_year(self):
        """G2's block-and-retry shape: blocked in 2026, granted in 2027 once a single
        holder covers it — and the credit is consumed in 2027, not 2026."""
        pool = bind_policy()
        pool.deposit(credit(1_000_000.0, 2019, owner="E_a"))
        pool.deposit(credit(1_500_000.0, 2020, owner="E_b"))
        fg = make_opportunity()

        blocked = track(fg, year=2026, hook=cp_handlers.greenfield_capacity_hook())
        assert blocked is None
        assert pool.total() == pytest.approx(2_500_000.0)

        pool.deposit(credit(2_000_000.0, 2026, owner="E_c"))
        granted = track(fg, year=2027, hook=cp_handlers.greenfield_capacity_hook())

        assert isinstance(granted, UpdateFurnaceGroupStatus)
        assert granted.new_status == "announced"
        assert pool.total() == pytest.approx(2_500_000.0)  # E_c consumed; A and B untouched
        assert {c.owner_id for c in pool.snapshot()} == {"E_a", "E_b"}
        assert fg.capacity_pool_granted_withdraw_mt == pytest.approx(2_000_000.0)
        assert fg.capacity_pool_attributed_owner_id == "E_c"

    def test_intense_greenfield_withdraws_planned_and_builds_penalised(self):
        """An emission-intense greenfield spends the planned capacity but the furnace
        group shrinks to the penalised build at announcement."""
        pool = bind_policy()
        pool.deposit(credit(3_000_000.0, 2020, owner="E_a", product="steel"))
        fg = make_opportunity(tech_name="EAF", capacity=3_000_000.0)

        command = track(fg, hook=cp_handlers.greenfield_capacity_hook())

        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert fg.capacity == pytest.approx(2_000_000.0)
        assert fg.capacity_pool_granted_withdraw_mt == pytest.approx(3_000_000.0)
        assert pool.total() == 0.0

    def test_wholly_unowned_draw_attributes_to_nobody(self):
        """The unowned pot is a holder like any other; its grant carries no owner, so
        the plant will stay at indi_<iso3>."""
        pool = bind_policy()
        pool.deposit(credit(2_000_000.0, 2018))
        fg = make_opportunity()

        command = track(fg, hook=cp_handlers.greenfield_capacity_hook())

        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert fg.capacity_pool_granted_withdraw_mt == pytest.approx(2_000_000.0)
        assert fg.capacity_pool_attributed_owner_id is None
        assert pool.total() == 0.0

    def test_key_province_build_spends_only_its_cluster(self):
        pool = bind_policy()
        pool.deposit(credit(5_000_000.0, 2020, owner="E_a"))
        fg = make_opportunity()

        command = track(fg, geo_unit="CN-HE", hook=cp_handlers.greenfield_capacity_hook())

        assert command is None
        assert pool.total() == pytest.approx(5_000_000.0)

    def test_non_chinese_opportunity_never_reaches_evaluator_or_pool(self, mocker):
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig())
        pool = CapacityPool()
        cp_handlers.bind_capacity_policy(evaluator, pool, CapacityPolicyRecorder())
        on_increase_spy = mocker.spy(evaluator, "on_increase")
        try_withdraw_spy = mocker.spy(pool, "try_withdraw")
        fg = make_opportunity()

        command = track(fg, iso3="IND", geo_unit=None, hook=cp_handlers.greenfield_capacity_hook())

        assert isinstance(command, UpdateFurnaceGroupStatus)
        assert on_increase_spy.call_count == 0
        assert try_withdraw_spy.call_count == 0
        assert fg.capacity_pool_granted_withdraw_mt is None
        assert fg.capacity_pool_attributed_owner_id is None


def make_attribution_world(
    *, stash_owner: str | None = "E_owner", owner_exists: bool = True, owner_dormant: bool = False
):
    """A funded opportunity plant in indi_CHN plus (optionally) its funding company."""
    opp_fg = get_furnace_group(fg_id="fg_gf_new", tech_name="DRI", capacity=Volumes(2_000_000.0))
    opp_fg.status = "construction"
    opp_fg.technology.capex = 900.0
    opp_fg.capacity_pool_granted_withdraw_mt = 2_000_000.0
    opp_fg.capacity_pool_attributed_owner_id = stash_owner
    opp_plant = get_plant(
        furnace_groups=[opp_fg],
        plant_id="plant_gf_new",
        location=Location(lat=30.0, lon=110.0, country="China", region="Asia", iso3="CHN", geo_unit="CN-GD"),
    )
    opp_plant.parent_gem_id = "indi_CHN"

    owner_fg = get_furnace_group(fg_id="fg_owner_asset", tech_name="EAF")
    owner_fg.status = "closed" if owner_dormant else "operating"
    owner_plant = get_plant(
        furnace_groups=[owner_fg],
        plant_id="plant_owner",
        location=Location(lat=39.0, lon=116.0, country="China", region="Asia", iso3="CHN", geo_unit="CN-HE"),
    )
    owner_plant.parent_gem_id = "E_owner"

    uow = UnitOfWork()
    uow.plants.add_list([opp_plant, owner_plant])
    indi = PlantGroup(plant_group_id="indi_CHN", plants=[opp_plant])
    uow.plant_groups.add(indi)
    owner_group = None
    if owner_exists:
        owner_group = PlantGroup(plant_group_id="E_owner", plants=[owner_plant])
        uow.plant_groups.add(owner_group)
    return uow, opp_plant, opp_fg, indi, owner_group


def added_event(*, is_new_plant: bool = True) -> events.FurnaceGroupAdded:
    return events.FurnaceGroupAdded(
        plant_id="plant_gf_new",
        furnace_group_id="fg_gf_new",
        technology_name="DRI",
        capacity=2_000_000.0,
        is_new_plant=is_new_plant,
    )


class TestGreenfieldAttribution:
    def test_plant_joins_the_funding_company_at_construction_start(self, caplog):
        """The plant moves into the owner's group — membership only, so the sweep and
        expansion candidacy follow while parent_gem_id keeps the GEO pricing paths —
        and the capital injection is logged."""
        bind_policy()
        uow, opp_plant, _, indi, owner_group = make_attribution_world()

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.attribute_greenfield_on_furnace_group_added(added_event(), uow=uow)

        assert opp_plant in owner_group.plants
        assert opp_plant not in indi.plants
        assert uow.plant_groups.get_by_plant_id("plant_gf_new") is owner_group
        assert opp_plant.parent_gem_id == "indi_CHN"
        assert "event=greenfield_attributed" in caplog.text
        assert "company=E_owner" in caplog.text
        assert f"capex_total={900.0 * 2_000_000.0:.2f}" in caplog.text
        assert f"equity_injection={900.0 * 2_000_000.0 * 0.2:.2f}" in caplog.text

    def test_dormant_owner_falls_back_to_indi(self, caplog):
        bind_policy()
        uow, opp_plant, _, indi, owner_group = make_attribution_world(owner_dormant=True)

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.attribute_greenfield_on_furnace_group_added(added_event(), uow=uow)

        assert opp_plant in indi.plants
        assert opp_plant not in owner_group.plants
        assert "decision=fallback_indi" in caplog.text
        assert "reason=owner_dormant" in caplog.text

    def test_missing_owner_group_falls_back_to_indi(self, caplog):
        bind_policy()
        uow, opp_plant, _, indi, _ = make_attribution_world(owner_exists=False)

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.attribute_greenfield_on_furnace_group_added(added_event(), uow=uow)

        assert opp_plant in indi.plants
        assert "reason=owner_group_missing" in caplog.text

    def test_unowned_grant_stays_at_indi(self):
        bind_policy()
        uow, opp_plant, _, indi, owner_group = make_attribution_world(stash_owner=None)

        cp_handlers.attribute_greenfield_on_furnace_group_added(added_event(), uow=uow)

        assert opp_plant in indi.plants
        assert opp_plant not in owner_group.plants

    def test_expansion_added_event_is_ignored(self):
        bind_policy()
        uow, opp_plant, _, indi, _ = make_attribution_world()

        cp_handlers.attribute_greenfield_on_furnace_group_added(added_event(is_new_plant=False), uow=uow)

        assert opp_plant in indi.plants

    def test_unbound_handler_is_a_no_op(self):
        uow, opp_plant, _, indi, _ = make_attribution_world()

        cp_handlers.attribute_greenfield_on_furnace_group_added(added_event(), uow=uow)

        assert opp_plant in indi.plants


class TestIsDormant:
    def test_operating_group_is_not_dormant(self):
        fg = get_furnace_group(fg_id="fg_alive")
        group = PlantGroup(plant_group_id="E_x", plants=[get_plant(furnace_groups=[fg], plant_id="p_alive")])
        assert group.is_dormant is False

    @pytest.mark.parametrize("status", ["closed", "construction", "announced", "considered"])
    def test_group_without_operating_assets_is_dormant(self, status):
        """A non-operating status — including construction — does not count as alive;
        balance is irrelevant, which is why this is not is_bankrupt."""
        fg = get_furnace_group(fg_id="fg_idle")
        fg.status = status
        group = PlantGroup(plant_group_id="E_x", plants=[get_plant(furnace_groups=[fg], plant_id="p_idle")])
        group.balance = 1e9
        assert group.is_dormant is True

    def test_operating_pre_retirement_counts_as_alive(self):
        fg = get_furnace_group(fg_id="fg_pre")
        fg.status = "Operating pre-retirement"
        group = PlantGroup(plant_group_id="E_x", plants=[get_plant(furnace_groups=[fg], plant_id="p_pre")])
        assert group.is_dormant is False


class TestDiscardLeak:
    def test_discarded_announced_greenfield_logs_the_leak(self, caplog):
        fg = make_opportunity()
        fg.capacity_pool_granted_withdraw_mt = 2_000_000.0
        fg.capacity_pool_attributed_owner_id = "E_c"

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.note_greenfield_discard(fg, "CHN", "CN-HE", 2027)

        assert "event=greenfield_discarded" in caplog.text
        assert "leaked_withdraw_mt=2000000.000" in caplog.text
        assert "attributed_owner=E_c" in caplog.text

    def test_ungated_discard_stays_silent(self, caplog):
        fg = make_opportunity()

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.note_greenfield_discard(fg, "CHN", "CN-HE", 2027)

        assert "[CAPACITY POOL]" not in caplog.text

    def test_bound_discard_records_the_leak_as_a_ledger_row(self):
        """The leak becomes a run artefact, not just a log line — but it never
        re-credits the pool, so it stays out of the reconciliation sum."""
        bind_policy()
        recorder = cp_handlers._policy.recorder
        fg = make_opportunity()
        fg.capacity_pool_granted_withdraw_mt = 2_000_000.0
        fg.capacity_pool_attributed_owner_id = "E_c"

        cp_handlers.note_greenfield_discard(fg, "CHN", "CN-HE", 2027)

        (row,) = recorder._ledger
        assert row["operation"] == "greenfield_discard"
        assert row["amount_t"] == pytest.approx(2_000_000.0)
        assert row["attributed_owner_id"] == "E_c"
        assert row["geo_key"] == "CHN:CN-HE"
        assert row["furnace_group_id"] == fg.furnace_group_id
