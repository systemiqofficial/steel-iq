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
        min_utilization_for_renovation: Utilisation floor below which a group
            is not eligible for renovation.
        utilization_window_years: Years of utilisation history the renovation
            gate averages over.
        inter_company_swap_cutoff_year: First year a company may only spend
            its own credits; None disables the owner partition.
        banked_credit_rule: Treatment of pre-cutoff credits from the cutoff
            year on — ``"reassign"``, ``"persist"`` or ``"expire"``.
        reline_counts_as_replace: Whether a reline is treated as a replacement
            rather than as neutral.

    Raises:
        ValueError: On an unknown ``banked_credit_rule``, a non-positive ratio
            or window, or a utilisation floor outside [0, 1].
    """

    enabled: bool = False
    replacement_ratio: float = 1.5
    emission_intense_penalty_divisor: float = 1.5
    min_utilization_for_renovation: float = 0.25
    utilization_window_years: int = 2
    inter_company_swap_cutoff_year: int | None = 2028
    banked_credit_rule: str = "reassign"
    reline_counts_as_replace: bool = False

    def __post_init__(self) -> None:
        if self.banked_credit_rule not in BANKED_CREDIT_RULES:
            raise ValueError(
                f"Unknown banked_credit_rule {self.banked_credit_rule!r}; expected one of {BANKED_CREDIT_RULES}"
            )
        if self.replacement_ratio <= 0:
            raise ValueError(f"replacement_ratio must be positive, got {self.replacement_ratio}")
        if self.emission_intense_penalty_divisor <= 0:
            raise ValueError(
                f"emission_intense_penalty_divisor must be positive, got {self.emission_intense_penalty_divisor}"
            )
        if self.utilization_window_years <= 0:
            raise ValueError(f"utilization_window_years must be positive, got {self.utilization_window_years}")
        if not 0 <= self.min_utilization_for_renovation <= 1:
            raise ValueError(
                f"min_utilization_for_renovation must be within [0, 1], got {self.min_utilization_for_renovation}"
            )
