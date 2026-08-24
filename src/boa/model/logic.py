from dataclasses import dataclass
from typing import Literal, overload

import numba
import numpy as np
import logging
import xarray as xr
from boa.model.cost_calculations import (
    calculate_lcoe_of_re_installation,
    calculate_lcoe_of_re_installation_vectorised,
    calculate_installation_cost,
)
from boa.config.settings import (
    BATTERY_UNIT_CAPEX_SCALING_FACTOR,
    MIN_SURVIVOR_FRACTION,
)
from boa.config.constants import AVERAGE_IMPLIED_STORAGE


def capacity_sampling(
    profile: dict[str, np.ndarray],
    p: float,
    n_samples: int,
    limit: dict[str, float] | None,
    seed: int,
    mus: dict[str, float],
) -> list[dict[str, float]]:
    """
    [REFERENCE IMPLEMENTATION — no current callers in production; only used by
    tests/test_logic_parity.py]

    Replaced in production by `optimize_point`, which fuses this sampling step and the
    downstream coverage-filtering/costing into one vectorised pass. Kept as the readable
    spec of the sampling step, paired with `filter_designs_according_to_coverage_and_calculate_costs`.
    `_draw_overscale_samples` is shared with `optimize_point`, so both produce bit-identical
    designs for the same seed.

    Generates a list of random samples for the wind and solar overscale factors following an exponential distribution
    and calculates the battery overscale factor based on the sampled wind and solar overscaling factors. If the capacity is limited,
    the wind overscale factors are sampled from a uniform distribution between 0 and the limit instead. For the solar overscale factors,
    the exponential distribution is used with a limit on the maximum value.

    Parameters:
        profile: Dictionary containing solar and wind profiles.
        p: Percentile of time where we don't cover the demand (e.g. 5 means 5th percentile).
        n_samples: Number of random samples to generate.
        mus: Dict with keys 'wind' and 'solar' for the respective means of the distributions.
        limit: Dict with keys 'wind' and 'solar' for the respective capacity limits, which are driven by land availability
        and physical spacing constraints. Units: scaling factor w.r.t. baseload demand (-).
        seed: random seed for reproducibility.
    """
    C_w_samples, C_s_samples = _draw_overscale_samples(n_samples, mus, limit, seed)

    # Battery overscale factor for every sample, computed in one vectorised pass: build the
    # (n, T) net-energy matrix once and size all batteries in a compiled kernel (was a pure-Python
    # loop calling estimate_battery_capacity per sample).
    net_energy = np.ascontiguousarray(
        (np.outer(profile["solar"], C_s_samples) + np.outer(profile["wind"], C_w_samples) - 1.0).T
    )
    deficit_vals = np.percentile(net_energy, p, axis=1)
    batteries = estimate_battery_capacity_batch(net_energy, deficit_vals, 100 - p)

    return [{"wind": C_w_samples[i], "solar": C_s_samples[i], "battery": batteries[i]} for i in range(n_samples)]


