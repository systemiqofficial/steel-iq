"""PyPSA capacity-expansion "ground truth" optimizer for the same solar+wind+battery
design problem BOA's `capacity_sampling` approximates by sampling.

Modeling choices (documented here, not hidden):

- **Coverage constraint**: two selectable formulations (`coverage_metric`), since they are
  genuinely different problems, not two views of the same one:
    - `"hours"` (**default**): a MILP with one binary `hour_covered[t]` indicator per
      snapshot, linked via `unserved[t] <= baseload_demand * (1 - hour_covered[t])` and
      `sum(hour_covered) >= (1 - coverage_p/100) * len(snapshots)`. This matches BOA's own
      `calculate_coverage` *exactly*: an hour only counts as covered if literally zero
      demand went unserved that hour (any nonzero shortfall, however small, marks the
      whole hour uncovered). This is the metric BOA's production filter
      (`filter_designs_according_to_coverage_and_calculate_costs`) actually enforces, so
      it's the one that makes PyPSA's result a genuine apples-to-apples ground truth for
      BOA's real behavior -- at the cost of a slower MILP solve.
    - `"energy"`: the original single-constraint LP,
      `sum(unserved.p) <= (p/100) * baseload_demand * len(snapshots)` -- caps *total*
      unserved energy across the year, with no regard for how many hours it's spread
      across. Fast (pure LP), but **not** equivalent to `"hours"`: a design can satisfy a
      95% energy-coverage cap while leaving >10% of hours with some (small) shortfall,
      which is a materially weaker requirement than BOA's own binary per-hour criterion.
      Keep this around for a fast sanity check / relaxed-bound comparison, not as the
      benchmark's headline "ground truth".
- **Battery**: modeled as a PyPSA `Store` (not `StorageUnit`) so `capital_cost` is a
  straight $/MWh energy-capacity rate with no separate power/discharge-rate limit,
  matching BOA's explicit "battery discharge rate is not considered" assumption. This
  means the battery can charge or discharge arbitrarily fast (unconstrained MW rating,
  in effect infinite C-rate) -- flagged here as an assumption, not a hidden default; a
  power (MW) rating would need its own extendable variable and capital cost the way BOA
  currently has none.
  `e_cyclic=True` gives a periodic SOC (enforced natively as a linear equality
  constraint), matching the benchmark's `cyclic_soc.state_of_charge_cyclic`.
- **Losses**: standing loss (self-discharge) is supported via the `standing_loss`
  parameter, using PyPSA `Store`'s own native `standing_loss` attribute (per-hour
  fractional loss) -- the same parameter/semantics as `cyclic_soc.py`'s dispatch rule,
  so both sides move together. Defaults to 0 (no loss), matching the original zero-loss
  benchmark exactly. Round-trip efficiency (charge/discharge losses, as opposed to idle
  self-discharge) is still NOT modeled: `Store` has no separate charge/discharge
  efficiency attribute, so supporting it would mean restructuring the battery into a
  `Store` plus two `Link`s (bus->store charging, store->bus discharging, each carrying an
  efficiency) -- flagged here as a known gap, not implemented in this pass.
- **Capital costs**: solar/wind/battery flat CAPEX (see `cost_inputs.py`) are annualized
  via a standard capital recovery factor for the optimizer's objective (a single
  representative year of operation). This is a linear proxy, not BOA's exact multi-year
  reinstall/degradation LCOE math -- and for the battery specifically, PyPSA optimizes
  against the *linear* modular-installation rate evaluated at the reference
  `average_implied_storage` size, since the real economies-of-scale curve
  (`correct_battery_capex_for_modular_installation`, a concave power law) isn't LP
  representable. The resulting design from this optimization is then rescored through
  BOA's own exact `calculate_lcoe_of_re_installation` (via `design_metrics.score_lcoe`)
  for the final apples-to-apples comparison -- this module's own objective value is only
  a secondary sanity check, not the number reported in the benchmark.
"""

import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
import pypsa
from baseload_optimisation_atlas.boa_logic import correct_battery_capex_for_modular_installation
from cost_inputs import BenchmarkCosts

Solver = Literal["highs", "gurobi"]
CoverageMetric = Literal["hours", "energy"]


@dataclass
class PypsaResult:
    design: dict[str, float]  # solar/wind/battery overscale factors (w.r.t. baseload_demand)
    objective: float
    status: str
    termination_condition: str
    solve_seconds: float
    coverage_metric: CoverageMetric


