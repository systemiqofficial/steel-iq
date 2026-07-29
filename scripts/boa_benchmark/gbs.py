"""Grid-Bisection Search (GBS): an exact-metric ground-truth design search for
the BOA benchmark -- the replacement for the `hours`-coverage MILP that used to live in
`pypsa_model.py` (removed; see that module's docstring and `README.md`). Named for its two
components: a coarse-to-fine grid search over `(solar, wind)`, with battery sized at every
point by a 1-D bisection (`b_min`) -- see below for why this decomposition is valid.

Why this exists rather than a MILP
----------------------------------
The old hours-coverage MILP had a structurally stuck bound: `covered[t]` appeared only in
`unserved[t] <= D * (1 - covered[t])` and `sum(covered) >= (1-p)*N`, so for any fixed
`unserved` vector the LP relaxation's best choice was `covered[t] = 1 - unserved[t]/D`, and
the cardinality constraint collapsed to `sum(unserved) <= p*N*D` -- *literally* the `energy`
metric's LP (still in `pypsa_model.py`). The root relaxation of the "hours" MILP was
therefore the "energy" LP, the measured gap was exactly the distance between the two
metrics' optima, and branching moved it by O(1/N) per node. No solver swap or warm start
fixes that; only cuts coupling hours to each other would, and the payoff isn't worth it
because the MILP was answering a subtly *wrong* question anyway (see "Dispatch" below).

Two structural facts make a direct search strictly better here:

1. **The objective is monotone and separable.** With `use_curtailment=True`,
   `calculate_lcoe_of_re_installation`'s denominator is `baseload_demand * HOURS_IN_YEAR`
   summed over the horizon -- *independent of the design*. So LCOE is a positive constant
   times total cost, and total cost is additive across solar/wind/battery and strictly
   increasing in each overscale factor.
2. **Feasibility is monotone.** Raising any of solar/wind/battery raises `net_energy`
   (solar/wind) or the storage ceiling (battery) pointwise, which raises the SOC
   trajectory pointwise by induction through the clipped recursion, which can only turn
   uncovered hours into covered ones. The feasible set is therefore up-closed in
   (solar, wind, battery).

Together: at any fixed (solar, wind) the optimal battery is the *smallest feasible* one,
so `b_min(s, w)` is a 1-D bisection over a monotone predicate, and the whole problem
reduces to a 2-D search over (solar, wind). Every evaluation runs BOA's own exact dispatch
and coverage rule, so unlike the MILP there is no linearized battery capex, no
optimal-foresight dispatch, and no rescoring mismatch between what was optimized and what
is reported.

**Dispatch.** BOA dispatches greedily (`boa_logic.state_of_charge`: charge every surplus,
discharge every deficit). For minimizing unserved *energy* that is provably optimal, which
is why the energy-mode LP and BOA agree exactly. For maximizing *covered hours* it is not:
perfect foresight will abandon one deep-deficit hour to fully serve two shallow ones later.
The MILP's free dispatch therefore returns designs BOA's own simulator scores *below* the
threshold -- an optimistic ground truth. This module inherits BOA's greedy dispatch, so the
design GBS reports is attainable by construction.

**Exactness of the metric.** `_coverage_jit` reproduces `boa_logic.calculate_coverage` and
`design_metrics.simulate_design` bit-for-bit, including two quirks that are deliberately
preserved rather than "fixed", because the BOA side of the benchmark is filtered on them:
  - the hours metric's hour 0 tests `net_energy[0] >= 0` (i.e. as if the battery were empty)
    even in `soc_mode="cyclic"`, whereas the energy metric's hour 0 uses the carried-over
    `soc[-1]`;
  - the coverage test uses raw `soc[t-1] + net_energy[t]`, without applying `standing_loss`
    to `soc[t-1]`, while the dispatch recursion does apply it.
`--self-test` asserts this agreement against the unmodified functions.

**What this is not.** This is a fine-grid numerical search, not a certified optimum. Its
honest error statistic is `refinement_delta` (how far the incumbent moved on the final grid
refinement); `--validate` additionally checks the machinery against a genuinely certified
optimum by running the search under the *energy* metric with the PyPSA LP's own linear
objective, where `pypsa_model` provides a certified LP answer to compare against. Chain:
LP certifies the search, search handles hours.

**Multiple weather years.** `find_robust_gbs_design` generalizes `find_gbs_design` to a
list of profiles (e.g. several weather years at one site), returning the design that meets
`coverage_p` in every one of them at once -- see its docstring for why the same
monotonicity argument extends cleanly (the per-profile `b_min` grids' elementwise max is
still pointwise-optimal). Used by `run_weather_year_sensitivity.py`.
"""

