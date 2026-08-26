"""
Design cache: persists the year-independent per-point compute output (surviving
designs + coverage) to disk so future cost-side runs can re-derive LCOE in
minutes instead of hours.

One Zarr store per region:
    inputs/<input_set>/cache_designs/p<p>/<REGION>/<n_seed_year_res>.zarr/

The cache is baseload-independent (schema v2): the sampler's proposal is scaled to
the pixel's capacity factors, and the capacity ceiling is applied as a query-time
mask, so one cache per (region, p, n, seed, weather_year, era5_res) serves every
baseload. Caches for different parameter choices coexist; mismatch -> cache miss
-> rebuild.

Per-region store layout (float32 throughout: the 0.25-degree grid coords are
float32-exact and the design/coverage values don't need float64 precision):
    all_lats         (n_lat,) float32  -- full region grid (lat axis)
    all_lons         (n_lon,) float32  -- full region grid (lon axis)
    lats             (npts,) float32   -- land-point identification (subset of all_lats)
    lons             (npts,) float32
    iy               (npts,) int32     -- index into all_lats per land point
    ix               (npts,) int32     -- index into all_lons per land point
    pv_max           (npts,) float32   -- physical capacity ceiling (MW)
    wind_max         (npts,) float32
    designs_flat     (sum_mp, 3) float32  -- CSR-style ragged; cols: solar/wind/battery
    design_offsets   (npts+1,) int32   -- designs_flat[offsets[k]:offsets[k+1]] is point k
    coverage_flat    (sum_mp,) float32

    group.attrs["meta"] = {schema_version, region, cache_key, n_points,
                           n_designs_total, params {...}, built_at, code_git_sha}
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import zarr


SCHEMA_VERSION = 2


# ----- cache path resolution -------------------------------------------------


def cache_path(
    cache_dir: Path,
    region: str,
    p: int,
    n_samples: int,
    seed: int,
    weather_year: int,
    era5_resolution_deg: float,
) -> Path:
    """
    Resolve a cache's on-disk path from its parameters:

        <cache_dir>/p<p>/<REGION>/n<n>_s<seed>_y<year>_r<res>.zarr/

    No baseload level: v2 caches are baseload-independent (the capacity ceiling is
    a query-time mask). Deterministic from the parameter tuple: same params produce
    the same path.
    """
    rest = f"n{int(n_samples)}_s{int(seed)}_y{int(weather_year)}_r{round(era5_resolution_deg * 100):03d}"
    return Path(cache_dir) / f"p{int(p)}" / region / f"{rest}.zarr"


# Pattern for the legacy v1.0 16-hex-char hash filenames at the top level of the
# cache dir; used by `migrate_legacy_cache_filenames`.
_LEGACY_HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def migrate_legacy_cache_filenames(cache_dir: Path) -> int:
    """
    Move legacy caches into the v1.2 nested folder layout
    `<baseload>MW/p<p>/<REGION>/<rest>.zarr`. Handles two pre-v1.2 formats
    found at the top level of `<cache_dir>`:

      - v1.0: `<REGION>_<16hex>.zarr` (random-looking hash names)
      - v1.1: `<REGION>_<baseload>MW_p<p>_n<n>_s<seed>_y<year>_r<res>.zarr`
        (flat readable names)

    Idempotent: caches already under the nested layout are skipped. Returns
    the number of caches moved.
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    n_moved = 0
    for path in sorted(cache_dir.iterdir()):
        if not path.is_dir() or path.suffix != ".zarr":
            continue
        # Anything at the top level is legacy by definition (v1.2 lives under
        # <baseload>MW/p<p>/<REGION>/...). Read its meta to derive the new path.
        try:
            g = zarr.open_group(str(path), mode="r")
            meta_attr = g.attrs.get("meta", {})
            if not isinstance(meta_attr, dict):
                logging.warning(f"Cache {path.name} has unexpected meta type {type(meta_attr).__name__}; skipping")
                continue
            params = meta_attr.get("params", {})
            region = meta_attr.get("region")
            if region is None:
                logging.warning(f"Cache {path.name} missing region in meta; skipping")
                continue
            # Legacy caches keep the v1.2 <baseload>MW layout; the baseload-free v2 layout is v2-only.
            rest = (
                f"n{int(params['n_samples'])}_s{int(params['random_seed'])}_"
                f"y{int(params['weather_year'])}_r{round(params['era5_resolution_deg'] * 100):03d}"
            )
            new_path = (
                cache_dir
                / f"{params['baseload_demand_mw']:g}MW"
                / f"p{int(params['p_percentile'])}"
                / str(region)
                / f"{rest}.zarr"
            )
            if new_path.exists():
                logging.warning(
                    f"Cache rename target {new_path.relative_to(cache_dir)} already exists; skipping {path.name}"
                )
                continue
            new_path.parent.mkdir(parents=True, exist_ok=True)
            path.rename(new_path)
            logging.info(f"Moved legacy cache {path.name} -> {new_path.relative_to(cache_dir)}")
            n_moved += 1
        except Exception as e:
            logging.warning(f"Failed to migrate {path.name}: {e}")
    return n_moved


