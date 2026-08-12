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
operation is measurable from any run. The same call sites feed the run's
:class:`~steelo.capacity_policy.recorder.CapacityPolicyRecorder`, whose CSVs
:func:`flush_capacity_policy_outputs` writes at the end of a bound run.

Credit and motion ownership is **group membership** throughout: every handler
resolves ``uow.plant_groups.get_by_plant_id(...)`` rather than reading the
events' own ``owner_id``, which carries ``Plant.ultimate_plant_group`` and
therefore still reports ``indi_<iso3>`` for a plant this package attributed to
its funding company. A plant with no registered group raises, the D6 discipline.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from steelo.domain import events
from steelo.domain.models import Environment, FurnaceGroup, Plant, compose_geo_key
from steelo.service_layer.unit_of_work import UnitOfWork

from .pool import CapacityPool
from .recorder import CapacityPolicyRecorder
from .tree import TreeEvaluator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _BoundPolicy:
    evaluator: TreeEvaluator
    pool: CapacityPool
    recorder: CapacityPolicyRecorder


_policy: _BoundPolicy | None = None


def bind_capacity_policy(evaluator: TreeEvaluator, pool: CapacityPool, recorder: CapacityPolicyRecorder) -> None:
    """Activate the deposit handlers for this run.

    The recorder is required rather than optional: a bound policy that records
    nothing would be a half-bound state whose artefacts silently disagree with
    its logs.
    """
    global _policy
    _policy = _BoundPolicy(evaluator=evaluator, pool=pool, recorder=recorder)


def unbind_capacity_policy() -> None:
    """Deactivate the deposit handlers; they become no-ops again."""
    global _policy
    _policy = None


def flush_capacity_policy_outputs(output_dir: Path) -> None:
    """Write the run's policy CSVs, or nothing at all while unbound."""
    policy = _policy
    if policy is None:
        return
    policy.recorder.refresh_final_state(policy.pool.snapshot())
    counts = policy.recorder.write_csvs(output_dir)
    logger.info(
        "[CAPACITY POOL] wrote observability CSVs to %s rows=%s",
        output_dir,
        " ".join(f"{name}={count}" for name, count in counts.items()),
    )


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
        furnace_group_id: str | None = None,
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
            furnace_group_id=furnace_group_id,
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
            policy.recorder.record_ledger(
                year=year,
                operation="blocked_expansion",
                amount_t=spec.withdraw_mt,
                region_tag=spec.region_tag,
                owner_id=owner_id,
                product=spec.product,
                blocked_reason=result.blocked_reason,
                geo_key=compose_geo_key(iso3, geo_unit),
            )
            return None
        policy.recorder.record_ledger(
            year=year,
            operation="withdraw_expansion",
            amount_t=spec.withdraw_mt,
            region_tag=spec.region_tag,
            owner_id=owner_id,
            product=spec.product,
            credits_consumed=result.credits_consumed,
            geo_key=compose_geo_key(iso3, geo_unit),
        )
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
            policy.recorder.record_ledger(
                year=year,
                operation="blocked_greenfield",
                amount_t=spec.withdraw_mt,
                region_tag=spec.region_tag,
                owner_id=f"indi_{iso3}",
                product=spec.product,
                blocked_reason=result.blocked_reason,
                geo_key=compose_geo_key(iso3, geo_unit),
            )
            return None
        policy.recorder.record_ledger(
            year=year,
            operation="withdraw_greenfield",
            amount_t=spec.withdraw_mt,
            region_tag=spec.region_tag,
            owner_id=f"indi_{iso3}",
            product=spec.product,
            credits_consumed=result.credits_consumed,
            attributed_owner_id=result.attributed_owner_id,
            geo_key=compose_geo_key(iso3, geo_unit),
        )
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


def _get_plant_and_furnace_group(uow: UnitOfWork, furnace_group_id: str) -> tuple[Plant, FurnaceGroup]:
    """Fetch a furnace group and its plant by id; the events carry no plant id, so scan.

    Closed groups stay on their plant with status ``"closed"``, so the lookup
    holds for every lifecycle event.
    """
    for plant in uow.plants.list():
        for fg in plant.furnace_groups:
            if fg.furnace_group_id == furnace_group_id:
                return plant, fg
    raise ValueError(f"Furnace group {furnace_group_id} not found in any plant")


