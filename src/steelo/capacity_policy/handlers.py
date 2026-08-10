"""Deposit handlers wiring furnace-group lifecycle events into the capacity pool.

Registered on ``EVENT_HANDLERS`` unconditionally but inert until
:func:`bind_capacity_policy` installs an evaluator and pool (bootstrapping,
D8): until then — and whenever ``config.capacity_policy.enabled`` is False,
since nothing binds a disabled policy — every handler returns immediately and
behaviour is byte-identical. Once bound, only Chinese events act.

Each deposit logs the furnace group's ``chosen_reductant`` so, paired with the
evaluation-side log in :mod:`.tree`, reductant drift between approval and
operation is measurable from any run.
"""

import logging
from dataclasses import dataclass

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
    """Observe a renovation; deliberately deposits nothing.

    Whether the renovation path shrinks too — a same-technology renovation of
    an emission-intense group reads as a penalised REPLACE — is an open
    question pinned to D7a. Until it is decided the event carries no
    ``old_capacity``, so there is nothing to deposit; this handler exists so
    the wiring and the reductant instrumentation are already in place.
    """
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    with uow:
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        logger.info(
            "[CAPACITY POOL] event=renovated deposit=none fg=%s geo_key=%s tag=%s "
            "product=%s owner=%s technology=%s chosen_reductant=%s",
            event.furnace_group_id,
            geo_key,
            policy.evaluator.key_regions.get(geo_key),
            event.product,
            event.owner_id,
            event.new_technology_name,
            _get_furnace_group(uow, event.furnace_group_id).chosen_reductant,
        )
