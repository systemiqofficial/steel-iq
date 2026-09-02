"""
Contracts for the query phase: the closed-form LCOE objective, the argmin over
cached grids, and the two certificates.

The query does no dispatch simulation at all -- it is pure arithmetic over cached
physics. That is only sound because the cached grids already encode feasibility
(every ladder rung sits at or above `b_min`), so the tests here focus on the two
places soundness could still leak: the certificates that prove the cached region
contains the optimum, and the objective matching what gets reported.
"""

import inspect

import numpy as np
import pytest

from _gate import require

require(
    "boa.model.bisection",
    "CostCoefficients",
    "SearchParams",
    "argmin_lcoe",
    "build_pixel_frontier",
    "check_repair_budget",
)
require("boa.model.cost_calculations", "lcoe_coefficients")

from boa.config.constants import AVERAGE_IMPLIED_STORAGE, HOURS_IN_YEAR  # noqa: E402
from boa.model.bisection import (  # noqa: E402
    GAMMA,
    CostCoefficients,
    SearchParams,
    argmin_lcoe,
    b_min_at,
    build_pixel_frontier,
    check_repair_budget,
    dispatch_metrics,
)
from boa.model.cost_calculations import (  # noqa: E402
    calculate_lcoe_of_re_installation_vectorised,
    lcoe_coefficients,
)

PARAMS = SearchParams()
HORIZON = 25


# Flat, plausible cost inputs. Kept as module constants rather than built inside a
# helper so their types stay concrete for the type checker.
CAPEX: dict[str, np.ndarray] = {
    "solar": np.full(HORIZON + 1, 8.0e5),  # USD/MW
    "wind": np.full(HORIZON + 1, 1.3e6),  # USD/MW
    "battery": np.full(HORIZON + 1, 2.0e5),  # USD/MWh
}
OPEX_PCT: dict[str, float] = {"solar": 0.02, "wind": 0.03, "battery": 0.015}
COST_OF_CAPITAL = 0.07
BASELOAD = 1000.0


def _cost_inputs(baseload: float = BASELOAD) -> dict:
    """Keyword bundle for `lcoe_coefficients`, varying only the baseload."""
    return dict(
        investment_horizon=HORIZON,
        capex=CAPEX,
        opex_pct=OPEX_PCT,
        cost_of_capital=COST_OF_CAPITAL,
        baseload_demand=baseload,
    )


@pytest.fixture
def coeffs():
    return lcoe_coefficients(**_cost_inputs())


@pytest.fixture
def frontier(profiles, anchor_costs):
    return build_pixel_frontier(profiles["solar"], profiles["wind"], 15, PARAMS, anchor_costs)


# --------------------------------------------------------------------------
# The closed-form objective
# --------------------------------------------------------------------------


def test_lcoe_coefficients_reproduce_the_vectorised_pricer(coeffs):
    """
    The closed form must be the existing pricer rearranged, not a reimplementation.
    Parity is checked at served fraction 1.0 because that is the convention
    `calculate_lcoe_of_re_installation_vectorised` uses when handed no delivery
    fraction.
    """
    designs = np.array([[1.5, 0.8, 4.0], [3.0, 2.0, 12.0], [0.4, 5.0, 30.0]])

    lcoes, *_ = calculate_lcoe_of_re_installation_vectorised(
        investment_horizon=HORIZON,
        installed_solar=designs[:, 0] * BASELOAD,
        installed_wind=designs[:, 1] * BASELOAD,
        installed_battery=designs[:, 2] * BASELOAD,
        baseload_demand=BASELOAD,
        capex=CAPEX,
        opex_pct=OPEX_PCT,
        cost_of_capital=COST_OF_CAPITAL,
        realised_delivery_fraction=np.ones(len(designs)),
    )

    closed_form = (
        coeffs.a_s * designs[:, 0] + coeffs.a_w * designs[:, 1] + coeffs.a_b * designs[:, 2] ** GAMMA
    ) / coeffs.d0
    np.testing.assert_allclose(closed_form, lcoes, rtol=1e-12)


