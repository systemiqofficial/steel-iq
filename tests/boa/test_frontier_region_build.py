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
