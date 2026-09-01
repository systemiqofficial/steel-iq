import numpy as np
import logging
from boa.config.settings import (
    LIFETIMES,
    YEARLY_DETERIORATION_RATES,
    BATTERY_UNIT_CAPEX_SCALING_FACTOR,
    MAINTENANCE_DOWNTIME_DAYS,
)
from boa.config.constants import HOURS_IN_YEAR, DAYS_IN_YEAR, HOURS_IN_DAY, AVERAGE_IMPLIED_STORAGE
from boa.model.bisection import GAMMA, CostCoefficients


def calculate_installation_cost(
    C_s: float, C_w: float, C_b: float, cost_solar: float, cost_wind: float, cost_battery: float
) -> dict[str, float]:
    """
    Calculate per-technology installation cost components. Total installation cost is the sum of the returned values.

    Parameters:
        C_s: Installed solar capacity (MW).
        C_w: Installed wind capacity (MW).
        C_b: Installed battery capacity (MWh — storage is energy, not power).
            Callers pass `battery_factor[h] × baseload[MW]`.
        cost_solar: Solar CAPEX ($/MW).
        cost_wind:  Wind CAPEX ($/MW).
        cost_battery: Battery CAPEX ($/MWh).

    Returns:
        Dict with per-tech installation cost in $ for keys 'solar', 'wind', 'battery'.
    """
    return {
        "solar": cost_solar * C_s,
        "wind": cost_wind * C_w,
        "battery": cost_battery * C_b,
    }


def calculate_generated_electricity_in_period(
    time_period: int,
    re_potential: float | np.ndarray | None = 1.0,
    installed_capacity: int = 1,
    deterioration_rate: float = 0,
    downtime: int = 0,
) -> np.ndarray:
    """
    Calculates the yearly generated electricity in MWh over a given time period, taking into account its deterioration. Note: must decrease with time.
    yearly_generated_electricity_t = yearly_re_potential * installed_capacity * (1 - deterioration_rate)^t * (1 - downtime / DAYS_IN_YEAR) for t in [0, time_period]

    Parameters:
        re_potential: ratio between total generated electricity and total electricity we could potentially generate if all installed renewable energy (RE) capacity
        was running at its fullest (%). Two formats allowed: Hourly time series for one reference year (profile) or annual average (capacity factor).
        time_period: number of years to calculate the generated electricity for (y).
        installed_capacity: initially installed capacity for a certain technology (MW).
        deterioration_rate: rate at which the capacity deteriorates with each year that passes by (%).
        downtime: number of days per year the capacity is not available (e.g. for maintenance) (days).

    Assumption: Battery degradation is represented implicitly - assuming it goes at the same (or slower) pace than wind and solar.

    Returns:
        np.ndarray: Yearly generated electricity for each year in the time period in MW. Note: must decrease over time!
    """
    if time_period < 1:
        raise ValueError("Time period must be greater than 0.")

    # Deterioration of the capacity over time
    actual_capacity = np.zeros(time_period)
    actual_capacity[0] = installed_capacity
    for i in range(1, time_period):
        actual_capacity[i] = actual_capacity[i - 1] * (1 - deterioration_rate)

    # Downtime correction
    downtime_correction = 1 - (downtime / DAYS_IN_YEAR)
    operational_capacity = actual_capacity * downtime_correction

    # Capacity factor
    if isinstance(re_potential, (float, np.float64)):
        capacity_factor = re_potential
    elif isinstance(re_potential, (np.ndarray, list)):
        if len(re_potential) not in range(HOURS_IN_YEAR, HOURS_IN_YEAR + HOURS_IN_DAY):
            raise ValueError("Yearly RE potential must be hourly")
        else:
            capacity_factor = np.mean(re_potential)
    else:
        raise ValueError("Invalid type for re_potential. Must be float or numpy array.")

    # Yearly generated electricity
    yearly_generated_electricity = operational_capacity * capacity_factor * HOURS_IN_YEAR

    return yearly_generated_electricity


