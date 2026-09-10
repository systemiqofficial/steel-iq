"""Deposit handlers, hook accessors and motion handlers for the capacity pool.

The event handlers are registered unconditionally and stay inert until
:func:`bind_capacity_policy` installs an evaluator, pool and recorder; the
hook accessors return None while unbound, so the decision paths behave
byte-identically. Applicability (China only) is decided here, never in the
domain. The motion handlers also feed the always-bound global recorder of
:mod:`steelo.motions`, for every country on every run. Ownership is group
membership: handlers resolve ``uow.plant_groups.get_by_plant_id(...)``, not
the events' ``owner_id``, which still reports ``indi_<iso3>`` for an
attributed greenfield plant; a plant with no registered group raises.
See docs/domain_simulation_logic/capacity_replacement_policy_reference.md#binding-lifecycle.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from steelo.domain import events
from steelo.domain.models import Environment, FurnaceGroup, Plant, compose_geo_key
from steelo.motions import global_motions_recorder
from steelo.service_layer.unit_of_work import UnitOfWork

from .pool import CapacityPool, Credit
from .recorder import CapacityPolicyRecorder
from .tree import TreeEvaluator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _BoundPolicy:
    """The evaluator, pool and recorder of the run's bound policy, installed by :func:`bind_capacity_policy`."""

    evaluator: TreeEvaluator
    pool: CapacityPool
    recorder: CapacityPolicyRecorder


_policy: _BoundPolicy | None = None
_warned_bare_chn_contexts: set[str] = set()


def bind_capacity_policy(evaluator: TreeEvaluator, pool: CapacityPool, recorder: CapacityPolicyRecorder) -> None:
    """Activate the handlers and hooks for this run; the recorder is required so the artefacts never disagree with the logs."""
    global _policy
    _policy = _BoundPolicy(evaluator=evaluator, pool=pool, recorder=recorder)
    _warned_bare_chn_contexts.clear()


def unbind_capacity_policy() -> None:
    """Deactivate the handlers and hooks; they become no-ops again."""
    global _policy
    _policy = None


def _motion_recorders(iso3: str) -> list[CapacityPolicyRecorder]:
    """The recorders a motion at this location feeds: the global one first, the policy's for China while bound.

    Global-first matters to the end-of-life source rule, which asks the first recorder for renovation history.
    """
    recorders: list[CapacityPolicyRecorder] = []
    global_recorder = global_motions_recorder()
    if global_recorder is not None:
        recorders.append(global_recorder)
    if _policy is not None and iso3 == "CHN":
        recorders.append(_policy.recorder)
    return recorders


def _policy_geo_key(iso3: str, geo_unit: str | None, *, context: str) -> str:
    """Compose the policy geo key, warning once per context when a Chinese location has no geo unit.

    The bare ``CHN`` key is treated as non-key, the policy's most permissive
    treatment, so it must never happen silently.
    """
    if iso3 == "CHN" and not geo_unit:
        message = (
            "[CAPACITY POOL] context=%s iso3=CHN has no geo_unit: falling back to the bare "
            "country key, which the policy treats as non-key (untagged deposit, unrestricted "
            "withdrawal, never exempt) — check the admin-1 layer and the province tagging"
        )
        if context in _warned_bare_chn_contexts:
            logger.debug(message, context)
        else:
            _warned_bare_chn_contexts.add(context)
            logger.warning(message + " (further occurrences log at DEBUG)", context)
    return compose_geo_key(iso3, geo_unit)


