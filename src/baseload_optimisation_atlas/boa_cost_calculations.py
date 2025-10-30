import numpy as np
import logging
from baseload_optimisation_atlas.boa_config import LIFETIMES, YEARLY_DETERIORATION_RATES
from baseload_optimisation_atlas.boa_constants import HOURS_IN_YEAR, DAYS_IN_YEAR, HOURS_IN_DAY


def calculate_installation_cost(
    C_s: float, C_w: float, C_b: float, cost_solar: float, cost_wind: float, cost_battery: float
) -> float:
    """
    Calculate total installation cost for a renewable energy system design.

    Formula:
        Total cost = cost_solar * C_s + cost_wind * C_w + cost_battery * C_b

    Args:
        C_s: Solar capacity (MW)
        C_w: Wind capacity (MW)
        C_b: Battery capacity (MWh)
        cost_solar: Unit cost of solar capacity (USD/MW)
        cost_wind: Unit cost of wind capacity (USD/MW)
        cost_battery: Unit cost of battery capacity (USD/MWh)

    Returns:
        Total installation cost (USD)
    """
    return cost_solar * C_s + cost_wind * C_w + cost_battery * C_b


def calculate_generated_electricity_in_period(
    time_period: int,
    re_potential: float | np.ndarray | None = 1.0,
    installed_capacity: int = 1,
    deterioration_rate: float = 0,
    downtime: int = 0,
) -> np.ndarray:
    """
    Calculate yearly generated electricity over a time period accounting for deterioration and downtime.

    Formula:
        yearly_generated_electricity_t = yearly_re_potential * installed_capacity * (1 - deterioration_rate)^t * (1 - downtime / DAYS_IN_YEAR)

    Steps:
        1. Calculate deteriorating actual capacity for each year
        2. Apply downtime correction to get operational capacity
        3. Calculate capacity factor from re_potential (either as annual average or from hourly profile)
        4. Multiply operational capacity by capacity factor and hours in year to get yearly generated electricity

    Args:
        time_period: Number of years to calculate generated electricity for
        re_potential: Ratio between total generated electricity and total potential if running at full capacity. Can be hourly time series for one reference year (profile) or annual average (capacity factor) (default: 1.0)
        installed_capacity: Initially installed capacity for a certain technology in MW (default: 1)
        deterioration_rate: Rate at which capacity deteriorates each year (default: 0)
        downtime: Number of days per year the capacity is unavailable (e.g., for maintenance) (default: 0)

    Returns:
        Yearly generated electricity for each year in the time period (MWh)

    Notes:
        - Battery degradation is represented implicitly - assuming it goes at the same or slower pace than wind and solar
        - Values must decrease over time due to deterioration
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
) -> float:
    """
    Calculate levelized cost of electricity (LCOE) for a renewable energy installation with baseload supply.

    Formula:
        lcoe = (capex_0 * (1 + fixed_opex_percentage * sum_t in 1,IH(1 / (1 + cost_of_capital)^t)) /
                sum_t in IH((generated_electricity_t * (1 - curtailment_t)) / (1 + cost_of_capital)^t))

    Steps:
        1. Calculate generated and sold electricity over investment horizon (year 0 is installation year with no generation)
        2. Calculate CAPEX and OPEX for full installed capacity
        3. Apply discount factors based on cost of capital
        4. Calculate total costs including reinstallation costs for short-lived technologies
        5. Divide total costs by generated or sold electricity depending on curtailment setting

    Args:
        investment_horizon: Number of years to calculate LCOE for
        installed_capacity: Dictionary with installed capacity for each technology (MW)
        baseload_demand: Baseload demand (MW)
        capex: Dictionary with CAPEX for each technology (USD/MW)
        opex_pct: Dictionary with OPEX as percentage of CAPEX for each technology
        renewable_energy_profile: Dictionary with hourly profiles for each renewable energy technology (MWh)
        cost_of_capital: Cost of capital (discount rate)
        use_curtailment: Whether to consider curtailment in LCOE calculation (default: False)

    Returns:
        Levelized cost of electricity (USD/MWh)

    Assumptions:
        - Isolated island-like energy production (no grid connection)
        - Capacity buildout happens in <1y (year 0), afterwards only O&M costs
        - O&M costs are fixed percentage of initial CAPEX (year 0), constant over full lifetime
        - Electricity generation starts in year 1 and lasts until end of lifetime. No generation in year 0
        - Investment horizon is set to maximum lifetime of all technologies
        - Different lifetimes accounted for by re-installing equipment with shorter lifetimes (lifetimes must be multiples of each other)
        - Sold electricity equals baseload demand for all years after installation year
        - LCOE calculation is purely supply based. Regional demand variations do not dynamically drive costs. Regional CAPEX variations only included via learning curve
        - Taxes not included - regional differences due to variable taxation levels not represented

    Notes:
        - For short-lived technologies, LCOE is expanded to full investment horizon by re-installing the technology
        - sum_t from t = 1 to T, where T is the number of years in the lifetime. t=0 (investment time) corresponds to CAPEX_0 and generated electricity = 0
        - Can be calculated without absolute capacity values since it cancels out (set capacity to 1 MW and use CAPEX per MW)
    """

    # Generated and sold electricity in the investment horizon. Year 0 is the installation year and has no generation/sales.
    gen_elect_ih = {}
    for tech in ["solar", "wind"]:
        gen_elect_ih[tech] = [0] + list(
            calculate_generated_electricity_in_period(
                investment_horizon,
                renewable_energy_profile[tech],
                installed_capacity=installed_capacity[tech],
                deterioration_rate=YEARLY_DETERIORATION_RATES[tech],
                downtime=10,  # days
            )
        )
    gen_elect_ih_all = [gen_elect_ih["solar"][i] + gen_elect_ih["wind"][i] for i in range(len(gen_elect_ih["solar"]))]
    sold_elect_ih_all = [0] + [baseload_demand * HOURS_IN_YEAR] * investment_horizon
    if use_curtailment:
        curtailment = [0] + [1 - sold_elect_ih_all[i] / gen_elect_ih_all[i] for i in range(1, investment_horizon)]
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
        total_capex_ih[tech] = capex_installed[tech][0]
        total_opex_ih[tech] = opex_installed[tech][0] * sum(discount_factors[1 : LIFETIMES[tech] + 1])
        if investment_horizon > LIFETIMES[tech]:
            num_reinstallations = int((investment_horizon - LIFETIMES[tech]) // LIFETIMES[tech])
            for i in range(1, num_reinstallations + 1):
                reinstallation_year = i * LIFETIMES[tech] + 1
                total_capex_ih[tech] += (
                    capex_installed[tech][investment_horizon] * discount_factors[reinstallation_year]
                )
                total_opex_ih[tech] += opex_installed[tech][investment_horizon] * sum(
                    discount_factors[reinstallation_year : reinstallation_year + LIFETIMES[tech] + 1]
                )
    total_costs_all = sum(total_capex_ih[tech] + total_opex_ih[tech] for tech in ["solar", "wind", "battery"])

    # Divide total costs by the generated or sold electricity, depending on whether curtailment is considered
    if use_curtailment is True:
        lcoe = total_costs_all / sum(sold_elect_ih_all_d)
    else:
        lcoe = total_costs_all / sum(gen_elect_ih_all_d)
    return lcoe


def calculate_lcoe_of_single_re_tech(
    generated_electricity: list,
    fixed_opex_percentage: float,
    cost_of_capital: float,
    capex_0: float,
    capex_t: list[float] | None = None,
    curtailment: list[float] | None = None,
) -> float:
    """
    Calculate levelized cost of electricity (LCOE) for a single renewable energy technology with variable supply.

    Formula:
        lcoe = (capex_0 + sum_t(fixed_opex_percentage * capex_t / (1 + cost_of_capital)^t)) /
                sum_t((generated_electricity_t * (1 - curtailment_t)) / (1 + cost_of_capital)^t))

    Steps:
        1. Set default values for capex_t and curtailment if not provided
        2. Calculate discount factors for each year
        3. Calculate numerator (initial CAPEX plus discounted OPEX)
        4. Calculate denominator (discounted sold electricity accounting for curtailment)
        5. Divide numerator by denominator to get LCOE

    Args:
        generated_electricity: Electricity generated per unit of installed capacity per timestep during lifetime (MW). Must decrease over time.
        fixed_opex_percentage: Fixed OPEX as percentage of CAPEX
        cost_of_capital: Discount rate (risk aversion factor)
        capex_0: Initial capital expenditure (USD/MW)
        capex_t: CAPEX for each timestep (default: None, uses capex_0 for all years)
        curtailment: Curtailment rate per timestep (default: None, assumes 0 for all years)

    Returns:
        Levelized cost of electricity (USD/MWh)

    Notes:
        - sum_t from t = 1 to T, where T is the number of years in the lifetime. t=0 (investment time) corresponds to CAPEX_0 and generated electricity = 0
        - Can be calculated without absolute capacity values since it cancels out (set capacity to 1 MW and use CAPEX per MW)
        - Used only for verification of more complex LCOE calculations with baseload demand and multiple technologies
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