def calculate_lcoe_of_re_installation(
    investment_horizon: int,
    installed_capacity: dict,
    baseload_demand: float,
    capex: dict,
    opex_pct: dict,
    renewable_energy_profile: dict,
    cost_of_capital: float,
    use_curtailment: bool = False,
    realised_delivery_fraction: float | None = None,
) -> float:
    """
    [REFERENCE IMPLEMENTATION — no current callers in production; called by the reference
    `filter_designs_according_to_coverage_and_calculate_costs` and directly by
    tests/test_lcoe.py]

    Replaced in production by `calculate_lcoe_of_re_installation_vectorised`, which computes
    the same closed-form result across all designs at once (parity `max_rel < 1e-12`, float
    reassociation only). Kept as the readable scalar spec of the baseload-supply LCOE (final
    model version).

    Calculates the levelized cost of electricity (LCOE) for a given investment horizon (IH) and installed capacity (USD/MWh).
    lcoe = (capex_0 * (1 + fixed_opex_percentage * sum_t in 1,IH(1 / (1 + cost_of_capital)^t)) /
            sum_t in IH((realised_delivery_fraction * baseload_demand * HOURS_IN_YEAR) / (1 + cost_of_capital)^t))
    Note: For short-lived technologies, the LCOE is expanded to the full investment horizon by re-installing the technology.

    Parameters:
        investment_horizon: number of years to calculate the LCOE for (y).
        installed_capacity: dictionary with installed capacity for each technology.
            Solar/wind in MW; battery in MWh (storage is energy, not power).
        capex: dictionary with CAPEX for each technology.
            Solar/wind in $/MW (upstream-converted from $/kW).
            Battery in $/MWh (upstream-converted from $/kWh).
        opex_pct: dictionary with OPEX as a percentage of CAPEX for each technology (%).
        renewable_energy_profile: dictionary with hourly profiles for each renewable energy technology (MWh).
        cost_of_capital: cost of capital (%). Discount rate (r) in % (risk aversion, discount gets higher with time as uncertainty increases).
        use_curtailment: whether to consider curtailment in the LCOE calculation (bool). If True, curtailment is considered.
        realised_delivery_fraction: fraction of baseload demand actually served by the design (0..1).
            Scales the denominator so LCOE is USD per MWh actually delivered, not USD per MWh of nameplate
            demand. The producer chooses what the fraction represents:
              - binary hours-coverage (fraction of hours where supply >= demand) — what `optimize_point`
                passes today, used for the bulk argmin.
              - dispatch-aware served-energy fraction (1 - sum(unmet)/sum(demand)) — used post-argmin to
                refine the picked optimum's LCOE; battery contribution to partial-coverage hours is
                included.
            If None, defaults to 1.0 and emits a warning — that fallback reproduces the historical
            (biased-low) behaviour and exists only for direct callers that predate this parameter.

    Assumptions:
        - We assume an isolated island-like energy production (i.e., no grid connection).
        - Capacity buildout is fast and happens in <1y (year 0). Afterwards, there are no CAPEX costs but O&M costs instead.
        - O&M costs are a fixed percentage of the initial CAPEX (year 0) and, thus, constant over the full LT.
        - Electricity generation starts in year 1 and lasts until the end of LT. There is no electricity generation in year 0.
        - The investment horizon is set to the maximum lifetime of all technologies.
        - Different lifetimes are accounted for by re-installing shorter-lived equipment within the investment
        horizon. Reinstalls happen at year k*LT (k>=1); when the horizon is not a multiple of a lifetime, a
        final unit is installed to cover the tail and its OPEX is clamped to the horizon (life remaining past
        the horizon carries no salvage credit).
        - Sold electricity per post-installation year = `realised_delivery_fraction * baseload_demand * HOURS_IN_YEAR`. With
        `realised_delivery_fraction=1.0` this collapses to the prior assumption that 100% of demand is served every hour;
        with the realised delivery fraction passed in by the optimiser, the unmet demand allowed by the coverage filter
        (or the dispatch-aware served-energy correction) is excluded from the denominator.
        - The LCOE calculation is purely supply based. Regional variations in energy  demand do not dynamically drive the costs. Regional variations to CAPEX are
        only included via the learning curve.
        - Taxes are not included - regional differences due to variable taxation levels per country are not represented.

    Notes:
        1. sum_t from t = 1 to T, where T is the number of years in the lifetime of the technology. t=0 (investment time)
        corresponds to CAPEX_0 and generated electricity = 0.
        2. Can be calculated without absolute capacity values, since it cancels out anyways (set capacity to 1 MW and use
        CAPEX per MW).
    """
    if realised_delivery_fraction is None:
        logging.warning(
            "calculate_lcoe_of_re_installation called without realised_delivery_fraction; defaulting to 1.0. "
            "LCOE will be biased low by the unmet-demand fraction — pass the design's coverage or served-fraction."
        )
        realised_delivery_fraction = 1.0

    # Generated and sold electricity in the investment horizon. Year 0 is the installation year and has no generation/sales.
    gen_elect_ih = {}
    for tech in ["solar", "wind"]:
        gen_elect_ih[tech] = [0] + list(
            calculate_generated_electricity_in_period(
                investment_horizon,
                renewable_energy_profile[tech],
                installed_capacity=installed_capacity[tech],
                deterioration_rate=YEARLY_DETERIORATION_RATES[tech],
                downtime=MAINTENANCE_DOWNTIME_DAYS,
            )
        )
    gen_elect_ih_all = [gen_elect_ih["solar"][i] + gen_elect_ih["wind"][i] for i in range(len(gen_elect_ih["solar"]))]
    sold_elect_ih_all = [0] + [realised_delivery_fraction * baseload_demand * HOURS_IN_YEAR] * investment_horizon
    if use_curtailment:
        curtailment = [0] + [1 - sold_elect_ih_all[i] / gen_elect_ih_all[i] for i in range(1, investment_horizon + 1)]
        logging.debug(f"curtailment: {curtailment}")

    # CAPEX and OPEX for the full installed capacity
    capex_installed = {}
    opex_installed = {}
    for tech in ["solar", "wind", "battery"]:
        capex_installed[tech] = [installed_capacity[tech] * capex[tech][i] for i in range(len(capex[tech]))]
        opex_installed[tech] = [capex_installed[tech][i] * opex_pct[tech] for i in range(len(capex[tech]))]

    # Discount electricity due to investment risk (cost of capital)
    discount_factors = [1 / (1 + cost_of_capital) ** t for t in range(investment_horizon + 1)]
    gen_elect_ih_all_d = [gen_elect_ih_all[i] * discount_factors[i] for i in range(investment_horizon + 1)]
    sold_elect_ih_all_d = [sold_elect_ih_all[i] * discount_factors[i] for i in range(investment_horizon + 1)]

    # Calculate total costs (CAPEX and OPEX, including discounted reinstallation costs for shorter-lived technologies)
    total_capex_ih = {}
    total_opex_ih = {}
    for tech in ["solar", "wind", "battery"]:
        lifetime = LIFETIMES[tech]
        total_capex_ih[tech] = capex_installed[tech][0]
        total_opex_ih[tech] = opex_installed[tech][0] * sum(discount_factors[1 : lifetime + 1])
        if investment_horizon > lifetime:
            # Reinstall at year k*lifetime so coverage runs unbroken to the horizon; ceiling division
            # adds a final unit for any non-multiple tail, and its OPEX slice is clamped to the horizon.
            num_reinstallations = (investment_horizon + lifetime - 1) // lifetime - 1
            for i in range(1, num_reinstallations + 1):
                install_year = i * lifetime
                total_capex_ih[tech] += capex_installed[tech][investment_horizon] * discount_factors[install_year]
                total_opex_ih[tech] += opex_installed[tech][investment_horizon] * sum(
                    discount_factors[install_year + 1 : install_year + lifetime + 1]
                )
    total_costs_all = sum(total_capex_ih[tech] + total_opex_ih[tech] for tech in ["solar", "wind", "battery"])

    # Divide total costs by the generated or sold electricity, depending on whether curtailment is considered
    if use_curtailment is True:
        lcoe = total_costs_all / sum(sold_elect_ih_all_d)
    else:
        lcoe = total_costs_all / sum(gen_elect_ih_all_d)
    return lcoe