def test_coefficients_are_linear_in_baseload():
    """
    Every coefficient and the denominator scale linearly with baseload, which is
    what makes LCOE exactly baseload-invariant rather than approximately so.
    """
    small = lcoe_coefficients(**_cost_inputs(baseload=150.0))
    large = lcoe_coefficients(**_cost_inputs(baseload=20_000.0))
    ratio = 20_000.0 / 150.0

    assert large.a_s == pytest.approx(small.a_s * ratio)
    assert large.a_w == pytest.approx(small.a_w * ratio)
    assert large.a_b == pytest.approx(small.a_b * ratio)
    assert large.d0 == pytest.approx(small.d0 * ratio)


def _battery_only_lcoe(battery_overscale: float) -> float:
    """Price a design that is all battery and no generation, at served fraction 1."""
    lcoes, *_ = calculate_lcoe_of_re_installation_vectorised(
        investment_horizon=HORIZON,
        installed_solar=np.zeros(1),
        installed_wind=np.zeros(1),
        installed_battery=np.array([battery_overscale * BASELOAD]),
        baseload_demand=BASELOAD,
        capex=CAPEX,
        opex_pct=OPEX_PCT,
        cost_of_capital=COST_OF_CAPITAL,
        realised_delivery_fraction=np.ones(1),
    )
    return float(lcoes[0])


def test_battery_coefficient_carries_the_modular_capex_correction(coeffs):
    """
    Battery capex is corrected by `(b/AVERAGE_IMPLIED_STORAGE)**-0.15`, which folds
    into the closed form as a constant times `b**GAMMA`. Losing that factor would
    make large batteries look linearly expensive.

    Checked two ways, both against the pricer rather than against the closed form's
    own algebra: the coefficient reproduces the pricer at the reference duration
    (where the correction is unity by definition), and the pricer's own cost genuinely
    scales sub-linearly away from it.
    """
    at_reference = _battery_only_lcoe(AVERAGE_IMPLIED_STORAGE)
    assert coeffs.a_b * AVERAGE_IMPLIED_STORAGE**GAMMA / coeffs.d0 == pytest.approx(at_reference, rel=1e-12)

    # Four times the reference duration must cost 4**0.85, not 4x -- in the pricer,
    # not merely in our formula.
    at_four_x = _battery_only_lcoe(4 * AVERAGE_IMPLIED_STORAGE)
    assert at_four_x / at_reference == pytest.approx(4.0**GAMMA, rel=1e-12)
    assert at_four_x / at_reference < 4.0

    # And the normalisation constant is exactly AVERAGE_IMPLIED_STORAGE**(1-GAMMA).
    weighted_capex = at_reference * coeffs.d0 / (AVERAGE_IMPLIED_STORAGE * BASELOAD)
    assert coeffs.a_b == pytest.approx(BASELOAD * AVERAGE_IMPLIED_STORAGE ** (1.0 - GAMMA) * weighted_capex, rel=1e-12)


def test_denominator_is_annual_delivered_energy(coeffs):
    """`d0` is the discounted MWh a fully-served year delivers -- the LCOE denominator."""
    discount = np.array([(1.0 + COST_OF_CAPITAL) ** -t for t in range(HORIZON + 1)])
    assert coeffs.d0 == pytest.approx(BASELOAD * HOURS_IN_YEAR * discount[1:].sum(), rel=1e-12)


# --------------------------------------------------------------------------
# The argmin
# --------------------------------------------------------------------------


