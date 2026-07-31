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

import logging
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

logger = logging.getLogger(__name__)

SocMode = Literal["cyclic", "empty_start"]

# `decompose_energy_flows`'s cyclic-composition fixed-point iteration: relative to battery
# capacity, matching `gbs.py`'s `SOC0_TOL_REL` convention (a tolerance on the *state*, not an
# absolute energy amount, since capacities span orders of magnitude across sites/designs).
# `MAX_COMPOSITION_PASSES` is a safety cap, not an expected iteration count -- convergence
# rate depends on how many times per year the battery's contents turn over, and a
# large-capacity/low-throughput battery can take many passes; see that function's docstring.
COMPOSITION_TOL_REL = 1e-6
MAX_COMPOSITION_PASSES = 500


@dataclass
class DesignMetrics:
    hours_coverage: float
    energy_coverage: float


@dataclass
class EnergyFlowShares:
    """Annual energy flows for a finalized design, attributed back to solar vs. wind by a
    well-mixed-pool convention (see `decompose_energy_flows`). `solar_direct`, `wind_direct`,
    `battery_solar`, `battery_wind`, and `unmet` are fractions of annual demand and sum to
    ~1.0. `curtailment_solar`/`curtailment_wind` are separate -- generation that reached
    neither demand nor the battery, expressed as a multiple of annual demand (same units as
    the design's own solar/wind overscale factors, not a fraction of demand like the other
    five)."""

    solar_direct: float
    wind_direct: float
    battery_solar: float
    battery_wind: float
    unmet: float
    curtailment_solar: float
    curtailment_wind: float


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


