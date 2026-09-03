"""
Contracts for the schema v3 frontier store.

Two themes run through this file. The first is *refusal*: the v2 cache validated only its
schema version, so changing a default silently reused an incompatible store, and the tests
here pin that a disagreement raises rather than warns. The second is *direction*: the two
fraction arrays are quantised to uint16, and for `energy_served_frac` -- the LCOE
denominator -- rounding the wrong way understates cost and can prune the cell holding the
true optimum. Neither would fail loudly anywhere else.
"""

import dataclasses

import numpy as np
import pytest

from _gate import require

require(
    "boa.model.frontier_cache",
    "RegionFrontierCache",
    "build_frontier_meta",
    "fraction_to_uint16_floor",
    "frontier_at",
    "frontier_cache_path",
    "params_hash",
    "read_frontier_cache",
    "stack_pixel_frontiers",
    "write_frontier_cache",
)

from boa.model.bisection import SearchParams, argmin_lcoe, build_pixel_frontier  # noqa: E402
from boa.model.frontier_cache import (  # noqa: E402
    FRONTIER_SCHEMA_VERSION,
    POINT_CHUNK,
    build_frontier_meta,
    expected_patch_shape,
    fraction_to_uint16_floor,
    fraction_to_uint16_nearest,
    frontier_at,
    frontier_cache_path,
    params_hash,
    read_frontier_cache,
    stack_pixel_frontiers,
    uint16_to_fraction,
    write_frontier_cache,
)

PARAMS = SearchParams()
COVERAGE = 0.85
WEATHER_YEAR = 2024
ERA5_RES = 0.25


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _one_pixel_cache(profiles, anchor_costs, params=PARAMS, coverage=COVERAGE):
    """A single-point region store, built from the shared synthetic profiles."""
    frontier = build_pixel_frontier(profiles["solar"], profiles["wind"], coverage, params, anchor_costs)
    meta = build_frontier_meta("TESTREGION", 1, coverage, params, WEATHER_YEAR, ERA5_RES)
    cache = stack_pixel_frontiers(
        [frontier],
        region="TESTREGION",
        all_lats=np.array([10.0, 10.25], dtype=np.float32),
        all_lons=np.array([20.0, 20.25], dtype=np.float32),
        lats=np.array([10.0], dtype=np.float32),
        lons=np.array([20.0], dtype=np.float32),
        iy=np.array([0], dtype=np.int32),
        ix=np.array([0], dtype=np.int32),
        meta=meta,
    )
    return frontier, cache


# --------------------------------------------------------------------------
# Path and identity
# --------------------------------------------------------------------------


def test_path_is_deterministic_and_carries_no_baseload(tmp_path):
    first = frontier_cache_path(tmp_path, "EUROPE", COVERAGE, PARAMS, WEATHER_YEAR, ERA5_RES)
    second = frontier_cache_path(tmp_path, "EUROPE", COVERAGE, PARAMS, WEATHER_YEAR, ERA5_RES)
    assert first == second
    # The store is baseload-independent, so no <baseload>MW level may appear in the path.
    assert "MW" not in str(first)
    assert first.parts[-3] == f"cov{COVERAGE:g}"
    assert first.parts[-2] == "EUROPE"


def test_every_search_param_changes_the_path():
    """
    A field that does not reach the hash is a field that can change while a stale store is
    silently reused -- the exact v2 defect. Cheaper to assert here than to discover later.
    """
    base = frontier_cache_path("/c", "R", COVERAGE, PARAMS, WEATHER_YEAR, ERA5_RES)
    for name in (f.name for f in dataclasses.fields(PARAMS)):
        current = getattr(PARAMS, name)
        moved = current + (1 if isinstance(current, int) else 0.5)
        altered = dataclasses.replace(PARAMS, **{name: moved})
        assert frontier_cache_path("/c", "R", COVERAGE, PARAMS, WEATHER_YEAR, ERA5_RES) == base
        assert frontier_cache_path("/c", "R", COVERAGE, altered, WEATHER_YEAR, ERA5_RES) != base, name


def test_the_path_moves_when_the_overscale_constant_moves(monkeypatch):
    """
    `OVERSCALE_SAMPLING_K` sets `mu = k / CF`, which sets the search box, which changes every
    value in the store -- but it is not a `SearchParams` field, so a digest over the dataclass
    alone would not move with it and a changed constant would silently reuse an incompatible
    store. That is the defect v2 had, and it is why this hash delegates to `identity_hash`.
    """
    import boa.model.bisection as bisection

    before = frontier_cache_path("/c", "R", COVERAGE, PARAMS, WEATHER_YEAR, ERA5_RES)
    monkeypatch.setattr(bisection, "OVERSCALE_SAMPLING_K", {**bisection.OVERSCALE_SAMPLING_K, "solar": 99.0})
    assert frontier_cache_path("/c", "R", COVERAGE, PARAMS, WEATHER_YEAR, ERA5_RES) != before


