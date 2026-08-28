"""
Grid-bisection search over the design space.

Replaces the Monte Carlo sampler: instead of drawing random designs and sizing each
battery with a heuristic, the search evaluates a deterministic grid over the
`(solar, wind)` overscale plane and, at each node, bisects for the smallest battery
that meets the hourly-coverage target.

The method is ported from `scripts/boa_benchmark/core/gbs.py` on the
`boa-sampling-benchmark` branch, which reproduced a certified PyPSA LP optimum to
5e-8 relative. Two deliberate departures from that reference:

  * the hours metric is hard-wired and cyclic state-of-charge and standing loss are
    not ported -- cyclic is ~6x slower for a <=0.3% LCOE effect, and production has
    no standing loss, so the un-decayed-SOC quirk the reference preserves is moot;
  * the absolute bisection tolerance becomes relative. `gamma = 0.85` means a 1e-3
    relative error in battery size moves LCOE by under 3e-4, so the reference's
    absolute 1e-4 bought roughly four wasted bisection steps.

Everything here is dimensionless. A design is three overscale factors against a
demand normalised to 1: solar and wind in multiples of baseload MW, battery in
baseload-hours. Nothing in this module reads a cost, a year, or a capacity ceiling
-- costs enter only through `argmin_lcoe`, and the ceiling lives in
`boa.model.capacity_box`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from json import dumps

import numba
import numpy as np

from boa.config.settings import BATTERY_UNIT_CAPEX_SCALING_FACTOR, OVERSCALE_SAMPLING_K

# Exponent on battery size in the total-cost numerator. The battery is priced as
# `installed_MWh x capex x (hours/AVERAGE_IMPLIED_STORAGE)**kappa` with kappa negative
# (unit cost falls as the battery gets longer), so quantity and correction collapse into
# a single power law with exponent 1 + kappa. Sub-linear: doubling the battery costs
# 2**0.85 ~ 1.80x, not 2x. See `cost_calculations.lcoe_coefficients`.
GAMMA = 1.0 + BATTERY_UNIT_CAPEX_SCALING_FACTOR

# Per-pixel verdicts. These are year-invariant by construction -- they depend only on the
# profiles, the coverage target and (for the capacity code) the box, never on costs --
# which is what lets `lcoe_promotion` require `status` to be identical across investment
# years. Code 4 is retired with the minimum-survivor cut and is never reused; code 5 is
# unallocated.
STATUS_OK = 1
STATUS_NO_OPTIMUM = 2
STATUS_ZERO_POTENTIAL = 3
STATUS_CAPACITY_INFEASIBLE = 6

# Floor under the relative bisection tolerance, so a pixel whose `b_min` is near zero does
# not spend a dozen full-year dispatch passes resolving a battery nobody can measure.
_B_TOL_ABS = 1e-6


@dataclass(frozen=True)
class SearchParams:
    """
    Everything that determines what the search looks at, and therefore what a cache
    holds. The whole set is hashed into the cache path, so changing any field yields a
    different store rather than silently reusing an incompatible one.
    """

    coarse_grid: int = 25
    coarse_stride: int = 3
    coarse_bisect_steps: int = 3
    patch_grid: int = 15
    ladder_rungs: int = 5
    ladder_span: float = 2.5
    ladder_sat_tol: float = 1e-4
    box_multiple: float = 6.0
    box_min: float = 2.0
    box_abs_max: float = 200.0
    max_box_widenings: int = 2
    patch_halfwidth: float = 0.45
    seed_tolerance: float = 0.05
    max_seeds: int = 3
    b_cap: float = 500.0
    tol_rel_patch: float = 1e-3
    repair_rate_cap: float = 0.02

    def as_dict(self) -> dict:
        return asdict(self)

    def identity_hash(self) -> str:
        """Stable 8-hex digest. Stable matters: it gates cache reuse, so an unstable
        hash would rebuild every store on every run."""
        payload = {"search": self.as_dict(), "overscale_sampling_k": dict(sorted(OVERSCALE_SAMPLING_K.items()))}
        return sha256(dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]


@dataclass(frozen=True)
class CostCoefficients:
    """
    The four scalars a year's prices collapse to:

        LCOE(s, w, b) = (a_s*s + a_w*w + a_b*b**GAMMA) / (d0 * served_fraction)

    All four scale linearly with baseload, which is why LCOE is exactly
    baseload-invariant, and only the ratios between `a_*` set the argmin.
    """

    a_s: float
    a_w: float
    a_b: float
    d0: float


@numba.njit(cache=True, nogil=True)
def dispatch_metrics(solar, wind, s, w, b):
    """
    One year of battery dispatch, returning `(hourly_coverage, served_fraction)`.

    Fuses three production functions into a single allocation-free pass:
    `state_of_charge`, `calculate_coverage` and `calculate_served_fraction` in
    `boa.model.logic`. Those three stay in place as the readable reference that
    `tests/boa/test_bisection_kernel.py` checks this against exactly -- they are the
    specification, this is the fast path.

    Two conventions inherited from them and reproduced deliberately:

      * the battery starts empty, so hour 0 is judged against `net_energy[0]` alone
        however large the battery is;
      * coverage is binary per hour, while served fraction gives partial credit for
        energy actually delivered. Hence `served_fraction >= coverage` always, with no
        round-trip loss in the model.
    """
    n_hours = solar.shape[0]
    prev = 0.0
    covered = 0
    unmet = 0.0
    for t in range(n_hours):
        net = s * solar[t] + w * wind[t] - 1.0
        available = prev + net
        if available >= 0.0:
            covered += 1
        current = available
        if current < 0.0:
            current = 0.0
        elif current > b:
            current = b
        discharged = prev - current
        if discharged < 0.0:
            discharged = 0.0
        shortfall = -net - discharged
        if shortfall > 0.0:
            unmet += shortfall
        prev = current
    return covered / n_hours, 1.0 - unmet / n_hours


@numba.njit(cache=True, nogil=True)
def _b_min_jit(solar, wind, s, w, target, hint, b_cap, tol_rel):
    """
    Smallest battery reaching `target` coverage at this `(s, w)`, with the metrics at
    that battery. Returns `(inf, 0, 0)` when no battery at or below `b_cap` gets there.

    Sound because coverage is non-decreasing in battery size, so the predicate is
    monotone and bisection converges on its jump point. The feasible end of the bracket
    is returned, so any residual error is on the safe side: `b_min` may sit marginally
    above the true minimum, never below it, and a design that missed the coverage target
    would be a correctness failure rather than an imprecision.

    `hint` is a `b_min` from a nearby grid point, or `<= 0` for none. `b_min` varies
    smoothly in `(s, w)`, so a neighbour is usually within a factor of two, which
    collapses up to ~11 doublings into a handful of probes. It changes only what the
    bracket costs to find: both branches still establish a genuine infeasible/feasible
    pair before bisecting, so the answer is hint-independent.

    The metrics come from the last probe that set `hi`, which by construction sits at the
    returned battery size -- so the ladder's first rung costs nothing.
    """
    cov, sf = dispatch_metrics(solar, wind, s, w, 0.0)
    if cov >= target:
        return 0.0, cov, sf

    cov_hi = 0.0
    sf_hi = 0.0

    if hint > 0.0:
        cov_h, sf_h = dispatch_metrics(solar, wind, s, w, hint)
        if cov_h >= target:
            # Already feasible at the hint: halve downward for a tighter lower bound
            # rather than restarting the doubling from scratch.
            hi, lo = hint, 0.0
            cov_hi, sf_hi = cov_h, sf_h
            probe = hint
            for _ in range(64):
                probe *= 0.5
                if probe <= _B_TOL_ABS:
                    break
                cov_p, sf_p = dispatch_metrics(solar, wind, s, w, probe)
                if cov_p >= target:
                    hi, cov_hi, sf_hi = probe, cov_p, sf_p
                else:
                    lo = probe
                    break
        else:
            lo, hi = hint, hint * 2.0
            found = False
            while hi <= b_cap:
                cov_p, sf_p = dispatch_metrics(solar, wind, s, w, hi)
                if cov_p >= target:
                    cov_hi, sf_hi = cov_p, sf_p
                    found = True
                    break
                lo = hi
                hi *= 2.0
            if not found:
                return np.inf, 0.0, 0.0
    else:
        hi = 0.25
        found = False
        while hi <= b_cap:
            cov_p, sf_p = dispatch_metrics(solar, wind, s, w, hi)
            if cov_p >= target:
                cov_hi, sf_hi = cov_p, sf_p
                found = True
                break
            hi *= 2.0
        if not found:
            return np.inf, 0.0, 0.0
        lo = 0.0 if hi == 0.25 else 0.5 * hi

    tol = tol_rel * hi
    if tol < _B_TOL_ABS:
        tol = _B_TOL_ABS
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        cov_m, sf_m = dispatch_metrics(solar, wind, s, w, mid)
        if cov_m >= target:
            hi, cov_hi, sf_hi = mid, cov_m, sf_m
        else:
            lo = mid
        tol = tol_rel * hi
        if tol < _B_TOL_ABS:
            tol = _B_TOL_ABS
    return hi, cov_hi, sf_hi


def b_min_at(
    solar: np.ndarray,
    wind: np.ndarray,
    s: float,
    w: float,
    p: float,
    params: SearchParams,
    hint: float = -1.0,
) -> tuple[float, float, float]:
    """
    Smallest feasible battery at one grid point, as `(b_min, coverage, served_fraction)`.

    Public callers pass `p`, the percentile of *uncovered* hours the CLI works in; the
    kernel takes the coverage target it implies. Converting once at the boundary keeps
    the two from being confused inside the kernels.
    """
    target = 1.0 - p / 100.0
    return _b_min_jit(solar, wind, float(s), float(w), target, float(hint), params.b_cap, params.tol_rel_patch)
