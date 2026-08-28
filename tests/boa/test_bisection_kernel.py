"""
Contracts for the numba kernels underneath the grid-bisection search.

These are the load-bearing tests of the whole rewrite. The search collapses a
3-D optimisation to a 2-D grid with a 1-D bisection precisely because coverage
is monotone in every build dimension; if `test_coverage_and_served_fraction_are_monotone_in_solar_wind_battery`
ever fails, the bisection is not merely imprecise, it is unsound.

`dispatch_metrics` replaces three separate production functions with one fused
pass, so its parity test pins it against all three originals rather than against
a hardcoded expectation.

Ordering convention: every function that returns both metrics returns them as
`(..., coverage, served_fraction)` -- coverage first, because it is the hard
constraint, and served fraction second, because it is the objective's denominator.
"""

import numpy as np
import pytest

pytest.importorskip("boa.model.bisection")

from boa.model.bisection import (  # noqa: E402
    SearchParams,
    b_min_at,
    dispatch_metrics,
)
from boa.model.logic import (  # noqa: E402
    calculate_coverage,
    calculate_served_fraction,
    state_of_charge,
)

PARAMS = SearchParams()

# Design points that clear annual energy balance for the shared fixtures
# (CF solar ~0.174, wind ~0.300) and so have a finite b_min. Below balance no battery
# can ever meet an hourly target, and a test that skipped such points would pass
# without asserting anything.
FEASIBLE_POINTS = [(3.0, 3.0), (4.0, 3.0), (3.0, 2.0)]


def _reference_metrics(solar, wind, s, w, b):
    """Coverage and served fraction the slow, unfused, already-trusted way."""
    net_energy = s * solar + w * wind - 1.0
    soc = state_of_charge(net_energy, b)
    return calculate_coverage(soc, net_energy), calculate_served_fraction(soc, net_energy)


def _random_designs(n=200, seed=0):
    rng = np.random.RandomState(seed)
    return np.column_stack(
        [
            rng.uniform(0.0, 8.0, n),  # solar overscale
            rng.uniform(0.0, 8.0, n),  # wind overscale
            rng.uniform(0.0, 40.0, n),  # battery, baseload-hours
        ]
    )


def test_dispatch_metrics_matches_reference_soc_coverage_and_served_fraction(profiles):
    """
    The fused kernel must reproduce `state_of_charge` + `calculate_coverage` +
    `calculate_served_fraction` exactly, including their hour-0 conventions.

    This is why those three functions survive the sampler deletion: they are the
    readable spec that the fast path is checked against.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    for s, w, b in _random_designs():
        cov, sf = dispatch_metrics(solar, wind, s, w, b)
        ref_cov, ref_sf = _reference_metrics(solar, wind, s, w, b)
        assert cov == pytest.approx(ref_cov, abs=1e-12)
        assert sf == pytest.approx(ref_sf, abs=1e-12)


def test_dispatch_metrics_reproduces_the_hour_zero_convention(profiles):
    """
    Hour 0 is tested against an empty battery (`net_energy[0] >= 0`) regardless of
    battery size, because `state_of_charge` starts from `soc[-1] == 0`. A design
    whose first hour is a deficit must therefore lose that hour no matter how big
    the battery is.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    assert solar[0] * 1.0 + wind[0] * 0.0 - 1.0 < 0, "fixture precondition: hour 0 is a deficit"

    small_cov, _ = dispatch_metrics(solar, wind, 1.0, 0.0, 1.0)
    huge_cov, _ = dispatch_metrics(solar, wind, 1.0, 0.0, 10_000.0)
    hours = len(solar)
    # Both lose hour 0; the huge battery may win later hours, never hour 0.
    assert small_cov <= huge_cov <= 1.0 - 1.0 / hours


