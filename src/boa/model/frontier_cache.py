"""
Frontier cache (schema v3): the region-level store for the grid-bisection search.

One Zarr store per region:

    <cache_dir>/cov<coverage>/<REGION>/gbs_g{Gc}p{Gp}r{R}_{hash8}_y{year}_r{res}.zarr/

What it holds and why that shape
--------------------------------
Dispatch is expensive, pricing is cheap, and only dispatch is year-independent. Finding
`b_min` at one `(solar, wind)` point costs ten to fifteen simulations of a full year of
battery state of charge; pricing a design costs four multiplications. So this store holds
the dispatch results and lets every query year replay them against its own prices. A
36-year sweep simulates the physics once and prices it 36 times.

Everything is dimensionless. A design is three overscale factors against a demand
normalised to 1, so `b = 4` means "four hours of demand in storage" and one store serves
every baseload -- LCOE is exactly baseload-invariant, which is an algebraic identity
rather than an approximation.

Fixed shape, not CSR
--------------------
The v2 cache had to be ragged: Monte Carlo produced a variable number of *surviving*
designs per pixel. A grid always produces exactly `Gc^2` coarse and `K x P x P x R` patch
values, so the arrays are dense and padded to the widest patch `SearchParams` allows.
Direct indexing, no offset indirection, no 2^31 row cap.

The padding is large -- roughly five sixths of a typical slot -- because patches sit on a
shared lattice with fixed spacing, so a wide patch has more points than a narrow one and
the allocation is sized for the widest. It is zero-filled and compresses away. **Chunk
along `point`, never across the patch axes**: that is what keeps the padding a storage
detail rather than a query cost. Every consumer must mask by `patch_points`, since an
unmasked zero prices as a free design and wins the argmin outright.

No capacity ceiling
-------------------
There is deliberately no `pv_max`/`wind_max` here, unlike the v2 cache. The search never
reads a ceiling -- the box comes from capacity factors, the point set from the ERA5
land-sea mask -- so this store depends on no land-availability assumption, and revising
the availability layers does not invalidate it. That is what lets two layer sets share one
store and be compared against literally the same physics bytes. The ceiling is read from
the max-capacity store at query time; that read must raise rather than fall back, because
it is now the only thing keeping a stale ceiling out.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import zarr

from boa.model.bisection import PixelFrontier, SearchParams, max_patch_points


FRONTIER_SCHEMA_VERSION = 3

# Points per chunk on the leading axis. The largest array is ~51 kB per point at default
# parameters, so 32 points is a few megabytes -- large enough that compression works and
# small enough to stay well inside memory. The access pattern is a full-region sweep, not
# a random single-pixel read, so a chunk wider than one point costs nothing in practice.
POINT_CHUNK = 32

# uint16 fixed point for the two fraction arrays. They live in [0, 1] and sit near 0.98,
# where differences of 1e-3 change the answer; `value / 65535` gives 1.5e-5 absolute error.
# float16 would give ~1e-3 *relative* near 1.0 -- too coarse. So uint16 is both smaller and
# more accurate here, which is unusual enough to be worth stating.
_FRACTION_SCALE = 65535.0


# ----- parameter identity -----------------------------------------------------


def params_hash(params: SearchParams) -> str:
    """
    Stable 8-hex digest identifying everything that determines a store's contents.

    Delegates to `SearchParams.identity_hash` rather than hashing the dataclass alone. That
    distinction is load-bearing: `identity_hash` also folds in `OVERSCALE_SAMPLING_K`, which
    sets `mu = k / CF` and therefore the search box, and therefore every value in the store.
    A digest over the dataclass by itself would not move when `k` did, so a changed constant
    would silently reuse an incompatible store -- the exact defect v2 had.

    Stable across processes, so `hash()` is not usable: it is salted per interpreter run and
    would send the same parameters to a different path every time.
    """
    return params.identity_hash()


def frontier_cache_path(
    cache_dir: Path,
    region: str,
    coverage: float,
    params: SearchParams,
    weather_year: int,
    era5_resolution_deg: float,
) -> Path:
    """
    Resolve a store's path from its parameters, deterministically.

    The grid sizes are spelled out in the name as well as hashed, because they are what a
    human comparing two stores actually wants to see; `hash8` covers the rest of
    `SearchParams` so no field can change without changing the path.

    No baseload level: the store is baseload-independent. The capacity ceiling is not in
    the path either, because it is not in the store.
    """
    grids = f"g{params.coarse_grid}p{params.patch_grid}r{params.ladder_rungs}"
    rest = f"gbs_{grids}_{params_hash(params)}_y{int(weather_year)}_r{round(era5_resolution_deg * 100):03d}"
    return Path(cache_dir) / f"cov{coverage:g}" / region / f"{rest}.zarr"


def frontier_cache_exists(
    cache_dir: Path,
    region: str,
    coverage: float,
    params: SearchParams,
    weather_year: int,
    era5_resolution_deg: float,
) -> bool:
    """True if the store exists on disk. Does not validate its contents."""
    return frontier_cache_path(cache_dir, region, coverage, params, weather_year, era5_resolution_deg).exists()


# ----- fraction quantisation --------------------------------------------------


def fraction_to_uint16_floor(values: np.ndarray) -> np.ndarray:
    """
    Quantise a fraction downward.

    For `energy_served_frac` the direction is not cosmetic. It is the LCOE denominator, so
    rounding it up understates LCOE, and an understated incumbent can prune the coarse cell
    that holds the true optimum -- a silently wrong answer rather than a conservative one.
    Flooring can only overstate cost, which costs the containment certificate a little
    slack. Same asymmetry that rounds `b_coarse` toward zero.
    """
    scaled = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0) * _FRACTION_SCALE
    return np.floor(scaled).astype(np.uint16)


def fraction_to_uint16_nearest(values: np.ndarray) -> np.ndarray:
    """
    Quantise a fraction to nearest.

    For `hours_covered_frac`, which is reported but never ranked on, no direction is safer
    than the other, so take the smaller error.
    """
    scaled = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0) * _FRACTION_SCALE
    return np.rint(scaled).astype(np.uint16)


def uint16_to_fraction(values: np.ndarray) -> np.ndarray:
    """Inverse of the two quantisers above."""
    return np.asarray(values, dtype=np.float64) / _FRACTION_SCALE


# ----- in-memory representation ----------------------------------------------


@dataclass
class RegionFrontierCache:
    """
    One region's frontiers, stacked on a leading `point` axis.

    Shapes below use `Gc = coarse_grid`, `K = max_patch_slots`, `P = max_patch_points(params)`
    and `R = ladder_rungs`. Every patch array is allocated at full `K` and `P`; only the
    first `n_patches` slots, and within each slot only `patch_points[k, slot]` rows and
    columns, hold real values.
    """

    region: str

    # Geometry. `all_lats`/`all_lons` are the full region grid so the query path can
    # assemble an output NetCDF without reopening the profile dataset; `iy`/`ix` scatter
    # the land points back onto it.
    all_lats: np.ndarray  # (n_lat,) float32
    all_lons: np.ndarray  # (n_lon,) float32
    lats: np.ndarray  # (npts,) float32
    lons: np.ndarray  # (npts,) float32
    iy: np.ndarray  # (npts,) int32
    ix: np.ndarray  # (npts,) int32

    # Per-pixel verdicts.
    status: np.ndarray  # (npts,) int8
    n_patches: np.ndarray  # (npts,) int8
    box_widenings: np.ndarray  # (npts,) int8

    # Coarse tier: a *lower bound* on b_min, rounded toward zero, `inf` where infeasible.
    s_coarse: np.ndarray  # (npts, Gc) float32
    w_coarse: np.ndarray  # (npts, Gc) float32
    b_coarse: np.ndarray  # (npts, Gc, Gc) float16

    # Patch tier.
    s_patch: np.ndarray  # (npts, K, P) float32
    w_patch: np.ndarray  # (npts, K, P) float32
    b_patch: np.ndarray  # (npts, K, P, P, R) float32
    energy_served_frac: np.ndarray  # (npts, K, P, P, R) uint16 -- the LCOE denominator
    hours_covered_frac: np.ndarray  # (npts, K, P, P, R) uint16 -- feasibility, reported only
    patch_bounds: np.ndarray  # (npts, K, 4) int32 -- coarse cells (i0, i1, j0, j1)
    patch_points: np.ndarray  # (npts, K, 2) int32 -- real extent (n_s, n_w) per slot

    meta: dict = field(default_factory=dict)

    @property
    def n_points(self) -> int:
        return int(self.lats.shape[0])


def stack_pixel_frontiers(
    frontiers: list[PixelFrontier],
    region: str,
    all_lats: np.ndarray,
    all_lons: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    iy: np.ndarray,
    ix: np.ndarray,
    meta: dict,
) -> RegionFrontierCache:
    """
    Stack per-pixel frontiers into the region store's arrays.

    A plain stack, because `build_pixel_frontier` already allocates every patch array at
    full `(K, P)` -- that is the point of the padding. The two fraction arrays are
    quantised here, each in its own direction (see `fraction_to_uint16_floor`).
    """
    if len(frontiers) != len(lats):
        raise ValueError(f"{len(frontiers)} frontiers for {len(lats)} points")

    return RegionFrontierCache(
        region=region,
        all_lats=np.asarray(all_lats, dtype=np.float32),
        all_lons=np.asarray(all_lons, dtype=np.float32),
        lats=np.asarray(lats, dtype=np.float32),
        lons=np.asarray(lons, dtype=np.float32),
        iy=np.asarray(iy, dtype=np.int32),
        ix=np.asarray(ix, dtype=np.int32),
        status=np.array([f.status for f in frontiers], dtype=np.int8),
        n_patches=np.array([f.n_patches for f in frontiers], dtype=np.int8),
        box_widenings=np.array([f.box_widenings for f in frontiers], dtype=np.int8),
        s_coarse=np.stack([f.s_coarse for f in frontiers]).astype(np.float32),
        w_coarse=np.stack([f.w_coarse for f in frontiers]).astype(np.float32),
        b_coarse=np.stack([f.b_coarse for f in frontiers]).astype(np.float16),
        s_patch=np.stack([f.s_patch for f in frontiers]).astype(np.float32),
        w_patch=np.stack([f.w_patch for f in frontiers]).astype(np.float32),
        b_patch=np.stack([f.b_patch for f in frontiers]).astype(np.float32),
        energy_served_frac=fraction_to_uint16_floor(np.stack([f.energy_served_frac for f in frontiers])),
        hours_covered_frac=fraction_to_uint16_nearest(np.stack([f.hours_covered_frac for f in frontiers])),
        patch_bounds=np.stack([f.patch_bounds for f in frontiers]).astype(np.int32),
        patch_points=np.stack([f.patch_points for f in frontiers]).astype(np.int32),
        meta=meta,
    )


# ----- meta -------------------------------------------------------------------


def _git_sha_short() -> str:
    """Current commit hash for provenance; empty if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def build_frontier_meta(
    region: str,
    n_points: int,
    coverage: float,
    params: SearchParams,
    weather_year: int,
    era5_resolution_deg: float,
) -> dict:
    """
    Assemble `group.attrs["meta"]`.

    `search_params` is stored field by field rather than only as a hash, so a mismatch can
    say *which* field moved instead of only that something did. `run_params` carries what
    is not a `SearchParams` field but still determines the contents.
    """
    return {
        "schema_version": FRONTIER_SCHEMA_VERSION,
        "region": region,
        "n_points": int(n_points),
        "search_params_hash": params_hash(params),
        "search_params": asdict(params),
        "run_params": {
            "coverage": float(coverage),
            "weather_year": int(weather_year),
            "era5_resolution_deg": float(era5_resolution_deg),
        },
        "built_at": datetime.now(timezone.utc).isoformat(),
        "code_git_sha": _git_sha_short(),
    }