def test_params_hash_is_stable_across_processes():
    """
    Python's `hash()` is salted per interpreter run, so a store keyed on it would land at a
    different path every time. Pin the digest to a literal.
    """
    assert params_hash(PARAMS) == params_hash(dataclasses.replace(PARAMS))
    assert len(params_hash(PARAMS)) == 8
    assert params_hash(PARAMS) == params_hash(SearchParams())


def test_coverage_and_weather_year_separate_stores(tmp_path):
    a = frontier_cache_path(tmp_path, "R", 0.85, PARAMS, 2024, ERA5_RES)
    b = frontier_cache_path(tmp_path, "R", 0.95, PARAMS, 2024, ERA5_RES)
    c = frontier_cache_path(tmp_path, "R", 0.85, PARAMS, 2023, ERA5_RES)
    assert len({a, b, c}) == 3


# --------------------------------------------------------------------------
# The ceiling is not in the store (D4)
# --------------------------------------------------------------------------


def test_store_holds_no_capacity_arrays(tmp_path, profiles, anchor_costs):
    """
    Structural, and the point of D4: the store must depend on no land-availability
    assumption, so that two layer sets can share it and be compared against the same
    physics. A `pv_max` here would silently reintroduce the coupling.
    """
    import zarr

    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    names = set(zarr.open_group(str(path), mode="r").array_keys())
    assert not names & {"pv_max", "wind_max", "max_cap", "pv_ceiling", "wind_ceiling"}


def test_cache_dataclass_exposes_no_ceiling_field():
    from boa.model.frontier_cache import RegionFrontierCache

    fields = {f.name for f in dataclasses.fields(RegionFrontierCache)}
    assert not fields & {"pv_max", "wind_max"}


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_round_trip_preserves_every_array(tmp_path, profiles, anchor_costs):
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    back = read_frontier_cache(path, PARAMS, COVERAGE, WEATHER_YEAR)

    for name in ("lats", "lons", "iy", "ix", "status", "n_patches", "box_widenings"):
        np.testing.assert_array_equal(getattr(back, name), getattr(cache, name))
    for name in ("s_coarse", "w_coarse", "s_patch", "w_patch", "b_patch"):
        np.testing.assert_allclose(getattr(back, name), getattr(cache, name), rtol=0, atol=0)
    # float16 with infinities where a node is infeasible: equal_nan covers neither, so
    # compare exactly and let inf == inf hold.
    np.testing.assert_array_equal(back.b_coarse, cache.b_coarse)
    np.testing.assert_array_equal(back.energy_served_frac, cache.energy_served_frac)
    np.testing.assert_array_equal(back.patch_bounds, cache.patch_bounds)
    np.testing.assert_array_equal(back.patch_points, cache.patch_points)


def test_patch_arrays_have_the_padded_shape_the_parameters_imply(profiles, anchor_costs):
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    assert cache.b_patch.shape[1:] == expected_patch_shape(PARAMS)
    assert cache.energy_served_frac.shape == cache.b_patch.shape
    assert cache.hours_covered_frac.shape == cache.b_patch.shape


def test_a_frontier_read_back_prices_the_same_optimum(tmp_path, profiles, anchor_costs):
    """
    The store is only useful if what comes out of it is interchangeable with what went in.
    Quantisation moves the served fraction by at most 1.5e-5, so LCOE may differ in the
    fifth decimal but the chosen design must not move.
    """
    frontier, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    restored = frontier_at(read_frontier_cache(path, PARAMS, COVERAGE, WEATHER_YEAR), 0)

    before = argmin_lcoe(frontier, anchor_costs)
    after = argmin_lcoe(restored, anchor_costs)
    assert (after.solar, after.wind) == pytest.approx((before.solar, before.wind))
    assert after.lcoe == pytest.approx(before.lcoe, rel=1e-4)