def flush_capacity_policy_outputs(output_dir: Path) -> None:
    """Write the run's policy CSVs, or nothing at all while unbound.

    Args:
        output_dir: Directory to write into — ``SimulationConfig.policy_output_dir``
            on a real run; created here rather than with the run's other output
            directories, so a policy-OFF run leaves no empty directory behind.
    """
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
    """Return the live ② REPLACE pre-NPV callable, or None while unbound."""
    policy = _policy
    if policy is None:
        return None

    def permitted_replace_capacity(
        *,
        iso3: str,
        geo_unit: str | None,
        product: str,
        old_technology: str,
        old_reductant: str | None,
        new_technology: str,
        new_reductant: str | None,
        capacity: float,
        historical_utilization: dict[int, float] | None,
        year: int,
        furnace_group_id: str | None = None,
    ) -> float | None:
        """Resolve one candidate transition's permitted capacity (branch ② REPLACE).

        A non-Chinese plant passes through at its own capacity. A
        same-technology candidate is a full REPLACE under
        ``renovation_counts_as_replace``, else it faces the utilisation gate
        alone. ``new_reductant`` is the candidate's operating-start pick, so
        reductant drift is measurable by joining the gate decisions against
        the motions. None means the gate blocks the candidate.
        """
        if iso3 != "CHN":
            return capacity
        if new_technology == old_technology and not policy.evaluator.config.renovation_counts_as_replace:
            return policy.evaluator.permitted_renovation(
                technology=old_technology,
                reductant=old_reductant or None,
                capacity_mt=capacity,
                product=product,
                geo_key=_policy_geo_key(iso3, geo_unit, context="replace_gate"),
                historical_utilization=historical_utilization,
                year=year,
                furnace_group_id=furnace_group_id,
            )
        return policy.evaluator.permitted_capacity(
            old_technology=old_technology,
            old_reductant=old_reductant or None,
            new_technology=new_technology,
            new_reductant=new_reductant or None,
            capacity_mt=capacity,
            product=product,
            geo_key=_policy_geo_key(iso3, geo_unit, context="replace_gate"),
            historical_utilization=historical_utilization,
            year=year,
            furnace_group_id=furnace_group_id,
        )

    return permitted_replace_capacity


def increase_sizing_hook() -> Callable[..., float] | None:
    """Return the live ③ INCREASE sizing query, or None while unbound.

    Pool availability is deliberately not part of the answer: probing the
    pool per candidate would make valuation depend on evaluation order.
    """
    policy = _policy
    if policy is None:
        return None

    def increase_sizing_query(*, iso3: str, technology: str, reductant: str | None, capacity: float) -> float:
        """Size one INCREASE candidate for its NPV; no pool state, and no logging since it runs per candidate."""
        if iso3 != "CHN":
            return capacity
        return policy.evaluator.increase_build_capacity(
            capacity_mt=capacity,
            technology=technology,
            reductant=reductant or None,
        )

    return increase_sizing_query


def expansion_capacity_hook() -> Callable[..., float | None] | None:
    """Return the live ③ INCREASE expansion gate callable, or None while unbound."""
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
        """Withdraw retirement credits for one approved expansion (branch ③ INCREASE).

        Called at the point of commitment. The credit is consumed at the
        decision, all or nothing; an expansion that later fails to materialise
        has still spent it (accepted, quantifiable from the grant log). None
        means the pool blocks the expansion this year.
        """
        if iso3 != "CHN":
            return capacity
        geo_key = _policy_geo_key(iso3, geo_unit, context="expansion_gate")
        spec = policy.evaluator.on_increase(
            geo_key=geo_key,
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
                geo_key,
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
                geo_key=geo_key,
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
            geo_key=geo_key,
        )
        logger.info(
            "[CAPACITY POOL] gate=expansion decision=granted owner=%s geo_key=%s technology=%s "
            "reductant=%s product=%s withdraw_mt=%.3f build_mt=%.3f tag=%s year=%d credits_consumed=%s",
            owner_id,
            geo_key,
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


def greenfield_retry_cap() -> int | None:
    """Return the years the greenfield gate may block before discarding, or None while unbound."""
    policy = _policy
    if policy is None:
        return None
    return policy.evaluator.config.capacity_pool_max_retry_years


def greenfield_feasibility_hook() -> Callable[..., str | None] | None:
    """Return the live pre-draw greenfield feasibility probe, or None while unbound.

    Non-consuming: the retry cap counts blocked years from this probe (not
    blocked draws), and a blocked answer records the same ``blocked_greenfield``
    ledger row the consuming gate would; a fundable answer records nothing.
    """
    policy = _policy
    if policy is None:
        return None

    def greenfield_feasibility(
        *,
        iso3: str,
        geo_unit: str | None,
        technology: str,
        reductant: str | None,
        capacity: float,
        product: str,
        year: int,
    ) -> str | None:
        """Probe one considered greenfield's fundability — no state is touched.

        Returns None when the withdrawal would be granted (or the opportunity
        is not Chinese), else the ``blocked_reason`` the consuming gate would
        refuse with.
        """
        if iso3 != "CHN":
            return None
        geo_key = _policy_geo_key(iso3, geo_unit, context="greenfield_gate")
        spec = policy.evaluator.on_increase(
            geo_key=geo_key,
            product=product,
            capacity_mt=capacity,
            technology=technology,
            reductant=reductant or None,
        )
        reason = policy.pool.can_withdraw(
            spec.withdraw_mt,
            spec.region_tag,
            product=spec.product,
            owner_id=f"indi_{iso3}",
            year=year,
            single_owner=True,
        )
        if reason is None:
            return None
        logger.info(
            "[CAPACITY POOL] gate=greenfield decision=blocked stage=pre_draw reason=%s geo_key=%s "
            "technology=%s reductant=%s product=%s withdraw_mt=%.3f tag=%s year=%d",
            reason,
            geo_key,
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
            blocked_reason=reason,
            geo_key=geo_key,
        )
        return reason

    return greenfield_feasibility


def greenfield_capacity_hook() -> Callable[..., tuple[float, str | None, bool, tuple[Credit, ...]] | None] | None:
    """Return the live ③ INCREASE greenfield gate callable, or None while unbound."""
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
    ) -> tuple[float, str | None, bool, tuple[Credit, ...]] | None:
        """Withdraw retirement credits for one announced greenfield (branch ③ INCREASE).

        Served from a single credit holder, so a build can be refused with an
        ample pool. The consumed slices travel with the opportunity and are
        refunded on a later discard.

        Returns:
            ``(build_capacity, attributed_owner_id, withdrew, credits_consumed)``
            on a grant (the unowned pot attributes to None), or None when
            blocked, which leaves the opportunity considered to retry next year.
        """
        if iso3 != "CHN":
            return (capacity, None, False, ())
        geo_key = _policy_geo_key(iso3, geo_unit, context="greenfield_gate")
        spec = policy.evaluator.on_increase(
            geo_key=geo_key,
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
                geo_key,
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
                geo_key=geo_key,
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
            geo_key=geo_key,
        )
        logger.info(
            "[CAPACITY POOL] gate=greenfield decision=granted attributed_owner=%s geo_key=%s "
            "technology=%s reductant=%s product=%s withdraw_mt=%.3f build_mt=%.3f tag=%s year=%d "
            "credits_consumed=%s",
            result.attributed_owner_id,
            geo_key,
            technology,
            reductant,
            spec.product,
            spec.withdraw_mt,
            spec.build_mt,
            spec.region_tag,
            year,
            [(c.owner_id, c.vintage_year, round(c.amount_mt, 6)) for c in result.credits_consumed],
        )
        return (spec.build_mt, result.attributed_owner_id, True, result.credits_consumed)

    return permitted_greenfield_capacity


