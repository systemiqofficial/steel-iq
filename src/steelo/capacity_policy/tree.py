"""Decision-tree evaluator for China's capacity-replacement policy.

Pure logic over the fixture rows and :class:`CapacityPolicyConfig`: no bus,
no environment, no domain imports. Branch ① RETIRE is
:meth:`TreeEvaluator.on_close`, ② REPLACE :meth:`TreeEvaluator.permitted_capacity`
and :meth:`TreeEvaluator.permitted_renovation`, ③ INCREASE
:meth:`TreeEvaluator.on_increase`. Capacities are model tonnes at run time;
the ``_mt`` names are a sheet-side convention.
See docs/domain_simulation_logic/capacity_replacement_policy.md#the-decision-tree.
"""

import logging
from dataclasses import dataclass

from steelo.utilities.utils import normalize_name

from .config import CapacityPolicyConfig
from .inputs import RegionRow, TechnologyRow, is_delegation_row, resolve_swap_ratio, technologies_with_reductant_rows
from .pool import Credit
from .recorder import CapacityPolicyRecorder

logger = logging.getLogger(__name__)


def _lookup_reductant(reductant: str | None) -> str | None:
    """Canonicalise a reductant for classification lookup.

    The sheets author the workbook vocabulary (``"Natural gas"``) while the
    runtime carries :func:`normalize_name` keys (``"natural_gas"``); both must
    land on the same classification row.
    """
    if not reductant:
        return None
    return normalize_name(reductant) or None


@dataclass(frozen=True)
class WithdrawSpec:
    """What an INCREASE build must withdraw from the pool, and may then build.

    Attributes:
        withdraw_mt: Credit to consume; always the planned capacity.
        build_mt: Capacity allowed to be built; the planned amount divided by
            the penalty divisor when the new route is emission-intense.
        region_tag: Cluster tag the credits must carry, or None for any credit.
        product: ``"iron"`` or ``"steel"``.
    """

    withdraw_mt: float
    build_mt: float
    region_tag: str | None
    product: str


