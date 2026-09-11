"""Run-behaviour parameters for the China capacity-replacement policy.

Nested on ``SimulationConfig`` as ``capacity_policy`` and read at run time as
``bus.env.config.capacity_policy.<field>``. Lever meanings:
docs/domain_simulation_logic/capacity_replacement_policy.md#configuration.
"""

from dataclasses import dataclass

from .pool import BANKED_CREDIT_RULES


@dataclass
class CapacityPolicyConfig:
    """Scenario levers for the capacity pool; the data sheets are facts, the flag is scenario intent.

    Attributes:
        enabled: Feature flag, off by default. Bootstrap refuses ``True``
            without complete fixtures rather than falling back to dormancy.
        replacement_ratio: Old-to-new capacity ratio for a penalised
            replacement; the furnace group shrinks to ``C_old / ratio``.
        emission_intense_penalty_divisor: Divisor on an emission-intense new
            build's permitted capacity; a separate lever sharing the 1.5 default.
        min_utilization_for_renovation: Utilisation floor of the gate before
            branch ②; applies to renovations and replacements alike.
        utilization_window_years: Consecutive recorded years at or below the
            floor that make a group ineligible.
        inter_company_swap_cutoff_year: First year a company may only spend
            its own credits; None disables the owner partition.
        banked_credit_rule: Treatment of pre-cutoff credits from the cutoff
            on: ``"reassign"`` (stay with their depositor, unowned ones swept),
            ``"persist"`` (freely spendable) or ``"expire"`` (unusable).
        credit_validity_years: Shelf life of a banked credit; a vintage ``V``
            credit is usable through ``V + N − 1``. None never expires.
        capacity_pool_max_retry_years: Blocked years (counted by the pre-draw
            probe, not by draws) after which a considered greenfield is discarded.
        renovation_counts_as_replace: Whether a same-technology renovation is
            a full REPLACE (gate, ratio, shrink, deposit). ``False`` exempts it
            from the ratio alone.

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
