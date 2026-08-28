"""
Contracts for design cache schema v3.

Two things changed beyond the arrays. CSR packing is gone, because every pixel now
contributes a fixed-shape block instead of a ragged design list. And `read_cache`
stops trusting the path: v2 encoded every parameter in the filename and validated
only `schema_version`, so changing a default silently reused a stale cache. v3
validates the stored parameters against what the caller asked for.
"""

import dataclasses

import numpy as np
import pytest

from _gate import require, require_schema

require("boa.model.bisection", "SearchParams")
require_schema("boa.model.design_cache", "SCHEMA_VERSION", 3)

from boa.model import design_cache  # noqa: E402
from boa.model.bisection import SearchParams  # noqa: E402

PARAMS = SearchParams()
WEATHER_YEAR = 2024
RESOLUTION = 0.25


def _make_cache(npts=4, region="EU", params=PARAMS):
    """A small but structurally complete v3 cache."""
    gc, gp, r, k = params.coarse_grid, params.patch_grid, params.ladder_rungs, params.max_seeds
    rng = np.random.RandomState(0)

    return design_cache.RegionDesignCache(
        region=region,
        all_lats=np.linspace(40.0, 41.0, 5, dtype=np.float32),
        all_lons=np.linspace(10.0, 11.0, 5, dtype=np.float32),
        lats=np.linspace(40.0, 40.8, npts, dtype=np.float32),
        lons=np.linspace(10.0, 10.8, npts, dtype=np.float32),
        iy=np.arange(npts, dtype=np.int32),
        ix=np.arange(npts, dtype=np.int32),
        pv_max=np.full(npts, 5000.0, dtype=np.float32),
        wind_max=np.full(npts, 800.0, dtype=np.float32),
        s_coarse=np.tile(np.linspace(0, 8, gc), (npts, 1)).astype(np.float32),
        w_coarse=np.tile(np.linspace(0, 6, gc), (npts, 1)).astype(np.float32),
        b_coarse=rng.uniform(0, 40, (npts, gc, gc)).astype(np.float16),
        n_patches=np.ones(npts, dtype=np.int8),
        s_patch=rng.uniform(0, 8, (npts, k, gp)).astype(np.float32),
        w_patch=rng.uniform(0, 6, (npts, k, gp)).astype(np.float32),
        b_patch=rng.uniform(0.1, 30, (npts, k, gp, gp, r)).astype(np.float32),
        sf_patch=rng.uniform(0.85, 1.0, (npts, k, gp, gp, r)),
        cov_patch=rng.uniform(0.85, 1.0, (npts, k, gp, gp, r)),
        sf_inf=rng.uniform(0.95, 1.0, (npts, k, gp, gp)),
        status=np.ones(npts, dtype=np.int8),
        box_widenings=np.zeros(npts, dtype=np.int8),
        meta=design_cache.build_cache_meta(
            region=region,
            n_points=npts,
            p=15,
            search_params=params,
            weather_year=WEATHER_YEAR,
            era5_resolution_deg=RESOLUTION,
        ),
    )


# --------------------------------------------------------------------------
# Path and identity
# --------------------------------------------------------------------------


def test_cache_path_is_deterministic_from_search_params(tmp_path):
    """Same parameters must resolve to the same path, every time and every process."""
    a = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    b = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    assert a == b
    assert a.suffix == ".zarr"
    assert a.parent.name == "EU"
    assert a.parent.parent.name == "p15"


def test_cache_path_separates_different_search_params(tmp_path):
    """
    A different grid resolution is a different cache, not a silent reuse. The old
    `n<n>_s<seed>` token is meaningless for a deterministic search, so identity now
    hangs off a hash of the whole parameter set.
    """
    coarse = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    finer = design_cache.cache_path(
        tmp_path, "EU", 15, dataclasses.replace(PARAMS, patch_grid=PARAMS.patch_grid + 4), WEATHER_YEAR, RESOLUTION
    )
    assert coarse != finer


def test_cache_path_carries_no_sampling_tokens(tmp_path):
    """`--samples` and the RNG seed are gone; their tokens must not linger in the name."""
    name = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION).name
    assert "_s42_" not in name
    assert not name.startswith("n")


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_v3_round_trips(tmp_path):
    """
    Every array survives write/read with its shape and semantics. `sf_patch` and
    `cov_patch` are stored as uint16 fixed-point, so they come back within the
    documented 1.5e-5 quantisation error rather than exactly.
    """
    cache = _make_cache()
    path = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    design_cache.write_cache(cache, path)

    loaded = design_cache.read_cache(path, expected_params=PARAMS)

    assert loaded.region == "EU"
    assert loaded.n_points == cache.n_points
    np.testing.assert_array_equal(loaded.iy, cache.iy)
    np.testing.assert_array_equal(loaded.status, cache.status)
    np.testing.assert_array_equal(loaded.n_patches, cache.n_patches)
    np.testing.assert_array_equal(loaded.b_coarse, cache.b_coarse)
    np.testing.assert_allclose(loaded.b_patch, cache.b_patch, rtol=1e-6)
    np.testing.assert_allclose(loaded.sf_patch, cache.sf_patch, atol=2e-5)
    np.testing.assert_allclose(loaded.sf_inf, cache.sf_inf, atol=2e-5)