def deposit_on_furnace_group_closed(event: events.FurnaceGroupClosed, uow: UnitOfWork, env: Environment) -> None:
    """Branch ① RETIRE: bank the full freed capacity, tagged by the cluster rule."""
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        credit = policy.evaluator.on_close(
            geo_key=geo_key,
            capacity_mt=event.capacity,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=event.product,
            year=int(env.year),
        )
        policy.pool.deposit(credit)
        policy.recorder.record_ledger(
            year=int(env.year),
            operation="deposit_close",
            amount_t=credit.amount_mt,
            region_tag=credit.region_tag,
            owner_id=credit.owner_id,
            product=credit.product,
            vintage_year=credit.vintage_year,
            geo_key=geo_key,
            furnace_group_id=event.furnace_group_id,
        )
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
            furnace_group.chosen_reductant,
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
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        credit = policy.evaluator.on_close(
            geo_key=geo_key,
            capacity_mt=freed,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=event.product,
            year=int(env.year),
        )
        policy.pool.deposit(credit)
        policy.recorder.record_ledger(
            year=int(env.year),
            operation="deposit_replace",
            amount_t=credit.amount_mt,
            region_tag=credit.region_tag,
            owner_id=credit.owner_id,
            product=credit.product,
            vintage_year=credit.vintage_year,
            geo_key=geo_key,
            furnace_group_id=event.furnace_group_id,
        )
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
            furnace_group.chosen_reductant,
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
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        geo_key = compose_geo_key(event.iso3, event.geo_unit)
        credit = policy.evaluator.on_close(
            geo_key=geo_key,
            capacity_mt=freed,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=event.product,
            year=int(env.year),
        )
        policy.pool.deposit(credit)
        policy.recorder.record_ledger(
            year=int(env.year),
            operation="deposit_replace",
            amount_t=credit.amount_mt,
            region_tag=credit.region_tag,
            owner_id=credit.owner_id,
            product=credit.product,
            vintage_year=credit.vintage_year,
            geo_key=geo_key,
            furnace_group_id=event.furnace_group_id,
        )
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
            furnace_group.chosen_reductant,
        )


