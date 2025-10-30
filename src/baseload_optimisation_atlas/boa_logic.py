import numba
import numpy as np
import logging
import xarray as xr
from baseload_optimisation_atlas.boa_cost_calculations import (
    calculate_lcoe_of_re_installation,
    calculate_installation_cost,
)
from baseload_optimisation_atlas.boa_config import BATTERY_UNIT_CAPEX_SCALING_FACTOR
from baseload_optimisation_atlas.boa_plotting import plot_state_of_charge_and_cost

logging.basicConfig(level=logging.INFO)


def capacity_sampling(
    profile: dict[str, np.ndarray],
    p: float,
    n_samples: int = 3000,
    mus: dict[str, float] = {"wind": 5, "solar": 5},
    limit: dict[str, float] | None = None,
    seed: int = 42,
) -> list[dict[str, float]]:
    """
    Generate renewable energy system designs by sampling wind and solar overscale factors.

    Steps:
        1. Sample wind and solar overscale factors from exponential or uniform distributions based on capacity limits
        2. Calculate required battery overscale factor for each sampled wind-solar combination

    Args:
        profile: Dictionary containing hourly solar and wind generation profiles
        p: Percentile threshold for demand coverage (e.g., 5 means 5th percentile)
        n_samples: Number of random samples to generate (default: 3000)
        mus: Mean values for exponential distributions for wind and solar (default: {"wind": 5, "solar": 5})
        limit: Maximum capacity limits for wind and solar as overscale factors relative to baseload demand, driven by land
        availability and spacing constraints (default: None)
        seed: Random seed for reproducibility (default: 42)

    Returns:
        List of designs, each containing wind, solar, and battery overscale factors

    Note:
        - If no capacity limits are provided, both wind and solar use exponential distributions
        - If capacity limits are provided, wind uses uniform distribution [0, limit] and solar uses clipped exponential distribution
    """
    np.random.seed(seed)

    # Overscale factor: exponential or uniform distribution; apply capacity limits if provided
    if limit is None:
        C_w_samples = np.random.exponential(scale=mus["wind"], size=n_samples)
        C_s_samples = np.random.exponential(scale=mus["solar"], size=n_samples)
    else:
        C_w_samples = np.random.uniform(size=n_samples, low=0, high=limit["wind"])
        C_s_samples = np.clip(np.random.exponential(scale=mus["solar"], size=n_samples), 0, limit["solar"])

    # Calculate the battery overscale factor based on the sampled wind and solar overscaling factors -> feasible designs
    # TODO: Use apply ufunc instead of for loop to speed up?
    feasible_designs = []
    for i in range(n_samples):
        design = {"wind": C_w_samples[i], "solar": C_s_samples[i]}
        net_nrg = calculate_net_energy_production(design["solar"], profile["solar"], design["wind"], profile["wind"])
        design["battery"] = estimate_battery_capacity(net_nrg, q_deficit=p, q_duration=100 - p)
        feasible_designs.append(design)

    return feasible_designs


