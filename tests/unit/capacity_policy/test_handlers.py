"""Tests for the deposit handlers: inert until bound, China-only, correct credits."""

import logging
from dataclasses import dataclass

import pytest

from steelo.capacity_policy import CapacityPolicyConfig, CapacityPool, Credit, TreeEvaluator
from steelo.capacity_policy import handlers as cp_handlers
from steelo.capacity_policy.inputs import RegionRow, TechnologyRow
from steelo.domain import events
from steelo.service_layer import handlers as service_handlers

REGIONS = [
    RegionRow(geo_key="CHN:CN-HE", region_name="Jing-Jin-Ji", type="key", from_year=None),
    RegionRow(geo_key="CHN:CN-GD", region_name="Guangdong", type=None, from_year=None),
]

TECHNOLOGIES = [
    TechnologyRow(
        technology="BF",
        product="iron",
        reductant=None,
        is_emission_intense=True,
        is_deep_abatement=False,
        switching_to=None,
        swap_ratio=None,
    ),
]


@dataclass
class FakeFurnaceGroup:
    furnace_group_id: str
    chosen_reductant: str = "Coke+PCI"


@dataclass
class FakePlant:
    furnace_groups: list[FakeFurnaceGroup]


@dataclass
class FakePlantsRepo:
    plants: list[FakePlant]

    def list(self):
        return list(self.plants)


class FakeUoW:
    def __init__(self, furnace_groups: list[FakeFurnaceGroup]):
        self.plants = FakePlantsRepo([FakePlant(furnace_groups=furnace_groups)])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@dataclass
class FakeEnv:
    year: float = 2031.0


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level binding must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()


@pytest.fixture
def pool() -> CapacityPool:
    return CapacityPool()


@pytest.fixture
def bound(pool: CapacityPool) -> CapacityPool:
    evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig())
    cp_handlers.bind_capacity_policy(evaluator, pool)
    return pool


def closed_event(geo_unit: str | None = "CN-HE", iso3: str = "CHN") -> events.FurnaceGroupClosed:
    return events.FurnaceGroupClosed(
        furnace_group_id="fg-1",
        capacity=2.0,
        iso3=iso3,
        geo_unit=geo_unit,
        owner_id="E1",
        product="iron",
    )


def tech_changed_event(old_capacity: float, capacity: float) -> events.FurnaceGroupTechChanged:
    return events.FurnaceGroupTechChanged(
        furnace_group_id="fg-1",
        technology_name="BF+CCS",
        capacity=capacity,
        iso3="CHN",
        geo_unit="CN-GD",
        old_technology_name="BF",
        old_capacity=old_capacity,
        owner_id="E1",
        product="iron",
    )


def renovated_event(
    old_capacity: float = 2.0, capacity: float = 2.0, iso3: str = "CHN"
) -> events.FurnaceGroupRenovated:
    return events.FurnaceGroupRenovated(
        furnace_group_id="fg-1",
        capacity=capacity,
        old_capacity=old_capacity,
        iso3=iso3,
        geo_unit="CN-HE",
        old_technology_name="BF",
        new_technology_name="BF",
        owner_id="E1",
        product="iron",
    )


def make_uow() -> FakeUoW:
    return FakeUoW([FakeFurnaceGroup(furnace_group_id="fg-1")])


