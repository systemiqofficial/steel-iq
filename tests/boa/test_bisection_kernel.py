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
    battery_ladder,
    dispatch_metrics,
)
from boa.model.logic import (  # noqa: E402
    calculate_coverage,
    calculate_served_fraction,
    state_of_charge,
)

PARAMS = SearchParams()


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
    for s, w in [(2.0, 1.0), (1.0, 2.0), (3.0, 3.0), (0.5, 4.0)]:
        b_min, _, _ = b_min_at(solar, wind, s, w, p, PARAMS)
        if not np.isfinite(b_min):
            continue
        cov_at, _ = dispatch_metrics(solar, wind, s, w, b_min)
        assert cov_at >= target, "b_min must itself be feasible"

        below = b_min * (1.0 - 10 * PARAMS.tol_rel_patch) - 1e-9
        if below > 0:
            cov_below, _ = dispatch_metrics(solar, wind, s, w, below)
            assert cov_below < target, "a battery below b_min must be infeasible"


def test_b_min_errs_high_never_low(profiles):
    """
    The bisection returns the feasible bracket end, so any error is on the safe
    side. A `b_min` that came back below the true minimum would silently emit
    designs that miss the coverage target.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    target = 1.0 - 15 / 100.0
    for s, w in [(1.5, 1.5), (4.0, 0.5), (0.5, 3.0)]:
        b_min, _, _ = b_min_at(solar, wind, s, w, 15, PARAMS)
        if np.isfinite(b_min):
            cov, _ = dispatch_metrics(solar, wind, s, w, b_min)
            assert cov >= target


@pytest.mark.parametrize("hint", [-1.0, 0.001, 0.5, 3.0, 50.0, 400.0])
def test_b_min_warm_start_is_result_invariant(profiles, hint):
    """
    Warm-starting changes how many probes the bracket search costs, never the
    answer. Both hint branches must still establish a genuine infeasible/feasible
    bracket before bisecting, so every hint converges to the same value within
    tolerance -- including hints that are absurdly high or low.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    cold, cold_cov, cold_sf = b_min_at(solar, wind, 2.0, 2.0, 15, PARAMS, hint=-1.0)
    warm, warm_cov, warm_sf = b_min_at(solar, wind, 2.0, 2.0, 15, PARAMS, hint=hint)

    assert warm == pytest.approx(cold, rel=2 * PARAMS.tol_rel_patch)
    assert warm_cov == pytest.approx(cold_cov, rel=1e-6)
    assert warm_sf == pytest.approx(cold_sf, rel=1e-6)


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
    b_min, cov, sf = b_min_at(solar, wind, 2.5, 1.5, 15, PARAMS)
    assert np.isfinite(b_min)

    cov_check, sf_check = dispatch_metrics(solar, wind, 2.5, 1.5, b_min)
    assert cov == pytest.approx(cov_check, abs=1e-12)
    assert sf == pytest.approx(sf_check, abs=1e-12)


def test_ladder_is_geometric_monotone_and_saturation_terminated(profiles):
    """
    The ladder walks battery sizes upward from `b_min`, because dividing LCOE by
    served fraction means the optimum can sit above the smallest feasible battery.

    Three contracts: rung 0 is exactly `b_min`; sizes and served fractions are
    non-decreasing; and once served fraction stops improving the remaining rungs
    are filled by duplication rather than by more dispatch work.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 2.0, 2.0, 15, PARAMS)
    assert np.isfinite(b_min) and b_min > 0

    b, cov, sf, sf_inf = battery_ladder(solar, wind, 2.0, 2.0, b_min, cov0, sf0, PARAMS)

    assert b.shape == sf.shape == cov.shape == (PARAMS.ladder_rungs,)
    assert b[0] == pytest.approx(b_min)
    assert sf[0] == pytest.approx(sf0)
    assert np.all(np.diff(b) >= -1e-12), "ladder battery sizes must not decrease"
    assert np.all(np.diff(sf) >= -1e-12), "served fraction must not decrease up the ladder"
    assert b[-1] <= b_min * PARAMS.ladder_span * (1 + 1e-9)

    # sf_inf bounds the ladder: it is the served fraction an unbounded battery buys.
    assert sf_inf >= sf[-1] - 1e-12
    assert sf_inf <= 1.0 + 1e-12

    # Duplicated tail is the saturation signature: equal sizes imply equal metrics.
    for r in range(1, PARAMS.ladder_rungs):
        if b[r] == pytest.approx(b[r - 1]):
            assert sf[r] == pytest.approx(sf[r - 1])
            assert cov[r] == pytest.approx(cov[r - 1])


def test_ladder_rungs_agree_with_an_independent_dispatch(profiles):
    """Every stored rung must be reproducible -- the cache is replayed, not trusted."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 1.0, 15, PARAMS)
    b, cov, sf, _ = battery_ladder(solar, wind, 3.0, 1.0, b_min, cov0, sf0, PARAMS)

    for r in range(PARAMS.ladder_rungs):
        cov_check, sf_check = dispatch_metrics(solar, wind, 3.0, 1.0, b[r])
        assert cov[r] == pytest.approx(cov_check, abs=1e-12)
        assert sf[r] == pytest.approx(sf_check, abs=1e-12)


def test_ladder_handles_a_zero_b_min_pixel(profiles):
    """
    A design generous enough to meet coverage with no battery still benefits from
    one, because a battery raises served fraction and so lowers LCOE. A geometric
    ladder from zero would never leave zero, so the ladder seeds additively instead.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    s = w = 30.0  # massively oversized: coverage met outright
    b_min, cov0, sf0 = b_min_at(solar, wind, s, w, 15, PARAMS)
    assert b_min == pytest.approx(0.0), "fixture precondition: no battery needed"

    b, _, sf, _ = battery_ladder(solar, wind, s, w, b_min, cov0, sf0, PARAMS)
    assert b[0] == pytest.approx(0.0)
    assert b[-1] > 0.0, "ladder must escape zero"
    assert np.all(np.diff(b) >= -1e-12)
    assert sf[-1] >= sf[0] - 1e-12
