"""National retirement-credit pool for China's capacity-replacement policy.

Retirements and penalised replacements deposit freed-capacity credits; new builds
must withdraw a matching amount before they are allowed. Net national capacity is
unchanged — it is reallocated: a penalised replacement surrenders capacity that a
cleaner project can then claim, which is the swap regime doing its job.
"""

import logging
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

BANKED_CREDIT_RULES = ("reassign", "persist", "expire")


@dataclass(frozen=True)
class Credit:
    """A retirement credit: freed capacity that can fund a later build.

    Attributes:
        amount_mt: Freed capacity in Mt.
        vintage_year: Year the capacity was retired; the pool consumes oldest first.
        region_tag: Cluster name of the key region (e.g. ``"Jing-Jin-Ji"``) when
            the retirement happened in one — member provinces share the tag, so
            a Hebei build can spend a Tianjin credit — else None (untagged).
        owner_id: Depositing PlantGroup.plant_group_id, or None for seeded
            historical retirements that name no company. Unowned credits stay
            freely drawable regardless of the banked-credit rule.
        product: ``"iron"`` or ``"steel"`` — iron and steel are separate stocks.
    """

    amount_mt: float
    vintage_year: int
    region_tag: str | None
    owner_id: str | None
    product: str


@dataclass(frozen=True)
class SeedEntry:
    """A historical retirement used to seed the pool at t=0.

    The region tag is derived at load time (``geo_key in key_regions``), never
    authored in the seed data, so re-tagging follows the key-region config.
    ``owner_id`` is None where the source names no company.
    """

    amount_mt: float
    vintage_year: int
    geo_key: str
    owner_id: str | None
    product: str


@dataclass(frozen=True)
class WithdrawResult:
    """Outcome of a withdrawal attempt.

    Attributes:
        granted: Whether the withdrawal was allowed and consumed.
        credits_consumed: FIFO-ordered consumed portions; a partially-consumed
            credit appears with just the consumed share.
        attributed_owner_id: The holder drawn from, for ``single_owner``
            withdrawals — None when that holder is the unowned pot. Always None
            for ordinary withdrawals, which have no attribution consumer.
        blocked_reason: None when granted. ``"insufficient_applicable_pool"``
            when the applicable credits sum below the requirement;
            ``"no_single_owner_with_sufficient_credits"`` when the pool is ample
            but no one holder covers the amount — the gate must log this case
            distinctly or it reads as a phantom block.
    """

    granted: bool
    credits_consumed: tuple[Credit, ...]
    attributed_owner_id: str | None
    blocked_reason: str | None


