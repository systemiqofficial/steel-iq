"""
Contracts for the per-pixel build phase: search box, coarse sweep, seed
selection, patch placement, and the resulting status codes.

The recurring theme is *which direction an error is allowed to point*. The coarse
grid stores lower bounds on `b_min`, and the query-phase containment certificate is
only valid because of that. Several tests here
exist purely to pin the direction down, because an off-by-one in a rounding mode
or a bound would not fail loudly anywhere else.
"""

import numpy as np
import pytest

from _gate import require

require(
    "boa.model.bisection",
    "SearchParams",
    "b_min_at",
    "build_pixel_frontier",
    "coarse_b_min_grid",
    "patch_box",
    "search_box",
    "select_seeds",
)

from boa.config.settings import OVERSCALE_SAMPLING_K  # noqa: E402
from boa.model.bisection import (  # noqa: E402
    STATUS_NO_OPTIMUM,
    STATUS_OK,
    STATUS_ZERO_POTENTIAL,
    SearchParams,
    b_min_at,
    build_pixel_frontier,
    coarse_b_min_grid,
    patch_box,
    search_box,
    select_seeds,
)

PARAMS = SearchParams()


# --------------------------------------------------------------------------
# Search box
# --------------------------------------------------------------------------


def test_box_scales_with_capacity_factor(profiles, poor_profiles):
    """
    The box is sized from `overscale_mus_from_cf`, so a worse resource gets a
    proportionally wider box. A fixed box would either truncate bad pixels or waste
    most of its resolution on good ones.
    """
    good_s, good_w = search_box(profiles["solar"], profiles["wind"], PARAMS)
    poor_s, poor_w = search_box(poor_profiles["solar"], poor_profiles["wind"], PARAMS)

    assert poor_s > good_s
    assert poor_w > good_w

    # The box tracks k/CF, the same scaling the deleted sampler used for its proposal.
    cf_solar = profiles["solar"].mean()
    expected = PARAMS.box_multiple * OVERSCALE_SAMPLING_K["solar"] / cf_solar
    assert good_s == pytest.approx(np.clip(expected, PARAMS.box_min, PARAMS.box_abs_max))


def test_box_is_clamped_for_a_zero_cf_technology(solar_only_profiles):
    """
    `overscale_mus_from_cf` guards its division with 1e-9, which turns a zero-CF
    technology into a mu of ~7.5e8. Left unclamped that would produce a search box
    no grid could resolve, so the box clamps at `box_abs_max`.
    """
    s_max, w_max = search_box(solar_only_profiles["solar"], solar_only_profiles["wind"], PARAMS)
    assert w_max == pytest.approx(PARAMS.box_abs_max)
    assert s_max < PARAMS.box_abs_max


def test_box_respects_the_lower_clamp(profiles):
    """An excellent resource must still get a box wide enough to hold the optimum."""
    s_max, w_max = search_box(profiles["solar"], profiles["wind"], PARAMS)
    assert s_max >= PARAMS.box_min
    assert w_max >= PARAMS.box_min


# --------------------------------------------------------------------------
# Coarse sweep
# --------------------------------------------------------------------------


