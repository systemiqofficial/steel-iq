"""Decision-tree evaluator for China's capacity-replacement policy.

Pure logic over the fixture rows and :class:`CapacityPolicyConfig` — no bus, no
environment, no domain-model imports — so the module stays deletable and
testable against hand-built rows. The three methods transcribe the policy
tree's branches: ① RETIRE (:meth:`TreeEvaluator.on_close`), ② REPLACE
(:meth:`TreeEvaluator.permitted_capacity`), ③ INCREASE
(:meth:`TreeEvaluator.on_increase`). Ratio precedence and derivation are
consumed from :mod:`.inputs`, never reimplemented.
"""

import logging
from dataclasses import dataclass

from steelo.utilities.utils import normalize_name

from .config import CapacityPolicyConfig
from .inputs import RegionRow, TechnologyRow, is_delegation_row, resolve_swap_ratio, technologies_with_reductant_rows
from .pool import Credit

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
        withdraw_mt: Credit to consume — always the originally planned
            capacity, even when the build itself is penalised.
        build_mt: Capacity actually allowed to be built; the planned amount
            divided by the emission-intense penalty divisor when the new route
            is emission-intense, else the planned amount unchanged.
        region_tag: Cluster tag the credits must carry — a build in a key
            province may spend only its own cluster's credits; None means any
            credit, tagged or untagged.
        product: ``"iron"`` or ``"steel"``, passed through to the withdrawal.
    """

    withdraw_mt: float
    build_mt: float
    region_tag: str | None
    product: str


class TreeEvaluator:
    """Evaluate the capacity-replacement decision tree for one Chinese plant action.

    Constructed once from the fixture row-lists and the policy config; methods
    take plain values so callers (event handlers now, decision gates later)
    adapt at their own call sites.

    Attributes:
        key_regions: geo_key → cluster name for the key provinces; also the
            mapping :meth:`CapacityPool.seed_from` needs at bootstrap.
        config: The policy's scenario levers, as constructed with; read by the
            decision-path adapter for switches the tree itself does not consult.
    """

    def __init__(
        self,
        regions: list[RegionRow],
        technologies: list[TechnologyRow],
        config: CapacityPolicyConfig,
    ) -> None:
        """
        Args:
            regions: Rows of ``Capacity pool - CHN provinces``.
            technologies: Rows of ``Capacity pool - technologies``.
            config: The policy's scenario levers.

        Raises:
            ValueError: If a key-province row carries no cluster name.
        """
        self.config = config
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
        """Build the retirement credit for freed capacity — branch ① RETIRE.

        A closure in a key province yields a credit tagged with the cluster
        name; anywhere else — non-key, exempt, or a bare country key — the
        credit is untagged. Technology is provenance only and is not carried.

        Args:
            geo_key: Combined geo key of the freeing plant, e.g. ``"CHN:CN-HE"``.
            capacity_mt: Freed capacity in Mt.
            owner_id: Ultimate plant group of the freeing plant.
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
        historical_utilization: dict[int, float] | None,
        year: int,
    ) -> float | None:
        """Resolve the capacity a replacement is allowed to build — branch ② REPLACE.

        Evaluated before the NPV so the agent values the switch it is actually
        allowed. The utilisation gate is checked first: a group whose recorded
        utilisation sat at or below the configured floor for the whole window
        of most recent consecutive recorded years is not eligible for
        renovation at all. Otherwise the permitted capacity is
        ``capacity_mt / ratio``, the ratio resolved per the override precedence
        and flag derivation in :func:`resolve_swap_ratio` — except in an
        exempt province, where replacement is always 1:1 regardless of
        technology.

        Args:
            old_technology: Technology being replaced.
            old_reductant: Its current reductant, or None when unset.
            new_technology: Candidate technology.
            new_reductant: The reductant the candidate is evaluated with.
            capacity_mt: The group's current capacity in Mt.
            geo_key: Combined geo key of the plant.
            historical_utilization: Per-year recorded utilisation of the
                group, or None when no history exists yet.
            year: Decision year.

        Returns:
            The permitted new capacity in Mt, or None when the utilisation
            gate blocks the replacement.

        Raises:
            ValueError: If a route has no classification row, or its flags are
                unauthored and no override decides the transition — the policy
                refuses to guess where a silent 1:1 would exempt the pair.
        """
        if self._utilization_blocks(historical_utilization, year):
            logger.info(
                "[CAPACITY POOL] evaluation=replace decision=blocked_utilization geo_key=%s "
                "old=%s old_reductant=%s new=%s new_reductant=%s capacity_mt=%.3f year=%d",
                geo_key,
                old_technology,
                old_reductant,
                new_technology,
                new_reductant,
                capacity_mt,
                year,
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
            "[CAPACITY POOL] evaluation=replace decision=ratio geo_key=%s "
            "old=%s old_reductant=%s new=%s new_reductant=%s ratio=%g capacity_mt=%.3f permitted_mt=%.3f year=%d",
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
        return permitted

    def on_increase(
        self,
        *,
        geo_key: str,
        product: str,
        capacity_mt: float,
        technology: str,
        reductant: str | None,
    ) -> WithdrawSpec:
        """Resolve what a new build must withdraw and may build — branch ③ INCREASE.

        A build in a key province may spend only that cluster's credits;
        elsewhere any credit applies. An emission-intense build still
        withdraws the full planned amount but may only build the planned
        amount divided by the penalty divisor — the originally planned freed
        capacity is removed.

        Args:
            geo_key: Combined geo key of the build location.
            product: ``"iron"`` or ``"steel"``.
            capacity_mt: Planned new capacity in Mt.
            technology: Technology being built.
            reductant: The reductant the build is evaluated with.

        Returns:
            The withdrawal specification for the pool gate.

        Raises:
            ValueError: If the route has no classification row or its
                emission-intense flag is unauthored.
        """
        row = self._classification(technology, reductant)
        if row.is_emission_intense is None:
            raise ValueError(
                f"is_emission_intense unauthored for {technology!r} (reductant {reductant!r}); "
                "the policy cannot evaluate an increase without it"
            )
        build_mt = (
            capacity_mt / self.config.emission_intense_penalty_divisor if row.is_emission_intense else capacity_mt
        )
        return WithdrawSpec(
            withdraw_mt=capacity_mt,
            build_mt=build_mt,
            region_tag=self.key_regions.get(geo_key),
            product=product,
        )

    def _classification(self, technology: str, reductant: str | None) -> TechnologyRow:
        """Return the classification row for a route, most specific first.

        A reductant-specific row wins over the technology's blank-reductant
        row; delegation rows classify nothing and were excluded at
        construction. Reductants are matched up to :func:`normalize_name`, the
        runtime's canonical key form.

        A reductant-split technology asked about with **no** reductant — a
        route the model has no reductant hypothesis for yet, such as a switch
        candidate no fleet precedent exists for — resolves to the conservative
        worst case over its authored reductant rows: emission-intense if any
        variant is, deep-abatement only if every variant is. That never grants
        the favourable 1:1 to an unresolved route, never blocks it either, and
        self-corrects once the route resolves to a real reductant. A *named*
        reductant with no row still refuses — that is a sheet gap, not a
        runtime unknown.

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
        unauthored = [row for row in variants if row.is_emission_intense is None or row.is_deep_abatement is None]
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
            is_deep_abatement=all(bool(row.is_deep_abatement) for row in variants),
            switching_to=None,
            swap_ratio=None,
        )
        self._conservative_rows[technology] = row
        logger.info(
            "[CAPACITY POOL] evaluation=classification decision=conservative_fallback technology=%s "
            "is_emission_intense=%s is_deep_abatement=%s (worst case over %d reductant rows; "
            "no reductant hypothesis for this route yet)",
            technology,
            row.is_emission_intense,
            row.is_deep_abatement,
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
