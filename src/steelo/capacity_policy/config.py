"""Run-behaviour parameters for the China capacity-replacement policy.

Nested onto ``SimulationConfig`` as the ``capacity_policy`` field, following
the ``GeoConfig`` precedent, and read at runtime as
``bus.env.config.capacity_policy.<field>``. Defined inside the package so
deleting the module removes one import and one field.
"""

from dataclasses import dataclass

from .pool import BANKED_CREDIT_RULES


@dataclass
class CapacityPolicyConfig:
    """Scenario levers for the capacity pool.

    ``enabled`` is the on/off switch: the data sheets are facts, the flag is
    scenario intent. With the flag off the module is dormant and behaviour is
    byte-identical to a build without it; bootstrap refuses ``enabled=True``
    with missing fixtures rather than falling back to silent dormancy.

    Attributes:
        enabled: Feature flag — off by default.
        replacement_ratio: Old-to-new capacity ratio for a penalised
            replacement; the furnace group shrinks to ``C_old / ratio``.
        emission_intense_penalty_divisor: Divisor applied to an
            emission-intense new build's permitted capacity — a separate lever
            that happens to share the 1.5 default.
        min_utilization_for_renovation: Utilisation floor; a group at or
            below it for the whole window is not eligible for renovation, nor
            for replacement, whatever ``renovation_counts_as_replace`` says.
        utilization_window_years: Number of consecutive recorded years at or
            below the floor that make a group ineligible for renovation.
        inter_company_swap_cutoff_year: First year a company may only spend
            its own credits; None disables the owner partition.
        banked_credit_rule: Treatment of pre-cutoff credits from the cutoff
            year on (options: ``"reassign"``, ``"persist"`` or ``"expire"``).
            ``"reassign"`` subjects them to the owner filter like any
            other credit, so each stays with its depositor and the unowned
            opening pool — which has no depositor to stay with — is swept.
            ``"persist"`` grandfathers them as freely spendable by anyone,
            unowned included, so the partition bites only on later deposits.
            ``"expire"`` makes them unusable by everyone, leaving only
            post-cutoff deposits spendable, each by its own depositor.
        credit_validity_years: Shelf life of a banked credit; a vintage ``V``
            credit is usable through ``V + N − 1`` and purged on entering
            ``V + N``. None (the default) never expires. A different concept
            from ``banked_credit_rule="expire"``, which is a withdrawal-time
            applicability rule over the ownership cutoff, not an age rule.
        capacity_pool_max_retry_years: Cumulative years the capacity gate may
            block a considered greenfield before the opportunity is discarded.
            Counted per blocked *year* via the non-consuming pre-draw probe —
            not per blocked announcement draw — so the cap means what it says
            regardless of the announcement probability.
        renovation_counts_as_replace: Whether a same-technology renovation is
            a full REPLACE, paying the replacement ratio like any switch — the
            default, because the tree's ② branch draws no
            same-technology exemption. ``False`` exempts a renovation from the
            ratio alone; the utilisation gate applies either way.

    Raises:
        ValueError: On an unknown ``banked_credit_rule``, a ratio or divisor
            below 1, a non-positive window, validity or retry cap, or a
            utilisation floor outside [0, 1].
    """

    enabled: bool = False
    replacement_ratio: float = 1.5
    emission_intense_penalty_divisor: float = 1.5
    min_utilization_for_renovation: float = 0.25
    utilization_window_years: int = 2
    inter_company_swap_cutoff_year: int | None = 2028
    banked_credit_rule: str = "reassign"
    credit_validity_years: int | None = None
    capacity_pool_max_retry_years: int = 2
    renovation_counts_as_replace: bool = True

    def __post_init__(self) -> None:
        if self.banked_credit_rule not in BANKED_CREDIT_RULES:
            raise ValueError(
                f"Unknown banked_credit_rule {self.banked_credit_rule!r}; expected one of {BANKED_CREDIT_RULES}"
            )
        # Below one, a replacement would grow the group and an intense build would exceed its withdrawal
        if self.replacement_ratio < 1:
            raise ValueError(f"replacement_ratio must be at least 1, got {self.replacement_ratio}")
        if self.emission_intense_penalty_divisor < 1:
            raise ValueError(
                f"emission_intense_penalty_divisor must be at least 1, got {self.emission_intense_penalty_divisor}"
            )
        if self.utilization_window_years <= 0:
            raise ValueError(f"utilization_window_years must be positive, got {self.utilization_window_years}")
        if self.credit_validity_years is not None and self.credit_validity_years <= 0:
            raise ValueError(f"credit_validity_years must be positive when set, got {self.credit_validity_years}")
        if self.capacity_pool_max_retry_years <= 0:
            raise ValueError(
                f"capacity_pool_max_retry_years must be positive, got {self.capacity_pool_max_retry_years}"
            )
        if not 0 <= self.min_utilization_for_renovation <= 1:
            raise ValueError(
                f"min_utilization_for_renovation must be within [0, 1], got {self.min_utilization_for_renovation}"
            )
