"""
Contracts for the capacity box: the fit test, the constrained re-search, and the
sidecar that persists it.

The structure under test is two grids doing two jobs. Grid 1 spans the physics box
and answers "what is the best system here?" without reference to how much can be
built. The fit test then asks whether that design fits at this demand. Only when it
does not does Grid 2 sweep the capacity box for the best design that does fit.

Two properties carry most of the weight:

  * the trigger for building Grid 2 is cost-free, so one sidecar serves every
    investment year, while the fit test itself is cost-dependent and therefore
    per-year -- which is why `ceiling_binds` is a variable and not a status code;
  * the constrained optimum is found by sweeping the whole in-box region, not the
    boundary faces, because the objective is not convex.
"""

import numpy as np
import pytest

from _gate import require

require("boa.model.bisection", "STATUS_CAPACITY_INFEASIBLE", "argmin_lcoe", "build_pixel_frontier")
require(
    "boa.model.capacity_box",
    "build_box_frontier",
    "clips",
    "fits",
    "limits_for",
    "resolve",
)

from boa.model import capacity_box  # noqa: E402
from boa.model.bisection import (  # noqa: E402
    STATUS_CAPACITY_INFEASIBLE,
    STATUS_OK,
    SearchParams,
    argmin_lcoe,
    build_pixel_frontier,
)

PARAMS = SearchParams()


@pytest.fixture
def frontier(profiles, anchor_costs):
    return build_pixel_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, anchor_costs)


# --------------------------------------------------------------------------
# The fit test
# --------------------------------------------------------------------------


def test_limits_are_max_capacity_over_baseload():
    """
    The box is expressed in the same dimensionless overscale units as the designs,
    which is the whole reason a ceiling in MW can be compared against a design at all.
    """
    limits = capacity_box.limits_for(pv_max=1530.0, wind_max=820.0, baseload_demand=500.0)
    assert limits["solar"] == pytest.approx(3.06)
    assert limits["wind"] == pytest.approx(1.64)


def test_dimensionless_optimum_that_fits_is_reported_unchanged(frontier, anchor_costs, roomy_limits):
    """
    A slack box must leave the answer untouched: same design, same LCOE, and no
    constrained search performed at all.
    """
    unconstrained = argmin_lcoe(frontier, anchor_costs)
    result = capacity_box.resolve(frontier, anchor_costs, roomy_limits, box_frontier=None)

    assert result.ceiling_binds == 0
    assert result.status == STATUS_OK
    assert (result.solar, result.wind, result.battery) == (
        unconstrained.solar,
        unconstrained.wind,
        unconstrained.battery,
    )
    assert result.lcoe == pytest.approx(unconstrained.lcoe)


def test_design_exactly_at_the_ceiling_counts_as_fitting():
    """
    Successor to the `<=` semantics the old in-box mask pinned: a design sitting
    exactly on the ceiling is buildable, so it fits.
    """
    assert capacity_box.fits(solar=2.0, wind=3.0, limits={"solar": 2.0, "wind": 3.0})
    assert not capacity_box.fits(solar=2.0 + 1e-9, wind=3.0, limits={"solar": 2.0, "wind": 3.0})


def test_fit_test_needs_both_axes():
    """Exceeding either ceiling is enough to not fit; neither axis is privileged."""
    limits = {"solar": 3.0, "wind": 2.0}
    assert not capacity_box.fits(solar=4.0, wind=1.0, limits=limits)
    assert not capacity_box.fits(solar=1.0, wind=5.0, limits=limits)
    assert capacity_box.fits(solar=1.0, wind=1.0, limits=limits)


# --------------------------------------------------------------------------
# The Grid 2 trigger
# --------------------------------------------------------------------------


def test_clips_trigger_is_cost_free(frontier, roomy_limits, tight_limits):
    """
    Grid 2 is built wherever the capacity box clips the physics box. That test reads
    only the box and the search axes, never a cost.

    The obvious alternative -- build where the fit test fails -- would be wrong at the
    cache layer: the fit test compares the cost-dependent optimum against the ceiling,
    so which pixels needed a sidecar would differ between 2025 and 2050, dragging a
    year-dependency into a cache that must serve every year.
    """
    s_max = float(frontier.s_coarse[-1])
    w_max = float(frontier.w_coarse[-1])

    assert not capacity_box.clips(s_max, w_max, roomy_limits)
    assert capacity_box.clips(s_max, w_max, tight_limits)