def _get_plant_and_furnace_group(uow: UnitOfWork, furnace_group_id: str) -> tuple[Plant, FurnaceGroup]:
    """Fetch a furnace group and its plant by id.

    The events carry no plant id, and the plant repository's furnace-group
    index only covers groups present at registration, so scan.
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
        geo_key = _policy_geo_key(event.iso3, event.geo_unit, context="deposit_close")
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
    """Branch ② REPLACE: bank the capacity the replacement shrank away (``old_capacity − capacity``, if positive)."""
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
        geo_key = _policy_geo_key(event.iso3, event.geo_unit, context="deposit_replace")
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
    """Branch ② REPLACE via renovation: bank the capacity a renovation shrank away, if any."""
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
        geo_key = _policy_geo_key(event.iso3, event.geo_unit, context="deposit_renovation")
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

    Called from ``finalise_iteration``, which closes an expired group with a
    bare status flip and raises no event. The vintage is the post-increment
    year, so these credits belong to the next yearly snapshot. Reads only, so
    it opens no unit-of-work context of its own.
    """
    policy = _policy
    recorders = _motion_recorders(plant.location.iso3)
    if not recorders:
        return
    owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
    if policy is not None and plant.location.iso3 == "CHN":
        geo_key = _policy_geo_key(plant.location.iso3, plant.location.geo_unit, context="deposit_end_of_life")
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
    # An age-out is data-scheduled only when no model action set the clock that
    # ran out: builds and switches stamp created_by_PAM, renovations reset the
    # lifetime without the stamp and are looked up in the run's own motions —
    # asked of the first (broadest) recorder, the global one whenever bound
    source = (
        "pam"
        if furnace_group.created_by_PAM or recorders[0].has_renovation(furnace_group.furnace_group_id)
        else "input_data"
    )
    for recorder in recorders:
        recorder.record_motion(
            year=int(env.year),
            kind="close",
            source=source,
            plant_id=plant.plant_id,
            furnace_group_id=furnace_group.furnace_group_id,
            geo_key=compose_geo_key(plant.location.iso3, plant.location.geo_unit),
            old_technology=furnace_group.technology.name,
            old_capacity_t=float(furnace_group.capacity),
            owner_id=owner_id,
            product=furnace_group.technology.product,
            reductant=furnace_group.chosen_reductant,
        )