def test_argmin_matches_a_brute_force_scan_of_the_cached_grids(frontier, coeffs):
    """The vectorised argmin must agree with an explicit loop over every cached point."""
    optimum = argmin_lcoe(frontier, coeffs)

    best = np.inf
    best_design = None
    for k in range(frontier.n_patches):
        # The slot's real extent, not the padded allocation and not `patch_grid`: patches
        # sit on a shared lattice, so their point count varies with box width.
        n_s, n_w = (int(x) for x in frontier.patch_points[k])
        n_r = frontier.b_patch.shape[-1]
        for i in range(n_s):
            for j in range(n_w):
                for r in range(n_r):
                    # float64 throughout, matching `argmin_lcoe`. The arrays are float32 and
                    # `np.float32 ** float` stays float32 (~2.4e-8 relative), which is coarser
                    # than this test's 1e-9 tolerance -- so without the cast this compares
                    # float precision rather than the two search algorithms.
                    s = np.float64(frontier.s_patch[k, i])
                    w = np.float64(frontier.w_patch[k, j])
                    b = np.float64(frontier.b_patch[k, i, j, r])
                    sf = np.float64(frontier.energy_served_frac[k, i, j, r])
                    if not np.isfinite(b) or sf <= 0:
                        continue
                    value = (coeffs.a_s * s + coeffs.a_w * w + coeffs.a_b * b**GAMMA) / (coeffs.d0 * sf)
                    if value < best:
                        best, best_design = value, (s, w, b)

    assert optimum.lcoe == pytest.approx(best, rel=1e-9)
    assert (optimum.solar, optimum.wind, optimum.battery) == pytest.approx(best_design, rel=1e-9)


def test_argmin_searches_every_patch_and_ignores_unused_slots(profiles, anchor_costs, coeffs):
    """
    Patch slots are allocated for `max_seeds` but only `n_patches` are populated.
    Unused slots are zero-filled, and a zeroed design would price as free -- so they
    must be excluded, not merely ignored by luck.
    """
    frontier = build_pixel_frontier(profiles["solar"], profiles["wind"], 15, PARAMS, anchor_costs)
    if frontier.n_patches >= PARAMS.max_seeds:
        pytest.skip("fixture pixel filled every seed slot; nothing to exclude")

    optimum = argmin_lcoe(frontier, coeffs)
    assert optimum.lcoe > 0.0
    assert 0 <= optimum.patch_index < frontier.n_patches


def test_winner_can_come_from_the_second_basin_at_a_different_cost_year():
    """
    Seeds are placed using the frozen anchor ratios, but a query year's real prices
    can promote a basin the anchor ranked second. Searching every cached patch --
    not just the anchor's favourite -- is what makes that safe.
    """
    from boa.model.bisection import PixelFrontier, STATUS_OK

    gc, gp = PARAMS.coarse_grid, 3
    # Two patches: the first is wind-heavy, the second solar-heavy.
    s_patch = np.array([[0.1, 0.2, 0.3], [4.0, 5.0, 6.0]])
    w_patch = np.array([[4.0, 5.0, 6.0], [0.1, 0.2, 0.3]])
    b_patch = np.full((2, gp, gp, 1), 5.0)
    served = np.full((2, gp, gp, 1), 0.98)

    frontier = PixelFrontier(
        s_coarse=np.linspace(0, 8, gc),
        w_coarse=np.linspace(0, 8, gc),
        b_coarse=np.full((gc, gc), 0.0, dtype=np.float16),
        n_patches=2,
        s_patch=s_patch,
        w_patch=w_patch,
        b_patch=b_patch,
        energy_served_frac=served,
        hours_covered_frac=np.full((2, gp, gp, 1), 0.95),
        # Both patches declared to span the whole coarse grid, so nothing is "outside" and
        # the containment certificate is trivially satisfied -- this test is about which
        # patch wins, not the certificate (see
        # test_coarse_certificate_detects_an_optimum_outside_the_patch for that).
        patch_bounds=np.array([[0, gc - 1, 0, gc - 1], [0, gc - 1, 0, gc - 1]], dtype=np.int32),
        patch_points=np.array([[gp, gp], [gp, gp]], dtype=np.int32),
        status=STATUS_OK,
        box_widenings=0,
    )

    cheap_wind = CostCoefficients(a_s=10.0, a_w=1.0, a_b=0.1, d0=1.0)
    cheap_solar = CostCoefficients(a_s=1.0, a_w=10.0, a_b=0.1, d0=1.0)

    assert argmin_lcoe(frontier, cheap_wind).patch_index == 0
    assert argmin_lcoe(frontier, cheap_solar).patch_index == 1


