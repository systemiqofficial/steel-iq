"""
Grid-bisection search over the design space.

Replaces the Monte Carlo sampler: instead of drawing random designs and sizing each
battery with a heuristic, the search evaluates a deterministic grid over the
`(solar, wind)` overscale plane and, at each node, bisects for the smallest battery
that meets the hourly-coverage target.

The method follows `scripts/boa_benchmark/core/gbs.py` on the `boa-sampling-benchmark`
branch. Three differences from that reference are deliberate: the hours metric is
hard-wired, cyclic state-of-charge and standing loss are not modelled, and the battery
bisection tolerance is relative rather than absolute.

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

    None of these is a physical parameter -- they are all search-quality dials. That
    matters for how to reason about them: a wrong value costs precision or build time,
    never feasibility, because hourly coverage is enforced at every node regardless.

    TODO: these defaults are provisional and are not the final set. `patch_grid` and
    `tol_rel_patch` drive build cost; what accuracy they buy is only known so far on a
    small regional sample, which is enough to choose values for the next runs but not to
    settle them. Fix the set once a sweep at global coverage has run.
    """

    # -- The search box: how far out in overscale the search looks at all. --------------
    box_multiple: float = 6.0  # box = this x mu, where mu = k / capacity_factor
    box_min: float = 2.0  # floor, so an excellent resource still gets a usable box
    box_abs_max: float = 200.0  # ceiling, catching the ~7.5e8 mu a zero-CF tech produces
    max_box_widenings: int = 2  # doublings allowed when a seed lands on the outer ring

    # -- Coarse tier: ranks basins and bounds the containment proof. Deliberately cheap;
    #    its output is a lower bound on b_min, never an answer. -------------------------
    coarse_grid: int = 25  # nodes per axis in the stored bound grid
    coarse_stride: int = 3  # only every 3rd node is solved; the rest inherit
    coarse_bisect_steps: int = 3  # early stop -- `lo` bounds b_min after any number

    # -- Patch tier: resolves the actual optimum, and drives build cost. ---------------
    patch_grid: int = 15  # nodes per axis, per patch; cost scales with the square
    patch_halfwidth: float = 0.45  # patch spans seed x (1 +/- this), floored at one
    #                                lattice step, since that is the seed's own resolution
    lattice_refinement: int = 2  # `patch_lattice` only: lattice points per coarse cell.
    #                              Must be an integer so lattice points land on coarse-cell
    #                              boundaries, which is what makes two patches on one pixel
    #                              share points exactly. 2 puts a node at each coarse-cell
    #                              boundary and one between, which is the coarsest spacing
    #                              that still resolves inside a cell. TODO: settle alongside
    #                              `patch_grid` in the grid-configuration sweep.
    seed_tolerance: float = 0.05  # coarse cells within 5% of the best also get a patch
    max_seeds: int = 3  # cap on patches per pixel; bounds worst-case cache size

    # -- Battery rungs: LCOE(b) is not monotone once divided by served fraction (which
    #    keeps rising with b), so the cheapest battery can sit above b_min. Rather than
    #    *choosing* one at build time, the build stores dispatch at `ladder_rungs` battery
    #    sizes spanning `b_min` to `ladder_max_span * b_min` and lets the query pick.
    #    That is what keeps the cache pure physics: a stored rung is a dispatch result,
    #    with no cost in it, so it is valid for every year and every cost scenario. It is
    #    also strictly more accurate than choosing at build time, because the pick then
    #    uses the query year's real prices instead of a frozen anchor's.
    #    Rungs are spaced quadratically, so they cluster just above b_min. The span is set
    #    by how far the optimum can travel: a cheaper battery moves it outward, so 1.35x
    #    covers the battery costs a multi-decade horizon reaches while staying far short of
    #    where coverage saturates. TODO: settle the count and the span in the
    #    grid-configuration sweep -- both are tuned against one cost trajectory.
    ladder_rungs: int = 4  # battery sizes stored per node; rung 0 is b_min itself
    ladder_max_span: float = 1.35  # top rung, as a multiple of b_min

    # -- Bisection tolerances. `tol_rel_patch` sets how many dispatch passes each patch
    #    node spends on the battery, so it is the other build-cost driver. gamma = 0.85
    #    damps its accuracy cost: a relative error e in battery size moves LCOE by well
    #    under e. -----------------------------------------------------------------------
    b_cap: float = 500.0  # baseload-hours; above this a pixel is called infeasible
    tol_rel_patch: float = 1e-2
    repair_rate_cap: float = 0.02  # share of pixels the containment certificate may
    #                                send back for repair before the run is suspect

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


def anchor_years(start: int, end: int, interval: int) -> list[int]:
    """
    Anchor years spanning `[start, end]` at `interval`-year spacing, always including
    `end` even when the final gap is shorter than `interval`.

    Re-anchoring rebuilds a pixel's frontier periodically against updated costs, rather
    than one frontier serving a whole multi-decade horizon off a single build-time
    anchor. `BOA_BISECTION_PLAN.md`'s re-anchoring benchmark found the excess-LCOE tail
    from an out-of-date anchor is flat and small for most of a 25-year horizon, then
    jumps sharply only in the final stretch -- so always anchoring the horizon's own
    end year matters more than evenly spacing every interval, and this guarantees it
    regardless of whether `end - start` divides evenly by `interval`.

    Only computes *which years* get their own anchor -- building a frontier per anchor
    year, storing more than one per pixel, and routing a query year to the right one are
    a cache-layout and build-orchestration concern (BOA_BISECTION_PLAN.md's M2, "cache
    schema v3"), deliberately out of scope here.
    """
    if interval <= 0:
        raise ValueError(f"interval must be positive, got {interval}")
    if end < start:
        raise ValueError(f"end ({end}) must be >= start ({start})")
    years = list(range(start, end, interval))
    if not years or years[-1] != end:
        years.append(end)
    return years


