"""PyPSA capacity-expansion "ground truth" optimizer for the same solar+wind+battery
design problem BOA's `capacity_sampling` approximates by sampling.

Solves a single-constraint LP: `sum(unserved.p) <= (p/100) * baseload_demand *
len(snapshots)` -- caps *total* unserved energy across the year (BOA/`design_metrics`'s
`energy_coverage` metric), with no regard for how many hours it's spread across. This
metric is a certified ground truth: minimizing unserved energy under BOA's own greedy
battery dispatch is provably optimal there (a straightforward exchange argument -- shifting
charge earlier never hurts and can only reduce shortfall), so BOA's dispatch rule and this
LP's free dispatch necessarily agree, and HiGHS/Gurobi solve it to a real, certified 0% gap.

This module used to also offer an `hours`-coverage MILP (one binary `hour_covered[t]` per
snapshot, matching BOA's stricter `calculate_coverage`/`hours_coverage` metric exactly).
That MILP's LP relaxation is *identical* to the energy-LP above -- for any fixed `unserved`
vector, the relaxation's optimal `covered[t] = 1 - unserved[t]/D`, and substituting into the
cardinality constraint `sum(covered) >= (1-p)*N` collapses it to exactly
`sum(unserved) <= p*N*D`. So its root bound was structurally stuck at the energy-LP's
answer, and it never certified better than a ~12.5% gap even at a 300s time cap on a real
8760-hour profile. Worse, its free (optimal-foresight) dispatch doesn't match BOA's greedy
one for the hours metric (unlike for energy, greedy is *not* optimal there -- foresight will
sacrifice one deep-deficit hour to fully serve two shallow ones), so it was returning
designs BOA's own simulator would score below the requested threshold: an unattainable,
optimistic ground truth. It was removed rather than kept as a slower, uncertified
alternative -- see `gbs.py` for what replaced it (a `(solar, wind)` grid search
with battery collapsed to a monotone bisection, run under BOA's own exact dispatch) and
`README.md` for the full writeup.

Modeling choices (documented here, not hidden):

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

import logging
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
import pypsa
from baseload_optimisation_atlas.boa_logic import correct_battery_capex_for_modular_installation
from cost_inputs import BenchmarkCosts

logger = logging.getLogger(__name__)

Solver = Literal["highs", "gurobi"]


@dataclass
class PypsaResult:
    design: dict[str, float]  # solar/wind/battery overscale factors (w.r.t. baseload_demand)
    objective: float
    status: str
    termination_condition: str
    solve_seconds: float


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
    # p_nom capped at baseload_demand: unserved is load shedding, never more than the load
    # itself -- an unbounded generator would let the LP charge the battery "for free" from
    # unserved supply, which is unphysical and (at zero marginal cost) not excluded by the
    # objective either.
    net.add("Generator", "unserved", bus="bus", p_nom=baseload_demand, marginal_cost=0)
    net.add(
        "Store",
        "battery",
        bus="bus",
        e_nom_extendable=True,
        e_cyclic=True,
        capital_cost=annualized_battery,
        standing_loss=standing_loss,
    )

    unserved_cap = (coverage_p / 100) * baseload_demand * n_hours

    def extra_functionality(n: pypsa.Network, sns) -> None:
        m = n.model
        unserved = m["Generator-p"].sel(name="unserved")
        m.add_constraints(unserved.sum() <= unserved_cap, name="unserved_energy_cap")

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
    )