def _capital_recovery_factor(cost_of_capital: float, lifetime_years: int) -> float:
    r = cost_of_capital
    n = lifetime_years
    if r == 0:
        return 1 / n
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


def solve_optimal_design(
    profile: dict[str, np.ndarray],
    baseload_demand: float,
    costs: BenchmarkCosts,
    coverage_p: float,
    solver: Solver = "highs",
    standing_loss: float = 0.0,
    coverage_metric: CoverageMetric = "hours",
) -> PypsaResult:
    from baseload_optimisation_atlas.boa_config import LIFETIMES

    n_hours = len(profile["solar"])
    snapshots = pd.RangeIndex(n_hours)

    crf_solar = _capital_recovery_factor(costs.cost_of_capital, LIFETIMES["solar"])
    crf_wind = _capital_recovery_factor(costs.cost_of_capital, LIFETIMES["wind"])
    crf_battery = _capital_recovery_factor(costs.cost_of_capital, LIFETIMES["battery"])

    capex_solar = costs.capex["solar"][0]
    capex_wind = costs.capex["wind"][0]
    annualized_solar = capex_solar * crf_solar + costs.opex_pct["solar"] * capex_solar
    annualized_wind = capex_wind * crf_wind + costs.opex_pct["wind"] * capex_wind

    # Reference-size linear battery rate (see module docstring): evaluate the modular
    # scaling correction at the reference "average_implied_storage" size.
    reference_battery_size = costs.storage_costs["average_implied_storage"][0]
    capex_battery_ref = correct_battery_capex_for_modular_installation(costs.storage_costs, reference_battery_size)[0]
    annualized_battery = capex_battery_ref * crf_battery + costs.opex_pct["battery"] * capex_battery_ref

    net = pypsa.Network()
    net.set_snapshots(snapshots)
    net.add("Carrier", "electricity")
    net.add("Bus", "bus", carrier="electricity")
    net.add("Load", "load", bus="bus", p_set=baseload_demand)
    net.add(
        "Generator",
        "solar",
        bus="bus",
        p_max_pu=profile["solar"],
        p_nom_extendable=True,
        capital_cost=annualized_solar,
        marginal_cost=0,
    )
    net.add(
        "Generator",
        "wind",
        bus="bus",
        p_max_pu=profile["wind"],
        p_nom_extendable=True,
        capital_cost=annualized_wind,
        marginal_cost=0,
    )
    net.add("Generator", "unserved", bus="bus", p_nom=1e9, marginal_cost=0)
    net.add(
        "Store",
        "battery",
        bus="bus",
        e_nom_extendable=True,
        e_cyclic=True,
        capital_cost=annualized_battery,
        standing_loss=standing_loss,
    )

    if coverage_metric == "energy":
        unserved_cap = (coverage_p / 100) * baseload_demand * n_hours

        def extra_functionality(n: pypsa.Network, sns) -> None:
            m = n.model
            unserved = m["Generator-p"].sel(name="unserved")
            m.add_constraints(unserved.sum() <= unserved_cap, name="unserved_energy_cap")
    else:
        min_covered_hours = (1 - coverage_p / 100) * n_hours

        def extra_functionality(n: pypsa.Network, sns) -> None:
            m = n.model
            unserved = m["Generator-p"].sel(name="unserved")
            # Binary per hour: matches calculate_coverage's "any shortfall == uncovered" rule.
            covered = m.add_variables(binary=True, coords={"snapshot": sns}, name="hour_covered")
            m.add_constraints(unserved <= baseload_demand * (1 - covered), name="coverage_link")
            m.add_constraints(covered.sum() >= min_covered_hours, name="min_covered_hours")

    start = time.time()
    status, termination_condition = net.optimize(
        solver_name=solver,
        extra_functionality=extra_functionality,
        include_objective_constant=False,
    )
    solve_seconds = time.time() - start

    design = {
        "solar": net.generators.p_nom_opt["solar"] / baseload_demand,
        "wind": net.generators.p_nom_opt["wind"] / baseload_demand,
        "battery": net.stores.e_nom_opt["battery"] / baseload_demand,
    }

    return PypsaResult(
        design=design,
        objective=net.objective,
        status=status,
        termination_condition=termination_condition,
        solve_seconds=solve_seconds,
        coverage_metric=coverage_metric,
    )