def test_reported_lcoe_equals_the_ranked_lcoe(frontier, coeffs):
    """
    Today's code ranks on coverage-divided LCOE but reports served-fraction-divided
    LCOE, so the winner is not the cheapest by the number printed next to it. The
    rewrite ranks and reports the same quantity; this test is what holds that line.
    """
    optimum = argmin_lcoe(frontier, coeffs)
    recomputed = (coeffs.a_s * optimum.solar + coeffs.a_w * optimum.wind + coeffs.a_b * optimum.battery**GAMMA) / (
        coeffs.d0 * optimum.served_fraction
    )
    assert optimum.lcoe == pytest.approx(recomputed, rel=1e-12)


def test_optimum_meets_the_coverage_constraint(profiles, frontier, coeffs):
    """
    Feasibility holds by construction -- every rung sits at or above `b_min` -- but
    the query never re-checks it, so the property is asserted here instead.
    """
    optimum = argmin_lcoe(frontier, coeffs)
    cov, _ = dispatch_metrics(profiles["solar"], profiles["wind"], optimum.solar, optimum.wind, optimum.battery)
    assert cov >= 1.0 - 15 / 100.0 - 1e-9


def test_battery_optimum_can_sit_above_b_min(profiles, frontier, coeffs):
    """
    The headline consequence of dividing by served fraction: buying more battery
    than feasibility demands can still lower LCOE, because it raises the denominator.
    The stored rungs are what make the search side of this reachable at all -- checked
    directly in `test_battery_ladder.py`; this test only holds the resulting invariant
    at the query layer, that the reported optimum never sits below b_min.
    """
    optimum = argmin_lcoe(frontier, coeffs)
    b_min, _, _ = b_min_at(profiles["solar"], profiles["wind"], optimum.solar, optimum.wind, 15, PARAMS)
    # Not bit-exact: the frontier's own b_min was found warm-started from a neighbouring
    # node, this one is cold -- both are valid within `tol_rel_patch`, per
    # `test_b_min_warm_start_is_result_invariant`'s own precedent.
    assert optimum.battery >= b_min * (1.0 - PARAMS.tol_rel_patch), "the optimum can never sit below b_min"


def test_lcoe_and_design_are_exactly_baseload_invariant(frontier):
    """
    Successor to `test_sampler.py:323`, and strictly stronger: the old test asserted
    a shared cache matched dedicated per-baseload builds, this one asserts the answer
    is bit-identical because baseload cancels algebraically.
    """
    small = argmin_lcoe(frontier, lcoe_coefficients(**_cost_inputs(baseload=150.0)))
    large = argmin_lcoe(frontier, lcoe_coefficients(**_cost_inputs(baseload=20_000.0)))

    assert small.solar == large.solar
    assert small.wind == large.wind
    assert small.battery == large.battery
    assert small.lcoe == pytest.approx(large.lcoe, rel=1e-12)


# --------------------------------------------------------------------------
# Certificates
# --------------------------------------------------------------------------


def test_certificate_passes_when_the_outside_region_is_provably_infeasible():
    """
    Certified True when every outside cell's own bound cannot beat the incumbent --
    checked on a controlled synthetic frontier rather than assumed of any particular
    real one. A real frontier can legitimately fail to certify even when queried with
    the exact costs it was built with: the coarse bound is deliberately loose (2-3
    early-stopped bisection steps, propagated by domination), and a finite propagated
    bound does not mean a cell is actually feasible -- a dominated cell can be truly
    infeasible while still inheriting a finite (just very loose) lower bound from a
    feasible dominating neighbour. See BOA_BISECTION_PLAN.md, "containment certificate
    finding" -- accepted as designed, not treated as a bug.
    """
    from boa.model.bisection import PixelFrontier, STATUS_OK

    gc, gp = PARAMS.coarse_grid, 3
    frontier = PixelFrontier(
        s_coarse=np.linspace(0, 8, gc),
        w_coarse=np.linspace(0, 8, gc),
        b_coarse=np.full((gc, gc), np.inf, dtype=np.float16),  # every outside cell proven infeasible
        n_patches=1,
        s_patch=np.array([[1.0, 2.0, 3.0]]),
        w_patch=np.array([[1.0, 2.0, 3.0]]),
        b_patch=np.full((1, gp, gp, 1), 5.0),
        energy_served_frac=np.full((1, gp, gp, 1), 0.98),
        hours_covered_frac=np.full((1, gp, gp, 1), 0.95),
        patch_bounds=np.array([[0, 2, 0, 2]], dtype=np.int32),
        patch_points=np.array([[gp, gp]], dtype=np.int32),
        status=STATUS_OK,
        box_widenings=0,
    )
    optimum = argmin_lcoe(frontier, CostCoefficients(a_s=1.0, a_w=1.0, a_b=1.0, d0=1.0))
    assert optimum.patch_certified


