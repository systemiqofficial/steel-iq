"""
Contracts for the stored battery rungs.

The rungs exist because our objective divides by served fraction, where GBS uses a
constant denominator and can stop at `b_min`: the cheapest battery can sit above the
smallest feasible one.

The build stores the candidates and the query chooses among them. That split is what
keeps the cache pure dispatch: choosing needs prices, and a battery chosen at build time
would carry the build's frozen costs into a value every later year has to replay. See
BOA_BISECTION_PLAN.md, "battery rungs", for why the two designs that preceded this one --
a certified ladder, then a build-time local search -- were each dropped.
"""

import numpy as np
import pytest

from _gate import require

require("boa.model.bisection", "SearchParams", "b_min_at", "battery_rungs", "rung_spans", "dispatch_metrics")

from boa.model.bisection import (  # noqa: E402
    GAMMA,
    CostCoefficients,
    SearchParams,
    b_min_at,
    battery_rungs,
    dispatch_metrics,
    rung_spans,
)

PARAMS = SearchParams()
COEFFS = CostCoefficients(a_s=1.0e6, a_w=1.6e6, a_b=0.30e6, d0=8.76e6)


def _lcoe(s: float, w: float, b: float, sf: float) -> float:
    return (COEFFS.a_s * s + COEFFS.a_w * w + COEFFS.a_b * b**GAMMA) / (COEFFS.d0 * sf)


def test_rungs_take_no_cost_argument():
    """
    Structural, and the reason this module was rewritten: if `battery_rungs` could see
    costs it could bake an anchor's economics into a cached value, and the cache would
    stop being replayable across years. Assert the signature, not just the behaviour.
    """
    import inspect

    params = set(inspect.signature(battery_rungs).parameters)
    assert not params & {"coeffs", "anchor", "cost", "costs"}


def test_rung_zero_is_b_min_and_costs_no_dispatch(profiles):
    """`(cov0, sf0)` are `b_min`'s own metrics -- the bisection's last probe -- so rung 0
    must reproduce them exactly rather than re-dispatching."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    assert np.isfinite(b_min) and b_min > 0

    b, cov, sf = battery_rungs(solar, wind, 3.0, 2.0, b_min, cov0, sf0, PARAMS)
    assert b[0] == pytest.approx(b_min, abs=1e-12)
    assert cov[0] == pytest.approx(cov0, abs=1e-12)
    assert sf[0] == pytest.approx(sf0, abs=1e-12)


def test_rungs_are_non_decreasing_and_stay_within_the_span(profiles):
    """Bounded and ordered: no rung below `b_min`, none above `ladder_max_span * b_min`."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    b, _, _ = battery_rungs(solar, wind, 3.0, 2.0, b_min, cov0, sf0, PARAMS)

    assert np.all(np.diff(b) >= -1e-12)
    assert b[0] >= b_min - 1e-12
    assert b[-1] <= b_min * PARAMS.ladder_max_span * (1 + 1e-9)


def test_every_rung_replays_to_an_independent_dispatch(profiles):
    """Stored metrics are replayed, not trusted: dispatching again at each rung's own
    battery must reproduce that rung's coverage and served fraction exactly."""
    solar, wind = profiles["solar"], profiles["wind"]
    b_min, cov0, sf0 = b_min_at(solar, wind, 4.0, 3.0, 15, PARAMS)
    b, cov, sf = battery_rungs(solar, wind, 4.0, 3.0, b_min, cov0, sf0, PARAMS)

    for r in range(len(b)):
        cov_check, sf_check = dispatch_metrics(solar, wind, 4.0, 3.0, float(b[r]))
        assert cov[r] == pytest.approx(cov_check, abs=1e-12)
        assert sf[r] == pytest.approx(sf_check, abs=1e-12)


