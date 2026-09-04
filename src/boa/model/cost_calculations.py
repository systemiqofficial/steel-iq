import numpy as np
import logging
from boa.config.settings import LIFETIMES, BATTERY_UNIT_CAPEX_SCALING_FACTOR
from boa.config.constants import HOURS_IN_YEAR, AVERAGE_IMPLIED_STORAGE
from boa.model.bisection import GAMMA, CostCoefficients


def _lcoe_tech_weights(
    discount_factors: np.ndarray, opex_pct: float, lifetime: int, horizon: int
) -> tuple[float, float]:
    """
    Per-technology CAPEX+OPEX weights so that, for a tech with a flat year-0/horizon CAPEX
    structure, the discounted lifetime cost is:

        total_tech_cost = installed_capacity * (w0 * capex[0] + wH * capex[horizon])

    This is the closed form of a technology's discounted CAPEX/OPEX accumulation over the
    investment horizon, including discounted reinstallation of shorter-lived technologies.
    With horizon == lifetime, wH == 0.
    """
    s0 = discount_factors[1 : lifetime + 1].sum()
    w0 = 1.0 + opex_pct * s0
    wH = 0.0
    if horizon > lifetime:
        num_reinstallations = (horizon + lifetime - 1) // lifetime - 1
        for i in range(1, num_reinstallations + 1):
            install_year = i * lifetime
            wH += discount_factors[install_year]
            wH += opex_pct * discount_factors[install_year + 1 : install_year + lifetime + 1].sum()
    return w0, wH


def lcoe_coefficients(
    investment_horizon: int,
    capex: dict,
    opex_pct: dict,
    cost_of_capital: float,
    baseload_demand: float,
) -> CostCoefficients:
    """
    The four scalars one (year, cost-key) combination's LCOE collapses to:

        LCOE(s, w, b) = (a_s*s + a_w*w + a_b*b**GAMMA) / (d0 * served_fraction)

    with `s`/`w` solar/wind overscale (installed MW / baseload) and `b` battery in
    baseload-hours (installed MWh / baseload). Closed form of the CAPEX/OPEX/reinstallation
    accumulation `_lcoe_tech_weights` already expresses, rearranged so a cached `(s, w, b)`
    design reprices in four multiplications with no dispatch -- see `boa.model.bisection`,
    "The objective, in closed form".

    Every coefficient scales linearly with `baseload_demand`, which is what makes LCOE
    exactly baseload-invariant rather than approximately so.
    """
    discount_factors = np.array([1.0 / (1.0 + cost_of_capital) ** t for t in range(investment_horizon + 1)])
    w0, wH = {}, {}
    for tech in ("solar", "wind", "battery"):
        w0[tech], wH[tech] = _lcoe_tech_weights(discount_factors, opex_pct[tech], LIFETIMES[tech], investment_horizon)

    a_s = baseload_demand * (w0["solar"] * capex["solar"][0] + wH["solar"] * capex["solar"][investment_horizon])
    a_w = baseload_demand * (w0["wind"] * capex["wind"][0] + wH["wind"] * capex["wind"][investment_horizon])
    # AVERAGE_IMPLIED_STORAGE**(1-GAMMA) is the modular battery-CAPEX correction's
    # normalisation constant, folded in here so a_b*b**GAMMA reproduces
    # capex * (b/AVERAGE_IMPLIED_STORAGE)**BATTERY_UNIT_CAPEX_SCALING_FACTOR * b exactly
    # (see the vectorised pricer's own battery term, which this replaces).
    a_b = (
        baseload_demand
        * AVERAGE_IMPLIED_STORAGE ** (1.0 - GAMMA)
        * (w0["battery"] * capex["battery"][0] + wH["battery"] * capex["battery"][investment_horizon])
    )
    d0 = baseload_demand * HOURS_IN_YEAR * discount_factors[1 : investment_horizon + 1].sum()
    return CostCoefficients(a_s=a_s, a_w=a_w, a_b=a_b, d0=d0)