def nearest_anchor(query_year: int, anchors: list[int]) -> int:
    """
    The anchor year closest to `query_year`, ties (an exact midpoint between two
    anchors) broken toward whichever comes first in `anchors`.

    This is what actually bounds drift: with anchors spaced `interval` years apart, no
    query year is ever more than `interval / 2` years from the anchor it routes to,
    versus up to the full horizon length under a single fixed anchor.
    """
    if not anchors:
        raise ValueError("anchors must be non-empty")
    return min(anchors, key=lambda a: abs(a - query_year))


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
def _bracket_jit(solar, wind, s, w, target, hint, b_cap):
    """
    An infeasible/feasible pair straddling `b_min`, as `(lo, hi, coverage, served_fraction)`
    with the metrics taken at `hi`.

    `(0, 0, ...)` when no battery is needed at all; `hi = inf` when none at or below
    `b_cap` reaches the target. Shared by both bisections above it, which differ only in
    how they then close the bracket.

    `hint` is a `b_min` from a nearby grid point, or `<= 0` for none. `b_min` varies
    smoothly in `(s, w)`, so a neighbour is usually within a factor of two, which collapses
    up to ~11 doublings into a handful of probes. It changes only what the bracket costs to
    find: both branches still establish a genuine infeasible/feasible pair, so whatever
    closes the bracket is hint-independent to its own tolerance.
    """
    cov, sf = dispatch_metrics(solar, wind, s, w, 0.0)
    if cov >= target:
        return 0.0, 0.0, cov, sf

    if hint > 0.0:
        cov_h, sf_h = dispatch_metrics(solar, wind, s, w, hint)
        if cov_h >= target:
            # Already feasible at the hint: halve downward for a tighter lower bound
            # rather than restarting the doubling from scratch.
            lo, hi, cov_hi, sf_hi = 0.0, hint, cov_h, sf_h
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
            return lo, hi, cov_hi, sf_hi
        lo, hi = hint, hint * 2.0
    else:
        lo, hi = 0.0, 0.25

    while hi <= b_cap:
        cov_p, sf_p = dispatch_metrics(solar, wind, s, w, hi)
        if cov_p >= target:
            return lo, hi, cov_p, sf_p
        lo = hi
        hi *= 2.0
    return 0.0, np.inf, 0.0, 0.0


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

    The metrics come from the last probe that set `hi`, which by construction sits at the
    returned battery size -- so the ladder's first rung costs nothing.
    """
    lo, hi, cov_hi, sf_hi = _bracket_jit(solar, wind, s, w, target, hint, b_cap)
    if not np.isfinite(hi):
        return np.inf, 0.0, 0.0

    tol = max(tol_rel * hi, _B_TOL_ABS)
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        cov_m, sf_m = dispatch_metrics(solar, wind, s, w, mid)
        if cov_m >= target:
            hi, cov_hi, sf_hi = mid, cov_m, sf_m
        else:
            lo = mid
        tol = max(tol_rel * hi, _B_TOL_ABS)
    return hi, cov_hi, sf_hi


@numba.njit(cache=True, nogil=True)
def _b_min_bound_jit(solar, wind, s, w, target, hint, b_cap, max_steps):
    """
    A *lower bound* on `b_min`, plus a feasible battery above it, as `(lo, hi)`.

    Same bracket as `_b_min_jit`, then `max_steps` bisection steps instead of running to
    tolerance. The running `lo` is infeasible after any number of steps, so it is a valid
    lower bound at every point, which is all the coarse sweep needs.

    `hi` comes back only so the caller can chain it into the next node's `hint`; it is a
    feasible battery, not an answer.
    """
    lo, hi, _, _ = _bracket_jit(solar, wind, s, w, target, hint, b_cap)
    if not np.isfinite(hi):
        return np.inf, np.inf
    if hi <= 0.0:
        return 0.0, 0.0

    for _ in range(max_steps):
        mid = 0.5 * (lo + hi)
        cov_m, _sf = dispatch_metrics(solar, wind, s, w, mid)
        if cov_m >= target:
            hi = mid
        else:
            lo = mid
    return lo, hi


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


@numba.njit(cache=True, nogil=True)
def _rung_metrics_jit(solar, wind, s, w, b_min, cov0, sf0, spans, out_b, out_cov, out_sf):
    """
    Dispatch at each rung above `b_min`, filling `(out_b, out_cov, out_sf)` in place.

    Rung 0 is `b_min` itself and costs no dispatch: `(cov0, sf0)` are the metrics of the
    last probe of the bisection that found it. Every later rung is one pass.

    `b_min <= 0` yields every rung at `b_min` with its own metrics: a multiple of zero is
    still zero, so there is nothing above it to price, and this design does not special-case
    seeding a zero-`b_min` node away from zero.
    """
    out_b[0], out_cov[0], out_sf[0] = b_min, cov0, sf0
    if b_min <= 0.0:
        for r in range(1, spans.shape[0]):
            out_b[r], out_cov[r], out_sf[r] = b_min, cov0, sf0
        return

    for r in range(1, spans.shape[0]):
        b = b_min * spans[r]
        cov, sf = dispatch_metrics(solar, wind, s, w, b)
        out_b[r], out_cov[r], out_sf[r] = b, cov, sf


def rung_spans(params: SearchParams) -> np.ndarray:
    """
    Rung positions as multiples of `b_min`: `ladder_rungs` values from 1.0 to
    `ladder_max_span`, spaced **quadratically** so they cluster just above `b_min`.

    The spacing is the design, not the count. LCOE(b) has a shallow trough close to
    `b_min`: the numerator grows as `b**GAMMA` from the first step, while the denominator
    can only climb toward a served fraction of 1, so the two cross early and the crossing
    moves outward only as the battery gets cheaper relative to solar and wind. Rungs
    therefore have to be dense where the trough is and may be sparse beyond it. Even spacing
    puts the first probe at `1/(R-1)` of the span, which steps over the trough entirely, and
    adding rungs at the top does not fix that -- they land past the crossing, where the
    objective is already rising.

    `ladder_max_span` bounds the other end: beyond it the numerator dominates for any cost
    ratio worth modelling, so further rungs would only ever duplicate the answer.
    """
    if params.ladder_rungs < 1:
        raise ValueError(f"ladder_rungs must be >= 1, got {params.ladder_rungs}")
    if params.ladder_rungs == 1:
        return np.ones(1, dtype=np.float64)
    frac = np.arange(params.ladder_rungs, dtype=np.float64) / (params.ladder_rungs - 1)
    return 1.0 + (params.ladder_max_span - 1.0) * frac**2


def battery_rungs(
    solar: np.ndarray,
    wind: np.ndarray,
    s: float,
    w: float,
    b_min: float,
    cov0: float,
    sf0: float,
    params: SearchParams,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Dispatch metrics at every stored battery size for one node, as
    `(b, hours_covered_frac, energy_served_frac)`, each of length `ladder_rungs`.

    **Takes no costs, and that is the point.** Choosing *which* battery is cheapest needs
    prices, and the only ones available at build time are frozen. Storing the candidates
    instead keeps the cache pure dispatch -- valid for every year and every cost scenario,
    and shareable between anchors -- and lets `argmin_lcoe` choose with the query year's
    real prices rather than the build's.

    Feasibility holds at every rung: coverage is non-decreasing in battery size and rung 0
    is `b_min`, so nothing stored here can miss the coverage target.
    """
    spans = rung_spans(params)
    r = spans.shape[0]
    out_b = np.empty(r, dtype=np.float64)
    out_cov = np.empty(r, dtype=np.float64)
    out_sf = np.empty(r, dtype=np.float64)
    _rung_metrics_jit(
        solar, wind, float(s), float(w), float(b_min), float(cov0), float(sf0), spans, out_b, out_cov, out_sf
    )
    return out_b, out_cov, out_sf