def test_sublattice_fill_is_a_lower_bound_on_exact_b_min(profiles):
    """
    The coarse sweep computes `b_min` exactly on a sparse sub-lattice and fills the
    rest from the nearest dominating node, exploiting that `b_min` is non-increasing
    in both solar and wind. Every filled value must therefore be at or below the
    exact answer -- the patch-containment certificate treats it as a lower bound.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    s_max, w_max = search_box(solar, wind, PARAMS)
    s_vals = np.linspace(0.0, s_max, PARAMS.coarse_grid)
    w_vals = np.linspace(0.0, w_max, PARAMS.coarse_grid)

    grid = coarse_b_min_grid(solar, wind, s_vals, w_vals, 0.85, PARAMS)
    assert grid.shape == (PARAMS.coarse_grid, PARAMS.coarse_grid)

    # Spot-check against exact bisections, including nodes the sub-lattice skipped.
    for i in range(0, PARAMS.coarse_grid, 4):
        for j in range(1, PARAMS.coarse_grid, 5):
            exact, _, _ = b_min_at(solar, wind, s_vals[i], w_vals[j], 0.85, PARAMS)
            if np.isfinite(exact) and np.isfinite(grid[i, j]):
                assert grid[i, j] <= exact + 1e-9, f"coarse value exceeded exact b_min at ({i},{j})"


def test_coarse_grid_is_monotone_non_increasing(profiles):
    """
    The stored lower bounds must preserve the monotonicity the fill relies on.

    Compared as shifted slices rather than with `np.diff`, because infeasible cells carry
    `inf` and `inf - inf` is `nan`, which fails every comparison. Two adjacent infeasible
    cells are perfectly monotone; only a difference-based test would say otherwise.
    """
    solar, wind = profiles["solar"], profiles["wind"]
    s_max, w_max = search_box(solar, wind, PARAMS)
    grid = coarse_b_min_grid(
        solar,
        wind,
        np.linspace(0.0, s_max, PARAMS.coarse_grid),
        np.linspace(0.0, w_max, PARAMS.coarse_grid),
        0.85,
        PARAMS,
    )
    assert np.all(grid[:-1, :] >= grid[1:, :] - 1e-9), "b_min rose with solar"
    assert np.all(grid[:, :-1] >= grid[:, 1:] - 1e-9), "b_min rose with wind"


def test_coarse_b_min_is_a_lower_bound_after_float16_rounding(profiles, anchor_costs):
    """
    float16 spacing is ~0.5 near b=500, so round-to-nearest could round a stored
    bound *up* and silently invalidate the containment certificate. The cast must
    round toward zero.
    """
    frontier = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    assert frontier.b_coarse.dtype == np.float16

    exact = coarse_b_min_grid(
        profiles["solar"],
        profiles["wind"],
        frontier.s_coarse.astype(np.float64),
        frontier.w_coarse.astype(np.float64),
        0.85,
        PARAMS,
    )
    stored = frontier.b_coarse.astype(np.float64)
    both_finite = np.isfinite(stored) & np.isfinite(exact)
    assert np.all(stored[both_finite] <= exact[both_finite] + 1e-9)


# --------------------------------------------------------------------------
# Seed selection
# --------------------------------------------------------------------------


def test_single_basin_pixel_uses_exactly_one_patch():
    """A single clear minimum must not spend storage on redundant patches."""
    lb = np.ones((25, 25)) * 100.0
    lb[10, 10] = 10.0
    seeds = select_seeds(lb, tolerance=0.05, min_separation=2, max_seeds=3)
    assert seeds == [(10, 10)]


def test_two_near_degenerate_basins_both_get_a_patch():
    """
    The coarse grid can show a solar-heavy and a wind-heavy design that are within
    a few percent of each other but far apart in (s, w). Refining only the argmin
    would miss the other basin entirely -- which is why `gbs._select_seeds` exists.
    """
    lb = np.ones((25, 25)) * 100.0
    lb[4, 20] = 10.0  # wind-heavy basin
    lb[20, 4] = 10.2  # solar-heavy basin, within tolerance
    seeds = select_seeds(lb, tolerance=0.05, min_separation=2, max_seeds=3)

    assert len(seeds) == 2
    assert (4, 20) in seeds and (20, 4) in seeds
    assert seeds[0] == (4, 20), "seeds must come back cheapest-first"


def test_seeds_respect_the_minimum_separation_rule():
    """
    Adjacent cells in one basin are not distinct basins. Without a separation rule
    all three seed slots would be spent on the same minimum's immediate neighbours.
    """
    lb = np.ones((25, 25)) * 100.0
    lb[10, 10] = 10.0
    lb[10, 11] = 10.1  # same basin, one cell away
    lb[11, 10] = 10.1  # same basin
    lb[2, 22] = 10.2  # genuinely separate basin

    seeds = select_seeds(lb, tolerance=0.05, min_separation=3, max_seeds=3)
    assert (10, 10) in seeds
    assert (2, 22) in seeds
    assert (10, 11) not in seeds and (11, 10) not in seeds


def test_seed_count_is_capped():
    """`max_seeds` bounds worst-case cache size, however many basins tie."""
    lb = np.ones((25, 25)) * 100.0
    for idx, (i, j) in enumerate([(2, 2), (2, 20), (20, 2), (20, 20), (11, 11)]):
        lb[i, j] = 10.0 + idx * 0.01
    seeds = select_seeds(lb, tolerance=0.05, min_separation=2, max_seeds=3)
    assert len(seeds) == 3


def test_seeds_ignore_infeasible_cells():
    """Cells where no battery meets coverage carry inf and can never be a seed."""
    lb = np.full((25, 25), np.inf)
    lb[7, 7] = 42.0
    seeds = select_seeds(lb, tolerance=0.05, min_separation=2, max_seeds=3)
    assert seeds == [(7, 7)]


# --------------------------------------------------------------------------
# Patch placement
# --------------------------------------------------------------------------


def test_patch_box_snaps_to_coarse_cell_boundaries(profiles):
    """
    The query-phase certificate partitions the coarse grid into "inside a patch" and
    "outside every patch". That partition is only exact if each patch box lands on
    whole coarse cells -- otherwise a sliver of some cell is neither checked nor
    covered, and the proof has a hole in it.
    """
    s_coarse = np.linspace(0.0, 8.0, PARAMS.coarse_grid)
    w_coarse = np.linspace(0.0, 6.0, PARAMS.coarse_grid)

    s_vals, w_vals, (i0, i1, j0, j1) = patch_box(s_coarse, w_coarse, 12, 9, PARAMS)

    assert s_vals.shape == w_vals.shape == (PARAMS.patch_grid,)
    assert s_vals[0] == pytest.approx(s_coarse[i0])
    assert s_vals[-1] == pytest.approx(s_coarse[i1])
    assert w_vals[0] == pytest.approx(w_coarse[j0])
    assert w_vals[-1] == pytest.approx(w_coarse[j1])
    assert i0 <= 12 <= i1 and j0 <= 9 <= j1, "the patch must contain its own seed"


def test_patch_box_is_clipped_at_the_search_box_edge(profiles):
    """A seed on the boundary must not produce a patch reaching outside the box."""
    s_coarse = np.linspace(0.0, 8.0, PARAMS.coarse_grid)
    w_coarse = np.linspace(0.0, 6.0, PARAMS.coarse_grid)
    s_vals, w_vals, _ = patch_box(s_coarse, w_coarse, 0, PARAMS.coarse_grid - 1, PARAMS)

    assert s_vals[0] >= s_coarse[0] - 1e-12
    assert w_vals[-1] <= w_coarse[-1] + 1e-12


def test_box_widening_is_recorded(anchor_costs, poor_profiles):
    """
    A boundary hit is never silently truncated: either the pixel is dismissed, or
    the box widens and says so.
    """
    frontier = build_pixel_frontier(poor_profiles["solar"], poor_profiles["wind"], 0.85, PARAMS, anchor_costs)
    assert 0 <= frontier.box_widenings <= PARAMS.max_box_widenings
    if frontier.box_widenings > 0:
        assert frontier.s_coarse[-1] > PARAMS.box_min


# --------------------------------------------------------------------------
# Status codes
# --------------------------------------------------------------------------


def test_healthy_pixel_is_status_ok(profiles, anchor_costs):
    """The baseline every other status test is a departure from."""
    frontier = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    assert frontier.status == STATUS_OK
    assert frontier.n_patches >= 1


def test_zero_potential_pixel_is_status_3(dead_profiles, anchor_costs):
    """No sun and no wind is physics, not economics -- it outranks the dismissal screen."""
    frontier = build_pixel_frontier(dead_profiles["solar"], dead_profiles["wind"], 0.85, PARAMS, anchor_costs)
    assert frontier.status == STATUS_ZERO_POTENTIAL
    assert frontier.n_patches == 0


def test_infeasible_pixel_is_status_2(anchor_costs):
    """
    A pixel with generation too feeble to ever meet the coverage target within the
    battery cap is proven infeasible at build time, cost-free and year-invariant.
    """
    hours = 24 * 21
    trickle = {"solar": np.full(hours, 1e-4), "wind": np.zeros(hours)}
    frontier = build_pixel_frontier(trickle["solar"], trickle["wind"], 0.85, PARAMS, anchor_costs)
    assert frontier.status == STATUS_NO_OPTIMUM


def test_status_is_year_invariant_by_construction(profiles, anchor_costs):
    """
    Nothing in the build phase reads a cost year, so a frontier's status cannot vary
    across the query years that reuse it. `lcoe_promotion.py` requires exactly this.
    """
    a = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    b = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    assert a.status == b.status
    assert a.n_patches == b.n_patches
    np.testing.assert_array_equal(a.b_coarse, b.b_coarse)


def test_build_is_deterministic(profiles, anchor_costs):
    """No RNG survives the rewrite: two builds of the same pixel are bit-identical."""
    a = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    b = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    np.testing.assert_array_equal(a.b_patch, b.b_patch)
    np.testing.assert_array_equal(a.energy_served_frac, b.energy_served_frac)


def test_cross_pixel_hint_does_not_change_the_result(profiles, anchor_costs):
    """
    Warm starting from a neighbouring pixel is a speed optimisation. Since the
    bracket search re-establishes a genuine bracket either way, the frontier must
    come out identical within bisection tolerance.
    """
    cold = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs, hint=-1.0)
    warm = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs, hint=7.5)
    np.testing.assert_allclose(cold.b_patch, warm.b_patch, rtol=2 * PARAMS.tol_rel_patch)


# --------------------------------------------------------------------------
# Shared patch lattice (M2)
# --------------------------------------------------------------------------


def _coarse_axes(s_max=24.0, w_max=12.0, gc=25):
    return np.linspace(0.0, s_max, gc), np.linspace(0.0, w_max, gc)


def test_lattice_points_are_multiples_of_the_lattice_step():
    """
    The property the whole design rests on: every patch point is an integer multiple of
    `coarse_spacing / lattice_refinement`. Without it, two patches on one pixel share no
    interior points and cross-anchor reuse is impossible.
    """
    from boa.model.bisection import patch_lattice

    s_c, w_c = _coarse_axes()
    params = SearchParams(lattice_refinement=2)
    step = (s_c[1] - s_c[0]) / params.lattice_refinement

    s_vals, _, _ = patch_lattice(s_c, w_c, 9, 6, params)
    multiples = s_vals / step
    assert np.allclose(multiples, np.round(multiples), atol=1e-9)


def test_two_patches_on_one_pixel_share_their_overlapping_points_exactly():
    """
    Reuse must be exact, not approximate -- a patch node holds dispatch physics with no
    cost term, so a shared point has an identical value under any anchor. Approximate
    agreement would mean recomputing anyway.
    """
    from boa.model.bisection import patch_lattice

    s_c, w_c = _coarse_axes()
    params = SearchParams(lattice_refinement=2)

    a_s, _, a_b = patch_lattice(s_c, w_c, 6, 6, params)
    b_s, _, b_b = patch_lattice(s_c, w_c, 9, 6, params)
    lo, hi = max(a_b[0], b_b[0]), min(a_b[1], b_b[1])
    assert lo < hi, "fixture must produce overlapping boxes"

    shared_a = a_s[(a_s >= s_c[lo] - 1e-9) & (a_s <= s_c[hi] + 1e-9)]
    shared_b = b_s[(b_s >= s_c[lo] - 1e-9) & (b_s <= s_c[hi] + 1e-9)]
    assert np.allclose(shared_a, shared_b, atol=1e-12)


def test_lattice_resolution_does_not_depend_on_box_width():
    """
    The accuracy argument for the lattice: `patch_box` puts a fixed point count on a
    variable box, so a wide patch is resolved more coarsely than a narrow one exactly
    where the seed is least certain. Spacing here is constant by construction.
    """
    from boa.model.bisection import patch_lattice

    s_c, w_c = _coarse_axes()
    params = SearchParams(lattice_refinement=2)

    spacings = []
    for i in (3, 9, 18):
        s_vals, _, bounds = patch_lattice(s_c, w_c, i, 6, params)
        assert bounds[1] - bounds[0] > 0
        spacings.append(s_vals[1] - s_vals[0])
    assert np.allclose(spacings, spacings[0], atol=1e-12)


def test_lattice_keeps_the_same_extent_as_the_fixed_grid():
    """
    Only the interior spacing changes. The extent still snaps outward to whole coarse
    cells, which the containment certificate requires -- a cell neither densely swept nor
    bounded is a hole nothing detects.
    """
    from boa.model.bisection import patch_box, patch_lattice

    s_c, w_c = _coarse_axes()
    params = SearchParams(lattice_refinement=3)
    for i, j in ((0, 0), (6, 6), (12, 3), (24, 24)):
        assert patch_box(s_c, w_c, i, j, params)[2] == patch_lattice(s_c, w_c, i, j, params)[2]


def test_lattice_refinement_below_one_is_rejected():
    """A refinement of 0 would ask for a patch with no interior; fail loudly, not by shape."""
    from boa.model.bisection import patch_lattice

    s_c, w_c = _coarse_axes()
    with pytest.raises(ValueError, match="lattice_refinement"):
        patch_lattice(s_c, w_c, 6, 6, SearchParams(lattice_refinement=0))


# --------------------------------------------------------------------------
# Seed union across anchors
# --------------------------------------------------------------------------
#
# Re-anchoring stores no anchor axis. The patch *values* are pure dispatch, so one set
# serves every anchor; only the seed *placement* is anchor-dependent, so the build unions
# the boxes each anchor proposes. These tests pin that the union is a union -- neither
# silently one anchor's answer nor a per-anchor duplicate.

import dataclasses  # noqa: E402

from boa.model.bisection import CostCoefficients  # noqa: E402

_CHEAP_SOLAR = CostCoefficients(a_s=0.1e6, a_w=5.0e6, a_b=0.30e6, d0=8.76e6)
_CHEAP_WIND = CostCoefficients(a_s=5.0e6, a_w=0.1e6, a_b=0.30e6, d0=8.76e6)


def _boxes(frontier):
    return {tuple(int(x) for x in frontier.patch_bounds[s]) for s in range(frontier.n_patches)}


def test_one_anchor_and_a_one_element_list_build_the_same_frontier(profiles, anchor_costs):
    """A single anchor is the degenerate union, so it must not take a different path."""
    single = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)
    listed = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, [anchor_costs])

    assert single.n_patches == listed.n_patches
    np.testing.assert_array_equal(single.patch_bounds, listed.patch_bounds)
    np.testing.assert_array_equal(single.b_patch, listed.b_patch)


def test_repeating_one_anchor_adds_no_patches(profiles, anchor_costs):
    """
    The dedupe is on the snapped box, not the seed cell. Anchors that agree -- which the
    real cost anchors do on 77.5% of pixels -- must collapse to one patch set, otherwise
    re-anchoring would pay per anchor for identical numbers.
    """
    one = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, [anchor_costs])
    thrice = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, [anchor_costs] * 3)

    assert thrice.n_patches == one.n_patches
    assert _boxes(thrice) == _boxes(one)


def test_disagreeing_anchors_union_their_boxes(profiles):
    """
    The case re-anchoring exists for. Priced with solar nearly free the optimum sits
    solar-heavy; with wind nearly free it sits wind-heavy. The union must hold both, so a
    query in either cost regime finds a densely searched patch around its own optimum.
    """
    solarish = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, _CHEAP_SOLAR)
    windish = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, _CHEAP_WIND)
    assert _boxes(solarish) != _boxes(windish), "fixture no longer separates the two regimes"

    union = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, [_CHEAP_SOLAR, _CHEAP_WIND])
    assert _boxes(union) == _boxes(solarish) | _boxes(windish)
    assert union.n_patches > max(solarish.n_patches, windish.n_patches)


def test_the_union_is_capped_at_max_patch_slots(profiles):
    """
    Overflow truncates rather than overrunning the allocation, and drops the boxes no anchor
    scored well. It is not silently wrong: a dropped basin is exactly what the containment
    certificate detects, so the answer degrades to uncertified.
    """
    params = dataclasses.replace(PARAMS, max_patch_slots=1)
    union = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, params, [_CHEAP_SOLAR, _CHEAP_WIND])

    assert union.n_patches == 1
    assert union.b_patch.shape[0] == 1
    singles = [
        _boxes(build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, params, a))
        for a in (_CHEAP_SOLAR, _CHEAP_WIND)
    ]
    assert any(_boxes(union) <= s for s in singles)


def test_patch_depth_follows_max_patch_slots_not_max_seeds(profiles, anchor_costs):
    """
    Two caps with two meanings: `max_seeds` bounds what one anchor may propose, while
    `max_patch_slots` bounds the union and therefore the stored depth. Conflating them
    would size the cache off the wrong one.
    """
    params = dataclasses.replace(PARAMS, max_seeds=2, max_patch_slots=5)
    frontier = build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, params, anchor_costs)
    assert frontier.b_patch.shape[0] == 5
    assert frontier.n_patches <= 2  # one anchor, so at most `max_seeds` boxes


def test_an_empty_anchor_list_raises(profiles):
    with pytest.raises(ValueError, match="at least one anchor"):
        build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, [])