def test_served_fraction_is_never_below_coverage(profiles):
    """
    The invariant `calculate_served_fraction` documents at logic.py:346-350: with no
    round-trip loss the battery can only add delivered energy, so partial credit can
    never score below binary credit.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    for s, w, b in _random_designs(seed=3):
        cov, sf = dispatch_metrics(solar, wind, s, w, b)
        assert sf >= cov - 1e-12


@pytest.mark.parametrize("axis", ["solar", "wind", "battery"])
def test_coverage_and_served_fraction_are_monotone_in_solar_wind_battery(profiles, axis):
    """
    Up-closedness of the feasible set, in all three directions.

    This is the bisection's correctness precondition: because coverage is
    non-decreasing in battery size at fixed (solar, wind), the smallest feasible
    battery is well-defined and findable by bisection. It is also what makes the
    patch-containment certificate valid, and it is the same property the deleted
    `corner_design_feasible` relied on (`test_sampler.py:96`).
    """
    solar, wind = profiles["solar"], profiles["wind"]
    rng = np.random.RandomState(7)
    for _ in range(60):
        base = [rng.uniform(0.2, 6.0), rng.uniform(0.2, 6.0), rng.uniform(0.5, 25.0)]
        bumped = list(base)
        bumped[{"solar": 0, "wind": 1, "battery": 2}[axis]] *= 1.5

        cov_lo, sf_lo = dispatch_metrics(solar, wind, *base)
        cov_hi, sf_hi = dispatch_metrics(solar, wind, *bumped)
        assert cov_hi >= cov_lo - 1e-12, f"coverage fell when {axis} increased"
        assert sf_hi >= sf_lo - 1e-12, f"served fraction fell when {axis} increased"


def test_b_min_is_the_coverage_jump_point(profiles):
    """
    `b_min` is the smallest battery meeting the coverage target: just below it the
    design is infeasible, at it the design is feasible. Checked against the target
    directly rather than against a brute-force scan, so the assertion is exact.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    p = 15
    target = 1.0 - p / 100.0
    tested = 0
    for s, w in FEASIBLE_POINTS:
        b_min, _, _ = b_min_at(solar, wind, s, w, p, PARAMS)
        assert np.isfinite(b_min), f"({s}, {w}) must be feasible for this test to mean anything"
        tested += 1
        cov_at, _ = dispatch_metrics(solar, wind, s, w, b_min)
        assert cov_at >= target, "b_min must itself be feasible"

        below = b_min * (1.0 - 10 * PARAMS.tol_rel_patch) - 1e-9
        if below > 0:
            cov_below, _ = dispatch_metrics(solar, wind, s, w, below)
            assert cov_below < target, "a battery below b_min must be infeasible"
    assert tested == len(FEASIBLE_POINTS)


def test_b_min_errs_high_never_low(profiles):
    """
    The bisection returns the feasible bracket end, so any error is on the safe
    side. A `b_min` that came back below the true minimum would silently emit
    designs that miss the coverage target.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    target = 1.0 - 15 / 100.0
    for s, w in FEASIBLE_POINTS:
        b_min, _, _ = b_min_at(solar, wind, s, w, 15, PARAMS)
        assert np.isfinite(b_min), f"({s}, {w}) must be feasible for this test to mean anything"
        cov, _ = dispatch_metrics(solar, wind, s, w, b_min)
        assert cov >= target


@pytest.mark.parametrize("hint", [-1.0, 0.001, 0.5, 3.0, 50.0, 400.0])
def test_b_min_warm_start_is_result_invariant(profiles, hint):
    """
    Warm-starting changes how many probes the bracket search costs, never the
    answer. Both hint branches must still establish a genuine infeasible/feasible
    bracket before bisecting, so every hint converges to the same value within
    tolerance -- including hints that are absurdly high or low.

    The three tolerances differ for a reason worth stating. `b_min` is only pinned to
    `tol_rel_patch`, since bisection stops there. Coverage is a *step* function of
    battery size, so every hint lands on the same plateau and the values are bit-equal.
    Served fraction is smooth in battery size, so it inherits `b_min`'s uncertainty --
    asserting it more tightly than `b_min` itself is solved would be incoherent.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    cold, cold_cov, cold_sf = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS, hint=-1.0)
    warm, warm_cov, warm_sf = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS, hint=hint)

    assert warm == pytest.approx(cold, rel=2 * PARAMS.tol_rel_patch)
    assert warm_cov == cold_cov, "coverage is a step function; every bracket lands on one plateau"
    assert warm_sf == pytest.approx(cold_sf, rel=2 * PARAMS.tol_rel_patch)


def test_b_min_returns_inf_when_no_battery_meets_coverage(dead_profiles):
    """
    A pixel with no generation can never meet a positive coverage target, however
    large the battery -- there is nothing to charge it with. The kernel reports
    infeasibility rather than returning `b_cap`, so callers can tell "needs a huge
    battery" from "impossible".
    """
    solar, wind = dead_profiles["solar"], dead_profiles["wind"]
    b_min, _, _ = b_min_at(solar, wind, 5.0, 5.0, 15, PARAMS)
    assert not np.isfinite(b_min)


def test_served_fraction_at_b_min_comes_from_the_final_feasible_probe(profiles):
    """
    The bisection's last feasible probe already sits at `b_min`, so its metrics are
    returned rather than recomputed. This is what makes ladder rung 0 free, and it
    must agree with an independent dispatch at the same battery size.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov, sf = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    assert np.isfinite(b_min)

    cov_check, sf_check = dispatch_metrics(solar, wind, 3.0, 2.0, b_min)
    assert cov == pytest.approx(cov_check, abs=1e-12)
    assert sf == pytest.approx(sf_check, abs=1e-12)
