"""Tests for the deposit and motion handlers: inert until bound, China-only, correct rows."""

import inspect
import logging
from dataclasses import dataclass, field

import pytest

from steelo import motions as global_motions
from steelo.capacity_policy import CapacityPolicyConfig, CapacityPolicyRecorder, CapacityPool, Credit, TreeEvaluator
from steelo.capacity_policy import handlers as cp_handlers
from steelo.capacity_policy.inputs import RegionRow, TechnologyRow
from steelo.domain import events
from steelo.service_layer import handlers as service_handlers

REGIONS = [
    RegionRow(geo_key="CHN:CN-HE", region_name="Jing-Jin-Ji", type="key"),
    RegionRow(geo_key="CHN:CN-GD", region_name="Guangdong", type=None),
]

TECHNOLOGIES = [
    TechnologyRow(
        technology="BF",
        product="iron",
        reductant=None,
        is_emission_intense=True,
        switching_to=None,
        swap_ratio=None,
    ),
    TechnologyRow(
        technology="EAF",
        product="steel",
        reductant=None,
        is_emission_intense=False,
        switching_to=None,
        swap_ratio=None,
    ),
]


@dataclass
class FakeTechnology:
    name: str = "BF"
    product: str = "iron"


@dataclass
class FakeFurnaceGroup:
    furnace_group_id: str
    chosen_reductant: str = "Coke+PCI"
    technology: FakeTechnology = field(default_factory=FakeTechnology)
    capacity: float = 2.0
    created_by_PAM: bool = False


@dataclass
class FakeLocation:
    iso3: str = "CHN"
    geo_unit: str | None = "CN-HE"


@dataclass
class FakePlant:
    furnace_groups: list[FakeFurnaceGroup]
    plant_id: str = "plant-1"
    location: FakeLocation = field(default_factory=FakeLocation)


@dataclass
class FakePlantsRepo:
    plants: list[FakePlant]

    def list(self):
        return list(self.plants)

    def get(self, plant_id: str) -> FakePlant:
        return next(plant for plant in self.plants if plant.plant_id == plant_id)


@dataclass
class FakePlantGroup:
    plant_group_id: str


@dataclass
class FakePlantGroupsRepo:
    group_id: str = "E1"

    def get_by_plant_id(self, plant_id: str) -> FakePlantGroup:
        return FakePlantGroup(plant_group_id=self.group_id)


class FakeUoW:
    def __init__(
        self,
        furnace_groups: list[FakeFurnaceGroup],
        *,
        iso3: str = "CHN",
        geo_unit: str | None = "CN-HE",
        group_id: str = "E1",
    ):
        self.plants = FakePlantsRepo(
            [FakePlant(furnace_groups=furnace_groups, location=FakeLocation(iso3=iso3, geo_unit=geo_unit))]
        )
        self.plant_groups = FakePlantGroupsRepo(group_id=group_id)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@dataclass
class FakeEnv:
    year: float = 2031.0


@pytest.fixture(autouse=True)
def unbind_after_test():
    """Module-level bindings must never leak between tests."""
    yield
    cp_handlers.unbind_capacity_policy()
    global_motions.unbind_global_motions()


@pytest.fixture
def pool() -> CapacityPool:
    return CapacityPool()


@pytest.fixture
def recorder() -> CapacityPolicyRecorder:
    return CapacityPolicyRecorder()


@pytest.fixture
def bound(pool: CapacityPool, recorder: CapacityPolicyRecorder) -> CapacityPool:
    evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(), recorder=recorder)
    cp_handlers.bind_capacity_policy(evaluator, pool, recorder)
    return pool


@pytest.fixture
def global_recorder() -> CapacityPolicyRecorder:
    """Bind the all-country motions recorder, as bootstrap does on every run."""
    global_motions.bind_global_motions()
    recorder = global_motions.global_motions_recorder()
    assert recorder is not None
    return recorder


def closed_event(geo_unit: str | None = "CN-HE", iso3: str = "CHN", owner_id: str = "E1") -> events.FurnaceGroupClosed:
    return events.FurnaceGroupClosed(
        furnace_group_id="fg-1",
        capacity=2.0,
        iso3=iso3,
        geo_unit=geo_unit,
        owner_id=owner_id,
        product="iron",
    )


def tech_changed_event(
    old_capacity: float, capacity: float, owner_id: str = "E1", iso3: str = "CHN", geo_unit: str | None = "CN-GD"
) -> events.FurnaceGroupTechChanged:
    return events.FurnaceGroupTechChanged(
        furnace_group_id="fg-1",
        technology_name="BF+CCS",
        capacity=capacity,
        iso3=iso3,
        geo_unit=geo_unit,
        old_technology_name="BF",
        old_capacity=old_capacity,
        owner_id=owner_id,
        product="iron",
    )


def renovated_event(
    old_capacity: float = 2.0,
    capacity: float = 2.0,
    iso3: str = "CHN",
    geo_unit: str | None = "CN-HE",
    owner_id: str = "E1",
) -> events.FurnaceGroupRenovated:
    return events.FurnaceGroupRenovated(
        furnace_group_id="fg-1",
        capacity=capacity,
        old_capacity=old_capacity,
        iso3=iso3,
        geo_unit=geo_unit,
        old_technology_name="BF",
        new_technology_name="BF",
        owner_id=owner_id,
        product="iron",
    )