# ----- write / read -----------------------------------------------------------


def _chunks_for(shape: tuple[int, ...]) -> tuple[int, ...]:
    """
    Chunk along `point` only, at `POINT_CHUNK` points, with every other axis whole.

    Splitting a patch axis is what would turn the padding from a storage detail into a
    query cost: a query needs a whole patch to run its argmin, so a chunk holding part of
    one buys nothing and costs an extra read.
    """
    return (min(POINT_CHUNK, max(1, shape[0])),) + shape[1:]


def write_frontier_cache(cache: RegionFrontierCache, path: Path) -> Path:
    """
    Atomic write: build into `<path>.tmp`, then swap. A pre-existing store is replaced
    (rebuild semantics) and a stale `.tmp` from a crashed run is wiped on entry.
    """
    final = Path(path)
    tmp = final.parent / f"{final.name}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    final.parent.mkdir(parents=True, exist_ok=True)

    g = zarr.open_group(str(tmp), mode="w")

    # Region grid coordinates: one chunk each, they are small and always read whole.
    grid_arrays: tuple[tuple[str, np.ndarray, type], ...] = (
        ("all_lats", cache.all_lats, np.float32),
        ("all_lons", cache.all_lons, np.float32),
    )
    for name, values, dtype in grid_arrays:
        arr: np.ndarray = np.ascontiguousarray(values, dtype=dtype)
        g.create_array(name, shape=arr.shape, dtype=dtype)[:] = arr

    point_arrays: tuple[tuple[str, np.ndarray, type], ...] = (
        ("lats", cache.lats, np.float32),
        ("lons", cache.lons, np.float32),
        ("iy", cache.iy, np.int32),
        ("ix", cache.ix, np.int32),
        ("status", cache.status, np.int8),
        ("n_patches", cache.n_patches, np.int8),
        ("box_widenings", cache.box_widenings, np.int8),
        ("s_coarse", cache.s_coarse, np.float32),
        ("w_coarse", cache.w_coarse, np.float32),
        ("b_coarse", cache.b_coarse, np.float16),
        ("s_patch", cache.s_patch, np.float32),
        ("w_patch", cache.w_patch, np.float32),
        ("b_patch", cache.b_patch, np.float32),
        ("energy_served_frac", cache.energy_served_frac, np.uint16),
        ("hours_covered_frac", cache.hours_covered_frac, np.uint16),
        ("patch_bounds", cache.patch_bounds, np.int32),
        ("patch_points", cache.patch_points, np.int32),
    )
    for name, values, dtype in point_arrays:
        arr = np.ascontiguousarray(values, dtype=dtype)
        g.create_array(name, shape=arr.shape, dtype=dtype, chunks=_chunks_for(arr.shape))[:] = arr

    g.attrs["meta"] = cache.meta

    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return final