import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numba
import numpy as np
import yaml
from baseload_optimisation_atlas.boa_logic import calculate_net_energy_production

from cost_inputs import BenchmarkCosts, load_benchmark_costs
from design_metrics import score_lcoe
from point_profile import load_point_profile

logger = logging.getLogger(__name__)

_HOURS = 0
_ENERGY = 1
_METRIC_CODE = {"hours": _HOURS, "energy": _ENERGY}

# Bisection tolerances. `B_TOL` is in overscale-factor units and bounds how far above the
# true minimum battery `b_min` can land; because cost increases in battery, erring high
# makes GBS marginally *pessimistic*, i.e. it understates BOA's gap rather than
# flattering the benchmark. `SOC0_TOL_REL` is relative to battery size -- production
# `cyclic_soc.state_of_charge_cyclic` hardcodes an absolute 1e-9, which is ~40 bisection
# steps of pointless precision inside an already-nested loop.
B_TOL = 1e-4
SOC0_TOL_REL = 1e-7
B_MAX = 500.0  # overscale factors above this are treated as "no battery can fix this (s,w)"


@dataclass
class GBSDesign:
    design: dict[str, float]
    lcoe: float
    objective: float  # value of whichever objective was minimized (== lcoe for objective="lcoe")
    coverage: float  # realized coverage under the selected metric, at the returned design
    metric: str
    soc_mode: str
    refinement_delta: float  # relative objective change on the final refinement stage
    on_boundary: bool  # True if the optimum sits on the edge of the search box (widen it)
    n_evaluations: int
    search_seconds: float


# --------------------------------------------------------------------------------------
# Jitted core: BOA's dispatch + coverage, fused so no 8760-length SOC array is materialized
# --------------------------------------------------------------------------------------


@numba.njit(cache=True)
def _soc_end(net_energy: np.ndarray, battery_capacity: float, soc0: float, standing_loss: float) -> float:
    """Final SOC after one pass of `cyclic_soc.simulate_soc`, without allocating the path."""
    keep = 1.0 - standing_loss
    prev = soc0
    for t in range(len(net_energy)):
        prev = min(max(prev * keep + net_energy[t], 0.0), battery_capacity)
    return prev


@numba.njit(cache=True)
def _cyclic_soc0(net_energy: np.ndarray, battery_capacity: float, standing_loss: float) -> float:
    """Periodic initial SOC, replicating `cyclic_soc.state_of_charge_cyclic`'s bisection
    (including its two degenerate boundary branches) but returning only `soc0`."""
    if battery_capacity <= 0.0:
        return 0.0
    lo = 0.0
    hi = battery_capacity
    if _soc_end(net_energy, battery_capacity, lo, standing_loss) - lo < 0.0:
        return lo
    if _soc_end(net_energy, battery_capacity, hi, standing_loss) - hi > 0.0:
        return hi
    tol = SOC0_TOL_REL * battery_capacity
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if _soc_end(net_energy, battery_capacity, mid, standing_loss) - mid >= 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@numba.njit(cache=True)
def _coverage_jit(
    net_energy: np.ndarray, battery_capacity: float, cyclic: bool, standing_loss: float, metric: int
) -> float:
    """Coverage under BOA's greedy dispatch, matching `design_metrics.simulate_design`.

    `metric == _HOURS` reproduces `boa_logic.calculate_coverage`; `metric == _ENERGY`
    reproduces `simulate_design`'s `energy_coverage`. See the module docstring for the two
    hour-0 conventions, which genuinely differ between the metrics and are preserved here.
    """
    n = len(net_energy)
    keep = 1.0 - standing_loss
    soc0 = _cyclic_soc0(net_energy, battery_capacity, standing_loss) if cyclic else 0.0

    covered = 0.0
    unmet = 0.0
    if metric == _HOURS:
        # Hour 0 ignores any carried-over SOC, even when cyclic (calculate_coverage's rule).
        if net_energy[0] >= 0.0:
            covered = 1.0
    else:
        # Hour 0 uses soc[-1], which at the cyclic fixed point equals soc0 (0.0 if empty-start).
        deficit = soc0 + net_energy[0]
        if deficit < 0.0:
            unmet = -deficit

    prev = min(max(soc0 * keep + net_energy[0], 0.0), battery_capacity)
    for t in range(1, n):
        # Coverage tests the *un-decayed* previous SOC; the dispatch below applies `keep`.
        deficit = prev + net_energy[t]
        if metric == _HOURS:
            if deficit >= 0.0:
                covered += 1.0
        elif deficit < 0.0:
            unmet -= deficit
        prev = min(max(prev * keep + net_energy[t], 0.0), battery_capacity)

    if metric == _HOURS:
        return covered / n
    return 1.0 - unmet / n