def attribute_greenfield_on_furnace_group_added(event: events.FurnaceGroupAdded, uow: UnitOfWork) -> None:
    """Move a credit-funded greenfield plant into the funding company at construction start.

    The plant must stay in ``indi_<iso3>`` until announced→construction
    because the opportunity pipeline walks only the indi groups. Only group
    membership moves: ``parent_gem_id`` stays ``indi_<iso3>`` so the site's
    own energy prices keep flowing, and the capex is a logged capital
    injection, not a treasury debit. A dormant or unknown owner leaves the
    plant where it is.
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


def refund_greenfield_on_discard(furnace_group: FurnaceGroup, iso3: str, geo_unit: str | None, year: int) -> None:
    """Return a discarded announced greenfield's credits to the pool at their original vintages.

    Inert by construction on unbound runs: the withdrawal stash is only ever
    set by a live greenfield gate. The ``greenfield_discard`` row stays
    outside the reconciliation sum; the ``refunded`` rows are the flow.
    """
    if furnace_group.capacity_pool_granted_withdraw_mt is None:
        return
    logger.info(
        "[CAPACITY POOL] event=greenfield_discarded refunded_withdraw_mt=%.3f fg=%s iso3=%s attributed_owner=%s",
        furnace_group.capacity_pool_granted_withdraw_mt,
        furnace_group.furnace_group_id,
        iso3,
        furnace_group.capacity_pool_attributed_owner_id,
    )
    policy = _policy
    if policy is None:
        return
    geo_key = compose_geo_key(iso3, geo_unit)
    for credit in furnace_group.capacity_pool_consumed_credits or ():
        policy.pool.refund(credit)
        policy.recorder.record_ledger(
            year=year,
            operation="refunded",
            amount_t=credit.amount_mt,
            region_tag=credit.region_tag,
            owner_id=credit.owner_id,
            product=credit.product,
            vintage_year=credit.vintage_year,
            geo_key=geo_key,
            furnace_group_id=furnace_group.furnace_group_id,
        )
    policy.recorder.record_ledger(
        year=year,
        operation="greenfield_discard",
        amount_t=furnace_group.capacity_pool_granted_withdraw_mt,
        owner_id=f"indi_{iso3}",
        product=furnace_group.technology.product,
        attributed_owner_id=furnace_group.capacity_pool_attributed_owner_id,
        geo_key=geo_key,
        furnace_group_id=furnace_group.furnace_group_id,
    )
    furnace_group.capacity_pool_granted_withdraw_mt = None
    furnace_group.capacity_pool_attributed_owner_id = None
    furnace_group.capacity_pool_consumed_credits = None


def snapshot_pool_state(_event: events.IterationOver, env: Environment) -> None:
    """Record the pool's credits for the year that is ending.

    Registered ahead of ``finalise_iteration``, whose boundary transactions
    stamp Y+1 and belong to the next snapshot.
    """
    policy = _policy
    if policy is None:
        return
    policy.recorder.record_state(int(env.year), policy.pool.snapshot())


def purge_expired_credits(_event: events.IterationOver, env: Environment) -> None:
    """Sweep dead credits on entering a year: shelf-life expiry and, at the swap cutoff, the unowned credits.

    Registered after ``finalise_iteration``, so the year-Y snapshot still
    shows a credit usable through Y and no snapshot shows a dead one.
    """
    policy = _policy
    if policy is None:
        return
    year = int(env.year)
    policy.recorder.record_expired(year, policy.pool.purge_expired(year))
    policy.recorder.record_expired(year, policy.pool.purge_unowned(year), operation="expired_unowned")


def record_motion_on_furnace_group_closed(event: events.FurnaceGroupClosed, uow: UnitOfWork, env: Environment) -> None:
    """Record a decided closure as a motion, in every country."""
    recorders = _motion_recorders(event.iso3)
    if not recorders:
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
        for recorder in recorders:
            recorder.record_motion(
                year=int(env.year),
                kind="close",
                source="pam",
                plant_id=plant.plant_id,
                furnace_group_id=event.furnace_group_id,
                geo_key=compose_geo_key(event.iso3, event.geo_unit),
                old_technology=furnace_group.technology.name,
                old_capacity_t=event.capacity,
                owner_id=owner_id,
                product=event.product,
                reductant=furnace_group.chosen_reductant,
            )


def record_motion_on_furnace_group_tech_changed(
    event: events.FurnaceGroupTechChanged, uow: UnitOfWork, env: Environment
) -> None:
    """Record a technology switch as a motion, shrunk or not; the reductant is the post-switch re-pick."""
    recorders = _motion_recorders(event.iso3)
    if not recorders:
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
        for recorder in recorders:
            recorder.record_motion(
                year=int(env.year),
                kind="switch",
                source="pam",
                plant_id=plant.plant_id,
                furnace_group_id=event.furnace_group_id,
                geo_key=compose_geo_key(event.iso3, event.geo_unit),
                old_technology=event.old_technology_name,
                new_technology=event.technology_name,
                old_capacity_t=event.old_capacity,
                new_capacity_t=event.capacity,
                owner_id=owner_id,
                product=event.product,
                reductant=furnace_group.chosen_reductant,
            )


def record_motion_on_furnace_group_renovated(
    event: events.FurnaceGroupRenovated, uow: UnitOfWork, env: Environment
) -> None:
    """Record a renovation as a motion, shrunk or not, in every country."""
    recorders = _motion_recorders(event.iso3)
    if not recorders:
        return
    with uow:
        plant, furnace_group = _get_plant_and_furnace_group(uow, event.furnace_group_id)
        owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
        for recorder in recorders:
            recorder.record_motion(
                year=int(env.year),
                kind="renovate",
                source="pam",
                plant_id=plant.plant_id,
                furnace_group_id=event.furnace_group_id,
                geo_key=compose_geo_key(event.iso3, event.geo_unit),
                old_technology=event.old_technology_name,
                new_technology=event.new_technology_name,
                old_capacity_t=event.old_capacity,
                new_capacity_t=event.capacity,
                owner_id=owner_id,
                product=event.product,
                reductant=furnace_group.chosen_reductant,
            )


def record_motion_on_furnace_group_added(event: events.FurnaceGroupAdded, uow: UnitOfWork, env: Environment) -> None:
    """Record a build as a motion: an expansion or a greenfield, in every country.

    Registered after the greenfield attribution, so a credit-funded plant is
    recorded under its funding company. Expansion rows stamp the decision
    year; greenfield rows stamp construction start.
    """
    if global_motions_recorder() is None and _policy is None:
        return
    with uow:
        plant = uow.plants.get(event.plant_id)
        recorders = _motion_recorders(plant.location.iso3)
        if not recorders:
            return
        furnace_group = next((fg for fg in plant.furnace_groups if fg.furnace_group_id == event.furnace_group_id), None)
        if furnace_group is None:
            raise ValueError(f"Furnace group {event.furnace_group_id} not found on plant {event.plant_id}")
        owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
        for recorder in recorders:
            recorder.record_motion(
                year=int(env.year),
                kind="greenfield" if event.is_new_plant else "expansion",
                source="pam",
                plant_id=plant.plant_id,
                furnace_group_id=event.furnace_group_id,
                geo_key=compose_geo_key(plant.location.iso3, plant.location.geo_unit),
                new_technology=event.technology_name,
                new_capacity_t=event.capacity,
                owner_id=owner_id,
                product=furnace_group.technology.product,
                reductant=furnace_group.chosen_reductant,
            )


def record_motion_on_pipeline_group_operating(
    plant: Plant, furnace_group: FurnaceGroup, uow: UnitOfWork, env: Environment
) -> None:
    """Record an input-data pipeline group entering the operating fleet.

    The year-start status flip in the simulation loop raises no event.
    ``created_by_PAM`` is the exact discriminator here: model-built
    expansions and greenfields complete through the same flip but were
    recorded at their decision, and switches never reach it. Reads only, so
    it opens no unit-of-work context of its own.
    """
    if global_motions_recorder() is None and _policy is None:
        return
    recorders = _motion_recorders(plant.location.iso3)
    if not recorders or furnace_group.created_by_PAM:
        return
    owner_id = uow.plant_groups.get_by_plant_id(plant.plant_id).plant_group_id
    for recorder in recorders:
        recorder.record_motion(
            year=int(env.year),
            kind="pipeline",
            source="input_data",
            plant_id=plant.plant_id,
            furnace_group_id=furnace_group.furnace_group_id,
            geo_key=compose_geo_key(plant.location.iso3, plant.location.geo_unit),
            new_technology=furnace_group.technology.name,
            new_capacity_t=float(furnace_group.capacity),
            owner_id=owner_id,
            product=furnace_group.technology.product,
            reductant=furnace_group.chosen_reductant,
        )