@dataclass(frozen=True)
class PixelFrontier:
    """
    One pixel's cached physics: a coarse lower-bound grid over the whole search box, plus
    up to `max_seeds` dense patches around the basins worth resolving.

    Everything here is dispatch, not economics. The only place a cost touches the build is
    which coarse cells become seeds, and that uses frozen anchor ratios rather than a
    query year's prices -- so a frontier serves every investment year and every cost
    scenario unchanged.

    Patch arrays are always allocated at the full `max_seeds` depth and at the widest
    patch `SearchParams` can produce (`max_patch_points`); only the first `n_patches`
    slots, and within each slot only the first `patch_points[slot]` rows and columns, hold
    real values. Fixed shape is what lets the region cache index directly instead of
    carrying CSR offsets.

    The padding is deliberate. Patches sit on a shared lattice with fixed spacing, so a
    wide patch has more points than a narrow one, and the allocation is sized for the widest
    the parameters allow -- most of a typical slot is therefore padding. It is zero-filled
    and compresses away, and chunking the cache along `point` keeps a read to one pixel's
    worth of it. **Do not chunk across the patch axes**: that is what would turn the padding
    from a storage detail into a query cost.
    """

    status: int
    n_patches: int
    box_widenings: int
    s_coarse: np.ndarray  # (Gc,) float32 -- the box axes
    w_coarse: np.ndarray  # (Gc,) float32
    b_coarse: np.ndarray  # (Gc, Gc) float16, rounded toward zero; inf where infeasible
    s_patch: np.ndarray  # (K, P) float32, P = max_patch_points(params)
    w_patch: np.ndarray  # (K, P) float32
    # Dispatch at each stored battery size per node. The trailing axis is the rung: rung 0
    # is `b_min`, the rest climb to `ladder_max_span * b_min`. Storing the set rather than
    # a build-time pick is what keeps this array pure physics -- no cost enters it, so it
    # serves every year, and `argmin_lcoe` chooses the rung with that year's real prices.
    b_patch: np.ndarray  # (K, P, P, R) float32 -- battery, in baseload-hours
    # Fraction of annual *energy* delivered. This is the LCOE denominator.
    energy_served_frac: np.ndarray  # (K, P, P, R) float64
    # Fraction of *hours* in which demand was fully met. This is the feasibility
    # constraint, reported but never ranked on -- the two differ, and conflating them is
    # the inconsistency this rewrite exists to remove.
    hours_covered_frac: np.ndarray  # (K, P, P, R) float64
    # Coarse-grid index bounds (i0, i1, j0, j1) each patch was snapped to, so the query-time
    # containment certificate can tell exactly which coarse cells are "inside some patch"
    # without re-deriving it from float comparisons against s_patch/w_patch.
    patch_bounds: np.ndarray  # (K, 4) int32
    # Real extent of each slot, as (n_s, n_w). Everything beyond it is padding, and every
    # consumer must mask it: an unmasked zero reads as a free design and would win the
    # argmin outright.
    patch_points: np.ndarray  # (K, 2) int32


