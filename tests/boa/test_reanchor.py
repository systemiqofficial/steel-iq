"""
Contracts for re-anchoring bookkeeping: which years get their own anchor, and which
anchor a query year routes to.

Deliberately narrow. Building a frontier per anchor year, storing more than one per
pixel, and wiring a query year's cost lookup to the right anchor are a cache-layout and
build-orchestration concern (BOA_BISECTION_PLAN.md's M2, "cache schema v3") -- not
covered here. This file only holds the two small, pure functions that decide the
schedule and the routing, independent of how they eventually get used.
"""

import pytest

from _gate import require

require("boa.model.bisection", "anchor_years", "nearest_anchor", "distinct_anchor_years")

from boa.model.bisection import (  # noqa: E402
    CostCoefficients,
    anchor_years,
    cost_ratio_simplex,
    distinct_anchor_years,
    nearest_anchor,
)


def test_anchor_years_spans_the_horizon_at_the_given_interval():
    assert anchor_years(2025, 2055, 10) == [2025, 2035, 2045, 2055]


def test_anchor_years_always_includes_the_horizon_end():
    """
    A 25-year horizon at a 10-year interval does not divide evenly (2025, 2035, 2045 is
    only 20 years in) -- the end year must still get its own anchor rather than being
    left 5 years short of the nearest regular one, since the re-anchoring benchmark
    found the risk from an out-of-date anchor concentrates in exactly that final stretch.
    """
    years = anchor_years(2025, 2050, 10)
    assert years[-1] == 2050
    assert years == [2025, 2035, 2045, 2050]


def test_anchor_years_handles_a_horizon_shorter_than_the_interval():
    assert anchor_years(2025, 2030, 10) == [2025, 2030]


def test_anchor_years_handles_a_single_year_horizon():
    assert anchor_years(2025, 2025, 10) == [2025]


def test_anchor_years_rejects_a_non_positive_interval():
    with pytest.raises(ValueError, match="interval"):
        anchor_years(2025, 2050, 0)


def test_anchor_years_rejects_end_before_start():
    with pytest.raises(ValueError, match="end"):
        anchor_years(2050, 2025, 10)


def test_nearest_anchor_picks_the_closest_year():
    anchors = [2025, 2035, 2045, 2050]
    assert nearest_anchor(2026, anchors) == 2025
    assert nearest_anchor(2041, anchors) == 2045
    assert nearest_anchor(2050, anchors) == 2050


def test_nearest_anchor_bounds_drift_at_half_the_interval():
    """The whole point of re-anchoring: no query year within the horizon should ever be
    more than interval / 2 years from the anchor it routes to."""
    anchors = anchor_years(2025, 2055, 10)
    worst = max(abs(year - nearest_anchor(year, anchors)) for year in range(2025, 2056))
    assert worst <= 5


def test_nearest_anchor_breaks_an_exact_tie_toward_the_earlier_anchor():
    assert nearest_anchor(2030, [2025, 2035]) == 2025


def test_nearest_anchor_rejects_an_empty_anchor_list():
    with pytest.raises(ValueError, match="anchors"):
        nearest_anchor(2030, [])


# --------------------------------------------------------------------------
# Dropping anchors that do not move the economics
# --------------------------------------------------------------------------


def _coeffs(a_s: float, a_w: float, a_b: float, d0: float = 1.0) -> CostCoefficients:
    return CostCoefficients(a_s=a_s, a_w=a_w, a_b=a_b, d0=d0)


def test_a_flat_cost_tail_collapses_to_one_anchor():
    """
    The case the real workbook produces: capex stops moving in 2050 and is constant to 2100,
    so `anchor_years(2025, 2060, 10)` proposes 2055 and 2060 that price designs exactly as
    2050 does. Two of five anchors would each cost a full build to reproduce an answer
    already held.
    """
    schedule = {2025: (1.0, 1.0, 1.0), 2035: (0.7, 0.95, 0.9), 2045: (0.55, 0.9, 0.82)}
    flat = (0.52, 0.88, 0.79)

    def coefficients_for(year: int) -> CostCoefficients:
        return _coeffs(*schedule.get(year, flat))

    assert distinct_anchor_years(anchor_years(2025, 2060, 10), coefficients_for) == [2025, 2035, 2045, 2055]