def added_event(is_new_plant: bool = False) -> events.FurnaceGroupAdded:
    return events.FurnaceGroupAdded(
        plant_id="plant-1",
        furnace_group_id="fg-1",
        technology_name="EAF",
        capacity=4.0,
        is_new_plant=is_new_plant,
    )


def make_uow(iso3: str = "CHN", geo_unit: str | None = "CN-HE", group_id: str = "E1") -> FakeUoW:
    return FakeUoW([FakeFurnaceGroup(furnace_group_id="fg-1")], iso3=iso3, geo_unit=geo_unit, group_id=group_id)


class TestInertness:
    def test_registered_on_the_event_bus(self):
        """Every handler sits on EVENT_HANDLERS under its event."""
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
        assert (
            cp_handlers.record_motion_on_furnace_group_closed
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupClosed]
        )
        assert (
            cp_handlers.record_motion_on_furnace_group_tech_changed
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupTechChanged]
        )
        assert (
            cp_handlers.record_motion_on_furnace_group_renovated
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupRenovated]
        )
        assert (
            cp_handlers.record_motion_on_furnace_group_added
            in service_handlers.EVENT_HANDLERS[events.FurnaceGroupAdded]
        )
        assert cp_handlers.snapshot_pool_state in service_handlers.EVENT_HANDLERS[events.IterationOver]

    def test_motion_records_after_the_greenfield_attribution(self):
        """A credit-funded plant must already sit in its funding company when its
        motion row reads the owner off group membership."""
        added = service_handlers.EVENT_HANDLERS[events.FurnaceGroupAdded]
        assert added.index(cp_handlers.record_motion_on_furnace_group_added) > added.index(
            cp_handlers.attribute_greenfield_on_furnace_group_added
        )

    def test_snapshot_records_before_the_year_increment(self):
        """finalise_iteration increments the year, so the snapshot must precede it."""
        iteration_over = service_handlers.EVENT_HANDLERS[events.IterationOver]
        assert iteration_over.index(cp_handlers.snapshot_pool_state) < iteration_over.index(
            service_handlers.finalise_iteration
        )

    def test_purge_runs_after_the_year_increment(self):
        """A credit usable through Y belongs in Y's snapshot and must be gone before
        any Y+1 decision, so the purge sits between the increment and everything after."""
        iteration_over = service_handlers.EVENT_HANDLERS[events.IterationOver]
        assert iteration_over.index(cp_handlers.purge_expired_credits) > iteration_over.index(
            service_handlers.finalise_iteration
        )

    def test_unbound_handlers_are_no_ops(self):
        """With both the policy and the global motions recorder unbound — never the
        case on a real run, where bootstrap always binds the latter — the handlers
        return before touching uow, env or any pool."""
        cp_handlers.deposit_on_furnace_group_closed(closed_event(), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.deposit_on_furnace_group_tech_changed(tech_changed_event(3.0, 2.0), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.deposit_on_furnace_group_renovated(renovated_event(), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.record_motion_on_furnace_group_closed(closed_event(), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.record_motion_on_furnace_group_tech_changed(tech_changed_event(3.0, 2.0), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.record_motion_on_furnace_group_renovated(renovated_event(), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.record_motion_on_furnace_group_added(added_event(), uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.record_motion_on_pipeline_group_operating(None, None, uow=None, env=None)  # type: ignore[arg-type]
        cp_handlers.snapshot_pool_state(events.IterationOver(time_step_increment=1, iron_price=1.0), env=None)  # type: ignore[arg-type]
        cp_handlers.purge_expired_credits(events.IterationOver(time_step_increment=1, iron_price=1.0), env=None)  # type: ignore[arg-type]

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
        """A renovation shrunk by the pre-NPV hook banks exactly old_capacity − capacity."""
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
        """An unshrunk renovation — 1:1 by derivation, or ratio-exempt — banks nothing."""
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


class TestEndOfLifeDeposit:
    """Retirements nobody decided: the bare status flip in ``finalise_iteration``."""

    def test_wired_into_the_end_of_life_branch(self):
        """The deposit must sit on the status flip itself, not on a later step that a
        scheduled switch or a foreign plant would also reach."""
        source = inspect.getsource(service_handlers.finalise_iteration)
        assert 'fg.status = "closed"\n' in source
        flip, _, rest = source.partition('fg.status = "closed"\n')
        assert rest.lstrip().startswith("capacity_policy_handlers.deposit_on_end_of_life_closure(plant, fg, uow, env)")
        assert flip.count("deposit_on_end_of_life_closure") == 0

    def test_deposits_the_full_capacity_at_the_post_increment_vintage(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        """finalise_iteration advances the year before closing, so the credit belongs
        to the next snapshot — the same convention the scheduled switches follow."""
        uow = make_uow()
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (credit,) = bound.snapshot()
        assert credit.amount_mt == pytest.approx(2.0)
        assert credit.vintage_year == 2031
        assert credit.region_tag == "Jing-Jin-Ji"
        assert credit.owner_id == "E1"
        assert credit.product == "iron"
        (row,) = recorder._ledger
        assert row["operation"] == "deposit_close_end_of_life"
        assert row["furnace_group_id"] == "fg-1"
        assert row["geo_key"] == "CHN:CN-HE"

    def test_records_the_closure_as_a_motion(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        """End-of-life closures move the fleet too; the mix is only readable from the
        complete set of motions."""
        uow = make_uow()
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (row,) = recorder._motions
        assert row["kind"] == "close"
        assert row["source"] == "input_data"
        assert row["old_technology"] == "BF"
        assert row["old_capacity_t"] == pytest.approx(2.0)
        assert row["owner_id"] == "E1"
        assert row["reductant"] == "Coke+PCI"

    def test_a_model_built_group_ages_out_as_a_pam_motion(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        """The lifetime that ran out was set by a model action, so the age-out is
        the model's own; only data-born groups retire on the dataset's clock."""
        uow = make_uow()
        plant = uow.plants.list()[0]
        plant.furnace_groups[0].created_by_PAM = True

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (row,) = recorder._motions
        assert row["source"] == "pam"

    def test_a_renovated_group_ages_out_as_a_pam_motion(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        """A renovation resets the lifetime clock without stamping created_by_PAM,
        so the age-out that follows runs out a model-set schedule, not the data's."""
        cp_handlers.record_motion_on_furnace_group_renovated(
            renovated_event(),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        uow = make_uow()
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        close_row = recorder._motions[-1]
        assert close_row["kind"] == "close"
        assert close_row["source"] == "pam"

    def test_unbound_closure_deposits_nothing(self, pool: CapacityPool):
        uow = make_uow()
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert pool.total() == 0.0

    def test_off_china_closure_deposits_nothing(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        uow = make_uow(iso3="DEU")
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert bound.total() == 0.0
        assert recorder._ledger == []
        assert recorder._motions == []

    def test_log_carries_the_chosen_reductant(self, bound: CapacityPool, caplog):
        uow = make_uow()
        plant = uow.plants.list()[0]

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert "event=end_of_life_closed" in caplog.text
        assert "chosen_reductant=Coke+PCI" in caplog.text


class TestMembershipOwner:
    """D-F: every credit and every motion names the plant's group by membership.

    The events stamp ``ultimate_plant_group``, which keeps reporting
    ``indi_<iso3>`` for a credit-funded plant this package moved into its funding
    company — so each case below hands the handler an event whose owner disagrees
    with the membership the repository holds.
    """

    @pytest.mark.parametrize(
        "call",
        [
            lambda: cp_handlers.deposit_on_furnace_group_closed(
                closed_event(owner_id="indi_CHN"),
                uow=make_uow(group_id="E_a"),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
            lambda: cp_handlers.deposit_on_furnace_group_tech_changed(
                tech_changed_event(3.0, 2.0, owner_id="indi_CHN"),
                uow=make_uow(group_id="E_a"),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
            lambda: cp_handlers.deposit_on_furnace_group_renovated(
                renovated_event(old_capacity=3.0, capacity=2.0, owner_id="indi_CHN"),
                uow=make_uow(group_id="E_a"),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
        ],
    )
    def test_deposits_credit_the_membership_group(self, bound: CapacityPool, call):
        call()

        (credit,) = bound.snapshot()
        assert credit.owner_id == "E_a"

    @pytest.mark.parametrize(
        "call",
        [
            lambda: cp_handlers.record_motion_on_furnace_group_closed(
                closed_event(owner_id="indi_CHN"),
                uow=make_uow(group_id="E_a"),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
            lambda: cp_handlers.record_motion_on_furnace_group_tech_changed(
                tech_changed_event(3.0, 2.0, owner_id="indi_CHN"),
                uow=make_uow(group_id="E_a"),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
            lambda: cp_handlers.record_motion_on_furnace_group_renovated(
                renovated_event(owner_id="indi_CHN"),
                uow=make_uow(group_id="E_a"),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
        ],
    )
    def test_motions_name_the_membership_group(self, bound: CapacityPool, recorder: CapacityPolicyRecorder, call):
        call()

        (row,) = recorder._motions
        assert row["owner_id"] == "E_a"

    def test_end_of_life_closure_credits_the_membership_group(self, bound: CapacityPool):
        """The retirement of a credit-funded plant banks to its funding company."""
        uow = make_uow(group_id="E_a")
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (credit,) = bound.snapshot()
        assert credit.owner_id == "E_a"


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
            product="steel",
            historical_utilization=None,
            year=2025,
        )
        assert permitted == 3.0

    def test_same_technology_is_gated_under_the_default_flag(self, bound: CapacityPool):
        """The utilisation gate blocks a renovation on either branch; under the shipped
        default it reaches the candidate as a full REPLACE (Decisions 34 and 36)."""
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
            product="iron",
            historical_utilization={2024: 0.1, 2025: 0.1},
            year=2025,
        )
        assert permitted is None

    def test_an_intense_renovation_shrinks_under_the_default_flag(self, bound: CapacityPool):
        """Decision 36: a BF→BF renovation of an intense group is a full REPLACE and
        derives 1.5:1 from the flags."""
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
            product="iron",
            historical_utilization=None,
            year=2025,
        )
        assert permitted == pytest.approx(2.0)

    @pytest.mark.parametrize("history", [None, {2024: 0.9, 2025: 0.9}, {2025: 0.1}])
    def test_a_non_intense_renovation_passes_one_to_one_under_the_default(self, bound: CapacityPool, history):
        """Even as a full REPLACE a non-intense renovation derives 1:1, so nothing shrinks."""
        hook = cp_handlers.replace_capacity_hook()
        assert hook is not None
        permitted = hook(
            iso3="CHN",
            geo_unit="CN-GD",
            old_technology="EAF",
            old_reductant="Electricity",
            new_technology="EAF",
            new_reductant="Electricity",
            capacity=3.0,
            product="steel",
            historical_utilization=history,
            year=2025,
        )
        assert permitted == 3.0

    @pytest.mark.parametrize("history", [None, {2024: 0.9, 2025: 0.9}, {2025: 0.1}])
    def test_a_ratio_exempt_renovation_keeps_its_capacity(self, pool: CapacityPool, history):
        """With the flag off a renovation faces no ratio: an unblocked BF→BF stays whole."""
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(renovation_counts_as_replace=False))
        cp_handlers.bind_capacity_policy(evaluator, pool, CapacityPolicyRecorder())
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
            product="iron",
            historical_utilization=history,
            year=2025,
        )
        assert permitted == 3.0

    def test_a_ratio_exempt_renovation_is_still_gated(self, pool: CapacityPool):
        """Decision 34: the flag is the ratio question alone — the gate applies regardless."""
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(renovation_counts_as_replace=False))
        cp_handlers.bind_capacity_policy(evaluator, pool, CapacityPolicyRecorder())
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
            product="iron",
            historical_utilization={2024: 0.1, 2025: 0.1},
            year=2025,
        )
        assert permitted is None


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


class TestIncreaseSizingHook:
    """The non-consuming ③ sizing query: pure arithmetic, no pool, no records."""

    def test_unbound_accessor_returns_none(self):
        assert cp_handlers.increase_sizing_hook() is None

    def test_bound_accessor_returns_a_callable(self, bound: CapacityPool):
        assert callable(cp_handlers.increase_sizing_hook())

    def test_non_chinese_build_never_reaches_the_evaluator(self, pool: CapacityPool, mocker):
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig())
        cp_handlers.bind_capacity_policy(evaluator, pool, CapacityPolicyRecorder())
        spy = mocker.spy(evaluator, "increase_build_capacity")
        query = cp_handlers.increase_sizing_hook()
        assert query is not None

        assert query(iso3="DEU", technology="not-a-technology", reductant=None, capacity=3.0) == 3.0
        assert spy.call_count == 0

    def test_emission_intense_route_is_sized_down(self, bound: CapacityPool):
        query = cp_handlers.increase_sizing_hook()
        assert query is not None
        assert query(iso3="CHN", technology="BF", reductant="Coke+PCI", capacity=3.0) == pytest.approx(2.0)

    def test_clean_route_keeps_its_planned_capacity(self, bound: CapacityPool):
        query = cp_handlers.increase_sizing_hook()
        assert query is not None
        assert query(iso3="CHN", technology="EAF", reductant="Electricity", capacity=3.0) == 3.0

    def test_an_empty_pool_sizes_identically(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        """Availability is not sizing: what a build may build does not depend on
        whether credits exist to fund it, and asking costs the pool nothing."""
        query = cp_handlers.increase_sizing_hook()
        assert query is not None
        on_empty = query(iso3="CHN", technology="BF", reductant="Coke+PCI", capacity=3.0)

        bound.deposit(Credit(amount_mt=9.0, vintage_year=2020, region_tag=None, owner_id="E9", product="iron"))
        assert query(iso3="CHN", technology="BF", reductant="Coke+PCI", capacity=3.0) == on_empty
        assert bound.total() == 9.0
        assert recorder._ledger == []
        assert recorder._gate_decisions == []


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
        assert grant == (3.0, None, False, ())
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
        consumed = Credit(amount_mt=3.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron")
        bound.deposit(consumed)
        hook = cp_handlers.greenfield_capacity_hook()
        assert hook is not None
        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            grant = greenfield_hook_call(hook)
        assert grant == (pytest.approx(2.0), "E_a", True, (consumed,))
        assert bound.total() == 0.0
        assert "gate=greenfield decision=granted" in caplog.text
        assert "attributed_owner=E_a" in caplog.text

    def test_unbound_retry_cap_accessor_returns_none(self):
        assert cp_handlers.greenfield_retry_cap() is None

    def test_bound_retry_cap_accessor_returns_the_configured_years(self, bound: CapacityPool):
        assert cp_handlers.greenfield_retry_cap() == CapacityPolicyConfig().capacity_pool_max_retry_years


class TestGreenfieldFeasibilityHook:
    """The non-consuming pre-draw probe: can_withdraw's answer plus the blocked row."""

    def test_unbound_accessor_returns_none(self):
        assert cp_handlers.greenfield_feasibility_hook() is None

    def test_non_chinese_opportunity_is_always_fundable(self, bound: CapacityPool):
        probe = cp_handlers.greenfield_feasibility_hook()
        assert probe is not None
        assert greenfield_hook_call(probe, iso3="DEU", technology="not-a-technology") is None

    def test_fundable_probe_returns_none_and_records_nothing(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        """A fundable answer consumes nothing and leaves observability to the gate."""
        bound.deposit(Credit(amount_mt=3.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        probe = cp_handlers.greenfield_feasibility_hook()
        assert probe is not None

        assert greenfield_hook_call(probe) is None
        assert bound.total() == pytest.approx(3.0)
        assert recorder._ledger == []

    def test_blocked_probe_names_the_reason_and_records_the_blocked_row(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder, caplog
    ):
        """A blocked year is visible in the artefacts even though no draw ever ran."""
        bound.deposit(Credit(amount_mt=2.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        bound.deposit(Credit(amount_mt=1.5, vintage_year=2020, region_tag=None, owner_id="E_b", product="iron"))
        probe = cp_handlers.greenfield_feasibility_hook()
        assert probe is not None

        with caplog.at_level(logging.INFO, logger="steelo.capacity_policy.handlers"):
            reason = greenfield_hook_call(probe)

        assert reason == "no_single_owner_with_sufficient_credits"
        assert bound.total() == pytest.approx(3.5)
        (row,) = recorder._ledger
        assert row["operation"] == "blocked_greenfield"
        assert row["blocked_reason"] == "no_single_owner_with_sufficient_credits"
        assert row["amount_t"] == pytest.approx(3.0)
        assert "stage=pre_draw" in caplog.text

    def test_probe_and_gate_agree_on_the_same_pool_state(self, bound: CapacityPool):
        """A fundable probe is a granted withdrawal within the same state."""
        bound.deposit(Credit(amount_mt=3.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        probe = cp_handlers.greenfield_feasibility_hook()
        gate = cp_handlers.greenfield_capacity_hook()
        assert probe is not None and gate is not None

        assert greenfield_hook_call(probe) is None
        grant = greenfield_hook_call(gate)
        assert grant is not None and grant[2] is True


class TestBareCountryGeoKeyWarning:
    """A Chinese location without a geo_unit must never fall through silently."""

    def test_first_occurrence_warns_then_debug(self, bound: CapacityPool, caplog):
        gate = cp_handlers.greenfield_capacity_hook()
        assert gate is not None

        with caplog.at_level(logging.WARNING, logger="steelo.capacity_policy.handlers"):
            greenfield_hook_call(gate, geo_unit=None)
            greenfield_hook_call(gate, geo_unit=None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "iso3=CHN has no geo_unit" in warnings[0].getMessage()

    def test_rebinding_resets_the_warning_dedup(self, bound: CapacityPool, caplog, pool, recorder):
        gate = cp_handlers.greenfield_capacity_hook()
        assert gate is not None
        with caplog.at_level(logging.WARNING, logger="steelo.capacity_policy.handlers"):
            greenfield_hook_call(gate, geo_unit=None)
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(), recorder=recorder)
        cp_handlers.bind_capacity_policy(evaluator, pool, recorder)
        gate = cp_handlers.greenfield_capacity_hook()
        assert gate is not None

        with caplog.at_level(logging.WARNING, logger="steelo.capacity_policy.handlers"):
            greenfield_hook_call(gate, geo_unit=None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2


class TestGateLedgerRows:
    """Each gate outcome writes exactly the ledger row its log line states."""

    def test_granted_expansion_records_the_consumed_credits(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        bound.deposit(Credit(amount_mt=2.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        bound.deposit(Credit(amount_mt=2.0, vintage_year=2020, region_tag=None, owner_id="E_b", product="iron"))
        hook = cp_handlers.expansion_capacity_hook()
        assert hook is not None

        expansion_hook_call(hook)

        (row,) = recorder._ledger
        assert row["operation"] == "withdraw_expansion"
        assert row["amount_t"] == pytest.approx(3.0)
        assert row["owner_id"] == "E1"
        assert row["geo_key"] == "CHN:CN-GD"
        assert row["credits_consumed"] == '[["E_a",2019,null,2.0],["E_b",2020,null,1.0]]'

    def test_blocked_expansion_records_the_refusal(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        hook = cp_handlers.expansion_capacity_hook()
        assert hook is not None

        expansion_hook_call(hook)

        (row,) = recorder._ledger
        assert row["operation"] == "blocked_expansion"
        assert row["amount_t"] == pytest.approx(3.0)
        assert row["blocked_reason"] == "insufficient_applicable_pool"
        assert row["credits_consumed"] is None

    def test_granted_greenfield_records_the_attributed_holder(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        bound.deposit(Credit(amount_mt=3.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        hook = cp_handlers.greenfield_capacity_hook()
        assert hook is not None

        greenfield_hook_call(hook)

        (row,) = recorder._ledger
        assert row["operation"] == "withdraw_greenfield"
        assert row["owner_id"] == "indi_CHN"
        assert row["attributed_owner_id"] == "E_a"

    def test_blocked_greenfield_records_the_single_owner_reason(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        bound.deposit(Credit(amount_mt=2.0, vintage_year=2019, region_tag=None, owner_id="E_a", product="iron"))
        bound.deposit(Credit(amount_mt=1.5, vintage_year=2020, region_tag=None, owner_id="E_b", product="iron"))
        hook = cp_handlers.greenfield_capacity_hook()
        assert hook is not None

        greenfield_hook_call(hook)

        (row,) = recorder._ledger
        assert row["operation"] == "blocked_greenfield"
        assert row["blocked_reason"] == "no_single_owner_with_sufficient_credits"

    def test_non_chinese_gates_record_nothing(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        expansion = cp_handlers.expansion_capacity_hook()
        greenfield = cp_handlers.greenfield_capacity_hook()
        assert expansion is not None and greenfield is not None

        expansion_hook_call(expansion, iso3="DEU", geo_unit=None, technology="not-a-technology")
        greenfield_hook_call(greenfield, iso3="DEU", geo_unit=None, technology="not-a-technology")

        assert recorder._ledger == []


class TestDepositLedgerRows:
    def test_closure_deposit_records_a_close_row(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        cp_handlers.deposit_on_furnace_group_closed(closed_event(), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        (row,) = recorder._ledger
        assert row["operation"] == "deposit_close"
        assert row["amount_t"] == pytest.approx(2.0)
        assert row["region_tag"] == "Jing-Jin-Ji"
        assert row["vintage_year"] == 2031
        assert row["furnace_group_id"] == "fg-1"

    @pytest.mark.parametrize(
        "call",
        [
            lambda: cp_handlers.deposit_on_furnace_group_tech_changed(
                tech_changed_event(3.0, 2.0),
                uow=make_uow(),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
            lambda: cp_handlers.deposit_on_furnace_group_renovated(
                renovated_event(old_capacity=3.0, capacity=2.0),
                uow=make_uow(),  # type: ignore[arg-type]
                env=FakeEnv(),  # type: ignore[arg-type]
            ),
        ],
    )
    def test_both_shrink_paths_record_a_replace_row(self, bound: CapacityPool, recorder, call):
        """A shrunk switch and a shrunk renovation are the same ① RETIRE-side fact."""
        call()
        (row,) = recorder._ledger
        assert row["operation"] == "deposit_replace"
        assert row["amount_t"] == pytest.approx(1.0)

    def test_unshrunk_switch_records_no_ledger_row(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        cp_handlers.deposit_on_furnace_group_tech_changed(tech_changed_event(2.0, 2.0), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        assert recorder._ledger == []


class TestMotions:
    def test_closure_records_the_closing_technology_and_capacity(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        cp_handlers.record_motion_on_furnace_group_closed(closed_event(), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]
        (row,) = recorder._motions
        assert row["kind"] == "close"
        assert row["source"] == "pam"
        assert row["plant_id"] == "plant-1"
        assert row["old_technology"] == "BF"
        assert row["old_capacity_t"] == pytest.approx(2.0)
        assert row["new_technology"] is None
        assert row["geo_key"] == "CHN:CN-HE"
        assert row["reductant"] == "Coke+PCI"

    def test_switch_records_both_sides(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_tech_changed(
            tech_changed_event(3.0, 2.0),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = recorder._motions
        assert row["kind"] == "switch"
        assert (row["old_technology"], row["new_technology"]) == ("BF", "BF+CCS")
        assert (row["old_capacity_t"], row["new_capacity_t"]) == (pytest.approx(3.0), pytest.approx(2.0))

    def test_unshrunk_switch_is_still_a_motion(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        """The deposit handler returns early on a zero delta; the fleet still moved."""
        cp_handlers.record_motion_on_furnace_group_tech_changed(
            tech_changed_event(2.0, 2.0),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        assert len(recorder._motions) == 1

    def test_renovation_records_a_renovate_row(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_renovated(
            renovated_event(),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = recorder._motions
        assert row["kind"] == "renovate"

    @pytest.mark.parametrize("is_new_plant, kind", [(False, "expansion"), (True, "greenfield")])
    def test_added_splits_expansion_from_greenfield(self, bound: CapacityPool, recorder, is_new_plant, kind):
        cp_handlers.record_motion_on_furnace_group_added(
            added_event(is_new_plant=is_new_plant),
            uow=make_uow(),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = recorder._motions
        assert row["kind"] == kind
        assert row["new_technology"] == "EAF"
        assert row["new_capacity_t"] == pytest.approx(4.0)
        assert row["old_technology"] is None
        assert row["owner_id"] == "E1"
        assert row["product"] == "iron"

    def test_non_chinese_events_record_no_motion(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_closed(
            closed_event(iso3="DEU", geo_unit=None),
            uow=make_uow(iso3="DEU"),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        cp_handlers.record_motion_on_furnace_group_added(
            added_event(),
            uow=make_uow(iso3="DEU"),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        assert recorder._motions == []


class TestPipelineMotions:
    """Input-data pipeline groups entering the operating fleet via the year-start flip."""

    def test_wired_into_the_operating_flip(self):
        """The recorder must sit on the status flip itself, not on the scheduled-switch
        branch, which the flip's own guard already excludes."""
        from steelo.simulation import SimulationRunner

        source = inspect.getsource(SimulationRunner.run)
        assert 'fg.status = "operating"\n' in source
        flip, _, rest = source.partition('fg.status = "operating"\n')
        statements = [line.strip() for line in rest.splitlines() if line.strip() and not line.strip().startswith("#")]
        assert statements[0].startswith("record_motion_on_pipeline_group_operating(plant, fg, bus.uow, bus.env)")
        assert flip.count("record_motion_on_pipeline_group_operating") == 0

    def test_a_data_born_group_records_a_pipeline_motion(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        uow = make_uow()
        plant = uow.plants.list()[0]

        cp_handlers.record_motion_on_pipeline_group_operating(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (row,) = recorder._motions
        assert row["kind"] == "pipeline"
        assert row["source"] == "input_data"
        assert row["year"] == 2031
        assert row["new_technology"] == "BF"
        assert row["new_capacity_t"] == pytest.approx(2.0)
        assert row["old_technology"] is None
        assert row["owner_id"] == "E1"
        assert row["product"] == "iron"
        assert row["geo_key"] == "CHN:CN-HE"
        assert row["reductant"] == "Coke+PCI"

    def test_a_model_built_group_passes_the_flip_unrecorded(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        """Expansions and greenfields complete their construction through the same
        flip; both were recorded as motions at their decision, where created_by_PAM
        is stamped, so a second row would double-count. Switches never reach this
        branch: the old technology operates through the construction window, and
        the scheduled execution sets operating itself."""
        uow = make_uow()
        plant = uow.plants.list()[0]
        plant.furnace_groups[0].created_by_PAM = True

        cp_handlers.record_motion_on_pipeline_group_operating(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert recorder._motions == []

    def test_non_chinese_plants_record_nothing(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        uow = make_uow(iso3="DEU")
        plant = uow.plants.list()[0]

        cp_handlers.record_motion_on_pipeline_group_operating(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert recorder._motions == []


class TestGlobalMotions:
    """All-country motion rows through the always-bound recorder of steelo.motions.

    The policy stays unbound in most cases here: the global record must not
    depend on ``--enable-capacity-policy``.
    """

    def test_non_chinese_close_records_a_global_row(self, global_recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_closed(
            closed_event(iso3="DEU", geo_unit=None),
            uow=make_uow(iso3="DEU", geo_unit=None),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = global_recorder.motions
        assert row["kind"] == "close"
        assert row["source"] == "pam"
        assert row["geo_key"] == "DEU"
        assert row["owner_id"] == "E1"

    def test_non_chinese_switch_records_a_global_row(self, global_recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_tech_changed(
            tech_changed_event(3.0, 2.0, iso3="DEU", geo_unit=None),
            uow=make_uow(iso3="DEU", geo_unit=None),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = global_recorder.motions
        assert row["kind"] == "switch"
        assert row["geo_key"] == "DEU"
        assert (row["old_technology"], row["new_technology"]) == ("BF", "BF+CCS")

    def test_non_chinese_renovation_records_a_global_row(self, global_recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_renovated(
            renovated_event(iso3="DEU", geo_unit=None),
            uow=make_uow(iso3="DEU", geo_unit=None),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = global_recorder.motions
        assert row["kind"] == "renovate"
        assert row["geo_key"] == "DEU"

    def test_non_chinese_build_records_a_global_row(self, global_recorder: CapacityPolicyRecorder):
        cp_handlers.record_motion_on_furnace_group_added(
            added_event(),
            uow=make_uow(iso3="DEU", geo_unit=None),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        (row,) = global_recorder.motions
        assert row["kind"] == "expansion"
        assert row["geo_key"] == "DEU"

    def test_non_chinese_pipeline_group_records_a_global_row(self, global_recorder: CapacityPolicyRecorder):
        uow = make_uow(iso3="DEU", geo_unit=None)
        plant = uow.plants.list()[0]

        cp_handlers.record_motion_on_pipeline_group_operating(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (row,) = global_recorder.motions
        assert row["kind"] == "pipeline"
        assert row["source"] == "input_data"
        assert row["geo_key"] == "DEU"

    def test_a_model_built_pipeline_group_is_still_skipped(self, global_recorder: CapacityPolicyRecorder):
        uow = make_uow(iso3="DEU", geo_unit=None)
        plant = uow.plants.list()[0]
        plant.furnace_groups[0].created_by_PAM = True

        cp_handlers.record_motion_on_pipeline_group_operating(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert list(global_recorder.motions) == []

    def test_non_chinese_end_of_life_records_a_motion_and_no_pool_state(self, global_recorder: CapacityPolicyRecorder):
        uow = make_uow(iso3="DEU", geo_unit=None)
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        (row,) = global_recorder.motions
        assert row["kind"] == "close"
        assert row["source"] == "input_data"
        assert row["geo_key"] == "DEU"
        assert global_recorder._ledger == []

    def test_end_of_life_source_rule_reads_the_global_renovation_history(self, global_recorder: CapacityPolicyRecorder):
        """A renovated group ages out as a pam motion even off-China, where the
        policy recorder never saw the renovation."""
        cp_handlers.record_motion_on_furnace_group_renovated(
            renovated_event(iso3="DEU", geo_unit=None),
            uow=make_uow(iso3="DEU", geo_unit=None),  # type: ignore[arg-type]
            env=FakeEnv(),  # type: ignore[arg-type]
        )
        uow = make_uow(iso3="DEU", geo_unit=None)
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        close_row = global_recorder.motions[-1]
        assert close_row["kind"] == "close"
        assert close_row["source"] == "pam"

    def test_chinese_event_records_identical_rows_in_both_recorders(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder, global_recorder: CapacityPolicyRecorder
    ):
        cp_handlers.record_motion_on_furnace_group_closed(closed_event(), uow=make_uow(), env=FakeEnv())  # type: ignore[arg-type]

        assert len(recorder.motions) == 1
        assert list(global_recorder.motions) == list(recorder.motions)

    def test_chinese_end_of_life_deposits_once_and_dual_writes_the_motion(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder, global_recorder: CapacityPolicyRecorder
    ):
        """The pool and ledger stay policy-scoped; only the motion goes global."""
        uow = make_uow()
        plant = uow.plants.list()[0]

        cp_handlers.deposit_on_end_of_life_closure(plant, plant.furnace_groups[0], uow=uow, env=FakeEnv())  # type: ignore[arg-type]

        assert bound.total() == pytest.approx(2.0)
        assert len(recorder._ledger) == 1
        assert global_recorder._ledger == []
        assert list(global_recorder.motions) == list(recorder.motions)


class TestSnapshotHandler:
    def test_bound_snapshot_labels_the_year_that_is_ending(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        bound.deposit(Credit(amount_mt=5.0, vintage_year=2030, region_tag=None, owner_id="E1", product="iron"))

        cp_handlers.snapshot_pool_state(
            events.IterationOver(time_step_increment=1, iron_price=1.0),
            env=FakeEnv(),  # type: ignore[arg-type]
        )

        assert recorder._state[2031][0].amount_mt == pytest.approx(5.0)


class TestPurgeHandler:
    @pytest.fixture
    def expiring(self, recorder: CapacityPolicyRecorder) -> CapacityPool:
        """A pool with a five-year shelf life, bound for the handler to find."""
        pool = CapacityPool(credit_validity_years=5)
        evaluator = TreeEvaluator(REGIONS, TECHNOLOGIES, CapacityPolicyConfig(), recorder=recorder)
        cp_handlers.bind_capacity_policy(evaluator, pool, recorder)
        return pool

    def test_purges_and_records_at_the_year_just_entered(
        self, expiring: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        """finalise_iteration has already advanced env.year, so 2031 is the year the
        purge is entering — the 2026 vintage is exactly one year past its validity."""
        expiring.deposit(
            Credit(amount_mt=4.0, vintage_year=2026, region_tag="Jing-Jin-Ji", owner_id="E1", product="iron")
        )
        expiring.deposit(Credit(amount_mt=1.0, vintage_year=2030, region_tag=None, owner_id="E1", product="iron"))

        cp_handlers.purge_expired_credits(
            events.IterationOver(time_step_increment=1, iron_price=1.0),
            env=FakeEnv(),  # type: ignore[arg-type]
        )

        assert [c.vintage_year for c in expiring.snapshot()] == [2030]
        (row,) = recorder._ledger
        assert row["operation"] == "expired"
        assert row["year"] == 2031
        assert row["vintage_year"] == 2026
        assert row["amount_t"] == pytest.approx(4.0)
        assert row["region_tag"] == "Jing-Jin-Ji"
        assert row["owner_id"] == "E1"

    def test_repurging_the_same_year_removes_nothing(self, expiring: CapacityPool, recorder: CapacityPolicyRecorder):
        """The final boundary increments by zero and fires the handler again."""
        expiring.deposit(Credit(amount_mt=4.0, vintage_year=2026, region_tag=None, owner_id="E1", product="iron"))
        event = events.IterationOver(time_step_increment=1, iron_price=1.0)

        cp_handlers.purge_expired_credits(event, env=FakeEnv())  # type: ignore[arg-type]
        cp_handlers.purge_expired_credits(event, env=FakeEnv())  # type: ignore[arg-type]

        assert len(recorder._ledger) == 1

    def test_no_shelf_life_purges_nothing(self, bound: CapacityPool, recorder: CapacityPolicyRecorder):
        """The shipped default: the handler runs and the pool is untouched."""
        bound.deposit(Credit(amount_mt=4.0, vintage_year=1990, region_tag=None, owner_id="E1", product="iron"))

        cp_handlers.purge_expired_credits(
            events.IterationOver(time_step_increment=1, iron_price=1.0),
            env=FakeEnv(),  # type: ignore[arg-type]
        )

        assert bound.total() == pytest.approx(4.0)
        assert recorder._ledger == []

    def test_boundary_past_the_cutoff_sweeps_unowned_credits(
        self, bound: CapacityPool, recorder: CapacityPolicyRecorder
    ):
        """Entering a year at or past the swap cutoff drops the unowned opening
        credits and writes them to the ledger as ``expired_unowned`` rows."""
        bound.deposit(Credit(amount_mt=3.0, vintage_year=2020, region_tag=None, owner_id=None, product="iron"))
        bound.deposit(Credit(amount_mt=1.0, vintage_year=2026, region_tag=None, owner_id="E1", product="iron"))

        cp_handlers.purge_expired_credits(
            events.IterationOver(time_step_increment=1, iron_price=1.0),
            env=FakeEnv(),  # type: ignore[arg-type]
        )

        assert bound.total() == pytest.approx(1.0)
        (row,) = recorder._ledger
        assert row["operation"] == "expired_unowned"
        assert row["year"] == 2031
        assert row["vintage_year"] == 2020
        assert row["amount_t"] == pytest.approx(3.0)
        assert row["owner_id"] is None