def test_every_rung_is_feasible(profiles):
    """
    Feasibility by construction is what lets `argmin_lcoe` skip the coverage check
    entirely: coverage is non-decreasing in battery size and rung 0 is `b_min`, so no
    stored rung can miss the target.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    target = 1.0 - 15 / 100.0
    b_min, cov0, sf0 = b_min_at(solar, wind, 3.0, 2.0, 15, PARAMS)
    _, cov, _ = battery_rungs(solar, wind, 3.0, 2.0, b_min, cov0, sf0, PARAMS)
    assert np.all(cov >= target - 1e-9)


def test_a_higher_rung_can_beat_b_min_on_lcoe(profiles):
    """
    The headline mechanism must be reachable somewhere, or storing rungs is pointless:
    at some `(s, w)` a rung above `b_min` should price below `b_min` itself. If this
    never fired, S3's benchmark would have found `b_true == b_min` everywhere, and it
    did not.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    beaten = False
    for s, w in [(2.0, 1.5), (3.0, 2.0), (4.0, 3.0), (2.5, 3.5), (5.0, 1.0)]:
        b_min, cov0, sf0 = b_min_at(solar, wind, s, w, 15, PARAMS)
        if not np.isfinite(b_min) or b_min <= 0.0:
            continue
        b, _, sf = battery_rungs(solar, wind, s, w, b_min, cov0, sf0, PARAMS)
        base = _lcoe(s, w, float(b[0]), float(sf[0]))
        if any(_lcoe(s, w, float(b[r]), float(sf[r])) < base - 1e-9 for r in range(1, len(b))):
            beaten = True
            break
    assert beaten, "no rung above b_min ever beat it; check the rungs are wired up"


def test_a_zero_b_min_node_collapses_every_rung_to_zero(profiles):
    """
    A design generous enough to meet coverage with no battery gets no special treatment:
    a multiple of zero is zero, so every rung sits at `b_min == 0` with its metrics, and
    the query simply sees one distinct candidate rather than a spurious spread.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    s = w = 30.0  # massively oversized: coverage met outright
    b_min, cov0, sf0 = b_min_at(solar, wind, s, w, 15, PARAMS)
    assert b_min == pytest.approx(0.0), "fixture precondition: no battery needed"

    b, cov, sf = battery_rungs(solar, wind, s, w, b_min, cov0, sf0, PARAMS)
    assert np.allclose(b, 0.0)
    assert np.allclose(cov, cov0)
    assert np.allclose(sf, sf0)


def test_rung_spans_start_at_one_and_end_at_the_configured_span():
    """The spans are multiples of `b_min`, so the first must be exactly 1.0 -- rung 0 is
    `b_min` itself, and anything else would silently move it."""
    spans = rung_spans(PARAMS)
    assert len(spans) == PARAMS.ladder_rungs
    assert spans[0] == pytest.approx(1.0)
    assert spans[-1] == pytest.approx(PARAMS.ladder_max_span)


def test_rungs_cluster_just_above_b_min():
    """
    The spacing is the design, not the count. LCOE(b) has a shallow trough close to
    `b_min`, so evenly spaced rungs step over it and adding more at the top only lands them
    past the crossing where the objective is already rising. The first step must therefore
    be a small fraction of the span, not `1/(R-1)` of it.
    """
    spans = rung_spans(PARAMS)
    first_step = spans[1] - 1.0
    even_step = (PARAMS.ladder_max_span - 1.0) / (PARAMS.ladder_rungs - 1)
    assert first_step < even_step / 2, "rungs are not clustered near b_min"
    assert np.all(np.diff(np.diff(spans)) > 0), "gaps must widen with distance from b_min"


def test_a_single_rung_degenerates_to_b_min_only():
    """`ladder_rungs = 1` must mean "store b_min alone", not "store the top of the span"."""
    spans = rung_spans(SearchParams(ladder_rungs=1))
    assert len(spans) == 1 and spans[0] == pytest.approx(1.0)


def test_zero_rungs_is_rejected():
    """A node with no stored battery is not a degenerate cache, it is an empty one."""
    with pytest.raises(ValueError, match="ladder_rungs"):
        rung_spans(SearchParams(ladder_rungs=0))