def search_box(solar: np.ndarray, wind: np.ndarray, params: SearchParams) -> tuple[float, float]:
    """
    The `(s_max, w_max)` extent of the physics box, from this pixel's capacity factors.

    Reuses `overscale_mus_from_cf` verbatim, so the box tracks `k / CF` -- the same
    resource scaling the deleted sampler used for its proposal distribution. A fixed box
    would either truncate poor pixels or spend most of its resolution on good ones.

    Both clamps earn their place. `box_min` keeps an excellent resource from getting a box
    too narrow to hold the optimum. `box_abs_max` catches `overscale_mus_from_cf`'s
    division guard: a zero-CF technology yields a mu of ~7.5e8, and an unclamped box that
    wide is unresolvable at any grid size.

    Nothing here reads a capacity ceiling. The box is physics; the ceiling is
    `boa.model.capacity_box`.
    """
    from boa.model.logic import overscale_mus_from_cf

    mus = overscale_mus_from_cf(float(np.mean(solar)), float(np.mean(wind)))
    s_max = float(np.clip(params.box_multiple * mus["solar"], params.box_min, params.box_abs_max))
    w_max = float(np.clip(params.box_multiple * mus["wind"], params.box_min, params.box_abs_max))
    return s_max, w_max


def _sub_lattice(n: int, stride: int) -> np.ndarray:
    """Indices `0, stride, 2*stride, ...` always including `n-1`, so every node has a
    dominating lattice node to inherit from."""
    idx = list(range(0, n, max(1, stride)))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return np.asarray(idx, dtype=np.int64)


def coarse_b_min_grid(
    solar: np.ndarray,
    wind: np.ndarray,
    s_vals: np.ndarray,
    w_vals: np.ndarray,
    p: float,
    params: SearchParams,
) -> np.ndarray:
    """
    Lower bounds on `b_min` across the whole box, cheaply.

    The coarse grid has two jobs -- rank basins so seeds land in the right places, and
    supply the query-time containment certificate with something to bound against -- and
    **neither needs `b_min`, only a lower bound on it**. Two economies follow, and both
    trade a looser bound for build time, never an answer:

      * **Sub-lattice.** `b_min` is non-increasing in both solar and wind, so a value
        computed at any *dominating* node `(i' >= i, j' >= j)` is at or below the exact
        `b_min` at `(i, j)`. Only the stride-`coarse_stride` lattice is solved; every
        other node inherits from the nearest dominating lattice node.
      * **Early stop.** `_b_min_bound_jit` returns the infeasible bracket end after
        `coarse_bisect_steps` steps rather than converging.

    A suffix-**maximum** over the lattice runs before the fill. Monotonicity holds for the
    exact `b_min` but not automatically for bounds -- two nodes' brackets are independent,
    so a dominating node can come back with the larger `lo`. The repair is to take, at
    each node, the largest lower bound found anywhere above-and-right of it. That is
    sound because a dominating node's `lo` bounds `b_min` there, which is itself at or
    below `b_min(i, j)`; and because a superset maximum can only shrink as the index
    grows, the result is non-increasing in both axes by construction.

    It has to be the max, not the min. Both are valid bounds, but the min would drag the
    far corner's near-zero `b_min` back across the whole grid, collapsing the bound to
    nothing near the origin and destroying the ranking the seeds depend on. The max is
    the tightest bound the lattice supports, and it carries infeasibility for free: an
    infeasible node correctly condemns everything it dominates.

    Returns float64 with `inf` where no battery at or below `b_cap` meets the target;
    `inf` propagates down-and-left correctly, since a dominated node needs at least as
    much battery.
    """
    target = 1.0 - p / 100.0
    si = _sub_lattice(len(s_vals), params.coarse_stride)
    wj = _sub_lattice(len(w_vals), params.coarse_stride)

    lattice = np.empty((len(si), len(wj)), dtype=np.float64)
    row_hint = -1.0
    for a, i in enumerate(si):
        hint = row_hint
        for b, j in enumerate(wj):
            lo, hi = _b_min_bound_jit(
                solar, wind, float(s_vals[i]), float(w_vals[j]), target, hint, params.b_cap, params.coarse_bisect_steps
            )
            lattice[a, b] = lo
            if np.isfinite(hi) and hi > 0.0:
                hint = hi
                if b == 0:
                    row_hint = hi

    # Suffix maximum from the high corner: lattice[a, b] <- max over a' >= a, b' >= b.
    # `np.asarray` only pins the type; numpy's ufunc `.accumulate` stub is not specific
    # enough for a type checker to see that the result is still indexable.
    lattice = np.asarray(np.maximum.accumulate(lattice[::-1, :], axis=0))[::-1]
    lattice = np.asarray(np.maximum.accumulate(lattice[:, ::-1], axis=1))[:, ::-1]

    # Each full node inherits from the first lattice node that dominates it on each axis.
    pos_s = np.searchsorted(si, np.arange(len(s_vals)), side="left")
    pos_w = np.searchsorted(wj, np.arange(len(w_vals)), side="left")
    return lattice[np.ix_(pos_s, pos_w)]


def anchor_score(s_vals: np.ndarray, w_vals: np.ndarray, b_lower: np.ndarray, anchor: CostCoefficients) -> np.ndarray:
    """
    The LCOE numerator on the coarse grid, under frozen anchor cost ratios.

    Used only to rank basins for seeding, which is why three simplifications are fine and
    one of them is deliberate:

      * `d0` is dropped -- a positive scalar rescales but never reorders;
      * `served_fraction` is unknown on the coarse grid, so this is a numerator, not an
        LCOE. The denominator lies in `[coverage, 1]`, a narrow band near 1, so it cannot
        reorder basins that are more than a few percent apart -- and `seed_tolerance`
        exists precisely to keep the ones that are;
      * `b_lower` is a lower bound, so the score is optimistic. That is the right
        direction: a basin is explored if it *could* be good, and the query-time
        containment certificate catches any basin that was skipped and should not have
        been.

    The anchor ratios are frozen rather than the query year's real prices. That is what
    keeps the build year-agnostic, and it costs at most repair work, never accuracy.
    """
    numerator = anchor.a_s * s_vals[:, None] + anchor.a_w * w_vals[None, :]
    return numerator + anchor.a_b * np.power(b_lower, GAMMA)