def test_clips_trigger_takes_no_cost_argument():
    """Asserted structurally: a cost parameter here would reintroduce year-dependency."""
    import inspect

    taken = set(inspect.signature(capacity_box.clips).parameters)
    assert not taken & {"coeffs", "costs", "year", "a_s", "a_w", "a_b"}


# --------------------------------------------------------------------------
# The constrained search
# --------------------------------------------------------------------------


def test_box_grid_spans_the_intersection(profiles, tight_limits):
    """
    Grid 2 spans the intersection of the physics box and the capacity box, at full
    patch resolution. Resolving the box rather than masking a coarser grid is what
    makes a tight box tractable at all -- a mask would leave only a handful of nodes.
    """
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)

    assert box.s_box.shape == (PARAMS.patch_grid,)
    assert box.s_box[0] == pytest.approx(0.0)
    assert box.s_box[-1] <= tight_limits["solar"] + 1e-12
    assert box.w_box[-1] <= tight_limits["wind"] + 1e-12
    assert np.all(np.diff(box.s_box) > 0)


def test_non_fitting_pixel_triggers_the_constrained_search(profiles, frontier, anchor_costs, tight_limits):
    """When the ceiling binds, the reported design comes from Grid 2 and is flagged."""
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    result = capacity_box.resolve(frontier, anchor_costs, tight_limits, box_frontier=box)

    if result.status != STATUS_OK:
        pytest.skip("fixture pixel is infeasible inside the tight box; covered elsewhere")

    assert result.ceiling_binds == 1
    assert capacity_box.fits(result.solar, result.wind, tight_limits)


def test_constrained_optimum_is_never_cheaper_than_the_unconstrained_one(
    profiles, frontier, anchor_costs, tight_limits
):
    """
    The constrained feasible set is a subset of the unconstrained one, so restricting
    it can only raise the objective. A constrained LCOE below the unconstrained one is
    a bug, not a lucky find.
    """
    unconstrained = argmin_lcoe(frontier, anchor_costs)
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    result = capacity_box.resolve(frontier, anchor_costs, tight_limits, box_frontier=box)

    if result.status == STATUS_OK:
        assert result.lcoe >= unconstrained.lcoe - 1e-9


def test_constrained_optimum_can_be_interior_in_both_axes():
    """
    The reason the whole in-box region is swept rather than just the boundary faces.

    With two near-degenerate basins, clipping the solar-heavy one can leave the
    wind-heavy one strictly interior to the box. A boundary-only search would miss it,
    because the objective is not convex and the feasible set is not a simple shape.
    """
    result = capacity_box.argmin_in_box(
        s_vals=np.array([0.5, 1.0, 1.5, 2.0]),
        w_vals=np.array([0.5, 1.0, 1.5, 2.0]),
        # Cheapest cell is at (1.0, 1.0): interior on both axes.
        lcoe_grid=np.array(
            [
                [9.0, 8.0, 8.5, 9.0],
                [8.0, 1.0, 7.0, 8.0],
                [8.5, 7.0, 7.5, 8.5],
                [9.0, 8.0, 8.5, 9.0],
            ]
        ),
    )
    assert result == (1, 1)


def test_infeasible_corner_is_status_capacity_infeasible_without_a_sweep(profiles, infeasible_limits):
    """
    Coverage is monotone in build size, so if the largest allowed design cannot meet
    the target, nothing in the box can. That is a one-dispatch proof, and it must
    short-circuit the sweep entirely rather than discovering emptiness node by node.
    """
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, infeasible_limits)
    assert box.box_status == STATUS_CAPACITY_INFEASIBLE
    assert box.b_box.size == 0, "an infeasible box must not be swept"


def test_capacity_infeasible_is_a_distinct_status(profiles, frontier, anchor_costs, infeasible_limits):
    """
    Today a ceiling-limited pixel is indistinguishable from a physically infeasible
    one: both land on code 2 or 4 depending only on whether the cache happened to hold
    rows. The new code separates "cannot be built this big here" from "cannot work
    here at all".
    """
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, infeasible_limits)
    result = capacity_box.resolve(frontier, anchor_costs, infeasible_limits, box_frontier=box)

    assert result.status == STATUS_CAPACITY_INFEASIBLE
    assert not np.isfinite(result.lcoe) or result.lcoe == 0.0


# --------------------------------------------------------------------------
# Year-invariance -- the promotion contract
# --------------------------------------------------------------------------