def estimate_battery_capacity(net_energy: np.ndarray, q_deficit: float = 5, q_duration: float = 5) -> float:
    """
    Estimate required battery capacity from a net energy time series.

    Steps:
        1. Calculate the q_deficit percentile of net energy to determine how often demand is not covered. The deficit_val
        is negative if deficits occur more than q_deficit percent of the time. If deficit_val is positive, demand is already
        covered at least (100-q_deficit) percent of the time, so no battery is needed.
        2. Calculate the q_duration percentile of contiguous negative-duration periods (in hours). This represents how long
        the demand is not covered at a time (e.g., size of windows with no solar or wind generation).
        3. Multiply deficit depth by deficit duration to approximate battery capacity

    Args:
        net_energy: Hourly net energy array (MWh)
        q_deficit: Percentile to capture deficit depth (default: 5)
        q_duration: Percentile to capture duration of contiguous deficits (default: 5)

    Returns:
        Approximate battery capacity needed as an overscaling factor relative to demand
    """
    # 1. Compute the q_deficit percentile
    deficit_val = np.percentile(net_energy, q_deficit)
    if deficit_val > 0:
        return 0

    # 2. Find contiguous periods when net energy is negative - window size (duration of the deficit)
    durations = []
    current_duration = 0
    for value in net_energy:
        if value < 0:
            current_duration += 1
        else:
            if current_duration > 0:
                durations.append(current_duration)
                current_duration = 0
    if current_duration > 0:
        durations.append(current_duration)
    durations_array = np.array(durations, dtype=np.float64)
    if len(durations_array) > 0:
        duration_val = float(np.percentile(durations_array, q_duration))
    else:
        duration_val = 0.0

    # 3. Approximate battery capacity as the product of the magnitude of the deficit and the duration
    battery_capacity = abs(deficit_val) * duration_val
    return battery_capacity


@numba.jit
def state_of_charge(
    gen_nrg: np.ndarray,
    battery_capacity: float,
) -> np.ndarray:
    """
    Simulate battery operation hour by hour starting from empty battery.

    Args:
        gen_nrg: Net generated energy at each time step (production - demand)
        battery_capacity: Battery overscale factor

    Returns:
        State of charge (SOC) at each hour (MWh)

    Note:
        - Surplus energy above battery capacity is wasted
        - Deficits draw down the battery
    """
    soc = np.zeros(len(gen_nrg))
    # First hour has special treatment - starting from empty battery
    soc[0] = min(max(gen_nrg[0], 0), battery_capacity)
    # Loop through the rest of the hours
    for t in range(len(gen_nrg)):
        soc[t] = min(max(soc[t - 1] + gen_nrg[t], 0), battery_capacity)
    return soc


@numba.jit
def calculate_coverage(soc: np.ndarray, net_energy: np.ndarray) -> float:
    """
    Calculate average demand coverage using solar, wind, and batteries.

    Steps:
        1. Calculate binary hourly coverage: 1 if state of charge at t-1 plus net energy at t is >= 0, 0 otherwise
        2. Calculate average coverage over the full time period

    Args:
        soc: State of charge array (MWh)
        net_energy: Net energy array (MWh)

    Returns:
        Average coverage as a fraction (0.0 to 1.0)
    """
    bin_hourly_coverage = np.zeros(len(net_energy))
    # First hour has special treatment - starting from empty battery
    if net_energy[0] >= 0:
        bin_hourly_coverage[0] = 1
    else:
        bin_hourly_coverage[0] = 0
    # Iterate over the rest of the hours
    for t in range(1, len(net_energy)):
        if soc[t - 1] + net_energy[t] >= 0:
            bin_hourly_coverage[t] = 1
        else:
            bin_hourly_coverage[t] = 0

    return bin_hourly_coverage.mean()


def calculate_net_energy_production(
    C_s: float, solar_profile: np.ndarray, C_w: float, wind_profile: np.ndarray
) -> np.ndarray:
    """
    Calculate net energy production from wind and solar without battery storage.

    Formula:
        Net Energy(t) = C_w * Wind(t) + C_s * Solar(t) - Demand(t)

    Args:
        C_s: Overscale factor for solar
        solar_profile: Hourly solar energy production
        C_w: Overscale factor for wind
        wind_profile: Hourly wind energy production

    Returns:
        Net energy production array (MWh)

    Note:
        Demand is set to 1 since all other parameters are relative to it (overscale factors w.r.t. demand). The actual demand value is
        factored in when calculating absolute costs and LCOE.
    """
    demand = 1.0
    return C_w * wind_profile + C_s * solar_profile - demand


