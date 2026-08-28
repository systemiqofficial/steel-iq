"""
Contracts for the battery ladder.

Split from `test_bisection_kernel` because the ladder is gated differently. Every
other kernel is a port of code the benchmark branch already validated against a
certified LP; the ladder is not. It exists only because our objective divides by
served fraction, where GBS uses a constant denominator and can stop at `b_min`.

So its parameters -- how many rungs, how far to climb -- are a guess until measured.
The plan benchmarks `LCOE(b)` densely at the optimal `(s, w)` before this is wired
into the pipeline, and these tests are what that benchmark settles.
"""

import numpy as np
import pytest

from _gate import require

require("boa.model.bisection", "SearchParams", "b_min_at", "battery_ladder", "dispatch_metrics")

from boa.model.bisection import (  # noqa: E402
    SearchParams,
    b_min_at,
    battery_ladder,
    dispatch_metrics,
)

PARAMS = SearchParams()


def test_ladder_is_geometric_monotone_and_saturation_terminated(profiles):
    """
    The ladder walks battery sizes upward from `b_min`, because dividing LCOE by
    served fraction means the optimum can sit above the smallest feasible battery.

    Three contracts: rung 0 is exactly `b_min`; sizes and served fractions are
    non-decreasing; and once served fraction stops improving the remaining rungs
    are filled by duplication rather than by more dispatch work.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    assert np.isfinite(b_min) and b_min > 0

    b, cov, sf, sf_inf = battery_ladder(solar, wind, 3.0, 2.0, b_min, cov0, sf0, PARAMS)

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
    b_min, cov0, sf0 = b_min_at(solar, wind, 4.0, 3.0, 15, PARAMS)
    b, cov, sf, _ = battery_ladder(solar, wind, 4.0, 3.0, b_min, cov0, sf0, PARAMS)

    for r in range(PARAMS.ladder_rungs):
        cov_check, sf_check = dispatch_metrics(solar, wind, 4.0, 3.0, b[r])
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