def select_seeds(
    values: np.ndarray,
    tolerance: float,
    min_separation: int,
    max_seeds: int,
) -> list[tuple[int, int]]:
    """
    Grid cells worth refining: the cheapest, plus any within `tolerance` of it that sit in
    a genuinely different basin.

    Two rules, each answering a different failure. The **tolerance** admits near-ties: the
    objective is not convex, and a solar-heavy and a wind-heavy design can land within a
    few percent of each other while being far apart in `(s, w)`, so refining only the
    argmin can miss the basin that wins once resolved. The **separation** rule stops the
    opposite failure -- a single minimum's immediate neighbours are all near-ties too, and
    without it every seed slot goes to one basin.

    Ports `gbs._select_seeds`' greedy Chebyshev-separation loop and adds the tolerance cut,
    since a fixed `k` would force patches onto basins that are not close to competitive.
    Returns cheapest first; non-finite cells can never be seeds.
    """
    finite = np.isfinite(values)
    if not finite.any():
        return []

    threshold = float(values[finite].min()) * (1.0 + tolerance)
    order = np.argsort(values, axis=None, kind="stable")
    seeds: list[tuple[int, int]] = []
    for flat in order:
        i, j = (int(x) for x in np.unravel_index(flat, values.shape))
        if not finite[i, j] or values[i, j] > threshold:
            break
        if all(max(abs(i - si), abs(j - sj)) >= min_separation for si, sj in seeds):
            seeds.append((i, j))
        if len(seeds) == max_seeds:
            break
    return seeds