def correct_battery_capex_for_modular_installation(
    storage_costs: dict[str, np.ndarray],
    battery_overscaling_factor: float,
) -> np.ndarray:
    """
    Correct battery CAPEX for economies of scale in modular installation.

    Formula:
        battery_cost_per_installed_unit * (battery_overscaling_factor / average_implied_storage) ^ BATTERY_UNIT_CAPEX_SCALING_FACTOR

    Args:
        storage_costs: Dictionary containing battery_cost_per_installed_unit and average_implied_storage
        battery_overscaling_factor: Battery capacity as overbuilding factor with respect to demand

    Returns:
        Corrected battery CAPEX array

    Note:
        - Installing a module with several battery units at once is cheaper than installing many single units
        - Storage capacity is set to installed battery capacity (overbuilding factor w.r.t. demand)
        - Battery discharge rate is not considered
        - If overscaling factor is 0, battery CAPEX is not corrected
    """
    if battery_overscaling_factor > 0:
        return (
            storage_costs["battery_cost_per_installed_unit"]
            * (battery_overscaling_factor / storage_costs["average_implied_storage"])
            ** BATTERY_UNIT_CAPEX_SCALING_FACTOR
        )
    else:
        return storage_costs["battery_cost_per_installed_unit"]


def filter_designs_according_to_coverage_and_calculate_costs(
    designs: list[dict[str, float]],
    baseload_demand: float,
    capex: dict[str, np.ndarray],
    storage_costs: dict[str, np.ndarray],
    opex_pct: dict[str, float],
    profile: dict[str, np.ndarray],
    cost_of_capital: float,
    investment_horizon: int,
    p: float,
) -> tuple[np.ndarray, list[float], list[float]]:
    """
    Filter designs by coverage threshold and calculate costs for accepted designs.

    Steps:
        1. Filter designs according to hourly coverage of the full system (solar, wind, battery), which must be above (100-p)% threshold
        2. For accepted designs, correct battery CAPEX for modular installation and calculate installation costs and LCOE

    Args:
        designs: List of system designs with wind, solar, and battery overscale factors
        baseload_demand: Baseload demand (MW)
        capex: Dictionary containing CAPEX arrays for solar, wind, and battery
        storage_costs: Dictionary containing battery cost parameters
        opex_pct: Dictionary containing OPEX percentages for solar, wind, and battery
        profile: Dictionary containing hourly solar and wind generation profiles
        cost_of_capital: Cost of capital for LCOE calculation
        investment_horizon: Investment horizon in years
        p: Percentile threshold for demand coverage (e.g., 5 means 95% coverage required)

    Returns:
        Tuple containing:
            - Array of accepted designs
            - List of installation costs for accepted designs
            - List of LCOE values for accepted designs
    """

    # Filter the feasible designs according to their coverage -> accepted designs
    accepted_designs = []
    installation_costs = []
    lcoes = []
    for design in designs:
        # TODO: Return from previous function to avoid recalculating
        net_energy = calculate_net_energy_production(design["solar"], profile["solar"], design["wind"], profile["wind"])
        soc = state_of_charge(net_energy, design["battery"])
        coverage = calculate_coverage(soc, net_energy)
        if coverage >= 1 - p / 100:
            # logging.debug(f"Accepted design with {coverage * 100:.1f}% coverage: {design}")
            accepted_designs.append(design)
            # Correct battery CAPEX for modular installation
            capex["battery"] = correct_battery_capex_for_modular_installation(
                storage_costs,
                design["battery"],
            )
            # Extract CAPEX for the first year of the investment horizon
            initial_costs = dict(
                cost_solar=capex["solar"][0],  # per MW overscaling for solar
                cost_wind=capex["wind"][0],  # per MW overscaling for wind
                cost_battery=capex["battery"][0],  # per MW overscaling for battery
            )
            installation_cost = calculate_installation_cost(
                design["solar"] * baseload_demand,
                design["wind"] * baseload_demand,
                design["battery"] * baseload_demand,
                **initial_costs,
            )
            installation_costs.append(installation_cost)
            x = {tech: design[tech] * baseload_demand for tech in ["solar", "wind", "battery"]}
            # Note: Important to use curtailment in the LCOE calculation; otherwise, the minimum LCOE is just the one which installs as much
            # solar as possible
            lcoes.append(
                calculate_lcoe_of_re_installation(
                    investment_horizon,
                    x,
                    baseload_demand,
                    capex,
                    opex_pct,
                    profile,
                    cost_of_capital,
                    use_curtailment=True,
                )
            )
    accepted_designs_array = np.array(accepted_designs, dtype=object)
    N_samples = len(designs)
    accepted_proposals = len(accepted_designs) / N_samples
    logging.debug(f"Accepted proposals: {accepted_proposals}")

    return accepted_designs_array, installation_costs, lcoes


