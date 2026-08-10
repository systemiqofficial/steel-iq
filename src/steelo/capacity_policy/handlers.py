"""Deposit handlers and decision-path hook for the capacity pool.

The event handlers are registered on ``EVENT_HANDLERS`` unconditionally but
inert until :func:`bind_capacity_policy` installs an evaluator and pool
(bootstrapping, D8): until then — and whenever
``config.capacity_policy.enabled`` is False, since nothing binds a disabled
policy — every handler returns immediately and behaviour is byte-identical.
Once bound, only Chinese events act. The same binding drives the decision-path
gates the plant agent threads on every evaluation: :func:`replace_capacity_hook`,
the pre-NPV ② REPLACE gate on ``Plant.evaluate_furnace_group_strategy``, and
:func:`expansion_capacity_hook`, the ③ INCREASE withdrawal gate on
``PlantGroup.evaluate_expansion``.

Each deposit logs the furnace group's ``chosen_reductant`` so, paired with the
evaluation-side log in :mod:`.tree`, reductant drift between approval and
operation is measurable from any run.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from steelo.domain import events
from steelo.domain.models import Environment, FurnaceGroup, compose_geo_key
from steelo.service_layer.unit_of_work import UnitOfWork

from .pool import CapacityPool
from .tree import TreeEvaluator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _BoundPolicy:
    evaluator: TreeEvaluator
    pool: CapacityPool


_policy: _BoundPolicy | None = None


def bind_capacity_policy(evaluator: TreeEvaluator, pool: CapacityPool) -> None:
    """Activate the deposit handlers for this run."""
    global _policy
    _policy = _BoundPolicy(evaluator=evaluator, pool=pool)


def unbind_capacity_policy() -> None:
    """Deactivate the deposit handlers; they become no-ops again."""
    global _policy
    _policy = None


def replace_capacity_hook() -> Callable[..., float | None] | None:
    """Return the live ② REPLACE pre-NPV callable, or None while unbound.

    The plant agent threads this value into
    ``Plant.evaluate_furnace_group_strategy`` on every evaluation, so binding
    at bootstrap (D8) activates the gate without reopening the domain module
    or the plant agent. Unbound — the default, and always the case while
    ``config.capacity_policy.enabled`` is False — the decision path receives
    None and behaves byte-identically.
    """
    policy = _policy
    if policy is None:
        return None

    def permitted_replace_capacity(
        *,
        iso3: str,
        geo_unit: str | None,
        old_technology: str,
        old_reductant: str | None,
        new_technology: str,
        new_reductant: str | None,
        capacity: float,
        historical_utilization: dict[int, float] | None,
        year: int,
    ) -> float | None:
        """Resolve one candidate transition's permitted capacity — branch ② REPLACE.

        Adapts the decision path's plain values onto the evaluator; all policy
        applicability lives here rather than in the domain module. A
        non-Chinese plant passes through at its own capacity without reaching
        the evaluator, as does a same-technology candidate — the renovation
        option — unless ``reline_counts_as_replace`` makes a reline a REPLACE.
        None means the utilisation gate blocks the replacement decision.
        """
        if iso3 != "CHN":
            return capacity
        if new_technology == old_technology and not policy.evaluator.config.reline_counts_as_replace:
            return capacity
        return policy.evaluator.permitted_capacity(
            old_technology=old_technology,
            old_reductant=old_reductant or None,
            new_technology=new_technology,
            new_reductant=new_reductant or None,
            capacity_mt=capacity,
            geo_key=compose_geo_key(iso3, geo_unit),
            historical_utilization=historical_utilization,
            year=year,
        )

    return permitted_replace_capacity


def expansion_capacity_hook() -> Callable[..., float | None] | None:
    """Return the live ③ INCREASE expansion gate callable, or None while unbound.

    The plant agent threads this value into ``PlantGroup.evaluate_expansion``
    on every evaluation, so binding at bootstrap (D8) activates the gate
    without reopening the domain module or the plant agent. Unbound — the
    default, and always the case while ``config.capacity_policy.enabled`` is
    False — the decision path receives None and behaves byte-identically.
    """
    policy = _policy
    if policy is None:
        return None

    def permitted_expansion_capacity(
        *,
        iso3: str,
        geo_unit: str | None,
        technology: str,
        reductant: str | None,
        capacity: float,
        product: str,
        owner_id: str,
        year: int,
    ) -> float | None:
        """Withdraw retirement credits for one approved expansion — branch ③ INCREASE.

        Called at the point of commitment, after every other expansion check
        has passed; capacities flow in model tonnes end-to-end (the pool's
        ``amount_mt`` naming is a sheet-side convention). A non-Chinese
        expansion passes through at its planned capacity without reaching the
        evaluator or the pool. The owner partition lives in the pool — this
        adapter only passes the withdrawing group and the year. The credit is
        consumed at the decision, all-or-nothing: an expansion that later
        fails to materialise has still spent it (accepted leak; the grant log
        carries every consumed credit so the leak is quantifiable from a run).
        None means the pool blocks the expansion this year.
        """
        if iso3 != "CHN":
            return capacity
        spec = policy.evaluator.on_increase(
            geo_key=compose_geo_key(iso3, geo_unit),
            product=product,
            capacity_mt=capacity,
            technology=technology,
            reductant=reductant or None,
        )
        result = policy.pool.try_withdraw(
            spec.withdraw_mt,
            spec.region_tag,
            product=spec.product,
            owner_id=owner_id,
            year=year,
            single_owner=False,
        )
        if not result.granted:
            logger.info(
                "[CAPACITY POOL] gate=expansion decision=blocked reason=%s owner=%s geo_key=%s "
                "technology=%s reductant=%s product=%s withdraw_mt=%.3f tag=%s year=%d",
                result.blocked_reason,
                owner_id,
                compose_geo_key(iso3, geo_unit),
                technology,
                reductant,
                spec.product,
                spec.withdraw_mt,
                spec.region_tag,
                year,
            )
            return None
        logger.info(
            "[CAPACITY POOL] gate=expansion decision=granted owner=%s geo_key=%s technology=%s "
            "reductant=%s product=%s withdraw_mt=%.3f build_mt=%.3f tag=%s year=%d credits_consumed=%s",
            owner_id,
            compose_geo_key(iso3, geo_unit),
            technology,
            reductant,
            spec.product,
            spec.withdraw_mt,
            spec.build_mt,
            spec.region_tag,
            year,
            [(c.owner_id, c.vintage_year, round(c.amount_mt, 6)) for c in result.credits_consumed],
        )
        return spec.build_mt

    return permitted_expansion_capacity


def _get_furnace_group(uow: UnitOfWork, furnace_group_id: str) -> FurnaceGroup:
    """Fetch a furnace group by id; the events carry no plant id, so scan.

    Closed groups stay on their plant with status ``"closed"``, so the lookup
    holds for every lifecycle event.
    """
    for plant in uow.plants.list():
        for fg in plant.furnace_groups:
            if fg.furnace_group_id == furnace_group_id:
                return fg
    raise ValueError(f"Furnace group {furnace_group_id} not found in any plant")


def deposit_on_furnace_group_closed(event: events.FurnaceGroupClosed, uow: UnitOfWork, env: Environment) -> None:
    """Branch ① RETIRE: bank the full freed capacity, tagged by the cluster rule."""
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    with uow:
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        credit = policy.evaluator.on_close(
            geo_key=geo_key,
            capacity_mt=event.capacity,
            owner_id=event.owner_id,
            product=event.product,
            year=int(env.year),
        )
        policy.pool.deposit(credit)
        logger.info(
            "[CAPACITY POOL] event=closed deposit_mt=%.3f fg=%s geo_key=%s tag=%s "
            "product=%s owner=%s vintage=%d chosen_reductant=%s",
            credit.amount_mt,
            event.furnace_group_id,
            geo_key,
            credit.region_tag,
            credit.product,
            credit.owner_id,
            credit.vintage_year,
            _get_furnace_group(uow, event.furnace_group_id).chosen_reductant,
        )


def deposit_on_furnace_group_tech_changed(
    event: events.FurnaceGroupTechChanged, uow: UnitOfWork, env: Environment
) -> None:
    """Branch ② REPLACE: bank the capacity the replacement shrank away.

    The deposit is ``old_capacity − capacity``, banked only when positive —
    zero means no shrink happened, which is every tech change until the
    pre-NPV hook (D7a) starts shrinking penalised replacements.
    """
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    freed = event.old_capacity - event.capacity
    if freed <= 0:
        return
    with uow:
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        credit = policy.evaluator.on_close(
            geo_key=geo_key,
            capacity_mt=freed,
            owner_id=event.owner_id,
            product=event.product,
            year=int(env.year),
        )
        policy.pool.deposit(credit)
        logger.info(
            "[CAPACITY POOL] event=tech_changed deposit_mt=%.3f fg=%s geo_key=%s tag=%s "
            "product=%s owner=%s vintage=%d old=%s new=%s chosen_reductant=%s",
            credit.amount_mt,
            event.furnace_group_id,
            geo_key,
            credit.region_tag,
            credit.product,
            credit.owner_id,
            credit.vintage_year,
            event.old_technology_name,
            event.technology_name,
            _get_furnace_group(uow, event.furnace_group_id).chosen_reductant,
        )


def deposit_on_furnace_group_renovated(event: events.FurnaceGroupRenovated, uow: UnitOfWork, env: Environment) -> None:
    """Branch ② REPLACE via renovation: bank capacity a reline shrank away.

    A renovation shrinks only when the pre-NPV hook treats a reline as a
    replacement (``reline_counts_as_replace``); under the default flag the
    event always carries ``old_capacity == capacity`` and this handler stays
    silent, exactly like an unshrunk tech change.
    """
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    freed = event.old_capacity - event.capacity
    if freed <= 0:
        return
    with uow:
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        credit = policy.evaluator.on_close(
            geo_key=geo_key,
            capacity_mt=freed,
            owner_id=event.owner_id,
            product=event.product,
            year=int(env.year),
        )
        policy.pool.deposit(credit)
        logger.info(
            "[CAPACITY POOL] event=renovated deposit_mt=%.3f fg=%s geo_key=%s tag=%s "
            "product=%s owner=%s vintage=%d technology=%s chosen_reductant=%s",
            credit.amount_mt,
            event.furnace_group_id,
            geo_key,
            credit.region_tag,
            credit.product,
            credit.owner_id,
            credit.vintage_year,
            event.new_technology_name,
            _get_furnace_group(uow, event.furnace_group_id).chosen_reductant,
        )