class TreeEvaluator:
    """Evaluate the capacity-replacement decision tree for one Chinese plant action.

    Attributes:
        key_regions: geo_key → cluster name for the key provinces.
        config: The policy's scenario levers.
    """

    def __init__(
        self,
        regions: list[RegionRow],
        technologies: list[TechnologyRow],
        config: CapacityPolicyConfig,
        *,
        recorder: CapacityPolicyRecorder | None = None,
    ) -> None:
        """
        Args:
            regions: Rows of ``Capacity pool - CHN provinces``.
            technologies: Rows of ``Capacity pool - technologies``.
            config: The policy's scenario levers.
            recorder: The run's observability recorder; None records nothing,
                which keeps the evaluator usable as pure logic.

        Raises:
            ValueError: If a key-province row carries no cluster name.
        """
        self.config = config
        self.recorder = recorder
        self.key_regions: dict[str, str] = {}
        for row in regions:
            if row.type == "key":
                if row.region_name is None:
                    raise ValueError(f"Key province {row.geo_key} has no cluster name")
                self.key_regions[row.geo_key] = row.region_name
        self._exempt = {row.geo_key for row in regions if row.type == "exempt"}
        self._overrides = [row for row in technologies if row.is_override]
        self._reductant_split = technologies_with_reductant_rows(technologies)
        self._classifications = {
            (row.technology, _lookup_reductant(row.reductant)): row
            for row in technologies
            if not row.is_override and not is_delegation_row(row, self._reductant_split)
        }
        self._conservative_rows: dict[str, TechnologyRow] = {}

    def on_close(self, *, geo_key: str, capacity_mt: float, owner_id: str | None, product: str, year: int) -> Credit:
        """Build the retirement credit for freed capacity (branch ① RETIRE).

        Args:
            geo_key: Combined geo key of the freeing plant, e.g. ``"CHN:CN-HE"``;
                a key province yields a tagged credit, anywhere else untagged.
            capacity_mt: Freed capacity.
            owner_id: Plant group the freeing plant belongs to, by membership.
            product: ``"iron"`` or ``"steel"``.
            year: Deposit year; becomes the credit's vintage.

        Returns:
            The credit to deposit.
        """
        return Credit(
            amount_mt=capacity_mt,
            vintage_year=year,
            region_tag=self.key_regions.get(geo_key),
            owner_id=owner_id,
            product=product,
        )

    def permitted_capacity(
        self,
        *,
        old_technology: str,
        old_reductant: str | None,
        new_technology: str,
        new_reductant: str | None,
        capacity_mt: float,
        geo_key: str,
        product: str,
        historical_utilization: dict[int, float] | None,
        year: int,
        furnace_group_id: str | None = None,
    ) -> float | None:
        """Resolve the capacity a replacement may build (branch ② REPLACE).

        The utilisation gate is checked first; otherwise the permitted capacity
        is ``capacity_mt / ratio``, 1:1 in an exempt province and else per
        :func:`resolve_swap_ratio`.

        Args:
            old_technology: Technology being replaced.
            old_reductant: Its current reductant, or None when unset.
            new_technology: Candidate technology.
            new_reductant: The reductant the candidate is evaluated with.
            capacity_mt: The group's current capacity.
            geo_key: Combined geo key of the plant.
            product: ``"iron"`` or ``"steel"``, recorded with the decision.
            historical_utilization: Per-year recorded utilisation of the group, or None.
            year: Decision year.
            furnace_group_id: The group under evaluation, for the log and the record.

        Returns:
            The permitted new capacity, or None when the utilisation gate blocks.

        Raises:
            ValueError: If a route has no classification row, or its flag is
                unauthored and no override decides the transition.
        """
        if self._utilization_blocks(historical_utilization, year):
            logger.info(
                "[CAPACITY POOL] evaluation=replace decision=blocked_utilization fg=%s geo_key=%s "
                "old=%s old_reductant=%s new=%s new_reductant=%s capacity_mt=%.3f year=%d",
                furnace_group_id,
                geo_key,
                old_technology,
                old_reductant,
                new_technology,
                new_reductant,
                capacity_mt,
                year,
            )
            self._record_gate_decision(
                year=year,
                furnace_group_id=furnace_group_id,
                geo_key=geo_key,
                product=product,
                old_technology=old_technology,
                old_reductant=old_reductant,
                new_technology=new_technology,
                new_reductant=new_reductant,
                decision="blocked_utilization",
                capacity_t=capacity_mt,
            )
            return None
        if geo_key in self._exempt:
            ratio = 1.0
        else:
            resolved = resolve_swap_ratio(
                self._classification(old_technology, old_reductant),
                self._classification(new_technology, new_reductant),
                self._overrides,
                self.config.replacement_ratio,
            )
            if resolved is None:
                raise ValueError(
                    f"Swap ratio undecided for {old_technology!r} (reductant {old_reductant!r}) -> "
                    f"{new_technology!r} (reductant {new_reductant!r}): "
                    "unauthored classification flags and no override"
                )
            ratio = resolved
        permitted = capacity_mt / ratio
        logger.info(
            "[CAPACITY POOL] evaluation=replace decision=ratio fg=%s geo_key=%s "
            "old=%s old_reductant=%s new=%s new_reductant=%s ratio=%g capacity_mt=%.3f permitted_mt=%.3f year=%d",
            furnace_group_id,
            geo_key,
            old_technology,
            old_reductant,
            new_technology,
            new_reductant,
            ratio,
            capacity_mt,
            permitted,
            year,
        )
        self._record_gate_decision(
            year=year,
            furnace_group_id=furnace_group_id,
            geo_key=geo_key,
            product=product,
            old_technology=old_technology,
            old_reductant=old_reductant,
            new_technology=new_technology,
            new_reductant=new_reductant,
            decision="ratio",
            capacity_t=capacity_mt,
            ratio=ratio,
            permitted_t=permitted,
        )
        return permitted

    def permitted_renovation(
        self,
        *,
        technology: str,
        reductant: str | None,
        capacity_mt: float,
        geo_key: str,
        product: str,
        historical_utilization: dict[int, float] | None,
        year: int,
        furnace_group_id: str | None = None,
    ) -> float | None:
        """Apply the utilisation gate alone to a same-technology renovation.

        Used when ``renovation_counts_as_replace`` is off: nothing to classify
        or shrink, so the renovation either proceeds untouched or is blocked.

        Args:
            technology: The incumbent technology.
            reductant: Its current reductant, or None when unset.
            capacity_mt: The group's current capacity.
            geo_key: Combined geo key of the plant.
            product: ``"iron"`` or ``"steel"``, recorded with the decision.
            historical_utilization: Per-year recorded utilisation of the group, or None.
            year: Decision year.
            furnace_group_id: The group under evaluation, for the log and the record.

        Returns:
            ``capacity_mt`` unchanged when the renovation may proceed, or None
            when the utilisation gate blocks it.
        """
        if not self._utilization_blocks(historical_utilization, year):
            return capacity_mt
        logger.info(
            "[CAPACITY POOL] evaluation=renovation decision=blocked_utilization fg=%s geo_key=%s "
            "technology=%s reductant=%s capacity_mt=%.3f year=%d",
            furnace_group_id,
            geo_key,
            technology,
            reductant,
            capacity_mt,
            year,
        )
        self._record_gate_decision(
            year=year,
            furnace_group_id=furnace_group_id,
            geo_key=geo_key,
            product=product,
            old_technology=technology,
            old_reductant=reductant,
            new_technology=technology,
            new_reductant=reductant,
            decision="blocked_utilization",
            capacity_t=capacity_mt,
        )
        return None

    def increase_build_capacity(self, *, capacity_mt: float, technology: str, reductant: str | None) -> float:
        """Resolve the capacity an INCREASE build may build (branch ③, sizing only).

        Reads no pool state, so the pre-NPV sizing query and the consuming
        gate get the same answer by construction.

        Args:
            capacity_mt: Planned new capacity.
            technology: Technology being built.
            reductant: The reductant the build is evaluated with.

        Returns:
            The planned capacity, divided by the penalty divisor when the
            route is emission-intense.

        Raises:
            ValueError: If the route has no classification row or its flag is unauthored.
        """
        row = self._classification(technology, reductant)
        if row.is_emission_intense is None:
            raise ValueError(
                f"is_emission_intense unauthored for {technology!r} (reductant {reductant!r}); "
                "the policy cannot evaluate an increase without it"
            )
        return capacity_mt / self.config.emission_intense_penalty_divisor if row.is_emission_intense else capacity_mt

    def on_increase(
        self,
        *,
        geo_key: str,
        product: str,
        capacity_mt: float,
        technology: str,
        reductant: str | None,
    ) -> WithdrawSpec:
        """Resolve what a new build must withdraw and may build (branch ③ INCREASE).

        Args:
            geo_key: Combined geo key of the build location; a key province
                restricts the withdrawal to its cluster's credits.
            product: ``"iron"`` or ``"steel"``.
            capacity_mt: Planned new capacity; withdrawn in full.
            technology: Technology being built.
            reductant: The reductant the build is evaluated with.

        Returns:
            The withdrawal specification for the pool gate.

        Raises:
            ValueError: If the route has no classification row or its flag is unauthored.
        """
        return WithdrawSpec(
            withdraw_mt=capacity_mt,
            build_mt=self.increase_build_capacity(capacity_mt=capacity_mt, technology=technology, reductant=reductant),
            region_tag=self.key_regions.get(geo_key),
            product=product,
        )

    def _record_gate_decision(
        self,
        *,
        year: int,
        furnace_group_id: str | None,
        geo_key: str,
        product: str,
        old_technology: str,
        old_reductant: str | None,
        new_technology: str,
        new_reductant: str | None,
        decision: str,
        capacity_t: float,
        ratio: float | None = None,
        permitted_t: float | None = None,
    ) -> None:
        """Record one evaluation, 1:1 with the line just logged.

        The conservative fallback is a flag per side, not its own row: the
        synthesis is cached once per technology and could not be joined back.
        """
        if self.recorder is None:
            return
        self.recorder.record_gate_decision(
            year=year,
            furnace_group_id=furnace_group_id,
            geo_key=geo_key,
            product=product,
            old_technology=old_technology,
            old_reductant=old_reductant,
            new_technology=new_technology,
            new_reductant=new_reductant,
            decision=decision,
            capacity_t=capacity_t,
            ratio=ratio,
            permitted_t=permitted_t,
            old_used_conservative_fallback=self._uses_conservative_fallback(old_technology, old_reductant),
            new_used_conservative_fallback=self._uses_conservative_fallback(new_technology, new_reductant),
        )

    def _uses_conservative_fallback(self, technology: str, reductant: str | None) -> bool:
        """Whether classifying this route lands on the worst-case synthesis (mirrors :meth:`_classification`)."""
        if _lookup_reductant(reductant) is not None:
            return False
        return technology in self._reductant_split and (technology, None) not in self._classifications

    def _classification(self, technology: str, reductant: str | None) -> TechnologyRow:
        """Return the classification row for a route, most specific first.

        A reductant-split technology asked about with no reductant resolves to
        the worst case over its reductant rows; a named reductant with no row
        is a sheet gap and refuses.

        Raises:
            ValueError: If no classification row covers the route, or the
                conservative fallback would rest on unauthored flags.
        """
        lookup = _lookup_reductant(reductant)
        row = self._classifications.get((technology, lookup)) or self._classifications.get((technology, None))
        if row is not None:
            return row
        if lookup is None and technology in self._reductant_split:
            return self._conservative_classification(technology)
        raise ValueError(f"No classification row for technology {technology!r} (reductant {reductant!r})")

    def _conservative_classification(self, technology: str) -> TechnologyRow:
        """Synthesise the worst-case classification across a technology's reductant rows."""
        cached = self._conservative_rows.get(technology)
        if cached is not None:
            return cached
        variants = [row for (name, _), row in self._classifications.items() if name == technology]
        unauthored = [row for row in variants if row.is_emission_intense is None]
        if unauthored:
            raise ValueError(
                f"Cannot classify technology {technology!r} without a reductant: "
                "its reductant rows carry unauthored flags"
            )
        row = TechnologyRow(
            technology=technology,
            product=variants[0].product,
            reductant=None,
            is_emission_intense=any(bool(row.is_emission_intense) for row in variants),
            switching_to=None,
            swap_ratio=None,
        )
        self._conservative_rows[technology] = row
        logger.info(
            "[CAPACITY POOL] evaluation=classification decision=conservative_fallback technology=%s "
            "is_emission_intense=%s (worst case over %d reductant rows; "
            "no reductant hypothesis for this route yet)",
            technology,
            row.is_emission_intense,
            len(variants),
        )
        return row

    def _utilization_blocks(self, historical_utilization: dict[int, float] | None, year: int) -> bool:
        """Whether recorded utilisation makes the group ineligible for renovation.

        Anchored at the latest recorded year at or before the decision year:
        blocks iff the window of consecutive years ending there is fully
        recorded and every year sits at or below the floor. Missing, short or
        gappy history cannot establish the condition and never blocks.
        """
        if not historical_utilization:
            return False
        recorded = [y for y in historical_utilization if y <= year]
        if not recorded:
            return False
        latest = max(recorded)
        window = range(latest - self.config.utilization_window_years + 1, latest + 1)
        return all(
            y in historical_utilization and historical_utilization[y] <= self.config.min_utilization_for_renovation
            for y in window
        )