class TestInertness:
    def test_registered_on_the_event_bus(self):
        """The four handlers sit on EVENT_HANDLERS under their events."""
        assert cp_handlers.deposit_on_furnace_group_closed in service_handlers.EVENT_HANDLERS[events.FurnaceGroupClosed]
        assert (
            cp_handlers.deposit_on_furnace_group_tech_changed
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupTechChanged]
        )
        assert (
            cp_handlers.deposit_on_furnace_group_renovated
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupRenovated]
        )
        assert (
            cp_handlers.attribute_greenfield_on_furnace_group_added
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupAdded]
        )

    def test_unbound_handlers_are_no_ops(self):
        """Unbound, the handlers return before touching uow, env or any pool."""
        cp_handlers.deposit_on_furnace_group_closed(closed_event(), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.deposit_on_furnace_group_tech_changed(tech_changed_event(3.0, 2.0), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.deposit_on_furnace_group_renovated(renovated_event(), uow=None, env=None)  # type: ignore[arg-type]

    def test_bound_handlers_ignore_non_chinese_events(self, bound: CapacityPool):
        cp_handlers.deposit_on_furnace_group_closed(
            closed_event(iso3="DEU", geo_unit=None), uow=make_uow(), env=FakeEnv()
        )  # type: ignore[arg-type]
        assert bound.total() == 0.0


class TestClosedDeposit:
    def test_deposits_full_capacity_with_cluster_tag(self, bound: CapacityPool):
        """A key-province closure banks the full capacity tagged with the cluster."""
        cp_handlers.deposit_on_furnace_group_closed(closed_event(), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        (credit,) = bound.snapshot()
        assert credit.amount_mt == 2.0
        assert credit.region_tag == "Jing-Jin-Ji"
        assert credit.owner_id == "E1"
        assert credit.product == "iron"
        assert credit.vintage_year == 2031

    def test_non_key_closure_deposits_untagged(self, bound: CapacityPool):
        cp_handlers.deposit_on_furnace_group_closed(closed_event(geo_unit="CN-GD"), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        (credit,) = bound.snapshot()
        assert credit.region_tag is None

    def test_deposit_log_carries_the_chosen_reductant(self, bound: CapacityPool, caplog):
        """The drift instrumentation names the reductant the group actually runs on."""
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.deposit_on_furnace_group_closed(closed_event(), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        assert "chosen_reductant=Coke+PCI" in caplog.text


class TestTechChangedDeposit:
    def test_positive_shrink_deposits_the_delta(self, bound: CapacityPool):
        """A shrunk replacement banks exactly old_capacity − capacity."""
        cp_handlers.deposit_on_furnace_group_tech_changed(tech_changed_event(3.0, 2.0), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        (credit,) = bound.snapshot()
        assert credit.amount_mt == pytest.approx(1.0)
        assert credit.region_tag is None
        assert credit.vintage_year == 2031

    @pytest.mark.parametrize("old_capacity, capacity", [(2.0, 2.0), (2.0, 3.0)])
    def test_non_positive_delta_deposits_nothing(self, bound: CapacityPool, old_capacity, capacity):
        """No shrink (the norm until D7a) or growth must not reach pool.deposit."""
        cp_handlers.deposit_on_furnace_group_tech_changed(
            tech_changed_event(old_capacity, capacity),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        assert bound.total() == 0.0


class TestRenovatedDeposit:
    def test_positive_shrink_deposits_the_delta(self, bound: CapacityPool):
        """A reline shrunk by the pre-NPV hook banks exactly old_capacity − capacity."""
        cp_handlers.deposit_on_furnace_group_renovated(
            renovated_event(old_capacity=3.0, capacity=2.0),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (credit,) = bound.snapshot()
        assert credit.amount_mt == pytest.approx(1.0)
        assert credit.region_tag == "Jing-Jin-Ji"
        assert credit.vintage_year == 2031

    @pytest.mark.parametrize("old_capacity, capacity", [(2.0, 2.0), (2.0, 3.0)])
    def test_non_positive_delta_deposits_nothing(self, bound: CapacityPool, old_capacity, capacity):
        """An unshrunk renovation — the norm under the default reline flag — banks nothing."""
        cp_handlers.deposit_on_furnace_group_renovated(
            renovated_event(old_capacity=old_capacity, capacity=capacity),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        assert bound.total() == 0.0

    def test_shrunk_deposit_logs_the_reductant(self, bound: CapacityPool, caplog):
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.deposit_on_furnace_group_renovated(
                renovated_event(old_capacity=3.0, capacity=2.0),
                uow=make_uow(),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            )
        assert "event=renovated" in caplog.text
        assert "chosen_reductant=Coke+PCI" in caplog.text


class TestReplaceCapacityHook:
    def test_unbound_accessor_returns_none(self):
        """Unbound — every real run until D8 — the decision path receives None."""
        assert cp_handlers.replace_capacity_hook() is None

    def test_bound_accessor_returns_a_callable(self, bound: CapacityPool):
        assert callable(cp_handlers.replace_capacity_hook())

    def test_non_chinese_plant_passes_through_untouched(self, bound: CapacityPool):
        """Foreign plants keep their capacity without reaching the evaluator: unknown
        technology names would raise if the classification lookup ran."""
        hook = cp_handlers.replace_capacity_hook()
        assert hook is not None
        permitted = hook(
            iso3="DEU",
            geo_unit=None,
            old_technology="not-a-technology",
            old_reductant=None,
            new_technology="also-not-one",
            new_reductant=None,
            capacity=3.0,
            historical_utilization=None,
            year=2025,
        )
        assert permitted == 3.0

    def test_same_technology_passes_through_under_the_default_reline_flag(self, bound: CapacityPool):
        """A reline is neutral by default: no shrink and no utilisation gate."""
        hook = cp_handlers.replace_capacity_hook()
        assert hook is not None
        permitted = hook(
            iso3="CHN",
            geo_unit="CN-GD",
            old_technology="BF",
            old_reductant="Coke+PCI",
            new_technology="BF",
            new_reductant="Coke+PCI",
            capacity=3.0,
            historical_utilization={2024: 0.1, 2025: 0.1},
            year=2025,
        )
        assert permitted == 3.0

    def test_same_technology_shrinks_when_reline_counts_as_replace(self, pool: CapacityPool):
        """With the flag on, a BF→BF reline of an intense group derives 1.5:1 from the flags."""
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(reline_counts_as_replace=True))
        cp_handlers.bind_capacity_policy(evaluator, pool)
        hook = cp_handlers.replace_capacity_hook()
        assert hook is not None
        permitted = hook(
            iso3="CHN",
            geo_unit="CN-GD",
            old_technology="BF",
            old_reductant="Coke+PCI",
            new_technology="BF",
            new_reductant="Coke+PCI",
            capacity=3.0,
            historical_utilization=None,
            year=2025,
        )
        assert permitted == pytest.approx(2.0)


def expansion_hook_call(hook, **overrides):
    """Call the expansion gate with a granted-shape default, overrides on top."""
    kwargs = dict(
        iso3="CHN",
        geo_unit="CN-GD",
        technology="BF",
        reductant="Coke+PCI",
        capacity=3.0,
        product="iron",
        owner_id="E1",
        year=2027,
    )
    kwargs.update(overrides)
    return hook(**kwargs)


class TestExpansionCapacityHook:
    def test_unbound_accessor_returns_none(self):
        """Unbound — every real run until D8 — the decision path receives None."""
        assert cp_handlers.expansion_capacity_hook() is None

    def test_bound_accessor_returns_a_callable(self, bound: CapacityPool):
        assert callable(cp_handlers.expansion_capacity_hook())

    def test_non_chinese_expansion_passes_through_untouched(self, bound: CapacityPool):
        """Foreign expansions keep their capacity without reaching the evaluator or the
        pool: an unknown technology would raise in the classification lookup."""
        hook = cp_handlers.expansion_capacity_hook()
        assert hook is not None
        granted = expansion_hook_call(hook, iso3="DEU", geo_unit=None, technology="not-a-technology")
        assert granted == 3.0
        assert bound.total() == 0.0

    def test_intense_grant_withdraws_planned_and_returns_penalised_build(self, bound: CapacityPool, caplog):
        """An emission-intense build spends the full planned amount but may only build
        the planned amount divided by the penalty divisor."""
        bound.deposit(Credit(amount_mt=3.0, vintage_year=2020, region_tag=None, owner_id="E9", product="iron"))
        hook = cp_handlers.expansion_capacity_hook()
        assert hook is not None
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            granted = expansion_hook_call(hook)
        assert granted == pytest.approx(2.0)
        assert bound.total() == 0.0
        assert "gate=expansion decision=granted" in caplog.text
        assert "withdraw_mt=3.000" in caplog.text
        assert "build_mt=2.000" in caplog.text

    def test_blocked_short_pool_returns_none_with_reason(self, bound: CapacityPool, caplog):
        bound.deposit(Credit(amount_mt=1.0, vintage_year=2020, region_tag=None, owner_id="E9", product="iron"))
        hook = cp_handlers.expansion_capacity_hook()
        assert hook is not None
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            granted = expansion_hook_call(hook)
        assert granted is None
        assert bound.total() == 1.0
        assert "gate=expansion decision=blocked" in caplog.text
        assert "reason=insufficient_applicable_pool" in caplog.text


def greenfield_hook_call(hook, **overrides):
    """Call the greenfield gate with a granted-shape default, overrides on top."""
    kwargs = dict(
        iso3="CHN",
        geo_unit="CN-GD",
        technology="BF",
        reductant="Coke+PCI",
        capacity=3.0,
        product="iron",
        year=2027,
    )
    kwargs.update(overrides)
    return hook(**kwargs)


class TestGreenfieldCapacityHook:
    def test_unbound_accessor_returns_none(self):
        """Unbound — every real run until D8 — the decision path receives None."""
        assert cp_handlers.greenfield_capacity_hook() is None

    def test_bound_accessor_returns_a_callable(self, bound: CapacityPool):
        assert callable(cp_handlers.greenfield_capacity_hook())

    def test_non_chinese_opportunity_passes_through_untouched(self, bound: CapacityPool):
        """Foreign opportunities keep their capacity, attribute to nobody and mark no
        withdrawal, without reaching the evaluator or the pool."""
        hook = cp_handlers.greenfield_capacity_hook()
        assert hook is not None
        grant = greenfield_hook_call(hook, iso3="DEU", geo_unit=None, technology="not-a-technology")
        assert grant == (3.0, None, False)
        assert bound.total() == 0.0

    def test_single_owner_rule_blocks_an_ample_pool(self, bound: CapacityPool, caplog):
        """Two holders sum past the requirement but neither covers it alone."""
        bound.deposit(Credit(amount_mt=2.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        bound.deposit(Credit(amount_mt=1.5, vintage_year=2020, region_tag=None, owner_id="E_b", product="iron"))
        hook = cp_handlers.greenfield_capacity_hook()
        assert hook is not None
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            grant = greenfield_hook_call(hook)
        assert grant is None
        assert bound.total() == pytest.approx(3.5)
        assert "gate=greenfield decision=blocked" in caplog.text
        assert "reason=no_single_owner_with_sufficient_credits" in caplog.text

    def test_intense_grant_names_the_single_funding_owner(self, bound: CapacityPool, caplog):
        """BF is emission-intense: the grant withdraws the planned 3.0 from one holder
        and allows a 2.0 build attributed to that holder."""
        bound.deposit(Credit(amount_mt=3.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        hook = cp_handlers.greenfield_capacity_hook()
        assert hook is not None
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            grant = greenfield_hook_call(hook)
        assert grant == (pytest.approx(2.0), "E_a", True)
        assert bound.total() == 0.0
        assert "gate=greenfield decision=granted" in caplog.text
        assert "attributed_owner=E_a" in caplog.text