def deposit_on_end_of_life_closure(
    plant: Plant, furnace_group: FurnaceGroup, uow: UnitOfWork, env: Environment
) -> None:
    """Branch ① RETIRE at end of life: bank the full freed capacity, and record the motion.

    Called from ``finalise_iteration`` immediately after it closes an expired
    group with a bare status flip. That path raises no ``FurnaceGroupClosed``,
    so the deposit handler never sees it — yet Decision 28 credits every
    retirement, whoever decided it. The deposit is made pool-scoped rather than
    by emitting the event, which would also wake the shared closed-handlers the
    direct path deliberately bypasses.

    The vintage is the *post-increment* year: ``finalise_iteration`` advances
    ``env.year`` before closing anything, so these credits belong to the next
    yearly snapshot, exactly like the scheduled switches at the same boundary.
    The ledger names the mechanism (``deposit_close_end_of_life``) because an
    end-of-life retirement is not an agent decision and the two must be
    separable in the artefacts.

    Runs inside ``finalise_iteration``'s own unit-of-work context and only
    reads, so it opens none of its own.
    """
    policy = _policy
    if policy is None:
        return
    if plant.location.iso3 != "CHN":
        return
    geo_key = compose_geo_key(plant.location.iso3, plant.location.geo_unit)
    owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
    credit = policy.evaluator.on_close(
        geo_key=geo_key,
        capacity_mt=float(furnace_group.capacity),
        owner_id=owner_id,
        product=furnace_group.technology.product,
        year=int(env.year),
    )
    policy.pool.deposit(credit)
    policy.recorder.record_ledger(
        year=int(env.year),
        operation="deposit_close_end_of_life",
        amount_t=credit.amount_mt,
        region_tag=credit.region_tag,
        owner_id=credit.owner_id,
        product=credit.product,
        vintage_year=credit.vintage_year,
        geo_key=geo_key,
        furnace_group_id=furnace_group.furnace_group_id,
    )
    policy.recorder.record_motion(
        year=int(env.year),
        kind="close",
        plant_id=plant.plant_id,
        furnace_group_id=furnace_group.furnace_group_id,
        geo_key=geo_key,
        old_technology=furnace_group.technology.name,
        old_capacity_t=float(furnace_group.capacity),
        owner_id=owner_id,
        product=furnace_group.technology.product,
        reductant=furnace_group.chosen_reductant,
    )
    logger.info(
        "[CAPACITY POOL] event=end_of_life_closed deposit_mt=%.3f fg=%s geo_key=%s tag=%s "
        "product=%s owner=%s vintage=%d chosen_reductant=%s",
        credit.amount_mt,
        furnace_group.furnace_group_id,
        geo_key,
        credit.region_tag,
        credit.product,
        credit.owner_id,
        credit.vintage_year,
        furnace_group.chosen_reductant,
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


def note_greenfield_discard(furnace_group: FurnaceGroup, iso3: str, geo_unit: str | None, year: int) -> None:
    """Log the credit a discarded announced greenfield leaks — accepted, not reclaimed.

    Called from the announced→discarded branch of the status handler. The
    withdrawal stash is only ever set by a live greenfield gate, so this is
    inert by construction on unbound runs; reservation machinery is deliberate
    non-scope (spec §Deferred, reserve-then-firm). The ledger row annotates the
    leak rather than reversing it — the withdrawal already debited the pool, so
    the row stays outside the reconciliation sum.
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
    policy = _policy
    if policy is None:
        return
    policy.recorder.record_ledger(
        year=year,
        operation="greenfield_discard",
        amount_t=furnace_group.capacity_pool_granted_withdraw_mt,
        owner_id=f"indi_{iso3}",
        product=furnace_group.technology.product,
        attributed_owner_id=furnace_group.capacity_pool_attributed_owner_id,
        geo_key=compose_geo_key(iso3, geo_unit),
        furnace_group_id=furnace_group.furnace_group_id,
    )


def snapshot_pool_state(_event: events.IterationOver, env: Environment) -> None:
    """Record the pool's credits for the year that is ending.

    Registered ahead of ``finalise_iteration``, which increments the year
    *before* executing scheduled switches and end-of-life closures: those
    transactions therefore stamp Y+1 and belong to the next snapshot, which is
    what makes state(Y) equal seed plus every ledger flow stamped up to Y.
    """
    policy = _policy
    if policy is None:
        return
    policy.recorder.record_state(int(env.year), policy.pool.snapshot())


def record_motion_on_furnace_group_closed(event: events.FurnaceGroupClosed, uow: UnitOfWork, env: Environment) -> None:
    """Record a Chinese closure as a motion — the deposit's fleet-side counterpart."""
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        policy.recorder.record_motion(
            year=int(env.year),
            kind="close",
            plant_id=plant.plant_id,
            furnace_group_id=event.furnace_group_id,
            geo_key=compose_geo_key(event.iso3, event.geo_unit),
            old_technology=furnace_group.technology.name,
            old_capacity_t=event.capacity,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=event.product,
            reductant=furnace_group.chosen_reductant,
        )


def record_motion_on_furnace_group_tech_changed(
    event: events.FurnaceGroupTechChanged, uow: UnitOfWork, env: Environment
) -> None:
    """Record a Chinese technology switch as a motion, shrunk or not.

    An unshrunk switch deposits nothing but still moves the fleet, so the row
    is written before any of the deposit handler's early returns would apply.
    The reductant is the group's post-switch re-pick, which is exactly what
    pairs with the gate decision the switch was approved under.
    """
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        policy.recorder.record_motion(
            year=int(env.year),
            kind="switch",
            plant_id=plant.plant_id,
            furnace_group_id=event.furnace_group_id,
            geo_key=compose_geo_key(event.iso3, event.geo_unit),
            old_technology=event.old_technology_name,
            new_technology=event.technology_name,
            old_capacity_t=event.old_capacity,
            new_capacity_t=event.capacity,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=event.product,
            reductant=furnace_group.chosen_reductant,
        )


def record_motion_on_furnace_group_renovated(
    event: events.FurnaceGroupRenovated, uow: UnitOfWork, env: Environment
) -> None:
    """Record a Chinese renovation as a motion, shrunk or not."""
    policy = _policy
    if policy is None:
        return
    if event.iso3 != "CHN":
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        policy.recorder.record_motion(
            year=int(env.year),
            kind="renovate",
            plant_id=plant.plant_id,
            furnace_group_id=event.furnace_group_id,
            geo_key=compose_geo_key(event.iso3, event.geo_unit),
            old_technology=event.old_technology_name,
            new_technology=event.new_technology_name,
            old_capacity_t=event.old_capacity,
            new_capacity_t=event.capacity,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=event.product,
            reductant=furnace_group.chosen_reductant,
        )


def record_motion_on_furnace_group_added(event: events.FurnaceGroupAdded, uow: UnitOfWork, env: Environment) -> None:
    """Record a Chinese build as a motion — an expansion or a greenfield.

    The event carries no location, so the plant lookup precedes the China
    guard; that costs one repository read per build on a bound run only.
    Registered after the greenfield attribution so a credit-funded plant is
    already sitting in its funding company, and the owner is read from group
    membership rather than ``ultimate_plant_group``, which still reports
    ``indi_<iso3>`` for an attributed plant.

    Expansion rows stamp the decision year; a greenfield row stamps
    construction start, one transition after the withdrawal that funded it.
    """
    policy = _policy
    if policy is None:
        return
    with uow:
        plant = uow.plants.get(event.plant_id)
        if plant.location.iso3 != "CHN":
            return
        furnace_group = next((fg for fg in plant.furnace_groups if fg.furnace_group_id == event.furnace_group_id), None)
        if furnace_group is None:
            raise ValueError(f"Furnace group {event.furnace_group_id} not found on plant {event.plant_id}")
        policy.recorder.record_motion(
            year=int(env.year),
            kind="greenfield" if event.is_new_plant else "expansion",
            plant_id=plant.plant_id,
            furnace_group_id=event.furnace_group_id,
            geo_key=compose_geo_key(plant.location.iso3, plant.location.geo_unit),
            new_technology=event.technology_name,
            new_capacity_t=event.capacity,
            owner_id=uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id,
            product=furnace_group.technology.product,
            reductant=furnace_group.chosen_reductant,
        )