def calculate_lcoe_of_re_installation_vectorised(
    investment_horizon: int,
    installed_solar: np.ndarray,
    installed_wind: np.ndarray,
    installed_battery: np.ndarray,
    baseload_demand: float,
    capex: dict,
    opex_pct: dict,
    cost_of_capital: float,
    realised_delivery_fraction: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Closed-form LCOE evaluated for many designs at once, dividing by *delivered* electricity
    (curtailment-aware): the discounted-sold-electricity denominator is `sum_t(discount[t] *
    baseload_demand * HOURS_IN_YEAR)` — a single horizon-wide scalar. Per-design `realised_delivery_fraction`
    (0..1) factors out as a multiplier on that scalar, giving an (n,) array of denominators in one
    broadcast (no per-design Python loop, no per-design summation). The numerator (discounted
    CAPEX+OPEX) stays linear in installed solar/wind capacity and a simple per-design power law in
    battery capacity (the modular CAPEX correction).

    Inputs are arrays of installed capacity per design: solar/wind in MW, battery in MWh.

    `realised_delivery_fraction` is a (n,) array of fractions in [0, 1] giving the share of baseload
    demand each design actually serves. Producers may pass binary hours-coverage (what the bulk argmin
    path uses today) or a dispatch-aware served-energy fraction (used at output time to refine the
    picked optimum's LCOE — battery contribution to partial-coverage hours included). Passing `None`
    defaults to ones — reproducing the historical (biased-low) behaviour — and emits a warning.

    Returns (lcoes, installation_costs, install_cost_solar, install_cost_wind, install_cost_battery)
    where the last three are the year-0 installation cost components (battery uses the modular-corrected
    CAPEX; see `installation_cost_breakdown` for the equivalent one-design form).
    """
    n = installed_solar.shape[0]
    if realised_delivery_fraction is None:
        logging.warning(
            "calculate_lcoe_of_re_installation_vectorised called without realised_delivery_fraction; "
            "defaulting to ones. LCOE will be biased low by the unmet-demand fraction — pass "
            "a served-energy fraction to remove this bias."
        )
        realised_delivery_fraction = np.ones(n)

    coeffs = lcoe_coefficients(investment_horizon, capex, opex_pct, cost_of_capital, baseload_demand)
    denominator = coeffs.d0 * realised_delivery_fraction

    s_overscale = installed_solar / baseload_demand
    w_overscale = installed_wind / baseload_demand
    b_overscale = installed_battery / baseload_demand  # baseload-hours

    # b_overscale == 0 needs no special case here: 0**GAMMA == 0, unlike the old
    # ratio**BATTERY_UNIT_CAPEX_SCALING_FACTOR form (negative exponent) it replaces.
    numerator = coeffs.a_s * s_overscale + coeffs.a_w * w_overscale + coeffs.a_b * np.power(b_overscale, GAMMA)
    lcoes = numerator / denominator

    # Installation-cost breakdown: year-0 cost per tech, battery still via the modular CAPEX
    # correction directly (not through the closed form, which only prices the LCOE numerator).
    ratio = b_overscale / AVERAGE_IMPLIED_STORAGE
    ratio[ratio <= 0] = 1.0  # battery==0 designs contribute 0 (installed==0); avoid 0**negative warning
    corrected_capex0 = capex["battery"][0] * ratio**BATTERY_UNIT_CAPEX_SCALING_FACTOR

    install_solar = installed_solar * capex["solar"][0]
    install_wind = installed_wind * capex["wind"][0]
    install_battery = installed_battery * corrected_capex0
    installation_costs = install_solar + install_wind + install_battery
    return lcoes, installation_costs, install_solar, install_wind, install_battery


def installation_cost_breakdown(
    solar: float,
    wind: float,
    battery: float,
    baseload_demand: float,
    capex: dict,
) -> tuple[float, float, float, float]:
    """
    Year-0 installation cost of one dimensionless design, as `(total, solar, wind, battery)`.

    `solar`/`wind` are overscale factors and `battery` is in baseload-hours, so each is
    multiplied by `baseload_demand` to reach installed MW and MWh.

    Split out because the grid-bisection query already knows its winner's LCOE and needs only
    the cost breakdown, where `calculate_lcoe_of_re_installation_vectorised` would reprice a
    one-element population to get there. The battery still goes through the modular CAPEX
    correction directly rather than the closed form, which prices the LCOE numerator only --
    the same reason the vectorised pricer keeps a separate path for it.
    """
    installed_solar = solar * baseload_demand
    installed_wind = wind * baseload_demand
    installed_battery = battery * baseload_demand

    # A zero battery installs nothing, so it contributes nothing; guarding here avoids raising
    # zero to a negative exponent to compute a factor that is then multiplied by zero.
    ratio = installed_battery / (baseload_demand * AVERAGE_IMPLIED_STORAGE) if installed_battery > 0 else 1.0
    corrected_capex0 = capex["battery"][0] * ratio**BATTERY_UNIT_CAPEX_SCALING_FACTOR

    install_solar = installed_solar * capex["solar"][0]
    install_wind = installed_wind * capex["wind"][0]
    install_battery = installed_battery * corrected_capex0
    return (
        float(install_solar + install_wind + install_battery),
        float(install_solar),
        float(install_wind),
        float(install_battery),
    )