@numba.njit(cache=True)
def _b_min_jit(net_energy: np.ndarray, target: float, cyclic: bool, standing_loss: float, metric: int) -> float:
    """Smallest battery overscale factor reaching `target` coverage, or `inf` if none does.

    Valid because coverage is non-decreasing in battery capacity (module docstring), so the
    predicate is monotone and bisection converges to the jump point of what is, for the
    hours metric, a step function. Returns the feasible (upper) side of the final bracket.
    """
    if _coverage_jit(net_energy, 0.0, cyclic, standing_loss, metric) >= target:
        return 0.0

    hi = 0.25
    while hi <= B_MAX:
        if _coverage_jit(net_energy, hi, cyclic, standing_loss, metric) >= target:
            break
        hi *= 2.0
    if hi > B_MAX:
        return np.inf

    lo = 0.0 if hi == 0.25 else 0.5 * hi
    while hi - lo > B_TOL:
        mid = 0.5 * (lo + hi)
        if _coverage_jit(net_energy, mid, cyclic, standing_loss, metric) >= target:
            hi = mid
        else:
            lo = mid
    return hi


@numba.njit(parallel=True, cache=True)
def _b_min_grid(
    solar_profile: np.ndarray,
    wind_profile: np.ndarray,
    s_vals: np.ndarray,
    w_vals: np.ndarray,
    target: float,
    cyclic: bool,
    standing_loss: float,
    metric: int,
) -> np.ndarray:
    """`b_min` over a (solar, wind) grid, one thread per solar row."""
    out = np.empty((len(s_vals), len(w_vals)))
    for i in numba.prange(len(s_vals)):
        for j in range(len(w_vals)):
            net_energy = s_vals[i] * solar_profile + w_vals[j] * wind_profile - 1.0
            out[i, j] = _b_min_jit(net_energy, target, cyclic, standing_loss, metric)
    return out


# --------------------------------------------------------------------------------------
# Objectives
# --------------------------------------------------------------------------------------


def _lcoe_objective(baseload_demand: float, costs: BenchmarkCosts, profile: dict[str, np.ndarray]):
    """BOA's exact LCOE (`design_metrics.score_lcoe`) -- the number the benchmark reports."""

    def objective(s: float, w: float, b: float) -> float:
        return score_lcoe({"solar": s, "wind": w, "battery": b}, baseload_demand, costs, profile)

    return objective


def _pypsa_linear_objective(baseload_demand: float, costs: BenchmarkCosts):
    """The *linear* annualized-capex proxy `pypsa_model.solve_optimal_design` actually
    minimizes (battery priced at the reference `average_implied_storage` size).

    Only used by `--validate`: comparing the search against the PyPSA LP is only meaningful
    if both minimize the same objective, otherwise a disagreement says nothing about
    whether the search machinery is correct.
    """
    from baseload_optimisation_atlas.boa_config import LIFETIMES
    from baseload_optimisation_atlas.boa_logic import correct_battery_capex_for_modular_installation
    from pypsa_model import _capital_recovery_factor

    capex_solar = costs.capex["solar"][0]
    capex_wind = costs.capex["wind"][0]
    reference_battery_size = costs.storage_costs["average_implied_storage"][0]
    capex_battery = correct_battery_capex_for_modular_installation(costs.storage_costs, reference_battery_size)[0]

    a_s = capex_solar * _capital_recovery_factor(costs.cost_of_capital, LIFETIMES["solar"]) + (
        costs.opex_pct["solar"] * capex_solar
    )
    a_w = capex_wind * _capital_recovery_factor(costs.cost_of_capital, LIFETIMES["wind"]) + (
        costs.opex_pct["wind"] * capex_wind
    )
    a_b = capex_battery * _capital_recovery_factor(costs.cost_of_capital, LIFETIMES["battery"]) + (
        costs.opex_pct["battery"] * capex_battery
    )

    def objective(s: float, w: float, b: float) -> float:
        return baseload_demand * (a_s * s + a_w * w + a_b * b)

    return objective


# --------------------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------------------