def cache_exists(
    cache_dir: Path,
    region: str,
    p: int,
    n_samples: int,
    seed: int,
    weather_year: int,
    era5_resolution_deg: float,
) -> bool:
    """True if the per-region cache exists on disk (does not validate contents)."""
    return cache_path(
        cache_dir,
        region,
        p,
        n_samples,
        seed,
        weather_year,
        era5_resolution_deg,
    ).exists()


# ----- in-memory cache representation ----------------------------------------


@dataclass
class RegionDesignCache:
    """In-memory representation of a single-region cache (build output / query input)."""

    region: str
    all_lats: np.ndarray  # (n_lat,) float32 -- full region grid (lat axis)
    all_lons: np.ndarray  # (n_lon,) float32 -- full region grid (lon axis)
    lats: np.ndarray  # (npts,) float32 -- land-point lats
    lons: np.ndarray  # (npts,)
    iy: np.ndarray  # (npts,) int32 -- index into all_lats
    ix: np.ndarray  # (npts,) int32 -- index into all_lons
    pv_max: np.ndarray  # (npts,) float32
    wind_max: np.ndarray  # (npts,) float32
    designs_flat: np.ndarray  # (sum_mp, 3) float32 -- cols: solar / wind / battery
    design_offsets: np.ndarray  # (npts+1,) int32
    coverage_flat: np.ndarray  # (sum_mp,) float32
    meta: dict = field(default_factory=dict)

    @property
    def n_points(self) -> int:
        return int(self.lats.shape[0])

    @property
    def n_designs_total(self) -> int:
        return int(self.designs_flat.shape[0])

    def designs_for_point(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Return (designs, coverage) slices for point k (zero-length if no designs)."""
        lo, hi = int(self.design_offsets[k]), int(self.design_offsets[k + 1])
        return self.designs_flat[lo:hi], self.coverage_flat[lo:hi]


def pack_csr(
    designs_per_point: list[np.ndarray],
    coverage_per_point: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Pack per-point design lists into CSR-style flat arrays.

    Returns (designs_flat, design_offsets, coverage_flat).
    `design_offsets[k+1] - design_offsets[k]` == count of designs for point k.
    """
    n_per_point = np.array([d.shape[0] for d in designs_per_point], dtype=np.int64)
    design_offsets = np.concatenate([[0], np.cumsum(n_per_point)]).astype(np.int32)
    if n_per_point.sum() == 0:
        return (
            np.empty((0, 3), dtype=np.float32),
            design_offsets,
            np.empty(0, dtype=np.float32),
        )
    designs_flat = np.concatenate(designs_per_point, axis=0).astype(np.float32, copy=False)
    coverage_flat = np.concatenate(coverage_per_point, axis=0).astype(np.float32, copy=False)
    return designs_flat, design_offsets, coverage_flat


# ----- zarr write / read -----------------------------------------------------


def _git_sha_short() -> str:
    """Current commit hash for cache provenance; empty if git unavailable."""
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


def build_cache_meta(
    region: str,
    n_points: int,
    n_designs_total: int,
    p: int,
    n_samples: int,
    seed: int,
    weather_year: int,
    era5_resolution_deg: float,
    overscale_sampling_k: dict,
) -> dict:
    """Assemble the meta dict that goes into `group.attrs["meta"]`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "region": region,
        "n_points": int(n_points),
        "n_designs_total": int(n_designs_total),
        "params": {
            "p_percentile": int(p),
            "n_samples": int(n_samples),
            "random_seed": int(seed),
            "weather_year": int(weather_year),
            "era5_resolution_deg": float(era5_resolution_deg),
            "overscale_sampling_k": {tech: float(k) for tech, k in overscale_sampling_k.items()},
        },
        "built_at": datetime.now(timezone.utc).isoformat(),
        "code_git_sha": _git_sha_short(),
    }


def write_cache(cache: RegionDesignCache, path: Path) -> Path:
    """
    Atomic write to `path`: build to `<path>.tmp`, swap into place. A
    pre-existing path is replaced (rebuild semantics); a stale `.tmp` from a
    crashed run is wiped on entry.
    """
    final = Path(path)
    tmp = final.parent / f"{final.name}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    final.parent.mkdir(parents=True, exist_ok=True)

    g = zarr.open_group(str(tmp), mode="w")
    npts = cache.n_points
    sum_mp = cache.n_designs_total
    n_lat = int(cache.all_lats.shape[0])
    n_lon = int(cache.all_lons.shape[0])

    # Full region grid coords (so the query path can assemble the output NetCDF
    # without needing the profile dataset).
    g.create_array("all_lats", shape=(n_lat,), dtype=np.float32)
    g["all_lats"][:] = np.ascontiguousarray(cache.all_lats, dtype=np.float32)
    g.create_array("all_lons", shape=(n_lon,), dtype=np.float32)
    g["all_lons"][:] = np.ascontiguousarray(cache.all_lons, dtype=np.float32)

    # Per-point arrays
    g.create_array("lats", shape=(npts,), dtype=np.float32)
    g["lats"][:] = np.ascontiguousarray(cache.lats, dtype=np.float32)
    g.create_array("lons", shape=(npts,), dtype=np.float32)
    g["lons"][:] = np.ascontiguousarray(cache.lons, dtype=np.float32)
    g.create_array("iy", shape=(npts,), dtype=np.int32)
    g["iy"][:] = np.ascontiguousarray(cache.iy, dtype=np.int32)
    g.create_array("ix", shape=(npts,), dtype=np.int32)
    g["ix"][:] = np.ascontiguousarray(cache.ix, dtype=np.int32)
    g.create_array("pv_max", shape=(npts,), dtype=np.float32)
    g["pv_max"][:] = np.ascontiguousarray(cache.pv_max, dtype=np.float32)
    g.create_array("wind_max", shape=(npts,), dtype=np.float32)
    g["wind_max"][:] = np.ascontiguousarray(cache.wind_max, dtype=np.float32)

    # Ragged CSR arrays (designs + coverage)
    g.create_array("designs_flat", shape=(sum_mp, 3), dtype=np.float32)
    if sum_mp > 0:
        g["designs_flat"][:] = np.ascontiguousarray(cache.designs_flat, dtype=np.float32)
    g.create_array("design_offsets", shape=(npts + 1,), dtype=np.int32)
    g["design_offsets"][:] = np.ascontiguousarray(cache.design_offsets, dtype=np.int32)
    g.create_array("coverage_flat", shape=(sum_mp,), dtype=np.float32)
    if sum_mp > 0:
        g["coverage_flat"][:] = np.ascontiguousarray(cache.coverage_flat, dtype=np.float32)

    g.attrs["meta"] = cache.meta

    # Atomic swap
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return final


def read_cache(path: Path) -> RegionDesignCache:
    """Load a design cache from a Zarr store at `path`; raises FileNotFoundError on miss."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"design cache missing: {path}")
    g = zarr.open_group(str(path), mode="r")
    raw_meta = g.attrs.get("meta", {})
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    version = meta.get("schema_version")
    if version != SCHEMA_VERSION:
        # No migration exists (the sampler change altered the cached designs); refuse, never a wrong answer.
        raise ValueError(
            f"design cache at {path} has schema version {version}; this code requires "
            f"v{SCHEMA_VERSION}. Delete the cache and rebuild (`boa-run build-cache`)."
        )
    region = str(meta.get("region", path.parent.name))
    return RegionDesignCache(
        region=region,
        all_lats=np.asarray(g["all_lats"][:]),
        all_lons=np.asarray(g["all_lons"][:]),
        lats=np.asarray(g["lats"][:]),
        lons=np.asarray(g["lons"][:]),
        iy=np.asarray(g["iy"][:]),
        ix=np.asarray(g["ix"][:]),
        pv_max=np.asarray(g["pv_max"][:]),
        wind_max=np.asarray(g["wind_max"][:]),
        designs_flat=np.asarray(g["designs_flat"][:]),
        design_offsets=np.asarray(g["design_offsets"][:]),
        coverage_flat=np.asarray(g["coverage_flat"][:]),
        meta=meta,
    )
