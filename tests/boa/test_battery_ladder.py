"""
Contracts for the battery ladder.

Split from `test_bisection_kernel` because the ladder was gated differently while its
parameters were unsettled. It exists only because our objective divides by served
fraction, where GBS uses a constant denominator and can stop at `b_min`.

Superseded a design with a fixed 5-rung geometric ladder (to 2.5x b_min) plus a
certificate proving nothing further out could win. The S3 benchmark (1000 global sites)
found that certificate needed a much longer, costlier climb purely to prove a negative,
for a benefit measured at under 1% of LCOE either way -- so both were dropped in favour
of this short, cheap local search that trusts (and was checked against real profiles to
confirm) LCOE(b) is single-troughed near b_min, rather than proving it. See
BOA_BISECTION_PLAN.md, "battery ladder redesign".
"""

import numpy as np
import pytest

from _gate import require

require("boa.model.bisection", "SearchParams", "CostCoefficients", "b_min_at", "battery_ladder", "dispatch_metrics")

from boa.model.bisection import (  # noqa: E402
    GAMMA,
    CostCoefficients,
    SearchParams,
    b_min_at,
    battery_ladder,
    dispatch_metrics,
)

PARAMS = SearchParams()
COEFFS = CostCoefficients(a_s=1.0e6, a_w=1.6e6, a_b=0.30e6, d0=8.76e6)


def _lcoe(s: float, w: float, b: float, sf: float) -> float:
    return (COEFFS.a_s * s + COEFFS.a_w * w + COEFFS.a_b * b**GAMMA) / (COEFFS.d0 * sf)


def test_first_step_is_free_and_matches_b_min(profiles):
    """`(cov0, sf0)` are `b_min`'s own metrics -- the bisection's last probe -- so the
    zero-step candidate must be reproduced exactly, not re-dispatched."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    assert np.isfinite(b_min) and b_min > 0

    b, cov, sf = battery_ladder(solar, wind, 3.0, 2.0, b_min, cov0, sf0, COEFFS, PARAMS)
    cov_check, sf_check = dispatch_metrics(solar, wind, 3.0, 2.0, b)
    assert cov == pytest.approx(cov_check, abs=1e-12)
    assert sf == pytest.approx(sf_check, abs=1e-12)


def test_ladder_never_reports_a_battery_below_b_min(profiles):
    """The climb only ever moves upward from b_min; it must never return less."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    b, _, _ = battery_ladder(solar, wind, 3.0, 2.0, b_min, cov0, sf0, COEFFS, PARAMS)
    assert b >= b_min - 1e-12


def test_ladder_never_climbs_past_the_configured_span(profiles):
    """Bounded search: never reports a battery beyond `ladder_max_span * b_min`."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    b, _, _ = battery_ladder(solar, wind, 3.0, 2.0, b_min, cov0, sf0, COEFFS, PARAMS)
    assert b <= b_min * PARAMS.ladder_max_span * (1 + 1e-9)


def test_ladder_never_reports_a_worse_lcoe_than_b_min(profiles):
    """The whole point: the reported step's LCOE must be at least as good as b_min's own,
    since b_min itself is always a candidate (the zero-step case)."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    b, cov, sf = battery_ladder(solar, wind, 3.0, 2.0, b_min, cov0, sf0, COEFFS, PARAMS)
    assert _lcoe(3.0, 2.0, b, sf) <= _lcoe(3.0, 2.0, b_min, sf0) + 1e-9


def test_ladder_result_agrees_with_an_independent_dispatch(profiles):
    """The reported (b, cov, sf) triple must be reproducible -- it is replayed, not
    trusted -- so an independent dispatch at the same b must match exactly."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 4.0, 3.0, 15, PARAMS)
    b, cov, sf = battery_ladder(solar, wind, 4.0, 3.0, b_min, cov0, sf0, COEFFS, PARAMS)

    cov_check, sf_check = dispatch_metrics(solar, wind, 4.0, 3.0, b)
    assert cov == pytest.approx(cov_check, abs=1e-12)
    assert sf == pytest.approx(sf_check, abs=1e-12)


def test_ladder_can_climb_above_b_min_when_it_helps(profiles):
    """
    The headline mechanism must be reachable somewhere, or the whole exercise is
    pointless: at least one (s, w) point on this pixel should see the ladder pick a
    battery above b_min. If this never fired anywhere, S3's benchmark would have found
    b_true == b_min everywhere, which it did not.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    climbed = False
    for s, w in [(2.0, 1.5), (3.0, 2.0), (4.0, 3.0), (2.5, 3.5), (5.0, 1.0)]:
        b_min, cov0, sf0 = b_min_at(solar, wind, s, w, 15, PARAMS)
        if not np.isfinite(b_min) or b_min <= 0.0:
            continue
        b, _, _ = battery_ladder(solar, wind, s, w, b_min, cov0, sf0, COEFFS, PARAMS)
        if b > b_min * (1.0 + 1e-9):
            climbed = True
            break
    assert climbed, "the ladder never climbed above b_min at any sampled point; check it is wired up"


def test_ladder_handles_a_zero_b_min_pixel(profiles):
    """
    A design generous enough to meet coverage with no battery still gets no special
    treatment here: a step of `ladder_step_pct` of zero is zero, so the ladder is a
    no-op and simply returns b_min == 0 unchanged (unlike the abandoned design, which
    seeded a zero-b_min pixel additively away from zero).
    """
    solar, wind = profiles["solar"], profiles["wind"]
    s = w = 30.0  # massively oversized: coverage met outright
    b_min, cov0, sf0 = b_min_at(solar, wind, s, w, 15, PARAMS)
    assert b_min == pytest.approx(0.0), "fixture precondition: no battery needed"

    b, cov, sf = battery_ladder(solar, wind, s, w, b_min, cov0, sf0, COEFFS, PARAMS)
    assert b == pytest.approx(0.0)
    assert cov == pytest.approx(cov0)
    assert sf == pytest.approx(sf0)