def test_coarse_certificate_detects_an_optimum_outside_the_patch(frontier, coeffs):
    """
    The containment proof compares every coarse cell outside the patch union against
    the incumbent. Planting an implausibly cheap cell outside the patch must fail the
    certificate rather than be silently ignored.
    """
    import dataclasses

    doctored = np.array(frontier.b_coarse, dtype=np.float16)
    doctored[0, 0] = np.float16(0.0)  # a free, battery-less design in the corner
    tampered = dataclasses.replace(frontier, b_coarse=doctored)

    optimum = argmin_lcoe(tampered, coeffs)
    assert not optimum.patch_certified


def test_repair_budget_allows_a_small_rate():
    """A handful of repairs per region is expected and must not stop a query."""
    check_repair_budget(n_repaired=5, n_points=10_000, params=PARAMS)


def test_query_fails_when_the_repair_rate_exceeds_the_cap():
    """
    Repair means a full on-the-fly patch sweep. Past a couple of percent the query
    stops being "minutes per year", so it fails loudly rather than degrading.
    """
    with pytest.raises(RuntimeError, match="patch"):
        check_repair_budget(n_repaired=900, n_points=10_000, params=PARAMS)


# --------------------------------------------------------------------------
# The ceiling is not visible to Grid 1
# --------------------------------------------------------------------------


def _frontier_with_cheapest_node(bounds, ci, cj, gp=3):
    """
    A frontier whose argmin is forced to patch node `(ci, cj)`, with patch cell `bounds`.

    Costs are set so only the battery term survives (`a_s = a_w = 0`, `d0 = 1`, `sf = 1`),
    which makes the argmin exactly the smallest `b_patch` entry and keeps the test about
    edge detection rather than about pricing.
    """
    from boa.model.bisection import PixelFrontier, STATUS_OK

    gc = PARAMS.coarse_grid
    b = np.full((1, gp, gp, 1), 5.0)
    b[0, ci, cj] = 1.0
    return PixelFrontier(
        s_coarse=np.linspace(0, 8, gc),
        w_coarse=np.linspace(0, 8, gc),
        b_coarse=np.full((gc, gc), np.inf, dtype=np.float16),
        n_patches=1,
        s_patch=np.array([np.linspace(1.0, 3.0, gp)]),
        w_patch=np.array([np.linspace(1.0, 3.0, gp)]),
        b_patch=b,
        energy_served_frac=np.full((1, gp, gp, 1), 1.0),
        hours_covered_frac=np.full((1, gp, gp, 1), 0.95),
        patch_bounds=np.array([bounds], dtype=np.int32),
        patch_points=np.array([[gp, gp]], dtype=np.int32),
        status=STATUS_OK,
        box_widenings=0,
    )


_EDGE_COEFFS = CostCoefficients(a_s=0.0, a_w=0.0, a_b=1.0, d0=1.0)