def _draw_overscale_samples(
    n_samples: int,
    mus: dict[str, float],
    limit: dict[str, float] | None,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Draw the wind and solar overscale-factor samples (the RNG part of `capacity_sampling`),
    shared between `capacity_sampling` and `optimize_point` so both produce identical designs.
    Returns (C_w_samples, C_s_samples). Draw order (wind then solar) is preserved so the RNG
    sequence matches the original implementation for a given seed.

    Uses a thread-local RandomState (Mersenne Twister, same algorithm as the legacy
    np.random.seed/np.random.exponential pair) so concurrent threads can call this without
    racing on numpy's module-global RNG. Bit-identical to the legacy global-state version
    for any given seed.
    """
    rng = np.random.RandomState(seed)
    if limit is None:
        C_w_samples = rng.exponential(scale=mus["wind"], size=n_samples)
        C_s_samples = rng.exponential(scale=mus["solar"], size=n_samples)
    else:
        C_w_samples = rng.uniform(size=n_samples, low=0, high=limit["wind"])
        C_s_samples = np.clip(rng.exponential(scale=mus["solar"], size=n_samples), 0, limit["solar"])
    return C_w_samples, C_s_samples


@numba.njit(cache=True, nogil=True)
def _percentile_linear(sorted_a: np.ndarray, q: float) -> float:
    """np.percentile default ('linear' interpolation) for a pre-sorted 1D array."""
    m = sorted_a.shape[0]
    if m == 0:
        return 0.0
    if m == 1:
        return sorted_a[0]
    rank = q / 100.0 * (m - 1)
    lo = int(np.floor(rank))
    frac = rank - lo
    if lo + 1 < m:
        return sorted_a[lo] + frac * (sorted_a[lo + 1] - sorted_a[lo])
    return sorted_a[lo]


@numba.njit(cache=True, nogil=True)
def estimate_battery_capacity_batch(net_energy: np.ndarray, deficit_vals: np.ndarray, q_duration: float) -> np.ndarray:
    """
    Vectorised equivalent of `estimate_battery_capacity` for many designs at once (compiled).

    Parameters:
        net_energy: (n, T) net-energy matrix (one row per design).
        deficit_vals: (n,) q_deficit percentile of each row (computed with np.percentile outside —
            NumPy's C implementation is faster than sorting inside numba).
        q_duration: percentile to capture the duration of contiguous deficits.

    Returns (n,) battery overscale factors (hours), identical to looping estimate_battery_capacity.
    """
    n, T = net_energy.shape
    out = np.zeros(n)
    durations = np.empty(T)
    for d in range(n):
        if deficit_vals[d] > 0:
            continue
        count = 0
        current = 0
        for t in range(T):
            if net_energy[d, t] < 0:
                current += 1
            else:
                if current > 0:
                    durations[count] = current
                    count += 1
                    current = 0
        if current > 0:
            durations[count] = current
            count += 1
        if count == 0:
            duration_val = 0.0
        else:
            duration_val = _percentile_linear(np.sort(durations[:count]), q_duration)
        out[d] = abs(deficit_vals[d]) * duration_val
    return out


@numba.njit(cache=True, nogil=True)
def coverage_batch(net_energy: np.ndarray, batteries: np.ndarray) -> np.ndarray:
    """
    Vectorised equivalent of state_of_charge() + calculate_coverage() for many designs at once.

    For each design d, mirrors the originals exactly:
        soc[t]     = clip(soc[t-1] + net_energy[t], 0, battery)   (soc[-1] treated as 0)
        covered[t] = (soc[t-1] + net_energy[t]) >= 0              (t=0 uses soc[-1]=0 => net[0]>=0)
    Returns (n,) mean hourly coverage.
    """
    n, T = net_energy.shape
    coverage = np.zeros(n)
    for d in range(n):
        cap = batteries[d]
        prev = 0.0
        covered = 0
        for t in range(T):
            x = prev + net_energy[d, t]
            if x >= 0.0:
                covered += 1
            cur = x
            if cur < 0.0:
                cur = 0.0
            elif cur > cap:
                cur = cap
            prev = cur
        coverage[d] = covered / T
    return coverage


def estimate_battery_capacity(net_energy: np.ndarray, q_deficit: float = 5, q_duration: float = 5) -> float:
    """
    [REFERENCE IMPLEMENTATION — no current callers in production; only used by
    tests/test_logic_parity.py for parity testing]

    Replaced in production by `estimate_battery_capacity_batch` (numba @njit, processes
    n designs in one compiled pass; bit-identical results). Kept as the readable scalar
    spec of the algorithm.

    Approximate battery capacity needed from a net energy time series by:
      - Taking the absolute value of the q_deficit percentile of net energy. This represents how often the demand is not covered. The deficit_val is negative
      if a deficits occurs more then q_deficit percent of the time. If the deficit_val is positive, it means that the demand is covered at least 1-q_deficit
      percent of the time already, so no battery is needed.
      - Multiplying it by the q_duration of the contiguous negative-duration (in hours). This represents for how long the demand is not covered at a time
      (e.g., size of windows of no solar or wind generation).

    Parameters:
      net_energy (array-like): Hourly net energy (normalised by baseload demand; demand = 1).
      q_deficit (float): Percentile to capture the depth of deficits (e.g. 5 means 5th percentile).
      q_duration (float): Percentile to capture the duration of contiguous deficits (e.g. 95 means 95th percentile).

    Returns:
      battery_capacity (float): Battery overscale factor w.r.t. demand.
        Units: **hours** (baseload-hours). Computed as (normalised-MW depth) × (hours duration),
        so installed_MWh = battery_capacity × baseload_MW. Unlike the solar/wind overscale
        factors (which are dimensionless), this one carries an implicit time dimension.
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


@numba.jit(cache=True, nogil=True)
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
    [REFERENCE IMPLEMENTATION — no current callers in production; called by the reference
    `filter_designs_according_to_coverage_and_calculate_costs` and directly by
    tests/test_logic_parity.py]

    Replaced in production by `coverage_batch` (numba, all designs in one pass). Kept as the
    readable scalar spec. Note its sibling `state_of_charge` is NOT a reference — it is still
    called directly in production to compute the chosen design's state of charge.

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
        callsites (precompute_point_state, the single-point API time-series path)
        always satisfy the contract because supply is a non-negative linear combo
        of non-negative profiles.

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


def correct_battery_capex_for_modular_installation(
    battery_capex: np.ndarray,
    battery_overscaling_factor: float,
) -> np.ndarray:
    """
    Installing a module with several battery units at once is cheaper than installing many single units. Corrects the battery installation CAPEX as:
        battery_capex * (battery_overscaling_factor/AVERAGE_IMPLIED_STORAGE)^BATTERY_UNIT_CAPEX_SCALING_FACTOR

    The storage capacity is set to the installed battery capacity here (= overbuilding factor with respect to demand). The rate of battery
    discharge is not considered. If the overscaling factor is 0, the battery CAPEX is not corrected.

    Note: `battery_overscaling_factor` has units of **hours** (baseload-hours), not MWh.
    """
    if battery_overscaling_factor > 0:
        return (
            battery_capex * (battery_overscaling_factor / AVERAGE_IMPLIED_STORAGE) ** BATTERY_UNIT_CAPEX_SCALING_FACTOR
        )
    else:
        return battery_capex


def filter_designs_according_to_coverage_and_calculate_costs(
    designs: list[dict[str, float]],
    baseload_demand: float,
    capex: dict[str, np.ndarray],
    opex_pct: dict[str, float],
    profile: dict[str, np.ndarray],
    cost_of_capital: float,
    investment_horizon: int,
    p: float,
) -> tuple[np.ndarray, list[float], list[float], list[float], list[dict[str, float]]]:
    """
    [REFERENCE IMPLEMENTATION — no current callers in production; only used by
    tests/test_logic_parity.py]

    Replaced in production by `optimize_point` (with `precompute_point_state` /
    `compute_lcoe_from_state`), which vectorises the coverage filter and costing across all
    designs. Kept as the readable scalar spec, paired with `capacity_sampling`.

    1. Filter the feasible designs according to the hourly coverage of the full system (solar, wind, battery), which must be
    above a certain percentile -> accepted designs.
    2. Calculate the installation costs (total and per-tech), LCOE, and hourly coverage for the accepted designs.
    """

    # Filter the feasible designs according to their coverage -> accepted designs
    accepted_designs = []
    installation_costs = []
    installation_cost_breakdowns = []
    coverages = []
    lcoes = []
    # Snapshot the raw per-country battery CAPEX so each design's modular correction starts from
    # the same source, not from the previous iteration's already-corrected value.
    original_battery_capex = capex["battery"]
    for design in designs:
        # TODO: Return from previous function to avoid recalculating
        net_energy = calculate_net_energy_production(design["solar"], profile["solar"], design["wind"], profile["wind"])
        soc = state_of_charge(net_energy, design["battery"])
        coverage = calculate_coverage(soc, net_energy)
        if coverage >= 1 - p / 100:
            accepted_designs.append(design)
            coverages.append(float(coverage))
            # Correct battery CAPEX for modular installation
            capex["battery"] = correct_battery_capex_for_modular_installation(
                original_battery_capex,
                design["battery"],
            )
            # Extract CAPEX for the first year of the investment horizon
            initial_costs = dict(
                cost_solar=capex["solar"][0],  # per MW overscaling for solar
                cost_wind=capex["wind"][0],  # per MW overscaling for wind
                cost_battery=capex["battery"][0],  # per MW overscaling for battery
            )
            ic_breakdown = calculate_installation_cost(
                design["solar"] * baseload_demand,
                design["wind"] * baseload_demand,
                design["battery"] * baseload_demand,
                **initial_costs,
            )
            installation_cost_breakdowns.append(ic_breakdown)
            installation_costs.append(sum(ic_breakdown.values()))
            x = {tech: design[tech] * baseload_demand for tech in ["solar", "wind", "battery"]}
            # Note: Important to use curtailment in the LCOE calculation; otherwise, the minimum LCOE is just the one which installs as much solar as possible
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
                    realised_delivery_fraction=float(coverage),
                )
            )
    accepted_designs_array = np.array(accepted_designs, dtype=object)
    N_samples = len(designs)
    accepted_proposals = len(accepted_designs) / N_samples
    logging.debug(f"Accepted proposals: {accepted_proposals}")

    return accepted_designs_array, installation_costs, lcoes, coverages, installation_cost_breakdowns


