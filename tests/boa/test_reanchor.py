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

require("boa.model.bisection", "anchor_years", "nearest_anchor")

from boa.model.bisection import anchor_years, nearest_anchor  # noqa: E402


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