def patch_box(
    s_coarse: np.ndarray,
    w_coarse: np.ndarray,
    i: int,
    j: int,
    params: SearchParams,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """
    The dense sub-grid around seed `(i, j)`, as `(s_vals, w_vals, (i0, i1, j0, j1))`.

    The half-width is *relative* to the seed's own coordinate, so a patch around a large
    overscale is proportionally wider -- the coarse grid's placement error scales the same
    way.

    It is floored at `coarse_stride` coarse cells, which is the resolution the seed itself
    was chosen at: seeds come from the sub-lattice, so the true optimum can sit anywhere
    within roughly one lattice step of the seed and the patch has to bracket that. The
    floor also keeps a seed at the origin from producing a patch of zero width.

    **The box is then snapped outward to whole coarse cells, and that is load-bearing.**
    The query-time containment certificate partitions the coarse grid into "inside some
    patch" and "outside every patch", and bounds the second set from below. If a patch
    edge cut through a coarse cell, that cell would be neither swept densely nor bounded,
    and the certificate would have a hole in it that nothing else would detect.

    Snapping outward also guarantees `i0 <= i <= i1`: the seed sits strictly inside the
    un-snapped interval, and snapping only ever widens.
    """
    i0, i1, j0, j1 = patch_bounds_for_seed(s_coarse, w_coarse, i, j, params)
    s_vals = np.linspace(float(s_coarse[i0]), float(s_coarse[i1]), params.patch_grid)
    w_vals = np.linspace(float(w_coarse[j0]), float(w_coarse[j1]), params.patch_grid)
    return s_vals, w_vals, (i0, i1, j0, j1)


def patch_bounds_for_seed(
    s_coarse: np.ndarray,
    w_coarse: np.ndarray,
    i: int,
    j: int,
    params: SearchParams,
) -> tuple[int, int, int, int]:
    """
    The coarse cells a seed's patch covers, as `(i0, i1, j0, j1)`.

    Split out of `patch_box` because `patch_lattice` needs the same extent under a
    different interior spacing, and the extent rule is the part that must not drift
    between them.
    """
    ds = float(s_coarse[1] - s_coarse[0]) * params.coarse_stride
    dw = float(w_coarse[1] - w_coarse[0]) * params.coarse_stride
    half_s = max(params.patch_halfwidth * float(s_coarse[i]), ds)
    half_w = max(params.patch_halfwidth * float(w_coarse[j]), dw)

    i0 = max(0, int(np.searchsorted(s_coarse, s_coarse[i] - half_s, side="right")) - 1)
    i1 = min(len(s_coarse) - 1, int(np.searchsorted(s_coarse, s_coarse[i] + half_s, side="left")))
    j0 = max(0, int(np.searchsorted(w_coarse, w_coarse[j] - half_w, side="right")) - 1)
    j1 = min(len(w_coarse) - 1, int(np.searchsorted(w_coarse, w_coarse[j] + half_w, side="left")))
    return i0, i1, j0, j1


def max_patch_points(params: SearchParams) -> int:
    """
    Widest lattice patch these parameters can produce, in points per axis.

    Exact rather than a cap or a guess, because `patch_bounds_for_seed` is scale-invariant:
    the half-width is `max(patch_halfwidth * s_coarse[i], coarse_stride * ds)` and
    `s_coarse[i] = i * ds`, so the span in coarse cells depends only on the seed's index,
    never on the axis extent. Evaluating every index therefore gives the true maximum over
    all pixels, which is what the padded arrays must be sized to.
    """
    axis = np.arange(params.coarse_grid, dtype=np.float64)
    span = 0
    for i in range(params.coarse_grid):
        i0, i1, _, _ = patch_bounds_for_seed(axis, axis, i, i, params)
        span = max(span, i1 - i0)
    return span * params.lattice_refinement + 1


def patch_lattice(
    s_coarse: np.ndarray,
    w_coarse: np.ndarray,
    i: int,
    j: int,
    params: SearchParams,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """
    The dense sub-grid around seed `(i, j)`, on a lattice shared by the whole pixel.

    Same extent as `patch_box`, different interior: spacing is fixed at one coarse cell
    divided by `lattice_refinement`, so the point *count* varies with the box width
    instead of the *spacing* varying. Two consequences, and both are the point:

    **Patches on one pixel share points exactly.** Every value here is an integer multiple
    of `d / R` where `d` is the coarse spacing, because the box edges are coarse nodes and
    `R` is an integer. So a node computed for one anchor's patch is reusable by another's
    wherever the two overlap -- and reuse is exact, not approximate, because a patch node
    holds `(b_min, coverage, served_fraction)`, which carry no cost term. Cost enters only
    in choosing the seeds and in the query-time argmin.

    **Resolution stops depending on box width.** Under `patch_box` a fixed point count
    spread over a variable box resolves the widest patches most coarsely -- and those are
    exactly the ones bracketing the least certain seeds, since the box widens with the
    seed's own coordinate. Here the spacing is constant, so every patch is resolved alike.

    The extent still snaps outward to whole coarse cells, which the containment certificate
    requires: a cell that is neither densely swept nor bounded is a hole nothing detects.
    """
    if params.lattice_refinement < 1:
        raise ValueError(f"lattice_refinement must be >= 1, got {params.lattice_refinement}")

    i0, i1, j0, j1 = patch_bounds_for_seed(s_coarse, w_coarse, i, j, params)
    r = params.lattice_refinement
    s_vals = np.linspace(float(s_coarse[i0]), float(s_coarse[i1]), (i1 - i0) * r + 1)
    w_vals = np.linspace(float(w_coarse[j0]), float(w_coarse[j1]), (j1 - j0) * r + 1)
    return s_vals, w_vals, (i0, i1, j0, j1)


def _to_float16_toward_zero(values: np.ndarray) -> np.ndarray:
    """
    Cast to float16, stepping down one ulp wherever the default round-to-nearest rounded
    *up*.

    `b_coarse` is only ever read as a lower bound in a proof, so its precision barely
    matters but its **direction** is absolute. float16 spacing is ~0.5 near `b = 500`, so
    a single round-to-nearest could lift a bound above the value it is meant to bound and
    silently invalidate the containment certificate -- with nothing failing loudly
    anywhere. Infinities are left alone: `inf > inf` is false, and `nextafter` would turn
    them into 65504.
    """
    out = values.astype(np.float16)
    too_high = out.astype(np.float64) > values
    if too_high.any():
        out[too_high] = np.nextafter(out[too_high], np.float16(0.0))
    return out


def build_pixel_frontier(
    solar: np.ndarray,
    wind: np.ndarray,
    p: float,
    params: SearchParams,
    anchor: CostCoefficients,
    hint: float = -1.0,
) -> PixelFrontier:
    """
    Everything one pixel contributes to the design cache.

    Degenerate check, coarse sweep, seed selection, box widening, then a dense patch per
    seed. Deterministic: no RNG survives the rewrite, so two builds of the same pixel are
    bit-identical.

    `hint` is a `b_min` carried in from a neighbouring pixel, and it reaches **only the
    patch bisections**, never the coarse sweep. That is not an oversight. The coarse sweep
    stops after `coarse_bisect_steps` steps, so its output genuinely depends on where the
    bracket started -- threading a neighbour's value in would change the bounds, which
    would change the seeds, which would move the patches. A warm start must not be able to
    do that. The patch bisections run to tolerance, so there the hint changes only cost.

    No cost year, cost scenario or baseload is read anywhere in here, which is what makes
    `status` year-invariant -- the property `lcoe_promotion` requires.
    """
    gc, k = params.coarse_grid, params.max_seeds
    pmax = max_patch_points(params)
    # Allocated once at full depth and at the widest patch the parameters allow, then
    # filled in place: an early return simply hands back the zeros, which is the right
    # content for `n_patches = 0`. `patch_points` stays zero there too, so a consumer that
    # honours it sees an empty patch rather than a grid of free designs.
    rungs = params.ladder_rungs
    patches: dict[str, np.ndarray] = {
        "s_patch": np.zeros((k, pmax), dtype=np.float32),
        "w_patch": np.zeros((k, pmax), dtype=np.float32),
        "b_patch": np.zeros((k, pmax, pmax, rungs), dtype=np.float32),
        "energy_served_frac": np.zeros((k, pmax, pmax, rungs), dtype=np.float64),
        "hours_covered_frac": np.zeros((k, pmax, pmax, rungs), dtype=np.float64),
        "patch_bounds": np.zeros((k, 4), dtype=np.int32),
        "patch_points": np.zeros((k, 2), dtype=np.int32),
    }

    s_max, w_max = search_box(solar, wind, params)

    # Physics outranks economics: a pixel with no resource is not "expensive", it is
    # impossible, and no amount of searching changes that.
    if float(np.sum(solar)) <= 0.0 and float(np.sum(wind)) <= 0.0:
        return PixelFrontier(
            status=STATUS_ZERO_POTENTIAL,
            n_patches=0,
            box_widenings=0,
            s_coarse=np.linspace(0.0, s_max, gc, dtype=np.float32),
            w_coarse=np.linspace(0.0, w_max, gc, dtype=np.float32),
            b_coarse=np.full((gc, gc), np.inf, dtype=np.float16),
            **patches,
        )

    widenings = 0
    while True:
        s_coarse = np.linspace(0.0, s_max, gc)
        w_coarse = np.linspace(0.0, w_max, gc)
        b_coarse = coarse_b_min_grid(solar, wind, s_coarse, w_coarse, p, params)

        # Rank on the sub-lattice only, never on the filled grid. A filled node inherits
        # its bound from a *dominating* node at higher (s, w), so cells just inside the
        # infeasible region carry a small finite bound: they look cheap, and the score
        # would send the seed straight at them, building a patch around a point where
        # nothing dispatches.
        #
        # On a lattice node the bound is about that node, and the suffix-max leaves it
        # infinite exactly when the node is infeasible -- infeasibility only ever
        # propagates to dominated nodes, so a feasible lattice node cannot inherit one.
        # A lattice seed is therefore always feasible. Scoring only the lattice also keeps
        # separation in its natural unit -- adjacent lattice nodes are usually the same
        # basin, so two lattice steps is the rule; on the filled grid it would have to be
        # restated in coarse cells to bind at all.
        li = _sub_lattice(gc, params.coarse_stride)
        seeds = [
            (int(li[i]), int(li[j]))
            for i, j in select_seeds(
                anchor_score(s_coarse[li], w_coarse[li], b_coarse[np.ix_(li, li)], anchor),
                params.seed_tolerance,
                2,
                params.max_seeds,
            )
        ]
        if not seeds:
            break

        # A seed on the outer ring means the optimum may lie beyond the box. Widen and
        # redo rather than report a truncated answer -- and record it, so a pixel that ran
        # out of widenings is visible in the cache instead of silently wrong.
        on_edge = any(i == gc - 1 or j == gc - 1 for i, j in seeds)
        at_abs_max = s_max >= params.box_abs_max and w_max >= params.box_abs_max
        if on_edge and widenings < params.max_box_widenings and not at_abs_max:
            s_max = min(2.0 * s_max, params.box_abs_max)
            w_max = min(2.0 * w_max, params.box_abs_max)
            widenings += 1
            continue
        break

    axes: dict[str, np.ndarray] = {
        "s_coarse": s_coarse.astype(np.float32),
        "w_coarse": w_coarse.astype(np.float32),
        "b_coarse": _to_float16_toward_zero(b_coarse),
    }

    if not seeds:
        # No battery at or below `b_cap` meets the coverage target anywhere in the box.
        # Proven at build time from the profiles alone, so it holds for every year.
        return PixelFrontier(status=STATUS_NO_OPTIMUM, n_patches=0, box_widenings=widenings, **axes, **patches)

    for slot, (i, j) in enumerate(seeds):
        s_vals, w_vals, bounds = patch_lattice(s_coarse, w_coarse, i, j, params)
        n_s, n_w = len(s_vals), len(w_vals)
        patches["s_patch"][slot, :n_s] = s_vals
        patches["w_patch"][slot, :n_w] = w_vals
        patches["patch_bounds"][slot] = bounds
        patches["patch_points"][slot] = (n_s, n_w)
        row_hint = hint
        for a in range(n_s):
            node_hint = row_hint
            for b in range(n_w):
                b_min, cov, sf = b_min_at(solar, wind, s_vals[a], w_vals[b], p, params, node_hint)
                if np.isfinite(b_min):
                    if b_min > 0.0:
                        node_hint = b_min
                        if b == 0:
                            row_hint = b_min
                    b_r, cov_r, sf_r = battery_rungs(
                        solar, wind, float(s_vals[a]), float(w_vals[b]), b_min, cov, sf, params
                    )
                else:
                    # Infeasible node: every rung carries the infinite b_min, and a zero
                    # served fraction masks it out of any argmin.
                    b_r = np.full(params.ladder_rungs, b_min, dtype=np.float64)
                    cov_r = np.zeros(params.ladder_rungs)
                    sf_r = np.zeros(params.ladder_rungs)
                patches["b_patch"][slot, a, b] = b_r
                patches["energy_served_frac"][slot, a, b] = sf_r
                patches["hours_covered_frac"][slot, a, b] = cov_r

    return PixelFrontier(status=STATUS_OK, n_patches=len(seeds), box_widenings=widenings, **axes, **patches)


@dataclass(frozen=True)
class Optimum:
    """
    One pixel's cheapest cached design under one year's real costs, plus whether that is
    provably the true optimum over the whole search box (`patch_certified`) rather than
    merely the best of what got densely searched.

    `argmin_truncated` is the sharper of the two diagnostics and answers a narrower
    question: did the winning design sit against a patch edge that the patch itself
    imposed? `patch_certified` is false whenever *any* outside cell fails to be bounded
    away, which is common; truncation says this particular answer may be an artefact of
    where the patch stopped.

    Both are year-dependent -- the argmin moves with the year's costs -- so neither may
    become a `status` code, which `lcoe_promotion` requires to be year-invariant.
    """

    lcoe: float
    solar: float
    wind: float
    battery: float
    served_fraction: float
    patch_index: int
    patch_certified: bool
    argmin_truncated: bool


def _patch_membership_mask(frontier: PixelFrontier) -> np.ndarray:
    """`(Gc, Gc)` boolean grid: is this coarse cell inside some patch's snapped range?"""
    gc = frontier.b_coarse.shape[0]
    covered = np.zeros((gc, gc), dtype=bool)
    for slot in range(frontier.n_patches):
        i0, i1, j0, j1 = (int(x) for x in frontier.patch_bounds[slot])
        covered[i0 : i1 + 1, j0 : j1 + 1] = True
    return covered


def _argmin_truncated(frontier: PixelFrontier, slot: int, i: int, j: int) -> bool:
    """
    True iff the winning node sits on a patch edge the *patch* imposed.

    An argmin on the boundary ring means the objective was still improving when the dense
    sweep ran out, so the reported design may be an artefact of the patch's extent. But
    only edges interior to the search box count. Two edges are not truncation and must not
    be flagged:

      * `s = 0` or `w = 0` -- a genuine corner solution (wind-only or solar-only). There is
        nothing below zero to have missed, and this is the common case by a wide margin.
      * the outer ring of the coarse grid -- that is the search box running out, which
        `box_widenings` already records and which widening already had its chance to fix.
    """
    gc = frontier.b_coarse.shape[0]
    # The slot's real extent, not the padded allocation: the last *valid* node is the edge.
    n_s, n_w = (int(x) for x in frontier.patch_points[slot])
    i0, i1, j0, j1 = (int(x) for x in frontier.patch_bounds[slot])
    return bool(
        (i == 0 and i0 > 0) or (i == n_s - 1 and i1 < gc - 1) or (j == 0 and j0 > 0) or (j == n_w - 1 and j1 < gc - 1)
    )


def _containment_certificate(frontier: PixelFrontier, coeffs: CostCoefficients, incumbent: float) -> bool:
    """
    True iff every coarse cell outside every patch is proven no cheaper than `incumbent`.

    For a cell at `(s_coarse[i], w_coarse[j])`, `b_coarse[i, j]` lower-bounds `b_min`
    everywhere the cell dominates. Since cost is monotone increasing in battery size, that
    lower bound gives the smallest possible numerator for *any* battery a design there
    might use -- not only `b_min` -- and `served_fraction <= 1` gives the largest possible
    denominator. So `(a_s*s + a_w*w + a_b*b_lo**GAMMA) / d0` bounds LCOE below for any
    design in that cell, at any battery size, with no reference to a ladder at all.
    """
    s_c = frontier.s_coarse.astype(np.float64)
    w_c = frontier.w_coarse.astype(np.float64)
    b_lo = np.maximum(frontier.b_coarse.astype(np.float64), 0.0)
    bound = (coeffs.a_s * s_c[:, None] + coeffs.a_w * w_c[None, :] + coeffs.a_b * np.power(b_lo, GAMMA)) / coeffs.d0

    outside = bound[~_patch_membership_mask(frontier)]
    if outside.size == 0:
        return True
    return bool(np.all(outside >= incumbent - 1e-9))


def argmin_lcoe(frontier: PixelFrontier, coeffs: CostCoefficients) -> Optimum:
    """
    The cheapest design among everything a build cached for this pixel, under this year's
    real costs, plus the containment certificate.

    No dispatch, no bisection, no coverage check: every cached rung sits at or above its
    node's own `b_min`, and coverage is non-decreasing in battery size, so feasibility
    holds by construction.

    Searches every populated patch slot **and every rung**, not just the anchor's
    favourite. Both matter for the same reason: the build's frozen anchor costs are not
    this year's, so a year's real prices can promote a basin the anchor ranked second, and
    can equally prefer a larger battery than the anchor would have chosen.
    """
    a_s, a_w, a_b, d0 = coeffs.a_s, coeffs.a_w, coeffs.a_b, coeffs.d0
    best_lcoe = np.inf
    best: tuple[int, int, int, int, float, float, float] | None = None

    for k in range(frontier.n_patches):
        # Slice to the slot's real extent before anything else. Padding is zeros, which
        # would price as a free design and win the argmin outright.
        n_s, n_w = (int(x) for x in frontier.patch_points[k])
        if n_s == 0 or n_w == 0:
            continue
        s_vals = frontier.s_patch[k, :n_s].astype(np.float64)
        w_vals = frontier.w_patch[k, :n_w].astype(np.float64)
        b = frontier.b_patch[k, :n_s, :n_w].astype(np.float64)
        sf = frontier.energy_served_frac[k, :n_s, :n_w]
        # Broadcast the axes against the trailing rung axis: (n_s, 1, 1) x (1, n_w, 1) x
        # (n_s, n_w, R). Picking the rung here, rather than at build time, is what lets the
        # battery be chosen under this year's real prices instead of a frozen anchor's.
        with np.errstate(divide="ignore", invalid="ignore"):
            lcoe = (a_s * s_vals[:, None, None] + a_w * w_vals[None, :, None] + a_b * np.power(b, GAMMA)) / (d0 * sf)
        lcoe = np.where(np.isfinite(b) & (sf > 0), lcoe, np.inf)
        i, j, r = (int(x) for x in np.unravel_index(int(np.argmin(lcoe)), lcoe.shape))
        val = float(lcoe[i, j, r])
        if val < best_lcoe:
            best_lcoe = val
            best = (k, i, j, r, float(s_vals[i]), float(w_vals[j]), float(b[i, j, r]))

    if best is None:
        raise ValueError("argmin_lcoe called on a frontier with no populated patches (status != STATUS_OK)")
    k, i, j, r, s, w, b_val = best
    sf_val = float(frontier.energy_served_frac[k, i, j, r])

    return Optimum(
        lcoe=best_lcoe,
        solar=s,
        wind=w,
        battery=b_val,
        served_fraction=sf_val,
        patch_index=k,
        patch_certified=_containment_certificate(frontier, coeffs, best_lcoe),
        argmin_truncated=_argmin_truncated(frontier, k, i, j),
    )


def check_repair_budget(n_repaired: int, n_points: int, params: SearchParams) -> None:
    """
    Raise if the repair rate exceeds `params.repair_rate_cap`.

    Repair means a full on-the-fly patch sweep for a pixel whose containment certificate
    failed to fire -- expected occasionally, since the patches are seeded with frozen
    anchor costs rather than the query year's real ones. Past a couple of percent the
    query stops being "minutes per year", so this fails loudly instead of silently letting
    a run degrade into one expensive re-search per pixel.
    """
    if n_points == 0:
        return
    rate = n_repaired / n_points
    if rate > params.repair_rate_cap:
        raise RuntimeError(
            f"repair rate {rate:.1%} ({n_repaired}/{n_points} points) exceeds "
            f"repair_rate_cap {params.repair_rate_cap:.1%} -- too many patch repairs needed."
        )