def decompose_energy_flows(
    design: dict[str, float],
    profile: dict[str, np.ndarray],
    soc_mode: SocMode,
    standing_loss: float = 0.0,
) -> EnergyFlowShares:
    """Attribute a finalized design's annual dispatch back to solar vs. wind origin, by a
    well-mixed-battery-pool convention: at every hour, whatever flow is happening (serving
    demand directly, charging the battery, or being curtailed) is split between solar and
    wind in proportion to each source's share of that hour's raw generation; discharging the
    battery is split instead by the battery's *current* internal composition
    (`soc_solar[t-1] / soc[t-1]`), which is itself the accumulated history of prior charges
    -- there's no physical distinction between solar- and wind-charged electrons once mixed,
    so this (rather than e.g. FIFO) is the natural analogue of weighted-average-cost
    inventory accounting applied to a battery.

    Rides entirely on `state_of_charge_cyclic`/`state_of_charge_empty_start`'s already-correct
    SOC trajectory -- `charge[t]`/`discharge[t]` are read off as the trajectory's own implied
    deltas, and the solar/wind split only ever redistributes those already-correct amounts.
    That makes `solar_direct + wind_direct + battery_solar + battery_wind + unmet == 1.0`
    (fractions of demand) hold by construction at every hour, not just on average -- the two-
    origin SOC tracks are a decomposition of the real `soc[t]`, not a parallel simulation that
    could drift from it.

    For `soc_mode="cyclic"`, the SOC *value* is already periodic (by construction of
    `state_of_charge_cyclic`), but the *composition* of that carried-over energy also needs to
    be periodic, and there's no closed form for it. Each pass re-runs the hourly split,
    seeding its initial composition from the previous pass's year-end composition, which
    converges onto the periodic composition the same way repeatedly running a damped
    recurrence converges on a fixed point -- there's no monotone predicate to bisect on here
    (unlike the SOC value itself), so plain fixed-point iteration is used instead, run to
    `COMPOSITION_TOL_REL` (relative to battery capacity, not a fixed pass count -- convergence
    rate depends on how many times per year the battery's contents turn over, which a fixed
    count can't account for). `soc_mode="empty_start"` needs none of this: the pool is
    unambiguously empty at t=0, so one pass is exact.
    """
    gen_solar = design["solar"] * profile["solar"]
    gen_wind = design["wind"] * profile["wind"]
    total_gen = gen_solar + gen_wind
    n = len(total_gen)
    battery_capacity = design["battery"]
    keep = 1.0 - standing_loss

    net_energy = total_gen - 1.0
    if soc_mode == "cyclic":
        soc = state_of_charge_cyclic(net_energy, battery_capacity, standing_loss=standing_loss)
    else:
        soc = state_of_charge_empty_start(net_energy, battery_capacity, standing_loss=standing_loss)
    prev_soc = np.roll(soc, 1)
    prev_soc[0] = soc[-1] if soc_mode == "cyclic" else 0.0

    with np.errstate(invalid="ignore", divide="ignore"):
        solar_frac = np.where(total_gen > 0.0, gen_solar / total_gen, 0.0)
    wind_frac = 1.0 - solar_frac

    direct_serve = np.minimum(1.0, total_gen)
    solar_direct = direct_serve * solar_frac
    wind_direct = direct_serve * wind_frac

    surplus = np.maximum(0.0, total_gen - 1.0)
    shortfall = np.maximum(0.0, 1.0 - total_gen)
    charge = np.maximum(0.0, soc - prev_soc * keep)
    discharge = np.maximum(0.0, prev_soc * keep - soc)
    curtailed = surplus - charge
    unmet = shortfall - discharge
    charge_solar = charge * solar_frac
    charge_wind = charge * wind_frac
    curtailed_solar = curtailed * solar_frac
    curtailed_wind = curtailed * wind_frac

    # Fixed-point iteration for the cyclic composition boundary condition -- see docstring.
    # `empty_start` always starts the pool at (0, 0), matching `prev_soc[0] == 0.0` above, so
    # convergence is immediate (delta 0.0 after the first pass) and the loop below only ever
    # runs once for it. `soc_wind_boundary` isn't independently tracked for convergence since
    # `soc_solar_boundary + soc_wind_boundary == prev_soc[0]` always (a fixed total), so a
    # delta on one component implies the same delta on the other.
    soc_solar_boundary = 0.0
    soc_wind_boundary = prev_soc[0]
    tol = COMPOSITION_TOL_REL * battery_capacity
    discharge_solar = np.zeros(n)
    discharge_wind = np.zeros(n)
    for _pass in range(MAX_COMPOSITION_PASSES):
        soc_solar = np.empty(n)
        soc_wind = np.empty(n)
        pool_solar_prev = soc_solar_boundary
        pool_wind_prev = soc_wind_boundary
        for t in range(n):
            pool_solar = pool_solar_prev * keep
            pool_wind = pool_wind_prev * keep
            pool_total = pool_solar + pool_wind
            if discharge[t] > 0.0 and pool_total > 0.0:
                discharge_solar[t] = discharge[t] * (pool_solar / pool_total)
                discharge_wind[t] = discharge[t] * (pool_wind / pool_total)
            else:
                discharge_solar[t] = 0.0
                discharge_wind[t] = 0.0
            soc_solar[t] = pool_solar - discharge_solar[t] + charge_solar[t]
            soc_wind[t] = pool_wind - discharge_wind[t] + charge_wind[t]
            pool_solar_prev = soc_solar[t]
            pool_wind_prev = soc_wind[t]
        delta = abs(soc_solar[-1] - soc_solar_boundary)
        soc_solar_boundary = soc_solar[-1]
        soc_wind_boundary = soc_wind[-1]
        if soc_mode == "empty_start" or delta <= tol:
            break
    else:
        logger.warning(
            f"decompose_energy_flows: composition boundary did not converge to "
            f"{COMPOSITION_TOL_REL:.1e} rel. tol within {MAX_COMPOSITION_PASSES} passes "
            f"(final delta={delta:.3e}, battery_capacity={battery_capacity:.3f}) -- treating "
            f"the last pass as final; result may still carry some initial-guess dependence."
        )

    return EnergyFlowShares(
        solar_direct=float(solar_direct.sum() / n),
        wind_direct=float(wind_direct.sum() / n),
        battery_solar=float(discharge_solar.sum() / n),
        battery_wind=float(discharge_wind.sum() / n),
        unmet=float(unmet.sum() / n),
        curtailment_solar=float(curtailed_solar.sum() / n),
        curtailment_wind=float(curtailed_wind.sum() / n),
    )


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