@dataclass(frozen=True, eq=False)
class PointDesignState:
    """
    Year-independent per-point compute output. Holds an (m, 3) array of overscale
    factors (columns: solar, wind, battery) and per-design coverage.

    Two forms exist:
      - "full" (from `precompute_point_state`): m == n_samples; `accepted_mask` is set,
        identifying which designs passed the coverage filter. Used by `optimize_point`
        and the single-point API (intermediates need every sampled design).
      - "filtered" (from `filter_to_accepted()` or a cache load): m == n_accepted;
        `accepted_mask` is None. Used by the design-cache query path — all designs
        in the array have already passed the coverage filter.
    """

    designs: np.ndarray  # (m, 3) float64, columns: solar / wind / battery
    coverage: np.ndarray  # (m,) float64
    accepted_mask: np.ndarray | None = None  # (m,) bool, or None if pre-filtered

    def filter_to_accepted(self) -> "PointDesignState":
        """Return a new state containing only accepted designs (for cache write)."""
        if self.accepted_mask is None:
            return self
        m = self.accepted_mask
        return PointDesignState(
            designs=self.designs[m],
            coverage=self.coverage[m],
            accepted_mask=None,
        )


def min_survivors_required(n: int, fraction: float = MIN_SURVIVOR_FRACTION) -> int:
    """
    Number of designs that must clear the coverage filter before a point's optimum is
    trusted (see `MIN_SURVIVOR_FRACTION`). Never below 1, so `fraction <= 0` reproduces
    the original "at least one survivor" behaviour exactly.
    """
    if fraction <= 0:
        return 1
    return max(1, int(np.ceil(fraction * n)))