def _lcoe_tech_weights(
    discount_factors: np.ndarray, opex_pct: float, lifetime: int, horizon: int
) -> tuple[float, float]:
    """
    Per-technology CAPEX+OPEX weights so that, for a tech with a flat year-0/horizon CAPEX
    structure, the discounted lifetime cost is:

        total_tech_cost = installed_capacity * (w0 * capex[0] + wH * capex[horizon])

    This is the closed form of the CAPEX/OPEX accumulation in
    `calculate_lcoe_of_re_installation` (including discounted reinstallation of
    shorter-lived technologies). With horizon == lifetime, wH == 0.
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
    Vectorised, closed-form equivalent of `calculate_lcoe_of_re_installation(..., use_curtailment=True)`
    evaluated for many designs at once.

    Only the curtailment=True convention is implemented here. The scalar's use_curtailment=False
    mode — dividing by *generated* rather than *delivered* electricity, which is the only branch
    where deterioration and downtime affect the LCOE — has no vectorised counterpart and is not
    modelled on this (production) path.

    Under curtailment-mode, the discounted-sold-electricity denominator is `sum_t(discount[t] *
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
    CAPEX, matching `filter_designs_according_to_coverage_and_calculate_costs`).
    """
    n = installed_solar.shape[0]
    if realised_delivery_fraction is None:
        logging.warning(
            "calculate_lcoe_of_re_installation_vectorised called without realised_delivery_fraction; "
            "defaulting to ones. LCOE will be biased low by the unmet-demand fraction — pass "
            "PointDesignState.coverage (or a served-energy fraction) to remove this bias."
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


def calculate_lcoe_of_single_re_tech(
    generated_electricity: list,
    fixed_opex_percentage: float,
    cost_of_capital: float,
    capex_0: float,
    capex_t: list[float] | None = None,
    curtailment: list[float] | None = None,
) -> float:
    """
    [REFERENCE IMPLEMENTATION — no current callers in production]

    LCOE for VARIABLE-SUPPLY scenarios (electricity sold at whatever the generation profile
    delivers, no fixed demand peg). Preserved from the MVP model version as a reference for
    an alternative cost-modeling approach. This function is NOT replaced one-to-one in the
    current codebase — it models a different problem (variable supply, no demand).

    The current production model assumes fixed baseload demand and computes LCOE differently:
      - Scalar:     `calculate_lcoe_of_re_installation`     (used by the single-point path
                                                              via `filter_designs_according_to_coverage_and_calculate_costs`)
      - Vectorised: `calculate_lcoe_of_re_installation_vectorised` (used by the GLOBAL path
                                                                    via `optimize_point`)

    Calculate the Levelized Cost of Electricity (LCOE) in USD/MWh. This function is used for variable supply (developed for the MVP model version).
    lcoe = (capex_0 + sum_t(fixed_opex_percentage * capex_t / (1 + cost_of_capital)^t)) /
            sum_t((generated_electricity_t * (1 - curtailment_t)) / (1 + cost_of_capital)^t))
    Notes:
        1. sum_t from t = 1 to T, where T is the number of years in the lifetime of the technology. t=0 (investment time)
        corresponds to CAPEX_0 and generated electricity = 0.
        2. Can be calculated without absolute capacity values, since it cancels out anyways (set capacity to 1 MW and use
        CAPEX per MW).

    Inputs:
        generated_electricity: Electricity generated per unit of installed capacity per timestep during lifetime in MW. Note: must decrease over time!
        capex_0: Initial capital expenditure (CAPEX) in USD/MW.
        fixed_opex_percentage: Fixed OPEX as a percentage of CAPEX in %.
        cost_of_capital: Discount rate (r) in % (risk aversion, discount gets higher with time as uncertainty increases).
        curtailment: Curtailment rate per timestep in %.

    Output:
        lcoe: Levelized Cost of Electricity (LCOE) in USD/MWh.
    """
    # Simplification depending on cases
    if not capex_t:
        capex_t = [capex_0] * len(generated_electricity)
    if not curtailment:
        curtailment = [0] * len(generated_electricity)
    years = range(len(generated_electricity))
    discount_factors = [
        (1 + cost_of_capital) ** (t + 1) for t in years
    ]  # (t + 1) since the index starts at 0 and t starts at 1
    # Numerator
    x = [capex_t[t] / discount_factors[t] for t in years]
    opex_proxy = fixed_opex_percentage * sum(x)
    numerator = capex_0 + opex_proxy
    # Denominator
    sold_electricity = [generated_electricity[t] * (1 - curtailment[t]) for t in years]
    discounted_electricity = [sold_electricity[t] / discount_factors[t] for t in years]
    denominator = sum(discounted_electricity)
    if denominator == 0:
        raise ZeroDivisionError("Denominator (discounted electricity) is 0, cannot divide by 0.")

    return numerator / denominator