def test_padding_beyond_a_slot_extent_cannot_win_the_argmin():
    """
    Patches are padded to the widest box `SearchParams` can produce, so most of a slot is
    not real. Consumers must mask it via `patch_points`.

    The padding is zero-filled in practice, and zeros happen to be excluded anyway by the
    `sf > 0` guard -- which makes this easy to get wrong and never notice. So the padding
    here is filled with an *attractive* design instead: a near-free battery at full served
    fraction, which would win outright if the extent were not respected.
    """
    from boa.model.bisection import PixelFrontier, STATUS_OK

    gc, real, alloc = PARAMS.coarse_grid, 3, 6
    b = np.full((1, alloc, alloc, 1), 5.0)
    sf = np.full((1, alloc, alloc, 1), 0.5)
    b[0, real:, :] = b[0, :, real:] = 1e-6  # would price as almost free
    sf[0, real:, :] = sf[0, :, real:] = 1.0

    frontier = PixelFrontier(
        s_coarse=np.linspace(0, 8, gc),
        w_coarse=np.linspace(0, 8, gc),
        b_coarse=np.full((gc, gc), np.inf, dtype=np.float16),
        n_patches=1,
        s_patch=np.array([np.linspace(1.0, 6.0, alloc)]),
        w_patch=np.array([np.linspace(1.0, 6.0, alloc)]),
        b_patch=b,
        energy_served_frac=sf,
        hours_covered_frac=np.full((1, alloc, alloc, 1), 0.95),
        patch_bounds=np.array([[0, 2, 0, 2]], dtype=np.int32),
        patch_points=np.array([[real, real]], dtype=np.int32),
        status=STATUS_OK,
        box_widenings=0,
    )
    optimum = argmin_lcoe(frontier, CostCoefficients(a_s=1.0, a_w=1.0, a_b=1.0, d0=1.0))

    assert optimum.battery == 5.0, "argmin took a padded node"
    assert optimum.solar <= frontier.s_patch[0, real - 1]
    assert optimum.wind <= frontier.w_patch[0, real - 1]


def test_argmin_on_an_interior_patch_edge_is_flagged_truncated():
    """
    The objective was still improving where the dense sweep stopped, so the reported
    design may be an artefact of the patch's extent rather than a real optimum. Nothing else
    reports this: `patch_certified` is false whenever *any* outside cell fails to be bounded
    away, which is a far broader condition, so it cannot stand in.
    """
    optimum = argmin_lcoe(_frontier_with_cheapest_node([2, 4, 2, 4], 2, 1), _EDGE_COEFFS)
    assert optimum.argmin_truncated


def test_argmin_against_a_zero_axis_is_a_corner_not_truncation():
    """
    A wind-only or solar-only optimum sits at `s = 0` / `w = 0` legitimately -- there is
    nothing below zero it could have missed. Corner solutions are far more common than real
    truncation, so flagging them would bury the signal this is meant to carry.
    """
    optimum = argmin_lcoe(_frontier_with_cheapest_node([0, 2, 0, 2], 0, 1), _EDGE_COEFFS)
    assert not optimum.argmin_truncated


def test_argmin_against_the_outer_coarse_ring_is_not_patch_truncation():
    """
    Reaching the edge of the search box is the box running out, not the patch: that is
    what `box_widenings` records and what widening already had its chance to fix.
    """
    gc = PARAMS.coarse_grid
    optimum = argmin_lcoe(_frontier_with_cheapest_node([2, gc - 1, 2, 4], 2, 1), _EDGE_COEFFS)
    assert not optimum.argmin_truncated


def test_truncation_is_reported_per_query_not_baked_into_status(frontier, coeffs):
    """
    `argmin_truncated` depends on where this year's costs put the argmin, so it is a
    per-query property. `lcoe_promotion` requires `status` to be year-invariant, so the
    flag must live on the query result and never on the frontier.
    """
    from boa.model.bisection import PixelFrontier

    assert "argmin_truncated" in argmin_lcoe(frontier, coeffs).__dataclass_fields__
    assert not hasattr(PixelFrontier, "argmin_truncated")


def test_grid_one_argmin_takes_no_capacity_parameter():
    """
    Grid 1 answers "what is the best system here?", with no reference to how much
    can physically be built. The capacity box lives entirely in Grid 2, so the
    ceiling is not merely unused by this search -- it is unreachable from it.

    Asserted structurally rather than behaviourally, because a test that calls the
    same function twice with the same arguments proves nothing.
    """
    taken = set(inspect.signature(argmin_lcoe).parameters)
    assert not taken & {"pv_max", "wind_max", "limit", "limits", "max_capacity", "baseload_demand"}


def test_grid_one_optimum_is_reproducible(frontier, coeffs):
    """No RNG survives the rewrite: repeated argmins over one frontier are identical."""
    a = argmin_lcoe(frontier, coeffs)
    b = argmin_lcoe(frontier, coeffs)
    assert (a.solar, a.wind, a.battery, a.lcoe) == (b.solar, b.wind, b.battery, b.lcoe)