def precompute_point_state(
    solar_profile: np.ndarray,
    wind_profile: np.ndarray,
    p: float,
    n: int,
    seed: int,
    mus: dict[str, float],
    limit: dict[str, float] | None = None,
) -> PointDesignState:
    """
    Year-independent compute for one grid point: Monte Carlo sample → net-energy matrix
    → deficit percentile → battery sizing → coverage → accepted mask. Returns the full
    n-design state; downstream code picks the optimum based on cost data (see
    `compute_lcoe_from_state`).

    All quantities are dimensionless overscale factors relative to demand = 1 unit;
    `baseload_demand` enters only at the LCOE step.
    """
    C_w, C_s = _draw_overscale_samples(n, mus, limit, seed)
    net_energy = np.ascontiguousarray((np.outer(solar_profile, C_s) + np.outer(wind_profile, C_w) - 1.0).T)
    deficit_vals = np.percentile(net_energy, p, axis=1)
    batteries = estimate_battery_capacity_batch(net_energy, deficit_vals, 100 - p)
    coverage = coverage_batch(net_energy, batteries)
    accepted = coverage >= 1 - p / 100.0
    designs = np.column_stack([C_s, C_w, batteries])
    return PointDesignState(designs=designs, coverage=coverage, accepted_mask=accepted)