def show_optimal_design(
    designs: list[dict[str, float]], installation_costs: list[float], lcoes: list[float], profile: dict[str, np.ndarray]
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Identify and display the optimal design that minimizes LCOE.

    Steps:
        1. Find design with minimum LCOE among all accepted designs
        2. Log optimal design parameters (solar, wind, battery overscale factors, installation cost, LCOE)
        3. Calculate state of charge for optimal design and plot results

    Args:
        designs: List of accepted system designs
        installation_costs: List of installation costs corresponding to designs
        lcoes: List of LCOE values corresponding to designs
        profile: Dictionary containing hourly solar and wind generation profiles

    Returns:
        Tuple containing:
            - Optimal design dictionary with wind, solar, and battery overscale factors
            - Optimal cost dictionary with installation cost and LCOE

    Side Effects:
        - Logs optimal design information
        - Plots state of charge and cost visualization
    """
    if len(designs) > 0:
        opt_index = np.argmin(np.array(lcoes))
        opt_design = designs[opt_index]
        opt_cost = {
            "installation cost": installation_costs[opt_index],
            "LCOE": lcoes[opt_index],
        }
        logging.info("Optimal Design to minimize LCOE:")
        logging.info("  Solar overscale (w.r.t. demand): {:.3f}".format(opt_design["solar"]))
        logging.info("  Wind overscale (w.r.t. demand): {:.3f}".format(opt_design["wind"]))
        logging.info("  Battery overscale (w.r.t. demand): {:.3f}".format(opt_design["battery"]))
        logging.info("  Total installation cost: {:.3f}".format(installation_costs[opt_index]))
        logging.info("  Total LCOE: {:.3f}".format(lcoes[opt_index]))

        # Compute the state of charge for the optimal design
        opt_net_nrg = calculate_net_energy_production(
            opt_design["solar"], profile["solar"], opt_design["wind"], profile["wind"]
        )
        opt_soc = state_of_charge(opt_net_nrg, opt_design["battery"])

        plot_state_of_charge_and_cost(lcoes, designs, opt_design, opt_soc)

        return opt_design, opt_cost
    else:
        logging.warning("No accepted design among the samples.")
        return {}, {}


def return_global_average_costs(costs: xr.Dataset) -> tuple[dict, dict, float]:
    """
    Calculate global average costs across all countries.

    Args:
        costs: Dataset containing CAPEX and OPEX for solar, wind, and battery, plus cost of capital, indexed by ISO3 country codes

    Returns:
        Tuple containing:
            - CAPEX dictionary for solar and wind (averaged across countries)
            - OPEX percentage dictionary for solar, wind, and battery (averaged across countries)
            - Cost of capital (averaged across countries)
    """
    capex = {}
    for tech in ["solar", "wind"]:
        capex[tech] = costs["Capex " + tech].mean(dim="iso3").values
    opex_pct = {}
    for tech in ["solar", "wind", "battery"]:
        opex_pct[tech] = costs["Opex " + tech].mean(dim="iso3").values
    cost_of_capital = float(costs["Cost of capital"].mean(dim="iso3").values)

    return capex, opex_pct, cost_of_capital