class CapacityPool:
    """Age-ordered queue of retirement credits with filtered FIFO withdrawal.

    Withdrawals see only the "applicable pool": credits passing the region and
    product filters plus the owner rules. Ordinary (expansion) withdrawals draw
    plain FIFO across all applicable credits; from the cutoff year they are
    restricted to the withdrawer's own credits, with pre-cutoff vintages treated
    per the banked-credit rule. Greenfield withdrawals (``single_owner=True``)
    are instead served from exactly one holder's credits, uniformly across the
    run. Grants are all-or-nothing. A partially-consumed credit splits and the
    remainder keeps its original vintage — restamping would send it to the back
    of the queue and break FIFO.
    """

    def __init__(
        self,
        *,
        inter_company_swap_cutoff_year: int | None = 2028,
        banked_credit_rule: str = "reassign",
    ) -> None:
        """
        Args:
            inter_company_swap_cutoff_year: First year a company may only spend
                credits it deposited. None disables the partition.
            banked_credit_rule: Treatment of pre-cutoff credits from the cutoff
                year on: ``"reassign"`` keeps them with their depositor (the
                owner filter applies), ``"persist"`` leaves them freely
                spendable, ``"expire"`` makes them unusable. Unowned credits are
                exempt from all three.

        Raises:
            ValueError: On an unknown ``banked_credit_rule``.
        """
        if banked_credit_rule not in BANKED_CREDIT_RULES:
            raise ValueError(
                f"Unknown banked_credit_rule {banked_credit_rule!r}; expected one of {BANKED_CREDIT_RULES}"
            )
        self.inter_company_swap_cutoff_year = inter_company_swap_cutoff_year
        self.banked_credit_rule = banked_credit_rule
        self._credits: list[Credit] = []

    def deposit(self, credit: Credit) -> None:
        """Append a credit to the queue.

        Deposits arrive in simulation-year order, which keeps the queue
        age-ordered without sorting.

        Args:
            credit: The retirement credit to bank.

        Raises:
            ValueError: If the credit amount is not positive.
        """
        if credit.amount_mt <= 0:
            raise ValueError(f"Credit amount must be positive, got {credit.amount_mt}")
        self._credits.append(credit)

    def try_withdraw(
        self,
        amount_mt: float,
        region_tag: str | None,
        product: str,
        owner_id: str,
        year: int,
        single_owner: bool = False,
    ) -> WithdrawResult:
        """Attempt to consume ``amount_mt`` from the applicable pool, FIFO.

        The applicable pool is the subset of credits passing every filter:
            - region: ``region_tag`` set ⇒ only credits carrying exactly that
              tag (a key-region build spends only its own region's credits);
              None ⇒ any credit, tagged or untagged.
            - product: exact match — an iron credit cannot fund a steel build.
            - owner: from the cutoff year an ordinary withdrawal may spend only
              the withdrawer's own credits, with pre-cutoff vintages treated per
              the banked-credit rule; unowned credits always stay drawable.

        ``single_owner=True`` (greenfield) replaces the owner filter: the whole
        withdrawal is served from one holder's credits — the holder whose oldest
        applicable credit sits earliest in the queue, among those holding
        enough. The unowned pot counts as one such holder; mixing holders is not
        allowed. A greenfield build can therefore fail with an ample pool when
        no single holder covers it, which ``blocked_reason`` distinguishes from
        a short pool.

        Args:
            amount_mt: Capacity to withdraw, in Mt. Any emission-intense
                penalty is applied by the caller before this point — the pool
                consumes exactly what it is asked for.
            region_tag: Required credit tag, or None for an unrestricted build.
            product: ``"iron"`` or ``"steel"``.
            owner_id: Withdrawing PlantGroup.plant_group_id.
            year: Decision year; activates the owner filter from the cutoff.
            single_owner: Serve the withdrawal from exactly one holder
                (greenfield rule, uniform across the run).

        Returns:
            WithdrawResult. Grants are all-or-nothing: nothing is consumed
            unless the full amount is available under the rules above.
        """

        def applicable(credit: Credit) -> bool:
            return self._is_applicable(credit, region_tag, product, owner_id, year, single_owner)

        applicable_credits = [c for c in self._credits if applicable(c)]
        if sum(c.amount_mt for c in applicable_credits) < amount_mt:
            return WithdrawResult(
                granted=False,
                credits_consumed=(),
                attributed_owner_id=None,
                blocked_reason="insufficient_applicable_pool",
            )

        if single_owner:
            found, holder = self._select_single_holder(applicable_credits, amount_mt)
            if not found:
                return WithdrawResult(
                    granted=False,
                    credits_consumed=(),
                    attributed_owner_id=None,
                    blocked_reason="no_single_owner_with_sufficient_credits",
                )
            consumed = self._consume(amount_mt, lambda c: c.owner_id == holder and applicable(c))
            return WithdrawResult(
                granted=True,
                credits_consumed=tuple(consumed),
                attributed_owner_id=holder,
                blocked_reason=None,
            )

        consumed = self._consume(amount_mt, applicable)
        return WithdrawResult(
            granted=True,
            credits_consumed=tuple(consumed),
            attributed_owner_id=None,
            blocked_reason=None,
        )

    def seed_from(self, entries: Iterable[SeedEntry], key_regions: Mapping[str, str]) -> None:
        """Bulk-deposit historical retirements, deriving region tags at load time.

        Args:
            entries: Historical retirements; deposited in vintage order so the
                queue stays age-ordered regardless of input order.
            key_regions: geo_key → cluster name for the key provinces; an entry
                whose geo_key is a key province deposits a credit tagged with
                its cluster name, otherwise untagged.
        """
        ordered = sorted(entries, key=lambda e: e.vintage_year)
        for entry in ordered:
            self.deposit(
                Credit(
                    amount_mt=entry.amount_mt,
                    vintage_year=entry.vintage_year,
                    region_tag=key_regions.get(entry.geo_key),
                    owner_id=entry.owner_id,
                    product=entry.product,
                )
            )
        unowned = sum(1 for entry in ordered if entry.owner_id is None)
        if unowned:
            logger.warning(
                "[CAPACITY POOL] %d of %d seeded credit(s) name no owner; "
                "they stay freely drawable regardless of the banked-credit rule",
                unowned,
                len(ordered),
            )
        logger.info(
            "[CAPACITY POOL] seeded total_mt=%.3f credits=%d by_tag=%s",
            self.total(),
            len(self._credits),
            self.total_by_tag(),
        )

    def snapshot(self) -> tuple[Credit, ...]:
        """Return the current queue, oldest first, for diagnostics."""
        return tuple(self._credits)

    def total(self) -> float:
        """Return the total banked capacity in Mt."""
        return sum(c.amount_mt for c in self._credits)

    def total_by_tag(self) -> dict[str | None, float]:
        """Return banked capacity in Mt keyed by region tag (None = untagged)."""
        totals: dict[str | None, float] = {}
        for c in self._credits:
            totals[c.region_tag] = totals.get(c.region_tag, 0.0) + c.amount_mt
        return totals

    def _is_applicable(
        self,
        credit: Credit,
        region_tag: str | None,
        product: str,
        owner_id: str,
        year: int,
        single_owner: bool,
    ) -> bool:
        """Apply the applicable-pool filters to one credit."""
        if region_tag is not None and credit.region_tag != region_tag:
            return False
        if credit.product != product:
            return False
        if credit.owner_id is None:
            # Unowned seeded credits stay drawable under every banked-credit rule —
            # silently deleting the seed at the boundary would be an artefact
            return True
        cutoff = self.inter_company_swap_cutoff_year
        if cutoff is None or year < cutoff:
            return True
        pre_cutoff_vintage = credit.vintage_year < cutoff
        if pre_cutoff_vintage and self.banked_credit_rule == "expire":
            return False
        if single_owner:
            # The one-holder restriction is enforced by selection, not per credit
            return True
        if pre_cutoff_vintage and self.banked_credit_rule == "persist":
            return True
        return credit.owner_id == owner_id

    def _consume(self, amount_mt: float, wanted: Callable[[Credit], bool]) -> list[Credit]:
        """Consume ``amount_mt`` FIFO from credits matching ``wanted``.

        Callers have already established sufficiency; the queue is rebuilt so a
        partially-consumed credit keeps its original vintage, tag and owner.
        """
        consumed: list[Credit] = []
        remaining = amount_mt
        kept: list[Credit] = []
        for credit in self._credits:
            if remaining > 0 and wanted(credit):
                take = min(credit.amount_mt, remaining)
                consumed.append(replace(credit, amount_mt=take))
                remaining -= take
                if take < credit.amount_mt:
                    kept.append(replace(credit, amount_mt=credit.amount_mt - take))
            else:
                kept.append(credit)
        self._credits = kept
        return consumed

    @staticmethod
    def _select_single_holder(applicable_credits: list[Credit], amount_mt: float) -> tuple[bool, str | None]:
        """Pick the holder to serve a single-owner withdrawal.

        Among holders whose applicable credits cover ``amount_mt``, choose the
        one whose oldest applicable credit sits earliest in the queue —
        ``applicable_credits`` is queue-ordered, so first appearance is oldest.
        The unowned pot (owner None) is a holder like any other.

        Returns:
            ``(True, holder)`` when a holder qualifies, else ``(False, None)``.
        """
        totals: dict[str | None, float] = {}
        first_seen: list[str | None] = []
        for credit in applicable_credits:
            if credit.owner_id not in totals:
                first_seen.append(credit.owner_id)
            totals[credit.owner_id] = totals.get(credit.owner_id, 0.0) + credit.amount_mt
        for holder in first_seen:
            if totals[holder] >= amount_mt:
                return True, holder
        return False, None