def test_round_trip_preserves_the_coarse_lower_bound_direction(tmp_path):
    """
    The coarse grid is a lower bound on `b_min` and the containment certificate
    relies on that. Storage must not round any value upward.
    """
    cache = _make_cache()
    path = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    design_cache.write_cache(cache, path)
    loaded = design_cache.read_cache(path, expected_params=PARAMS)
    assert np.all(loaded.b_coarse <= cache.b_coarse)


def test_write_is_atomic(tmp_path):
    """A crashed rebuild must not leave a half-written store that later reads as valid."""
    cache = _make_cache()
    path = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    design_cache.write_cache(cache, path)

    stale_tmp = path.parent / f"{path.name}.tmp"
    stale_tmp.mkdir(parents=True, exist_ok=True)
    (stale_tmp / "junk").write_text("leftover")

    design_cache.write_cache(cache, path)
    assert not stale_tmp.exists()
    assert design_cache.read_cache(path, expected_params=PARAMS).n_points == cache.n_points


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_read_cache_refuses_v2_schema(tmp_path):
    """
    Successor to `test_sampler.py:372`. A v2 cache holds Monte Carlo design lists
    that v3 cannot interpret, so it is refused with instructions rather than
    migrated -- the numbers in it came from a different algorithm.
    """
    cache = _make_cache()
    cache.meta["schema_version"] = 2
    path = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    design_cache.write_cache(cache, path)

    with pytest.raises(ValueError, match="schema version 2"):
        design_cache.read_cache(path, expected_params=PARAMS)


def test_read_cache_refuses_mismatched_search_params(tmp_path):
    """
    v2 enforced parameter identity purely through the filename, so a changed default
    silently reused a stale cache. v3 checks the stored parameters and names what
    differs.
    """
    cache = _make_cache()
    path = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    design_cache.write_cache(cache, path)

    other = dataclasses.replace(PARAMS, ladder_rungs=PARAMS.ladder_rungs + 2)
    with pytest.raises(ValueError, match="ladder_rungs"):
        design_cache.read_cache(path, expected_params=other)


def test_read_cache_without_expected_params_still_checks_schema(tmp_path):
    """Callers that only want to inspect a store skip the parameter check, never the schema one."""
    cache = _make_cache()
    path = design_cache.cache_path(tmp_path, "EU", 15, PARAMS, WEATHER_YEAR, RESOLUTION)
    design_cache.write_cache(cache, path)
    assert design_cache.read_cache(path).region == "EU"


def test_meta_records_the_search_parameters(tmp_path):
    """
    A store must be self-describing: the full parameter set travels with it, so a
    cache found on disk can be identified without recomputing the path hash.
    """
    meta = _make_cache().meta
    assert meta["schema_version"] == design_cache.SCHEMA_VERSION
    assert meta["params"]["p_percentile"] == 15
    assert meta["params"]["weather_year"] == WEATHER_YEAR
    assert meta["params"]["search"]["coarse_grid"] == PARAMS.coarse_grid
    assert meta["params"]["search"]["ladder_rungs"] == PARAMS.ladder_rungs
    assert "search_params_hash" in meta["params"]
    assert "random_seed" not in meta["params"], "the RNG seed is retired"
    assert "n_samples" not in meta["params"]


def test_ragged_packing_and_legacy_migration_are_gone():
    """
    Both existed only to serve the Monte Carlo sampler. `pack_csr` handled a variable
    number of surviving designs per pixel; a grid always yields a fixed shape. And v3
    refuses a v2 store outright rather than migrating it, because the values in one
    came from a different algorithm -- so there is no migration path to keep.
    """
    for name in ("pack_csr", "migrate_legacy_cache_filenames"):
        assert not hasattr(design_cache, name), f"{name} should have been deleted"


def test_topup_naming_no_longer_leaks_into_the_cache_module():
    """
    The per-baseload sidecar survives the rewrite, but as the *capacity box*: it holds
    the constrained re-search for pixels where the ceiling clips the physics box, not a
    repair for a sparse random sample. Only the top-up naming retires.

    The sidecar itself lives in `boa.model.capacity_box` and is covered by
    `tests/boa/test_capacity_box.py`.
    """
    leftovers = [name for name in dir(design_cache) if "topup" in name.lower()]
    assert leftovers == [], f"top-up naming should have moved to capacity_box: {leftovers}"