def test_a_uniform_price_change_is_not_a_new_anchor():
    """
    The point of comparing ratios rather than capex. Scaling every coefficient by a constant
    rescales every design's LCOE identically, so it cannot reorder designs and cannot move a
    seed. A guard on the raw values would keep all four of these years.
    """

    def coefficients_for(year: int) -> CostCoefficients:
        scale = 2.0 ** (year - 2025)  # prices quadruple, the mix never budges
        return _coeffs(1.0 * scale, 2.0 * scale, 3.0 * scale, d0=scale)

    assert distinct_anchor_years([2025, 2026, 2027, 2028], coefficients_for) == [2025]


def test_a_change_in_the_mix_is_a_new_anchor():
    """The converse: same total, different split, so designs genuinely reorder."""

    def coefficients_for(year: int) -> CostCoefficients:
        return _coeffs(1.0, 2.0, 3.0) if year < 2030 else _coeffs(3.0, 2.0, 1.0)

    assert distinct_anchor_years([2025, 2027, 2030, 2033], coefficients_for) == [2025, 2030]


def test_drift_is_measured_against_the_last_kept_anchor_not_the_previous_candidate():
    """
    What lets slow drift accumulate instead of being dismissed one small step at a time.
    Each step here moves the mix by well under the tolerance, but four of them together
    clear it.
    """

    def coefficients_for(year: int) -> CostCoefficients:
        return _coeffs(1.0 - 0.01 * (year - 2025), 1.0, 1.0)

    years = list(range(2025, 2041))
    # Each single year moves the mix by ~0.0023, well under the tolerance; five of them
    # together clear it. Comparing against the previous candidate would keep only 2025.
    assert distinct_anchor_years(years, coefficients_for, tol=0.01) == [2025, 2030, 2035, 2040]


def test_a_larger_tolerance_places_anchors_at_equal_steps_of_drift():
    """
    The same function doing the other job: over a full range of years, the tolerance becomes
    the spacing rule. The real trajectory is front-loaded, so equal steps of drift are not
    equal steps of time -- the early years get anchors closer together.
    """

    def coefficients_for(year: int) -> CostCoefficients:
        # Solar capex decaying fast then levelling, the shape the workbook actually has.
        solar = 0.5 + 0.5 * 0.85 ** (year - 2025)
        return _coeffs(solar, 1.0, 1.0)

    years = list(range(2025, 2061))
    anchors = distinct_anchor_years(years, coefficients_for, tol=0.02)
    gaps = [b - a for a, b in zip(anchors, anchors[1:])]
    assert anchors[0] == 2025
    assert gaps == sorted(gaps), f"gaps should widen as drift slows, got {gaps}"


def test_the_first_year_is_always_kept():
    assert distinct_anchor_years([2040], lambda y: _coeffs(1.0, 1.0, 1.0)) == [2040]
    assert distinct_anchor_years([], lambda y: _coeffs(1.0, 1.0, 1.0)) == []


def test_ratios_drop_the_scale_and_the_denominator():
    """`d0` rescales but never reorders, so it must not reach the comparison at all."""
    assert cost_ratio_simplex(_coeffs(1.0, 2.0, 3.0, d0=1.0)) == pytest.approx(
        cost_ratio_simplex(_coeffs(10.0, 20.0, 30.0, d0=999.0))
    )
    assert sum(cost_ratio_simplex(_coeffs(1.0, 2.0, 3.0))) == pytest.approx(1.0)


def test_degenerate_coefficients_raise():
    with pytest.raises(ValueError, match="positive"):
        cost_ratio_simplex(_coeffs(0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="tol"):
        distinct_anchor_years([2025], lambda y: _coeffs(1.0, 1.0, 1.0), tol=-1.0)