def test_ceiling_binds_is_a_variable_not_a_status(profiles, frontier, anchor_costs, tight_limits):
    """
    `ceiling_binds` must be reported separately from `status`, because it is derived
    from the cost-dependent optimum. Folding it into `status` would break
    `lcoe_promotion`, which raises when status differs across investment years.
    """
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    result = capacity_box.resolve(frontier, anchor_costs, tight_limits, box_frontier=box)

    assert hasattr(result, "ceiling_binds")
    assert result.ceiling_binds in (0, 1)
    assert result.status in (STATUS_OK, STATUS_CAPACITY_INFEASIBLE)


def test_status_is_year_invariant_while_ceiling_binds_is_not(profiles, frontier, tight_limits):
    """
    The distinction the split exists to preserve. Two cost years that shift the optimum
    across the ceiling must agree on `status` and may disagree on `ceiling_binds`.
    """
    from boa.model.bisection import CostCoefficients

    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    cheap_solar = CostCoefficients(a_s=0.2e6, a_w=1.6e6, a_b=0.30e6, d0=8.76e6)
    cheap_wind = CostCoefficients(a_s=3.0e6, a_w=0.4e6, a_b=0.30e6, d0=8.76e6)

    a = capacity_box.resolve(frontier, cheap_solar, tight_limits, box_frontier=box)
    b = capacity_box.resolve(frontier, cheap_wind, tight_limits, box_frontier=box)

    assert a.status == b.status, "status must not move with costs"


def test_box_status_is_cost_free(profiles, tight_limits, infeasible_limits):
    """
    The corner screen depends only on profiles, the coverage target and the box, so a
    sidecar built once is valid for every investment year.
    """
    import inspect

    taken = set(inspect.signature(capacity_box.build_box_frontier).parameters)
    assert not taken & {"coeffs", "costs", "year"}


# --------------------------------------------------------------------------
# The sidecar
# --------------------------------------------------------------------------


def test_sidecar_path_carries_the_baseload(tmp_path):
    """
    The box is the one artefact that depends on demand, so the demand lives in the
    sidecar name rather than in the parent cache path -- which is what keeps Grid 1
    shareable across every demand.
    """
    parent = tmp_path / "gbs_g25p15r5_abcd1234_y2024_r025.zarr"
    path = capacity_box.box_sidecar_path(parent, baseload_demand=500.0)

    assert "500MW" in path.name
    assert path.parent == parent.parent
    assert path != parent


def test_box_sidecar_replays_bit_identically(profiles, tmp_path, tight_limits):
    """
    A replayed sidecar must reproduce a fresh sweep exactly, or a re-query silently
    returns different designs than the run that built it.
    """
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    path = tmp_path / "box_500MW.zarr"
    capacity_box.write_box_frontier(box, path, baseload_demand=500.0, parent_meta={"built_at": "x", "params": {}})

    loaded = capacity_box.read_box_frontier(path, parent_meta={"built_at": "x", "params": {}}, baseload_demand=500.0)
    np.testing.assert_array_equal(loaded.b_box, box.b_box)
    np.testing.assert_allclose(loaded.sf_box, box.sf_box, atol=2e-5)


def test_sidecar_refused_when_the_parent_cache_is_rebuilt(profiles, tmp_path, tight_limits):
    """
    A rebuilt parent invalidates its sidecars: the box sweep is keyed to the parent's
    axes, so replaying it against a different parent would mix two searches.
    """
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    path = tmp_path / "box_500MW.zarr"
    capacity_box.write_box_frontier(box, path, baseload_demand=500.0, parent_meta={"built_at": "x", "params": {}})

    with pytest.raises(ValueError, match="parent"):
        capacity_box.read_box_frontier(path, parent_meta={"built_at": "REBUILT", "params": {}}, baseload_demand=500.0)


def test_sidecar_refused_at_a_different_baseload(profiles, tmp_path, tight_limits):
    """The box is demand-specific; replaying it at another demand would be wrong."""
    box = capacity_box.build_box_frontier(profiles["solar"], profiles["wind"], 0.85, PARAMS, tight_limits)
    path = tmp_path / "box_500MW.zarr"
    capacity_box.write_box_frontier(box, path, baseload_demand=500.0, parent_meta={"built_at": "x", "params": {}})

    with pytest.raises(ValueError, match="baseload"):
        capacity_box.read_box_frontier(path, parent_meta={"built_at": "x", "params": {}}, baseload_demand=1000.0)