def _check_stored_params(path: Path, meta: dict, expected: SearchParams) -> None:
    """
    Refuse a store built under different search parameters, naming the fields that moved.

    The v2 cache validated only `schema_version`, so changing a default silently reused an
    incompatible store. Every `SearchParams` field changes what a node holds, so any
    disagreement is a rebuild, not a warning.
    """
    stored = meta.get("search_params")
    if not isinstance(stored, dict):
        raise ValueError(f"frontier cache at {path} carries no search_params; delete it and rebuild.")
    wanted = asdict(expected)
    differing = sorted(
        key for key in set(stored) | set(wanted) if stored.get(key, "<missing>") != wanted.get(key, "<missing>")
    )
    if differing:
        detail = ", ".join(
            f"{k}: stored {stored.get(k, '<missing>')!r} != expected {wanted.get(k, '<missing>')!r}" for k in differing
        )
        raise ValueError(f"frontier cache at {path} was built with different search parameters ({detail}); rebuild it.")


def _check_run_params(path: Path, meta: dict, coverage: float | None, weather_year: int | None) -> None:
    """Same refusal for the run-level parameters, which are not `SearchParams` fields."""
    stored = meta.get("run_params")
    if not isinstance(stored, dict):
        raise ValueError(f"frontier cache at {path} carries no run_params; delete it and rebuild.")
    if coverage is not None and float(stored.get("coverage", float("nan"))) != float(coverage):
        raise ValueError(
            f"frontier cache at {path} was built at coverage {stored.get('coverage')!r}, not {coverage!r}; rebuild it."
        )
    if weather_year is not None and int(stored.get("weather_year", -1)) != int(weather_year):
        raise ValueError(
            f"frontier cache at {path} was built for weather year {stored.get('weather_year')!r}, "
            f"not {weather_year!r}; rebuild it."
        )


