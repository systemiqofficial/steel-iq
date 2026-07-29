"""Battery state-of-charge dispatch helpers for the benchmark: a cyclic (periodic) SOC
alternative to `boa_logic.state_of_charge`'s hardcoded empty start, and an optional
standing-loss (self-discharge) term neither production `state_of_charge` nor the
original version of this module supported.

BOA's production `state_of_charge` always starts the year with an empty battery
(`soc[0] = clip(gen_nrg[0], 0, capacity)`), which is an artificial pessimism unrelated to
the sampling-vs-optimization question this benchmark is actually about: a battery that
has been running for years doesn't reset to empty every January 1st. `state_of_charge_cyclic`
instead finds, by bisection, the initial state of charge that a full year of the same
deterministic dispatch rule returns to by year's end -- a periodic boundary condition,
matching what PyPSA's `Store(e_cyclic=True)` enforces natively via a linear constraint.

Kept local to this benchmark rather than added to `src/baseload_optimisation_atlas/`
pending validation of its impact (see the benchmark's empty_start vs. cyclic comparison).

`standing_loss` (fraction of stored energy lost per hour, matching PyPSA `Store`'s own
`standing_loss` semantics) defaults to 0 everywhere, so all of the above is unchanged
when it's left at its default -- it's an added parameter, not a changed assumption.
Round-trip efficiency (charge/discharge losses, as opposed to idle self-discharge) is
NOT modeled here; see the module docstring note in `pypsa_model.py`.

Same power-rating assumption as production `state_of_charge`, carried over unchanged and
flagged here rather than silently kept: no charge/discharge power (MW) rating -- the
battery can charge or discharge arbitrarily fast each hour.
"""

import numba
import numpy as np


@numba.jit
def simulate_soc(gen_nrg: np.ndarray, battery_capacity: float, soc0: float, standing_loss: float = 0.0) -> np.ndarray:
    """Same dispatch rule as boa_logic.state_of_charge, generalized with an explicit
    initial SOC and an optional per-hour standing loss (self-discharge fraction)."""
    soc = np.empty(len(gen_nrg))
    prev = soc0
    for t in range(len(gen_nrg)):
        prev = min(max(prev * (1 - standing_loss) + gen_nrg[t], 0.0), battery_capacity)
        soc[t] = prev
    return soc


def state_of_charge_empty_start(gen_nrg: np.ndarray, battery_capacity: float, standing_loss: float = 0.0) -> np.ndarray:
    """Empty-start dispatch (same as boa_logic.state_of_charge when standing_loss=0),
    generalized to support standing loss, which production state_of_charge cannot."""
    return simulate_soc(gen_nrg, battery_capacity, soc0=0.0, standing_loss=standing_loss)


def state_of_charge_cyclic(
    gen_nrg: np.ndarray, battery_capacity: float, standing_loss: float = 0.0, tol: float = 1e-9
) -> np.ndarray:
    """
    Simulate battery operation hour by hour with a periodic state of charge: the initial
    SOC is chosen (by bisection) so that the SOC at the end of the year equals it.

    Args:
        gen_nrg: Net generated energy at each time step (production - demand)
        battery_capacity: Battery overscale factor
        standing_loss: Fraction of stored energy lost per hour (0 = no loss, matches
            PyPSA Store's standing_loss semantics)
        tol: Bisection tolerance on the initial SOC (same units as battery_capacity)

    Returns:
        State of charge (SOC) at each hour (MWh), for the periodic initial condition

    Note:
        f(soc0) = soc_end(soc0) - soc0 is continuous and non-decreasing in soc0 (the
        dispatch rule is a composition of clips and a nonnegative scaling, each
        non-expansive), with f(0) >= 0 and f(battery_capacity) <= 0, so a root exists in
        [0, battery_capacity] and bisection converges reliably -- this still holds with
        standing_loss > 0 since scaling by (1 - standing_loss) >= 0 preserves
        monotonicity.
    """
    if battery_capacity <= 0:
        return np.zeros(len(gen_nrg))

    def f(soc0: float) -> float:
        return simulate_soc(gen_nrg, battery_capacity, soc0, standing_loss)[-1] - soc0

    lo, hi = 0.0, battery_capacity
    f_lo, f_hi = f(lo), f(hi)
    if f_lo < 0:  # degenerate: numerical noise at the boundary
        return simulate_soc(gen_nrg, battery_capacity, lo, standing_loss)
    if f_hi > 0:
        return simulate_soc(gen_nrg, battery_capacity, hi, standing_loss)

    while hi - lo > tol:
        mid = (lo + hi) / 2
        if f(mid) >= 0:
            lo = mid
        else:
            hi = mid

    soc0 = (lo + hi) / 2
    return simulate_soc(gen_nrg, battery_capacity, soc0, standing_loss)
