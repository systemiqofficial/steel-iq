import numba
import numpy as np
import xarray as xr
from boa.config.settings import OVERSCALE_SAMPLING_K


def overscale_mus_from_cf(cf_solar: float, cf_wind: float) -> dict[str, float]:
    """
    mu = k / CF from the time-mean capacity factors (see OVERSCALE_SAMPLING_K). Sets the
    grid-bisection search box (`search_box`, `bisection.py`) to `box_multiple * mu`, clamped.
    The 1e-9 is a division guard only, not a behavioural floor: a zero-CF technology gets an
    astronomically large mu, `box_abs_max` catches it, and the pixel comes back
    STATUS_ZERO_POTENTIAL or STATUS_NO_OPTIMUM rather than an unresolvable box.
    """
    return {
        "solar": OVERSCALE_SAMPLING_K["solar"] / max(cf_solar, 1e-9),
        "wind": OVERSCALE_SAMPLING_K["wind"] / max(cf_wind, 1e-9),
    }


def state_of_charge(
    gen_nrg: np.ndarray,
    battery_capacity: float,
) -> np.ndarray:
    """
    Simulate battery operation. The battery starts empty and is updated hour by hour. Surplus energy above battery capacity is wasted;
    deficits draw down the battery.

    Parameters:
      gen_nrg (array-like): Net generated energy at each time step (production - demand),
        normalised by baseload demand (demand = 1).
      battery_capacity: Battery overscale factor in **hours** (baseload-hours).
        installed_MWh = battery_capacity × baseload_MW.

    Returns:
      soc_values (array-like): State of charge (SOC) at each hour, in the same
        normalised units as gen_nrg (multiply by baseload_MW to get absolute MWh).
    """
    soc = np.zeros(len(gen_nrg))
    # First hour has special treatment - starting from empty battery
    soc[0] = min(max(gen_nrg[0], 0), battery_capacity)
    # Loop through the rest of the hours
    for t in range(len(gen_nrg)):
        soc[t] = min(max(soc[t - 1] + gen_nrg[t], 0), battery_capacity)
    return soc


@numba.jit(cache=True, nogil=True)
def calculate_coverage(soc: np.ndarray, net_energy: np.ndarray) -> float:
    """
    [REFERENCE IMPLEMENTATION — no production callers. Kept as the readable scalar spec that
    `bisection.dispatch_metrics` is checked against in tests/test_bisection_kernel.py. Its
    sibling `state_of_charge` is NOT a reference — it is still called directly in production
    (single_point_run.py) to compute a chosen design's state of charge for plotting.]

    Calculate the binary demand coverage for each hour using solar, wind, and batteries: 1 if the sum of the state of charge at t-1 and the net energy at t is >= 0, 1 otherwise.
    Then get the average coverage over the full time period.
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


def calculate_served_fraction(soc: np.ndarray, net_energy: np.ndarray) -> float:
    """
    Dispatch-aware fraction of baseload demand actually delivered over the year (0..1).

    The finer-grained sibling of `calculate_coverage`: coverage is a binary
    per-hour metric (1 if demand met, 0 if not), so a 50%-served hour gets
    counted the same as a full blackout. `served_fraction` instead integrates
    the *energy* delivered — every hour gets partial credit for what the
    system actually supplied (RE direct + battery release).

    Derivation (mirroring `single_point_run.py` time-series output):

        delta_soc[t]   = soc[t] - soc[t-1]                  (soc[-1] := 0)
        discharge[t]   = max(0, -delta_soc[t])              ← battery release
        unmet[t]       = max(0, -net_energy[t] - discharge[t])
        served_fraction = 1 - mean(unmet)                   ← demand normalised to 1.0

    Invariant: `served_fraction >= calculate_coverage(soc, net_energy)` always
    holds with the current dispatch model (no round-trip loss, so the battery
    can only *add* delivered energy, never subtract). If this invariant is
    ever violated, the dispatch arithmetic has drifted from this derivation
    and something needs fixing — both consumers should see the same physics.

    Parameters:
      soc: per-hour state of charge from `state_of_charge` (normalised: demand = 1).
      net_energy: per-hour `supply - demand` (normalised). Contract: supply >= 0
        and demand = 1, so net_energy >= -1 always. Synthetic inputs that violate
        this (e.g. random gaussians) can drive unmet > 1 per hour and yield
        served_fraction < 0 or break the `served_fraction >= coverage` invariant —
        that's a caller-side contract violation, not a function bug. The production
        callsite (the single-point API time-series path) always satisfies the
        contract because supply is a non-negative linear combo of non-negative
        profiles. `bisection.dispatch_metrics` reproduces this function's numerics
        exactly (see tests/test_bisection_kernel.py) rather than calling it, since
        it fuses state_of_charge/calculate_coverage/calculate_served_fraction into
        one allocation-free jitted pass for the grid-bisection search's hot loop.

    Returns:
      Fraction in [0, 1] for well-formed inputs. 1.0 means every hour fully served,
      0.0 means nothing delivered all year. Not JIT-compiled — called once per
      output point, not in the hot dispatch loop.
    """
    soc_prev = np.concatenate(([0.0], soc[:-1]))
    delta_soc = soc - soc_prev
    discharge_norm = np.maximum(0.0, -delta_soc)
    unmet_norm = np.maximum(0.0, -net_energy - discharge_norm)
    return float(1.0 - unmet_norm.mean())


def calculate_net_energy_production(
    C_s: float, solar_profile: np.ndarray, C_w: float, wind_profile: np.ndarray
) -> np.ndarray:
    """
    Generate net energy production given overscale factors for wind and solar, and demand. No battery is currently considered.
    Net Energy(t) = C_w * Wind(t) + C_s * Solar(t) - Demand(t)

    Parameters:
        C_s: Overscale factor for solar.
        solar_profile: hourly solar energy production
        C_w: Overscale factor for wind.
        wind_profile: hourly wind energy production

    Note: Demand is hardcoded to 1 MW to make all calculations relative to it. If changed, other parameters governing the sampling will need to be modified proportionally.
    """
    demand = 1.0  # MW
    return C_w * wind_profile + C_s * solar_profile - demand


def return_global_average_costs(costs: xr.Dataset) -> tuple[dict, dict, float]:
    """
    Calculate global average costs for all technologies (CAPEX for solar and wind, OPEX for solar, wind, and battery).
    """
    capex = {}
    for tech in ["solar", "wind", "battery"]:
        capex[tech] = costs["Capex " + tech].mean(dim="iso3").values
    opex_pct = {}
    for tech in ["solar", "wind", "battery"]:
        opex_pct[tech] = costs["Opex " + tech].mean(dim="iso3").values
    cost_of_capital = float(costs["Cost of capital"].mean(dim="iso3").values)

    return capex, opex_pct, cost_of_capital