def _read_array(g: zarr.Group, name: str) -> np.ndarray:
    """
    Read one array out of the store.

    Checked rather than indexed straight through: a `zarr.Group` where an array belongs
    means the store is malformed, and the resulting error should name the array rather
    than surface later as a shape mismatch.
    """
    node = g[name]
    if not isinstance(node, zarr.Array):
        raise ValueError(f"frontier cache entry {name!r} is a {type(node).__name__}, not an array")
    return np.asarray(node[:])


def read_frontier_cache(
    path: Path,
    expected_params: SearchParams | None = None,
    coverage: float | None = None,
    weather_year: int | None = None,
) -> RegionFrontierCache:
    """
    Load a frontier cache, refusing anything that does not match what was asked for.

    Pass `expected_params` (and, where known, `coverage` and `weather_year`) at every
    production call site. They are optional only so a diagnostic can open a store whose
    parameters it does not yet know.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"frontier cache missing: {path}")
    g = zarr.open_group(str(path), mode="r")
    raw_meta = g.attrs.get("meta", {})
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}

    version = meta.get("schema_version")
    if version != FRONTIER_SCHEMA_VERSION:
        # No migration exists and none is planned: v2 held Monte Carlo designs, which have
        # no counterpart here. Refuse rather than risk a wrong answer.
        raise ValueError(
            f"frontier cache at {path} has schema version {version}; this code requires "
            f"v{FRONTIER_SCHEMA_VERSION}. Delete the cache and rebuild."
        )
    if expected_params is not None:
        _check_stored_params(path, meta, expected_params)
    if coverage is not None or weather_year is not None:
        _check_run_params(path, meta, coverage, weather_year)

    return RegionFrontierCache(
        region=str(meta.get("region", path.parent.name)),
        all_lats=_read_array(g, "all_lats"),
        all_lons=_read_array(g, "all_lons"),
        lats=_read_array(g, "lats"),
        lons=_read_array(g, "lons"),
        iy=_read_array(g, "iy"),
        ix=_read_array(g, "ix"),
        status=_read_array(g, "status"),
        n_patches=_read_array(g, "n_patches"),
        box_widenings=_read_array(g, "box_widenings"),
        s_coarse=_read_array(g, "s_coarse"),
        w_coarse=_read_array(g, "w_coarse"),
        b_coarse=_read_array(g, "b_coarse"),
        s_patch=_read_array(g, "s_patch"),
        w_patch=_read_array(g, "w_patch"),
        b_patch=_read_array(g, "b_patch"),
        energy_served_frac=_read_array(g, "energy_served_frac"),
        hours_covered_frac=_read_array(g, "hours_covered_frac"),
        patch_bounds=_read_array(g, "patch_bounds"),
        patch_points=_read_array(g, "patch_points"),
        meta=meta,
    )


def frontier_at(cache: RegionFrontierCache, k: int) -> PixelFrontier:
    """
    Rebuild one pixel's `PixelFrontier` from the store.

    The fraction arrays come back as floats, so a frontier read from disk is
    interchangeable with one straight out of `build_pixel_frontier` apart from the
    quantisation. `argmin_lcoe` takes it unchanged.
    """
    return PixelFrontier(
        status=int(cache.status[k]),
        n_patches=int(cache.n_patches[k]),
        box_widenings=int(cache.box_widenings[k]),
        s_coarse=np.asarray(cache.s_coarse[k]),
        w_coarse=np.asarray(cache.w_coarse[k]),
        b_coarse=np.asarray(cache.b_coarse[k]),
        s_patch=np.asarray(cache.s_patch[k]),
        w_patch=np.asarray(cache.w_patch[k]),
        b_patch=np.asarray(cache.b_patch[k]),
        energy_served_frac=uint16_to_fraction(cache.energy_served_frac[k]),
        hours_covered_frac=uint16_to_fraction(cache.hours_covered_frac[k]),
        patch_bounds=np.asarray(cache.patch_bounds[k]),
        patch_points=np.asarray(cache.patch_points[k]),
    )


def expected_patch_shape(params: SearchParams) -> tuple[int, int, int, int]:
    """`(K, P, P, R)`, the padded patch allocation the parameters imply."""
    p = max_patch_points(params)
    return (params.max_patch_slots, p, p, params.ladder_rungs)