def compute_lcoe_from_state(
    state: PointDesignState,
    baseload_demand: float,
    capex: dict[str, np.ndarray],
    opex_pct: dict[str, float],
    cost_of_capital: float,
    investment_horizon: int,
) -> dict[str, np.ndarray]:
    """
    Year-dependent LCOE pass for every design in `state`. Returns a dict of arrays
    (each length m): `lcoes`, `installation_costs`, `ic_solar`, `ic_wind`, `ic_battery`.
    """
    installed_solar = state.designs[:, 0] * baseload_demand
    installed_wind = state.designs[:, 1] * baseload_demand
    installed_battery = state.designs[:, 2] * baseload_demand
    # Per-design realised coverage scales the LCOE denominator so each design is divided by
    # the demand it actually served, not the nameplate baseload. With the year-1 ERA5
    # dispatch we have today, this is the year-1 realised coverage (a slight over-estimate
    # of horizon-average coverage)
    lcoes, installation_costs, ic_solar, ic_wind, ic_battery = calculate_lcoe_of_re_installation_vectorised(
        investment_horizon,
        installed_solar,
        installed_wind,
        installed_battery,
        baseload_demand,
        capex,
        opex_pct,
        cost_of_capital,
        realised_delivery_fraction=state.coverage,
    )
    return {
        "lcoes": lcoes,
        "installation_costs": installation_costs,
        "ic_solar": ic_solar,
        "ic_wind": ic_wind,
        "ic_battery": ic_battery,
    }


@overload
def optimize_point(
    profile: dict[str, np.ndarray],
    p: float,
    baseload_demand: float,
    capex: dict[str, np.ndarray],
    opex_pct: dict[str, float],
    cost_of_capital: float,
    investment_horizon: int,
    n: int,
    limit: dict[str, float] | None,
    seed: int,
    mus: dict[str, float],
    return_intermediates: Literal[False] = False,
    *,
    min_survivors: int = 1,
) -> dict | None: ...


@overload
def optimize_point(
    profile: dict[str, np.ndarray],
    p: float,
    baseload_demand: float,
    capex: dict[str, np.ndarray],
    opex_pct: dict[str, float],
    cost_of_capital: float,
    investment_horizon: int,
    n: int,
    limit: dict[str, float] | None,
    seed: int,
    mus: dict[str, float],
    *,
    return_intermediates: Literal[True],
    min_survivors: int = 1,
) -> tuple[dict | None, dict]: ...


