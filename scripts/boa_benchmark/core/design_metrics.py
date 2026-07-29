"""Evaluate a (solar, wind, battery) overscale-factor design consistently, regardless of
which method (BOA sampling, empty-start or cyclic SOC; PyPSA) produced it.

Both `simulate_design` and `score_lcoe` reuse `boa_logic`/`boa_cost_calculations`
functions directly rather than reimplementing them, so the only genuinely new logic here
is picking which SOC dispatch (`empty_start` vs `cyclic`) to run and wiring the resulting
design into `calculate_lcoe_of_re_installation` with the identical formula used for every
design, so different methods' results are directly comparable.

`standing_loss` defaults to 0, matching production `state_of_charge` exactly (see
`cyclic_soc.state_of_charge_empty_start`'s docstring) -- passing a nonzero value is
additive, not a change to the zero-loss benchmark's behavior.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from baseload_optimisation_atlas.boa_cost_calculations import calculate_lcoe_of_re_installation
from baseload_optimisation_atlas.boa_logic import (
    calculate_coverage,
    calculate_net_energy_production,
    correct_battery_capex_for_modular_installation,
)
from .cost_inputs import BenchmarkCosts
from .cyclic_soc import state_of_charge_cyclic, state_of_charge_empty_start

SocMode = Literal["cyclic", "empty_start"]


@dataclass
class DesignMetrics:
    hours_coverage: float
    energy_coverage: float


def simulate_design(
    design: dict[str, float],
    profile: dict[str, np.ndarray],
    baseload_demand: float,
    soc_mode: SocMode,
    standing_loss: float = 0.0,
) -> DesignMetrics:
    net_energy = calculate_net_energy_production(design["solar"], profile["solar"], design["wind"], profile["wind"])
    if soc_mode == "cyclic":
        soc = state_of_charge_cyclic(net_energy, design["battery"], standing_loss=standing_loss)
    else:
        soc = state_of_charge_empty_start(net_energy, design["battery"], standing_loss=standing_loss)

    hours_coverage = calculate_coverage(soc, net_energy)

    # Realized partial-shortfall energy coverage (continuous), reported alongside the
    # binary hours_coverage metric so the two can be compared per design.
    prev_soc = np.roll(soc, 1)
    prev_soc[0] = soc[-1] if soc_mode == "cyclic" else 0.0
    unmet = np.maximum(0.0, -(prev_soc + net_energy))
    energy_coverage = 1 - unmet.sum() / len(net_energy)  # demand is normalized to 1 in net_energy

    return DesignMetrics(hours_coverage=hours_coverage, energy_coverage=energy_coverage)


def score_lcoe(
    design: dict[str, float],
    baseload_demand: float,
    costs: BenchmarkCosts,
    profile: dict[str, np.ndarray],
) -> float:
    capex = dict(costs.capex)
    capex["battery"] = correct_battery_capex_for_modular_installation(costs.storage_costs, design["battery"])
    installed_capacity = {tech: design[tech] * baseload_demand for tech in ["solar", "wind", "battery"]}

    return calculate_lcoe_of_re_installation(
        costs.investment_horizon,
        installed_capacity,
        baseload_demand,
        capex,
        costs.opex_pct,
        profile,
        costs.cost_of_capital,
        use_curtailment=True,
    )
