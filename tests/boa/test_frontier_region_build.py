"""
Contracts for the region-level frontier build worker.

Scope is deliberately the tile worker, not the whole region build. `global_extension` has no
test coverage and no staged-store fixture in the tree, so exercising the full function needs a
synthetic region -- a profile store named so the weather year resolves, a land-sea mask, and a
`PathConfig` rooted at a temp dir. That belongs with the CLI wiring, where real stores exist to
point at.

What is testable cheaply is the property the region build's reassembly depends on: tiles are
strided index lists, so a worker's output must line up with its input positionally. Getting
that wrong scatters frontiers onto the wrong pixels, which produces a complete, plausible,
silently wrong cache.
"""

import numpy as np
import pytest

from _gate import require

require("boa.model.global_extension", "_frontier_tile", "build_frontier_cache_for_region")

from boa.model.bisection import STATUS_OK, STATUS_ZERO_POTENTIAL, SearchParams  # noqa: E402
from boa.model.global_extension import _frontier_tile  # noqa: E402

PARAMS = SearchParams()
COVERAGE = 0.85


@pytest.fixture
def point_arrays(profiles, dead_profiles):
    """Four pixels: three live, one with no resource at all in slot 2."""
    solar = np.stack([profiles["solar"], profiles["solar"], dead_profiles["solar"], profiles["solar"]])
    wind = np.stack([profiles["wind"], profiles["wind"], dead_profiles["wind"], profiles["wind"]])
    return np.ascontiguousarray(solar), np.ascontiguousarray(wind)


def test_tile_output_lines_up_with_its_index_list(point_arrays, anchor_costs):
    """
    The reassembly contract. Tiles are strided (`order[i::n_tiles]`), so the worker's j-th
    frontier must be the frontier of `tile_indices[j]` -- not of j. A mismatch would scatter
    results onto neighbouring pixels and produce a cache that looks entirely normal.
    """
    solar, wind = point_arrays
    strided = np.array([2, 0])  # deliberately out of order, and includes the dead pixel
    _, frontiers = _frontier_tile(strided, solar, wind, COVERAGE, PARAMS, [anchor_costs])

    assert len(frontiers) == len(strided)
    assert frontiers[0].status == STATUS_ZERO_POTENTIAL, "slot 0 must follow index 2, the dead pixel"
    assert frontiers[1].status == STATUS_OK


def test_every_point_yields_a_frontier_including_degenerate_ones(point_arrays, anchor_costs):
    """
    Unlike the Monte Carlo worker, which emitted empty arrays for points it skipped, every
    point produces a frontier. That is what lets the region build index positionally instead
    of carrying sentinels, and it is why the build can assert that no slot is left unfilled.
    """
    solar, wind = point_arrays
    _, frontiers = _frontier_tile(np.arange(4), solar, wind, COVERAGE, PARAMS, [anchor_costs])
    assert len(frontiers) == 4
    assert all(f is not None for f in frontiers)
    assert [f.status for f in frontiers].count(STATUS_ZERO_POTENTIAL) == 1


def test_the_worker_reports_its_own_elapsed_time(point_arrays, anchor_costs):
    """Load balance is measured from these; a tile that reported zero would hide a straggler."""
    solar, wind = point_arrays
    elapsed, _ = _frontier_tile(np.arange(4), solar, wind, COVERAGE, PARAMS, [anchor_costs])
    assert elapsed > 0.0


def test_the_worker_takes_the_anchor_list_through_unchanged(point_arrays, anchor_costs):
    """
    Anchors reach `build_pixel_frontier` as a list, so the union across them happens per pixel.
    Passing several must not raise or silently collapse to the first.
    """
    solar, wind = point_arrays
    one = _frontier_tile(np.array([0]), solar, wind, COVERAGE, PARAMS, [anchor_costs])[1][0]
    many = _frontier_tile(np.array([0]), solar, wind, COVERAGE, PARAMS, [anchor_costs] * 3)[1][0]
    assert many.n_patches == one.n_patches


def test_the_build_reads_no_capacity_ceiling():
    """
    Structural, and the point of keeping the store availability-free: if the region build ever
    grows a `max_cap` read, the cache stops being shareable across layer sets and the
    weather-year key becomes unsound. Cheaper to assert than to detect later.
    """
    import inspect

    from boa.model.global_extension import build_frontier_cache_for_region

    source = inspect.getsource(build_frontier_cache_for_region)
    assert "max_cap" not in source
    assert "pv_max" not in source and "wind_max" not in source

    signature = set(inspect.signature(build_frontier_cache_for_region).parameters)
    assert not signature & {"costs", "n", "seed", "baseload_demand"}


# --------------------------------------------------------------------------
# Query worker
# --------------------------------------------------------------------------