def _select_seeds(values: np.ndarray, k: int, min_separation: int) -> list[tuple[int, int]]:
    """Best `k` grid cells, greedily forced at least `min_separation` cells apart, so the
    refinement stages explore distinct basins instead of crowding one minimum."""
    finite = np.isfinite(values)
    if not finite.any():
        return []
    order = np.argsort(values, axis=None)
    seeds: list[tuple[int, int]] = []
    for flat in order:
        i, j = np.unravel_index(flat, values.shape)
        if not finite[i, j]:
            break
        if all(max(abs(i - si), abs(j - sj)) >= min_separation for si, sj in seeds):
            seeds.append((int(i), int(j)))
        if len(seeds) == k:
            break
    return seeds


def _grid_bisection_search(
    battery_grid_fn,
    cost_of,
    s_max: float,
    w_max: float,
    coarse_grid: int,
    refine_grid: int,
    n_refinements: int,
    n_seeds: int,
    infeasible_msg: str,
) -> tuple[float, float, float, float, float, int]:
    """Coarse-to-fine (solar, wind) search shared by `find_gbs_design` and
    `find_robust_gbs_design` -- `battery_grid_fn(s_vals, w_vals) -> battery array` is the
    only thing that differs between them (one profile's `b_min` grid vs. the elementwise
    max of several). See `find_gbs_design`'s docstring for why `n_refinements`, not
    `coarse_grid`, is the budget knob to sweep.

    Returns `(best_objective, s_star, w_star, b_star, refinement_delta, n_evaluations)`.
    """
    n_evaluations = 0

    def evaluate(s_vals: np.ndarray, w_vals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        nonlocal n_evaluations
        batteries = battery_grid_fn(s_vals, w_vals)
        n_evaluations += batteries.size
        objectives = np.full(batteries.shape, np.inf)
        for i in range(len(s_vals)):
            for j in range(len(w_vals)):
                if not np.isfinite(batteries[i, j]):
                    continue
                if s_vals[i] <= 0.0 and w_vals[j] <= 0.0:
                    continue  # score_lcoe divides by generated electricity when computing curtailment
                objectives[i, j] = cost_of(s_vals[i], w_vals[j], batteries[i, j])
        return batteries, objectives

    s_vals = np.linspace(0.0, s_max, coarse_grid)
    w_vals = np.linspace(0.0, w_max, coarse_grid)
    batteries, objectives = evaluate(s_vals, w_vals)

    seeds = _select_seeds(objectives, n_seeds, min_separation=max(2, coarse_grid // 10))
    if not seeds:
        raise RuntimeError(f"No feasible design in [0,{s_max}] x [0,{w_max}] {infeasible_msg} -- widen the search box.")

    # Each basin carries its own local box and is refined independently, so a second,
    # distant minimum can't be lost by the incumbent's box walking away from it. The
    # reported answer is the best across basins; `refinement_delta` is how far that
    # incumbent moved on the final stage -- the honest convergence statistic that stands in
    # for a MIP gap here.
    basins = [
        (float(s_vals[i]), float(w_vals[j]), float(s_vals[1] - s_vals[0]), float(w_vals[1] - w_vals[0]))
        for i, j in seeds
    ]
    # Seed `best` from the coarse grid's own best point (not None) so `n_refinements=0`
    # returns that answer directly instead of spuriously hitting the "no feasible design"
    # check below -- a real budget level (the cheapest one), not just a degenerate no-op.
    best_seed = seeds[0]
    best: tuple[float, float, float, float] = (
        float(objectives[best_seed]),
        float(s_vals[best_seed[0]]),
        float(w_vals[best_seed[1]]),
        float(batteries[best_seed]),
    )
    previous_best: float = best[0]
    refinement_delta = np.inf

    for _stage in range(n_refinements):
        stage_best = None
        next_basins = []
        for s_center, w_center, s_step, w_step in basins:
            local_s = np.linspace(max(0.0, s_center - 1.5 * s_step), s_center + 1.5 * s_step, refine_grid)
            local_w = np.linspace(max(0.0, w_center - 1.5 * w_step), w_center + 1.5 * w_step, refine_grid)
            local_b, local_obj = evaluate(local_s, local_w)
            if not np.isfinite(local_obj).any():
                continue
            bi, bj = np.unravel_index(np.argmin(local_obj), local_obj.shape)
            candidate = (float(local_obj[bi, bj]), float(local_s[bi]), float(local_w[bj]), float(local_b[bi, bj]))
            next_basins.append(
                (candidate[1], candidate[2], float(local_s[1] - local_s[0]), float(local_w[1] - local_w[0]))
            )
            if stage_best is None or candidate[0] < stage_best[0]:
                stage_best = candidate

        if stage_best is None:
            break
        basins = next_basins
        if stage_best[0] < best[0]:
            best = stage_best
        refinement_delta = abs(best[0] - previous_best) / max(abs(best[0]), 1e-12)
        previous_best = best[0]

    best_objective, s_star, w_star, b_star = best
    return best_objective, s_star, w_star, b_star, refinement_delta, n_evaluations


def find_gbs_design(
    profile: dict[str, np.ndarray],
    baseload_demand: float,
    costs: BenchmarkCosts,
    coverage_p: float,
    soc_mode: str = "empty_start",
    metric: str = "hours",
    standing_loss: float = 0.0,
    objective: str = "lcoe",
    s_max: float = 8.0,
    w_max: float = 8.0,
    coarse_grid: int = 41,
    refine_grid: int = 21,
    n_refinements: int = 5,
    n_seeds: int = 5,
) -> GBSDesign:
    """Minimize cost over designs meeting `coverage_p`, using BOA's exact dispatch/metric.

    Coarse grid over (solar, wind) -> `n_seeds` distinct basins -> `n_refinements` rounds of
    local re-gridding around each. At every grid point the battery is `b_min(s, w)`, which
    is optimal there because cost strictly increases in battery capacity.

    `n_refinements` is the parameter to sweep for a convergence/budget curve, not
    `coarse_grid`: total work is `coarse_grid**2 + n_refinements * n_seeds * refine_grid**2`,
    and refinement dominates once `n_refinements >= 1` at the defaults below (coarse_grid
    contributes only ~4% of total evaluations at coarse_grid=41 vs. coarse_grid=11) --
    varying `coarse_grid` alone barely moves total work or the answer. `n_refinements=0` is
    a valid, cheap budget level: it returns the coarse grid's own best point directly, no
    local refinement at all.

    `coarse_grid` should also stay fixed across a budget sweep rather than growing with
    `n_refinements`: each refinement stage's local box is `1.5x` the *previous* stage's own
    spacing, so the box shrinks geometrically (~0.15x/stage) regardless of how coarse the
    very first grid was -- `coarse_grid`'s only job is landing the first seed inside the
    right basin, not precision, and precision comes entirely from that geometric shrinkage.
    A finer `coarse_grid` at higher `n_refinements` would add evaluations with no accuracy
    payoff. The one place `coarse_grid` matters on its own is `n_refinements=0`, where
    there's no refinement to fall back on -- that argues for keeping it at a reasonable
    fixed value, not for scaling it.

    See `find_robust_gbs_design` for the analogous search across several weather years at
    once, rather than one.
    """
    if metric not in _METRIC_CODE:
        raise ValueError(f"metric must be one of {sorted(_METRIC_CODE)}, got {metric!r}")
    if soc_mode not in ("cyclic", "empty_start"):
        raise ValueError(f"soc_mode must be 'cyclic' or 'empty_start', got {soc_mode!r}")

    metric_code = _METRIC_CODE[metric]
    cyclic = soc_mode == "cyclic"
    target = 1 - coverage_p / 100
    solar_profile = np.ascontiguousarray(profile["solar"], dtype=np.float64)
    wind_profile = np.ascontiguousarray(profile["wind"], dtype=np.float64)

    if objective == "lcoe":
        cost_of = _lcoe_objective(baseload_demand, costs, profile)
    elif objective == "pypsa_linear":
        cost_of = _pypsa_linear_objective(baseload_demand, costs)
    else:
        raise ValueError(f"objective must be 'lcoe' or 'pypsa_linear', got {objective!r}")

    def battery_grid_fn(s_vals: np.ndarray, w_vals: np.ndarray) -> np.ndarray:
        return _b_min_grid(solar_profile, wind_profile, s_vals, w_vals, target, cyclic, standing_loss, metric_code)

    start = time.time()
    best_objective, s_star, w_star, b_star, refinement_delta, n_evaluations = _grid_bisection_search(
        battery_grid_fn,
        cost_of,
        s_max,
        w_max,
        coarse_grid,
        refine_grid,
        n_refinements,
        n_seeds,
        infeasible_msg=f"at coverage_p={coverage_p} (metric={metric}, soc_mode={soc_mode})",
    )

    design = {"solar": s_star, "wind": w_star, "battery": b_star}
    net_energy = calculate_net_energy_production(s_star, solar_profile, w_star, wind_profile)
    coverage = _coverage_jit(net_energy, b_star, cyclic, standing_loss, metric_code)

    on_boundary = bool(np.isclose(s_star, s_max, rtol=1e-3) or np.isclose(w_star, w_max, rtol=1e-3))
    if on_boundary:
        logger.warning(
            f"GBS optimum sits on the search-box boundary (solar={s_star:.3f}/{s_max}, "
            f"wind={w_star:.3f}/{w_max}) -- widen --s-max/--w-max, the true optimum may lie outside."
        )

    lcoe = score_lcoe(design, baseload_demand, costs, profile)
    return GBSDesign(
        design=design,
        lcoe=lcoe,
        objective=best_objective,
        coverage=coverage,
        metric=metric,
        soc_mode=soc_mode,
        refinement_delta=refinement_delta,
        on_boundary=on_boundary,
        n_evaluations=n_evaluations,
        search_seconds=time.time() - start,
    )


def find_robust_gbs_design(
    profiles: list[dict[str, np.ndarray]],
    baseload_demand: float,
    costs: BenchmarkCosts,
    coverage_p: float,
    soc_mode: str = "empty_start",
    metric: str = "hours",
    standing_loss: float = 0.0,
    objective: str = "lcoe",
    s_max: float = 8.0,
    w_max: float = 8.0,
    coarse_grid: int = 41,
    refine_grid: int = 21,
    n_refinements: int = 5,
    n_seeds: int = 5,
) -> GBSDesign:
    """Like `find_gbs_design`, but the returned design meets `coverage_p` in *every* one of
    `profiles` simultaneously (e.g. several weather years at the same site), not just one --
    the design you'd actually have to commit to before knowing which year's weather occurs,
    as opposed to `find_gbs_design` run separately per year (which answers "what would have
    been optimal in hindsight for that year alone").

    Valid by the same monotonicity argument `find_gbs_design` relies on: at fixed
    (solar, wind), the smallest battery meeting profile `i` alone is `b_min_i(s, w)`; the
    smallest battery meeting *all* profiles is therefore `max_i b_min_i(s, w)`, since cost is
    strictly increasing in battery and every `b_min_i` is itself already the minimum feasible
    value for its own profile. So the elementwise max across per-profile `b_min` grids is
    still the pointwise-optimal battery at every (solar, wind) grid point, and the same
    coarse-to-fine search (`_grid_bisection_search`) applies unchanged. `coverage` on the
    returned `GBSDesign` is the *worst* (minimum) of the per-profile realized coverages --
    i.e. the binding year.

    `lcoe` does not actually depend on `profile` at all: `score_lcoe` always evaluates
    `calculate_lcoe_of_re_installation` with `use_curtailment=True`, under which
    `sold_elect_ih_all` is a constant and `total_costs_all` depends only on installed
    capacity -- so which of `profiles` is passed in for the final `lcoe`/`objective="lcoe"`
    calculation is arbitrary. `profiles[0]` is used for concreteness.
    """
    if metric not in _METRIC_CODE:
        raise ValueError(f"metric must be one of {sorted(_METRIC_CODE)}, got {metric!r}")
    if soc_mode not in ("cyclic", "empty_start"):
        raise ValueError(f"soc_mode must be 'cyclic' or 'empty_start', got {soc_mode!r}")
    if not profiles:
        raise ValueError("profiles must be non-empty")

    metric_code = _METRIC_CODE[metric]
    cyclic = soc_mode == "cyclic"
    target = 1 - coverage_p / 100
    solar_profiles = [np.ascontiguousarray(p["solar"], dtype=np.float64) for p in profiles]
    wind_profiles = [np.ascontiguousarray(p["wind"], dtype=np.float64) for p in profiles]

    if objective == "lcoe":
        cost_of = _lcoe_objective(baseload_demand, costs, profiles[0])
    elif objective == "pypsa_linear":
        cost_of = _pypsa_linear_objective(baseload_demand, costs)
    else:
        raise ValueError(f"objective must be 'lcoe' or 'pypsa_linear', got {objective!r}")

    def battery_grid_fn(s_vals: np.ndarray, w_vals: np.ndarray) -> np.ndarray:
        batteries = None
        for solar_profile, wind_profile in zip(solar_profiles, wind_profiles):
            b = _b_min_grid(solar_profile, wind_profile, s_vals, w_vals, target, cyclic, standing_loss, metric_code)
            batteries = b if batteries is None else np.maximum(batteries, b)
        return batteries

    start = time.time()
    best_objective, s_star, w_star, b_star, refinement_delta, n_evaluations = _grid_bisection_search(
        battery_grid_fn,
        cost_of,
        s_max,
        w_max,
        coarse_grid,
        refine_grid,
        n_refinements,
        n_seeds,
        infeasible_msg=f"at coverage_p={coverage_p} (metric={metric}, soc_mode={soc_mode}) across {len(profiles)} weather years",
    )
    # Each grid point cost len(profiles) b_min bisections, not one -- _grid_bisection_search
    # only counts grid points evaluated, so scale up to the true evaluation count.
    n_evaluations *= len(profiles)

    design = {"solar": s_star, "wind": w_star, "battery": b_star}
    coverage = min(
        _coverage_jit(
            calculate_net_energy_production(s_star, solar_profile, w_star, wind_profile),
            b_star,
            cyclic,
            standing_loss,
            metric_code,
        )
        for solar_profile, wind_profile in zip(solar_profiles, wind_profiles)
    )

    on_boundary = bool(np.isclose(s_star, s_max, rtol=1e-3) or np.isclose(w_star, w_max, rtol=1e-3))
    if on_boundary:
        logger.warning(
            f"Robust GBS optimum sits on the search-box boundary (solar={s_star:.3f}/{s_max}, "
            f"wind={w_star:.3f}/{w_max}) -- widen --s-max/--w-max, the true optimum may lie outside."
        )

    lcoe = score_lcoe(design, baseload_demand, costs, profiles[0])
    return GBSDesign(
        design=design,
        lcoe=lcoe,
        objective=best_objective,
        coverage=coverage,
        metric=metric,
        soc_mode=soc_mode,
        refinement_delta=refinement_delta,
        on_boundary=on_boundary,
        n_evaluations=n_evaluations,
        search_seconds=time.time() - start,
    )


# --------------------------------------------------------------------------------------
# Correctness checks
# --------------------------------------------------------------------------------------


def self_test(profile: dict[str, np.ndarray], standing_loss: float = 0.0, n_designs: int = 200, seed: int = 0) -> None:
    """Assert `_coverage_jit` reproduces the unmodified `design_metrics.simulate_design`,
    and that coverage is monotone in each design variable (the property `b_min`'s bisection
    and the up-closed feasible set both rest on)."""
    from design_metrics import simulate_design

    rng = np.random.default_rng(seed)
    solar_profile = np.ascontiguousarray(profile["solar"], dtype=np.float64)
    wind_profile = np.ascontiguousarray(profile["wind"], dtype=np.float64)

    worst = {"hours": 0.0, "energy": 0.0}
    for _ in range(n_designs):
        s, w = rng.uniform(0.05, 6.0), rng.uniform(0.05, 6.0)
        b = rng.uniform(0.0, 8.0)
        net_energy = calculate_net_energy_production(s, solar_profile, w, wind_profile)
        for soc_mode in ("empty_start", "cyclic"):
            cyclic = soc_mode == "cyclic"
            expected = simulate_design(
                {"solar": s, "wind": w, "battery": b}, profile, 1.0, soc_mode, standing_loss=standing_loss
            )
            for metric, want in (("hours", expected.hours_coverage), ("energy", expected.energy_coverage)):
                got = _coverage_jit(net_energy, b, cyclic, standing_loss, _METRIC_CODE[metric])
                worst[metric] = max(worst[metric], abs(got - want))
    for metric, deviation in worst.items():
        assert deviation < 1e-9, f"{metric} coverage deviates from simulate_design by {deviation:.3e}"
    logger.info(f"Metric agreement vs simulate_design: max |diff| = {worst} over {n_designs} designs x 2 SOC modes")

    violations = 0
    for _ in range(n_designs):
        s, w, b = rng.uniform(0.05, 5.0), rng.uniform(0.05, 5.0), rng.uniform(0.0, 6.0)
        for cyclic in (False, True):
            for metric in (_HOURS, _ENERGY):
                base = _coverage_jit(
                    calculate_net_energy_production(s, solar_profile, w, wind_profile), b, cyclic, standing_loss, metric
                )
                for ds, dw, db in ((0.3, 0.0, 0.0), (0.0, 0.3, 0.0), (0.0, 0.0, 0.5)):
                    up = _coverage_jit(
                        calculate_net_energy_production(s + ds, solar_profile, w + dw, wind_profile),
                        b + db,
                        cyclic,
                        standing_loss,
                        metric,
                    )
                    if up < base - 1e-12:
                        violations += 1
    assert violations == 0, f"{violations} monotonicity violations -- b_min's bisection is not valid"
    logger.info(f"Monotonicity holds on {n_designs} random designs x 2 SOC modes x 2 metrics x 3 directions")


def validate_against_energy_lp(
    profile: dict[str, np.ndarray],
    baseload_demand: float,
    costs: BenchmarkCosts,
    coverage_p: float,
    solver: str = "highs",
    standing_loss: float = 0.0,
    **search_kwargs,
) -> dict[str, float]:
    """Check the search against a *certified* optimum.

    Under the energy metric greedy dispatch is optimal, so `pypsa_model`'s LP is an exact
    ground truth. Running the search with the LP's own linear objective on that metric must
    reproduce it to within grid resolution; if it does, the same machinery applied to the
    hours metric (where no tractable certified optimum exists) is trustworthy.
    """
    from pypsa_model import solve_optimal_design

    lp = solve_optimal_design(
        profile,
        baseload_demand,
        costs,
        coverage_p,
        solver=solver,
        standing_loss=standing_loss,
    )
    search = find_gbs_design(
        profile,
        baseload_demand,
        costs,
        coverage_p,
        soc_mode="cyclic",  # matches PyPSA Store(e_cyclic=True)
        metric="energy",
        standing_loss=standing_loss,
        objective="pypsa_linear",
        **search_kwargs,
    )
    relative_gap = (search.objective - lp.objective) / lp.objective
    logger.info(
        f"LP objective={lp.objective:.4f} design={lp.design} ({lp.solve_seconds:.1f}s) | "
        f"search objective={search.objective:.4f} design={search.design} ({search.search_seconds:.1f}s) | "
        f"relative gap={relative_gap:+.4%}"
    )
    return {
        "lp_objective": lp.objective,
        "search_objective": search.objective,
        "relative_gap": relative_gap,
        "lp_seconds": lp.solve_seconds,
        "search_seconds": search.search_seconds,
        "refinement_delta": search.refinement_delta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sites-file", type=Path, default=Path("scripts/boa_benchmark/sites.yaml"))
    parser.add_argument("--site-name", type=str, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data"))
    parser.add_argument("--cache-dir", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data/cache"))
    parser.add_argument(
        "--flat-costs-csv", type=Path, default=Path("scripts/boa_benchmark/preprocessed_data/flat_costs.csv")
    )
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--baseload-demand", type=float, default=500.0)
    parser.add_argument("--coverage", type=float, default=0.95)
    parser.add_argument("--metric", type=str, choices=["hours", "energy"], default="hours")
    parser.add_argument("--soc-mode", type=str, choices=["empty_start", "cyclic"], default="empty_start")
    parser.add_argument("--standing-loss", type=float, default=0.0)
    parser.add_argument("--s-max", type=float, default=8.0)
    parser.add_argument("--w-max", type=float, default=8.0)
    parser.add_argument("--coarse-grid", type=int, default=41)
    parser.add_argument("--self-test", action="store_true", help="Check metric agreement and monotonicity, then exit.")
    parser.add_argument(
        "--validate", action="store_true", help="Cross-check the search against the certified energy LP."
    )
    parser.add_argument("--solver", type=str, choices=["highs", "gurobi"], default="highs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    with open(args.sites_file) as f:
        sites = yaml.safe_load(f)
    matches = [s for s in sites if s["name"] == args.site_name]
    if not matches:
        raise ValueError(f"Site {args.site_name!r} not in {args.sites_file}")
    site = matches[0]

    profile = load_point_profile(args.data_dir, args.year, site["lat"], site["lon"], args.cache_dir)
    costs = load_benchmark_costs(args.flat_costs_csv, site["cost_region"])
    coverage_p = (1 - args.coverage) * 100

    if args.self_test:
        self_test(profile, standing_loss=args.standing_loss)
        return

    if args.validate:
        validate_against_energy_lp(
            profile,
            args.baseload_demand,
            costs,
            coverage_p,
            solver=args.solver,
            standing_loss=args.standing_loss,
            s_max=args.s_max,
            w_max=args.w_max,
            coarse_grid=args.coarse_grid,
        )
        return

    gbs = find_gbs_design(
        profile,
        args.baseload_demand,
        costs,
        coverage_p,
        soc_mode=args.soc_mode,
        metric=args.metric,
        standing_loss=args.standing_loss,
        s_max=args.s_max,
        w_max=args.w_max,
        coarse_grid=args.coarse_grid,
    )
    logger.info(
        f"{site['name']} coverage={args.coverage} metric={args.metric} soc_mode={args.soc_mode}\n"
        f"  design = solar {gbs.design['solar']:.4f}, wind {gbs.design['wind']:.4f}, "
        f"battery {gbs.design['battery']:.4f}\n"
        f"  lcoe = {gbs.lcoe:.4f} USD/MWh   realized coverage = {gbs.coverage:.6f}\n"
        f"  refinement_delta = {gbs.refinement_delta:.3e}   "
        f"{gbs.n_evaluations} evaluations in {gbs.search_seconds:.1f}s"
    )


if __name__ == "__main__":
    main()