def optimize_point(
    profile: dict[str, np.ndarray],
    p: float,
    baseload_demand: float,
    capex: dict[str, np.ndarray],
    opex_pct: dict[str, float],
    cost_of_capital: float,
    investment_horizon: int,
    n: int,
    limit: dict[str, float] | None,
    seed: int,
    mus: dict[str, float],
    return_intermediates: bool = False,
    *,
    min_survivors: int = 1,
) -> dict | tuple[dict | None, dict] | None:
    """
    Thin wrapper around `precompute_point_state` + `compute_lcoe_from_state`: runs the
    full per-point pipeline (sample → coverage filter → LCOE → argmin). Preserved as
    the backward-compatible entry point for the single-point API (the GLOBAL path
    uses the split functions directly via the design cache).

    `min_survivors` is the minimum number of designs that must clear the coverage
    filter for the argmin to be trusted (see `min_survivors_required`); the default
    of 1 keeps the original behaviour.

    Returns:
      - `return_intermediates=False` (default): optimal design dict, or None if fewer
        than `min_survivors` sampled designs met the coverage threshold.
      - `return_intermediates=True`: `(optimum, intermediates)` tuple, where
        `intermediates` carries the full design space (samples, accepted mask, LCOEs,
        costs, coverages) for plotting / API plot-data payloads.
    """
    state = precompute_point_state(
        profile["solar"],
        profile["wind"],
        p,
        n,
        seed,
        mus=mus,
        limit=limit,
    )
    accepted = state.accepted_mask
    assert accepted is not None  # precompute always returns the full form

    if int(accepted.sum()) < min_survivors:
        if return_intermediates:
            return None, {
                "feasible_designs": {
                    "solar": state.designs[:, 0],
                    "wind": state.designs[:, 1],
                    "battery": state.designs[:, 2],
                },
                "accepted_mask": accepted,
                "lcoes": np.zeros(n),
                "installation_costs": np.zeros(n),
                "installation_cost_breakdowns": {
                    "solar": np.zeros(n),
                    "wind": np.zeros(n),
                    "battery": np.zeros(n),
                },
                "coverages": state.coverage,
            }
        return None

    # Only the accepted designs contribute to the optimum (argmin) and to the
    # consumed intermediates fields (every reader downstream slices the lcoes /
    # installation_costs arrays by accepted_mask). Filter the state before the
    # LCOE pass so rejected designs — including any with coverage == 0, which
    # would divide-by-zero in the realised-coverage denominator — never enter
    # the calculation. Mirrors what the global build/query paths already do via
    # the design cache.
    state_acc = state.filter_to_accepted()
    lcoe = compute_lcoe_from_state(
        state_acc,
        baseload_demand,
        capex,
        opex_pct,
        cost_of_capital,
        investment_horizon,
    )
    lcoes_acc = lcoe["lcoes"]

    # `argmin` is over the filtered array; map back to the original sample index
    # so callers still see the design's position in the full (n,) state.
    accepted_idx = np.where(accepted)[0]
    k_acc = int(np.argmin(lcoes_acc))
    k = int(accepted_idx[k_acc])

    # Refine the picked optimum's LCOE: re-dispatch on this one design and swap
    # the denominator from binary coverage to dispatch-aware served_fraction.
    coverage_pick = float(state.coverage[k])
    lcoe_coverage_based = float(lcoes_acc[k_acc])
    net_nrg_pick = state.designs[k, 0] * profile["solar"] + state.designs[k, 1] * profile["wind"] - 1.0
    soc_pick = state_of_charge(net_nrg_pick, float(state.designs[k, 2]))
    served_fraction_pick = calculate_served_fraction(soc_pick, net_nrg_pick)
    lcoe_refined = lcoe_coverage_based * coverage_pick / served_fraction_pick

    optimum = {
        "design": {
            "solar": float(state.designs[k, 0]),
            "wind": float(state.designs[k, 1]),
            "battery": float(state.designs[k, 2]),
        },
        "lcoe": float(lcoe_refined),
        "lcoe_coverage_based": lcoe_coverage_based,
        "installation_cost": float(lcoe["installation_costs"][k_acc]),
        "installation_cost_breakdown": {
            "solar": float(lcoe["ic_solar"][k_acc]),
            "wind": float(lcoe["ic_wind"][k_acc]),
            "battery": float(lcoe["ic_battery"][k_acc]),
        },
        "coverage": coverage_pick,
        "served_fraction": float(served_fraction_pick),
    }

    if return_intermediates:
        # Re-expand the (n_accepted,) LCOE/cost arrays back to (n,) for the
        # intermediates payload, with zero in the rejected slots. This matches
        # the existing schema (downstream consumers always slice by
        # accepted_mask) and the `if not accepted.any()` early-return above,
        # which also uses zeros for "uncomputed".
        def _expand(values: np.ndarray) -> np.ndarray:
            full = np.zeros(n)
            full[accepted] = values
            return full

        return optimum, {
            "feasible_designs": {
                "solar": state.designs[:, 0],
                "wind": state.designs[:, 1],
                "battery": state.designs[:, 2],
            },
            "accepted_mask": accepted,
            "lcoes": _expand(lcoes_acc),
            "installation_costs": _expand(lcoe["installation_costs"]),
            "installation_cost_breakdowns": {
                "solar": _expand(lcoe["ic_solar"]),
                "wind": _expand(lcoe["ic_wind"]),
                "battery": _expand(lcoe["ic_battery"]),
            },
            "coverages": state.coverage,
        }
    return optimum


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