def _region_cache(profiles, dead_profiles, anchor_costs, tmp_path):
    """A two-point region store: one live pixel, one with no resource."""
    from boa.model.bisection import build_pixel_frontier
    from boa.model.frontier_cache import build_frontier_meta, stack_pixel_frontiers

    frontiers = [
        build_pixel_frontier(profiles["solar"], profiles["wind"], COVERAGE, PARAMS, anchor_costs),
        build_pixel_frontier(dead_profiles["solar"], dead_profiles["wind"], COVERAGE, PARAMS, anchor_costs),
    ]
    return stack_pixel_frontiers(
        frontiers,
        region="TESTREGION",
        all_lats=np.array([10.0, 10.25], dtype=np.float32),
        all_lons=np.array([20.0, 20.25], dtype=np.float32),
        lats=np.array([10.0, 10.25], dtype=np.float32),
        lons=np.array([20.0, 20.0], dtype=np.float32),
        iy=np.array([0, 1], dtype=np.int32),
        ix=np.array([0, 0], dtype=np.int32),
        meta=build_frontier_meta("TESTREGION", 2, COVERAGE, PARAMS, 2024, 0.25),
    )


@pytest.fixture
def query_inputs():
    capex = {t: np.full((2, 26), v) for t, v in (("solar", 9e5), ("wind", 1.4e6), ("battery", 3e5))}
    opex = {t: np.full(2, v) for t, v in (("solar", 0.01), ("wind", 0.02), ("battery", 0.02))}
    return capex, opex, np.full(2, 0.08), np.array(["DEU", "DEU"])


def test_query_prices_a_live_pixel_and_passes_through_a_dead_one(
    profiles, dead_profiles, anchor_costs, tmp_path, query_inputs
):
    """
    A pixel whose build found no optimum must not be priced. Its status travels from the
    store to the output untouched, because status is year-invariant by construction and the
    query has no business revising it.
    """
    from boa.model.global_extension import _query_frontier_tile

    cache = _region_cache(profiles, dead_profiles, anchor_costs, tmp_path)
    capex, opex, coc, keys = query_inputs
    _, results, counters = _query_frontier_tile(np.arange(2), cache, capex, opex, coc, keys, 500.0, 25)

    assert results[0]["status"] == STATUS_OK
    assert results[0]["lcoe"] > 0.0
    assert results[1]["status"] == STATUS_ZERO_POTENTIAL
    assert results[1]["lcoe"] == 0.0
    assert counters["zero_potential"] == 1


def test_ranking_and_reporting_use_the_same_lcoe(profiles, dead_profiles, anchor_costs, tmp_path, query_inputs):
    """
    The inconsistency this rewrite exists to remove: the sampler ranked on a
    coverage-divided LCOE and reported a served-fraction-divided one, so the winner was not
    the cheapest by the number printed beside it. Here `lcoe_coverage_based` is a derived
    reference, not a second ranking.
    """
    from boa.model.global_extension import _query_frontier_tile

    cache = _region_cache(profiles, dead_profiles, anchor_costs, tmp_path)
    capex, opex, coc, keys = query_inputs
    _, results, _ = _query_frontier_tile(np.array([0]), cache, capex, opex, coc, keys, 500.0, 25)
    r = results[0]

    assert r["lcoe_coverage_based"] == pytest.approx(r["lcoe"] * r["served_fraction"])
    assert r["lcoe_coverage_based"] <= r["lcoe"], "serving every hour cannot cost more"


def test_query_output_is_baseload_invariant_in_lcoe_but_not_in_cost(
    profiles, dead_profiles, anchor_costs, tmp_path, query_inputs
):
    """
    LCOE is exactly baseload-invariant and the design is dimensionless, so both must be
    identical at any baseload. The installation cost is not -- it is what the baseload buys.
    """
    from boa.model.global_extension import _query_frontier_tile

    cache = _region_cache(profiles, dead_profiles, anchor_costs, tmp_path)
    capex, opex, coc, keys = query_inputs
    _, small, _ = _query_frontier_tile(np.array([0]), cache, capex, opex, coc, keys, 100.0, 25)
    _, large, _ = _query_frontier_tile(np.array([0]), cache, capex, opex, coc, keys, 1000.0, 25)

    assert small[0]["lcoe"] == pytest.approx(large[0]["lcoe"], rel=1e-12)
    assert small[0]["design"] == large[0]["design"]
    assert large[0]["installation_cost"] == pytest.approx(10.0 * small[0]["installation_cost"], rel=1e-9)


def test_the_query_reads_no_capacity_ceiling():
    """
    Pins the M3-to-M4 window rather than hiding it: the ceiling is deliberately not applied
    yet, so this asserts the worker takes no capacity argument. When Grid 2 lands the
    constrained search arrives through a sidecar, not by growing a parameter here.
    """
    import inspect

    from boa.model.global_extension import _query_frontier_tile

    params = set(inspect.signature(_query_frontier_tile).parameters)
    assert not params & {"pv_max", "wind_max", "limit", "max_cap"}