def test_write_is_atomic_and_leaves_no_temporary(tmp_path, profiles, anchor_costs):
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    assert path.exists()
    assert not (tmp_path / "store.zarr.tmp").exists()
    # Rebuild semantics: writing again replaces rather than failing or merging.
    again = write_frontier_cache(cache, tmp_path / "store.zarr")
    assert again == path


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_chunks_run_along_point_and_never_split_a_patch_axis(tmp_path, profiles, anchor_costs):
    """
    The padding is ~5/6 of a patch slot. Chunking along `point` keeps that a storage cost;
    a chunk that held part of a patch would make a query read several chunks to run one
    argmin, turning the padding into a query cost.
    """
    import zarr

    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    g = zarr.open_group(str(path), mode="r")
    for name in ("b_patch", "energy_served_frac", "hours_covered_frac", "b_coarse"):
        arr = g[name]
        assert arr.chunks[0] == min(POINT_CHUNK, arr.shape[0]), name
        assert arr.chunks[1:] == arr.shape[1:], name


# --------------------------------------------------------------------------
# Quantisation direction
# --------------------------------------------------------------------------


def test_energy_served_fraction_only_ever_rounds_down():
    """
    It is the LCOE denominator. Rounding it up understates LCOE, and an understated
    incumbent can prune the coarse cell that holds the true optimum -- wrong, not merely
    imprecise. Flooring can only overstate cost.
    """
    values = np.array([0.0, 0.5, 0.98, 0.999999, 1.0])
    back = uint16_to_fraction(fraction_to_uint16_floor(values))
    assert np.all(back <= values + 1e-12)
    assert np.all(values - back < 1.0 / 65535.0)


def test_hours_covered_fraction_rounds_to_nearest():
    """Reported, never ranked on, so no direction is safer -- take the smaller error."""
    values = np.array([0.0, 0.123456, 0.98, 1.0])
    back = uint16_to_fraction(fraction_to_uint16_nearest(values))
    np.testing.assert_allclose(back, values, atol=0.5 / 65535.0)


def test_quantisation_keeps_the_endpoints_exact():
    for quantise in (fraction_to_uint16_floor, fraction_to_uint16_nearest):
        assert uint16_to_fraction(quantise(np.array([0.0])))[0] == 0.0
        assert uint16_to_fraction(quantise(np.array([1.0])))[0] == 1.0


# --------------------------------------------------------------------------
# Refusal
# --------------------------------------------------------------------------


def test_read_refuses_a_store_built_with_different_search_params(tmp_path, profiles, anchor_costs):
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    other = dataclasses.replace(PARAMS, patch_grid=PARAMS.patch_grid + 2)
    with pytest.raises(ValueError, match="different search parameters"):
        read_frontier_cache(path, other, COVERAGE, WEATHER_YEAR)


def test_the_refusal_names_the_field_that_moved(tmp_path, profiles, anchor_costs):
    """A rebuild is cheap; finding out which default moved is not. Say it in the message."""
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    other = dataclasses.replace(PARAMS, ladder_rungs=PARAMS.ladder_rungs + 1)
    with pytest.raises(ValueError, match="ladder_rungs"):
        read_frontier_cache(path, other, COVERAGE, WEATHER_YEAR)


def test_read_refuses_a_different_coverage_or_weather_year(tmp_path, profiles, anchor_costs):
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    with pytest.raises(ValueError, match="coverage"):
        read_frontier_cache(path, PARAMS, 0.95, WEATHER_YEAR)
    with pytest.raises(ValueError, match="weather year"):
        read_frontier_cache(path, PARAMS, COVERAGE, 2023)


def test_read_refuses_a_foreign_schema_version(tmp_path, profiles, anchor_costs):
    import zarr

    _, cache = _one_pixel_cache(profiles, anchor_costs)
    path = write_frontier_cache(cache, tmp_path / "store.zarr")
    g = zarr.open_group(str(path), mode="a")
    meta = dict(g.attrs["meta"])
    meta["schema_version"] = FRONTIER_SCHEMA_VERSION - 1
    g.attrs["meta"] = meta
    with pytest.raises(ValueError, match="schema version"):
        read_frontier_cache(path, PARAMS, COVERAGE, WEATHER_YEAR)


def test_missing_store_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_frontier_cache(tmp_path / "absent.zarr", PARAMS, COVERAGE, WEATHER_YEAR)


def test_meta_records_what_built_it(profiles, anchor_costs):
    _, cache = _one_pixel_cache(profiles, anchor_costs)
    meta = cache.meta
    assert meta["schema_version"] == FRONTIER_SCHEMA_VERSION
    assert meta["search_params_hash"] == params_hash(PARAMS)
    assert meta["run_params"]["coverage"] == COVERAGE
    # Stored field by field as well as hashed, so a mismatch can name the field.
    assert meta["search_params"]["patch_grid"] == PARAMS.patch_grid
