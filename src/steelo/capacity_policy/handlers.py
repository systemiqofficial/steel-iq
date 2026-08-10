"""Deposit handlers and decision-path hook for the capacity pool.

The event handlers are registered on ``EVENT_HANDLERS`` unconditionally but
inert until :func:`bind_capacity_policy` installs an evaluator and pool
(bootstrapping, D8): until then — and whenever
``config.capacity_policy.enabled`` is False, since nothing binds a disabled
policy — every handler returns immediately and behaviour is byte-identical.
Once bound, only Chinese events act. The same binding drives the decision-path
gates the plant agent threads on every evaluation: :func:`replace_capacity_hook`,
the pre-NPV ② REPLACE gate on ``Plant.evaluate_furnace_group_strategy``;
:func:`expansion_capacity_hook`, the ③ INCREASE withdrawal gate on
``PlantGroup.evaluate_expansion``; and :func:`greenfield_capacity_hook`, the
③ INCREASE withdrawal gate at considered→announced in
``FurnaceGroup.track_business_opportunities``, whose single-owner grant
:func:`attribute_greenfield_on_furnace_group_added` later turns into the plant
joining the funding company at construction start.

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


def greenfield_capacity_hook() -> Callable[..., tuple[float, str | None, bool] | None] | None:
    """Return the live ③ INCREASE greenfield gate callable, or None while unbound.

    The plant agent threads this value through
    ``PlantGroup.update_status_of_business_opportunities`` into
    ``FurnaceGroup.track_business_opportunities``, so binding at bootstrap (D8)
    activates the gate without reopening the domain module or the plant agent.
    Unbound — the default, and always the case while
    ``config.capacity_policy.enabled`` is False — the decision path receives
    None and behaves byte-identically.
    """
    policy = _policy
    if policy is None:
        return None

    def permitted_greenfield_capacity(
        *,
        iso3: str,
        geo_unit: str | None,
        technology: str,
        reductant: str | None,
        capacity: float,
        product: str,
        year: int,
    ) -> tuple[float, str | None, bool] | None:
        """Withdraw retirement credits for one announced greenfield — branch ③ INCREASE.

        Called when the announcement draw succeeds, so consumption coincides
        with the considered→announced commitment; capacities flow in model
        tonnes end-to-end. The whole withdrawal is served from a single credit
        holder — the spec's uniform-across-the-run greenfield rule — so a build
        can be refused with an ample pool when no one holder covers it, which
        the block log states distinctly. A non-Chinese opportunity passes
        through untouched without reaching the evaluator or the pool. The
        credit is consumed at announcement, all-or-nothing: a later discarded
        project has still spent it (accepted leak, logged at discard).

        Returns:
            ``(build_capacity, attributed_owner_id, withdrew)`` on a grant —
            the unowned pot attributes to None — or None when blocked, which
            leaves the opportunity considered to retry next year.
        """
        if iso3 != "CHN":
            return (capacity, None, False)
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
            owner_id=f"indi_{iso3}",
            year=year,
            single_owner=True,
        )
        if not result.granted:
            logger.info(
                "[CAPACITY POOL] gate=greenfield decision=blocked reason=%s geo_key=%s "
                "technology=%s reductant=%s product=%s withdraw_mt=%.3f tag=%s year=%d",
                result.blocked_reason,
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
            "[CAPACITY POOL] gate=greenfield decision=granted attributed_owner=%s geo_key=%s "
            "technology=%s reductant=%s product=%s withdraw_mt=%.3f build_mt=%.3f tag=%s year=%d "
            "credits_consumed=%s",
            result.attributed_owner_id,
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
        return (spec.build_mt, result.attributed_owner_id, True)

    return permitted_greenfield_capacity


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


def attribute_greenfield_on_furnace_group_added(event: events.FurnaceGroupAdded, uow: UnitOfWork) -> None:
    """Move a credit-funded greenfield plant into the funding company at construction start.

    The greenfield gate stashes the withdrawal's ``attributed_owner_id`` on the
    opportunity furnace group at announcement; the plant itself must stay in
    ``indi_<iso3>`` until announced→construction because the opportunity
    pipeline walks only the indi groups. This event fires at exactly that
    transition, when the pipeline is done with the plant, so the move is safe:
    the construction→operating flip and the P&L sweep are group-independent.

    Only the group membership moves — ``parent_gem_id`` stays ``indi_<iso3>``,
    keeping the site's own pixel energy prices (a physical fact of the site,
    not an ownership fact) flowing through the existing GEO-plant paths. The
    P&L sweep and expansion candidacy follow ``PlantGroup.plants``, so the
    receiving company gains the asset through existing mechanics.

    A dormant or unknown owner sends the plant nowhere — it stays in
    ``indi_<iso3>``, exactly as a wholly-unowned draw (no stash) does. The
    capex is a named capital injection, not a treasury debit: ``deduct_equity``
    is untouched and the injection is logged so the treasury story is
    auditable.
    """
    policy = _policy
    if policy is None:
        return
    if not event.is_new_plant:
        return
    with uow:
        plant = uow.plants.get(event.plant_id)
        furnace_group = next((fg for fg in plant.furnace_groups if fg.furnace_group_id == event.furnace_group_id), None)
        if furnace_group is None:
            raise ValueError(f"Furnace group {event.furnace_group_id} not found on plant {event.plant_id}")
        owner_id = furnace_group.capacity_pool_attributed_owner_id
        if owner_id is None:
            return
        current_group = uow.plant_groups.get_by_plant_id(plant.plant_id)
        try:
            owner_group = uow.plant_groups.get(owner_id)
        except KeyError:
            owner_group = None
        if owner_group is None or owner_group.is_dormant:
            logger.info(
                "[CAPACITY POOL] event=greenfield_attribution decision=fallback_indi plant=%s fg=%s "
                "owner=%s reason=%s current_group=%s",
                plant.plant_id,
                furnace_group.furnace_group_id,
                owner_id,
                "owner_group_missing" if owner_group is None else "owner_dormant",
                current_group.plant_group_id,
            )
            return
        if owner_group is current_group:
            return
        current_group.plants.remove(plant)
        uow.plant_groups.register_plant_in_group(plant, owner_id)
        if furnace_group.technology.capex is None:
            # A None capex yields -inf NPVs at tracking, which never announce
            raise ValueError(f"Announced greenfield {furnace_group.furnace_group_id} carries no capex")
        investment = float(furnace_group.technology.capex) * float(furnace_group.capacity)
        logger.info(
            "[CAPACITY POOL] event=greenfield_attributed plant=%s fg=%s company=%s from_group=%s "
            "capacity=%.3f capex_total=%.2f equity_injection=%.2f",
            plant.plant_id,
            furnace_group.furnace_group_id,
            owner_id,
            current_group.plant_group_id,
            float(furnace_group.capacity),
            investment,
            investment * furnace_group.equity_share,
        )
        uow.commit()


def note_greenfield_discard(furnace_group: FurnaceGroup, iso3: str) -> None:
    """Log the credit a discarded announced greenfield leaks — accepted, not reclaimed.

    Called from the announced→discarded branch of the status handler. The
    withdrawal stash is only ever set by a live greenfield gate, so this is
    inert by construction on unbound runs; reservation machinery is deliberate
    non-scope (spec §Deferred, reserve-then-firm).
    """
    if furnace_group.capacity_pool_granted_withdraw_mt is None:
        return
    logger.info(
        "[CAPACITY POOL] event=greenfield_discarded leaked_withdraw_mt=%.3f fg=%s iso3=%s attributed_owner=%s",
        furnace_group.capacity_pool_granted_withdraw_mt,
        furnace_group.furnace_group_id,
        iso3,
        furnace_group.capacity_pool_attributed_owner_id,
    )
