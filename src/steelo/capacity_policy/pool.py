"""National retirement-credit pool for China's capacity-replacement policy.

Capacities flow in model tonnes at run time; the ``amount_mt`` names are a
sheet-side convention. Filter rules, purges and units:
docs/domain_simulation_logic/capacity_replacement_policy_reference.md#pool-arithmetic-and-units.
"""

import bisect
import logging
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

BANKED_CREDIT_RULES = ("reassign", "persist", "expire")

# Relative slack on the all-or-nothing sufficiency checks
_REL_TOL = 1e-9


@dataclass(frozen=True)
class Credit:
    """A retirement credit: freed capacity that can fund a later build.

    Attributes:
        amount_mt: Freed capacity (model tonnes at run time).
        vintage_year: Year the capacity was retired; consumed oldest first.
        region_tag: Cluster name of the key region the retirement happened in,
            else None (untagged).
        owner_id: Depositing plant group id, or None for an unowned opening credit.
        product: ``"iron"`` or ``"steel"``; separate stocks.
    """

    amount_mt: float
    vintage_year: int
    region_tag: str | None
    owner_id: str | None
    product: str


@dataclass(frozen=True)
class SeedEntry:
    """An opening credit before seeding; its region tag is derived from ``geo_key`` at seed time."""

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
        credits_consumed: FIFO-ordered consumed portions; a partially consumed
            credit appears with just the consumed share.
        attributed_owner_id: The holder drawn from on a ``single_owner``
            withdrawal (None for the unowned pot); always None otherwise.
        blocked_reason: None when granted, else
            ``"insufficient_applicable_pool"`` or
            ``"no_single_owner_with_sufficient_credits"``.
    """

    granted: bool
    credits_consumed: tuple[Credit, ...]
    attributed_owner_id: str | None
    blocked_reason: str | None


class CapacityPool:
    """Age-ordered queue of retirement credits with filtered FIFO withdrawal.

    Withdrawals see only the applicable pool (region, product and owner
    filters) and are all-or-nothing. A partially consumed credit splits and
    the remainder keeps its vintage, so it holds its place in the queue.
    """

    def __init__(
        self,
        *,
        inter_company_swap_cutoff_year: int | None = 2028,
        banked_credit_rule: str = "reassign",
        credit_validity_years: int | None = None,
    ) -> None:
        """
        Args:
            inter_company_swap_cutoff_year: First year a company may only spend
                credits it deposited. None disables the partition.
            banked_credit_rule: Treatment of pre-cutoff credits from the cutoff
                on: ``"reassign"`` (owner filter applies), ``"persist"``
                (freely spendable) or ``"expire"`` (unusable). A withdrawal-time
                rule, not an age rule.
            credit_validity_years: Years a credit may sit banked before it is
                purged. None never expires.

        Raises:
            ValueError: On an unknown ``banked_credit_rule``.
        """
        if banked_credit_rule not in BANKED_CREDIT_RULES:
            raise ValueError(
                f"Unknown banked_credit_rule {banked_credit_rule!r}; expected one of {BANKED_CREDIT_RULES}"
            )
        self.inter_company_swap_cutoff_year = inter_company_swap_cutoff_year
        self.banked_credit_rule = banked_credit_rule
        self.credit_validity_years = credit_validity_years
        self._credits: list[Credit] = []

    def deposit(self, credit: Credit) -> None:
        """Append a credit; deposits arrive in year order, so the queue stays age-ordered.

        Args:
            credit: The retirement credit to bank.

        Raises:
            ValueError: If the credit amount is not positive.
        """
        if credit.amount_mt <= 0:
            raise ValueError(f"Credit amount must be positive, got {credit.amount_mt}")
        self._credits.append(credit)

    def refund(self, credit: Credit) -> None:
        """Hand back a consumed slice whose build never happened.

        Inserted after every credit of the same or older vintage, not appended,
        so the slice resumes the FIFO position it left.

        Args:
            credit: The consumed portion, original vintage intact; its shelf
                life keeps running from that vintage.

        Raises:
            ValueError: If the credit amount is not positive.
        """
        if credit.amount_mt <= 0:
            raise ValueError(f"Credit amount must be positive, got {credit.amount_mt}")
        position = bisect.bisect_right(self._credits, credit.vintage_year, key=lambda c: c.vintage_year)
        self._credits.insert(position, credit)

    def purge_expired(self, year: int) -> list[Credit]:
        """Remove every credit whose shelf life has run out on entering ``year``.

        A vintage ``V`` credit is usable through ``V + validity − 1``. Unlike
        ``banked_credit_rule="expire"`` this is a real sweep, so ``total()`` and
        ``snapshot()`` stay honest; it applies to unowned credits too.

        Args:
            year: The year being entered.

        Returns:
            The removed credits; empty when no shelf life is configured.
        """
        validity = self.credit_validity_years
        if validity is None:
            return []
        expired = [credit for credit in self._credits if credit.vintage_year + validity <= year]
        if not expired:
            return []
        self._credits = [credit for credit in self._credits if credit.vintage_year + validity > year]
        logger.info(
            "[CAPACITY POOL] purged expired credits year=%d credits=%d expired_mt=%.3f remaining_mt=%.3f",
            year,
            len(expired),
            sum(credit.amount_mt for credit in expired),
            self.total(),
        )
        return expired

    def purge_unowned(self, year: int) -> list[Credit]:
        """Remove every unowned credit once ``year`` has reached the swap cutoff.

        Nobody can spend an unowned credit from the cutoff (no depositor to
        keep it), except under ``"persist"``, so it is swept rather than left
        inflating the totals.

        Args:
            year: The year being entered.

        Returns:
            The removed credits; empty before the cutoff, with no cutoff
            configured, or under persist.
        """
        cutoff = self.inter_company_swap_cutoff_year
        if cutoff is None or year < cutoff or self.banked_credit_rule == "persist":
            return []
        purged = [credit for credit in self._credits if credit.owner_id is None]
        if not purged:
            return []
        self._credits = [credit for credit in self._credits if credit.owner_id is not None]
        logger.info(
            "[CAPACITY POOL] purged unowned credits at the swap cutoff year=%d credits=%d "
            "purged_mt=%.3f remaining_mt=%.3f",
            year,
            len(purged),
            sum(credit.amount_mt for credit in purged),
            self.total(),
        )
        return purged

    def can_withdraw(
        self,
        amount_mt: float,
        region_tag: str | None,
        product: str,
        owner_id: str,
        year: int,
        single_owner: bool = False,
    ) -> str | None:
        """Non-consuming check: would :meth:`try_withdraw` grant this?

        Args:
            Same as :meth:`try_withdraw`.

        Returns:
            None when the withdrawal would be granted, else the
            ``blocked_reason`` it would be refused with.
        """
        applicable_credits = [
            c for c in self._credits if self._is_applicable(c, region_tag, product, owner_id, year, single_owner)
        ]
        if sum(c.amount_mt for c in applicable_credits) < amount_mt * (1.0 - _REL_TOL):
            return "insufficient_applicable_pool"
        if single_owner:
            found, _ = self._select_single_holder(applicable_credits, amount_mt)
            if not found:
                return "no_single_owner_with_sufficient_credits"
        return None

    def try_withdraw(
        self,
        amount_mt: float,
        region_tag: str | None,
        product: str,
        owner_id: str,
        year: int,
        single_owner: bool = False,
    ) -> WithdrawResult:
        """Consume ``amount_mt`` from the applicable pool, oldest first, all or nothing.

        With ``single_owner=True`` (greenfield) the whole withdrawal is served
        by one holder: the one whose oldest applicable credit sits earliest in
        the queue among those holding enough, the unowned pot counting as a
        holder.

        Args:
            amount_mt: Capacity to withdraw; any emission-intense penalty is
                applied by the caller, the pool consumes exactly what it is asked.
            region_tag: Required credit tag, or None for an unrestricted build.
            product: ``"iron"`` or ``"steel"``.
            owner_id: Withdrawing plant group id.
            year: Decision year; activates the owner filter from the cutoff.
            single_owner: Serve the withdrawal from exactly one holder.

        Returns:
            The result; nothing is consumed unless the full amount is available.
        """

        def applicable(credit: Credit) -> bool:
            return self._is_applicable(credit, region_tag, product, owner_id, year, single_owner)

        blocked_reason = self.can_withdraw(amount_mt, region_tag, product, owner_id, year, single_owner)
        if blocked_reason is not None:
            return WithdrawResult(
                granted=False,
                credits_consumed=(),
                attributed_owner_id=None,
                blocked_reason=blocked_reason,
            )

        if single_owner:
            applicable_credits = [c for c in self._credits if applicable(c)]
            _, holder = self._select_single_holder(applicable_credits, amount_mt)
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
        """Bulk-deposit opening credits in vintage order, deriving region tags from ``key_regions``.

        Args:
            entries: Opening credits; deposited in vintage order regardless of input order.
            key_regions: geo_key → cluster name for the key provinces.
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
                "they are freely drawable before the swap cutoff and purged at it",
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
        """Return the total banked capacity."""
        return sum(c.amount_mt for c in self._credits)

    def total_by_tag(self) -> dict[str | None, float]:
        """Return banked capacity keyed by region tag (None = untagged)."""
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
        cutoff = self.inter_company_swap_cutoff_year
        if cutoff is None or year < cutoff:
            return True
        pre_cutoff_vintage = credit.vintage_year < cutoff
        if pre_cutoff_vintage and self.banked_credit_rule == "persist":
            return True
        if pre_cutoff_vintage and self.banked_credit_rule == "expire":
            return False
        if credit.owner_id is None:
            # Only opening-pool credits are unowned; with no depositor to keep
            # them they are unspendable from the cutoff
            return False
        if single_owner:
            # The one-holder restriction is enforced by selection, not per credit
            return True
        return credit.owner_id == owner_id

    def _consume(self, amount_mt: float, wanted: Callable[[Credit], bool]) -> list[Credit]:
        """Consume ``amount_mt`` FIFO from credits matching ``wanted``; callers established sufficiency."""
        consumed: list[Credit] = []
        remaining = amount_mt
        kept: list[Credit] = []
        for credit in self._credits:
            if remaining > amount_mt * _REL_TOL and wanted(credit):
                take = min(credit.amount_mt, remaining)
                if credit.amount_mt - take <= credit.amount_mt * _REL_TOL:
                    take = credit.amount_mt
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

        ``applicable_credits`` is queue-ordered, so the first holder seen with
        enough is the one whose oldest credit comes first; owner None is the
        unowned pot.

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
            if totals[holder] >= amount_mt * (1.0 - _REL_TOL):
                return True, holder
        return False, None
