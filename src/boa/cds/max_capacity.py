"""
Build max_capacity stores: spherical pixel area x density x LULC usable fraction.

Regenerates the per-region max-capacity ceilings. The original generator was
never committed, but the shipped files reproduce exactly as
pixel_area(lat, R=6371.0) x density, with no land-cover signal in them. This
module adds the land-use term they lack: the ESA-CCI 300 m land-cover class
map is converted to a per-class usable fraction (settings.LULC_CODES) and
averaged over each 0.25 deg cell, scaling ONLY the capacity ceiling —
capacity factors are never touched (CF = per-machine efficiency, LULC = how
many machines fit).

The ESA-CCI grid is 360 cells/degree, so each 0.25 deg cell covers exactly a
90x90 block; aggregation is an exact block mean, no reprojection involved.

DIVERGENCE from upstream BOA: geometry-only (apply_lulc=False) is the
production default here and its output installs under the plain store name —
upstream treats it as a regression mode with a _nolulc suffix. Geometry-only
reproduces the shipped stores bit-for-bit; the LULC term returns when the
availability question is settled (the `lulc_source` attr records which one a
store is).

Output is a NetCDF (for inspection and the legacy local_nc backend) plus a
Zarr twin, which is what `boa_cds install` promotes to the live dir.
"""

import datetime
import logging
import shutil
import time
from pathlib import Path

import numpy as np
import xarray as xr

from boa.config.constants import EARTH_RADIUS_KM, ESA_CCI_CELLS_PER_DEG
from boa.config.paths import PathConfig
from boa.config.settings import (
    CAPACITY_DENSITY_MW_PER_KM2,
    ERA5_DATA_RESOLUTION,
    ERA5_DATA_YEAR,
    LULC_CODES,
    REGION_COORDS,
)
from boa.store_schema import MAX_CAP_CHUNKS, ZARR_FORMAT, max_cap_store_stem, profile_store_stem

log = logging.getLogger(__name__)

BLOCK = int(ERA5_DATA_RESOLUTION * ESA_CCI_CELLS_PER_DEG)  # 90


def pixel_area(lat: np.ndarray) -> np.ndarray:
    y_size = ERA5_DATA_RESOLUTION * 2 * np.pi * EARTH_RADIUS_KM / 360
    return y_size * (y_size * np.cos(np.radians(lat)))


def fraction_lut(tech: str) -> np.ndarray:
    lut = np.zeros(256, dtype=np.float32)
    for code, frac in LULC_CODES[tech].items():
        lut[code] = frac
    return lut


def target_grid(region: str, path_config: PathConfig) -> tuple[np.ndarray, np.ndarray]:
    """Prefer the shipped max_capacity grid, then the live profile store, then REGION_COORDS."""
    shipped = path_config.cav_dir / (max_cap_store_stem(region, ERA5_DATA_YEAR) + ".nc")
    if shipped.exists():
        ds = xr.open_dataset(shipped)
        y, x = ds.y.values.copy(), ds.x.values.copy()
        ds.close()
        return y, x
    store = path_config.zarr_dir / (profile_store_stem(region, ERA5_DATA_YEAR) + ".zarr")
    if store.exists():
        ds = xr.open_zarr(store, consolidated=True)
        y, x = ds.y.values.copy(), ds.x.values.copy()
        ds.close()
        return y, x
    if region not in REGION_COORDS:
        raise KeyError(f"{region}: no shipped file, no live store, and no REGION_COORDS entry")
    north, west, south, east = REGION_COORDS[region]
    step = ERA5_DATA_RESOLUTION
    return (np.arange(south, north + step / 2, step), np.arange(west, min(east, 180.0 - step) + step / 2, step))


def usable_fraction(y: np.ndarray, x: np.ndarray, tech: str, lulc_path: Path) -> np.ndarray:
    """Mean usable fraction per 0.25 deg cell from exact 90x90 ESA-CCI blocks.

    Cell for node (y, x) spans [y-0.125, y+0.125] x [x-0.125, x+0.125]; the
    ESA-CCI axes are cell-centred at half-steps of 1/360 deg, so each node's
    block starts at a computable integer index.
    """
    ds = xr.open_dataset(lulc_path)
    i0 = int(round((90.0 - (y.max() + ERA5_DATA_RESOLUTION / 2)) * ESA_CCI_CELLS_PER_DEG))
    j0 = int(round((x.min() - ERA5_DATA_RESOLUTION / 2 + 180.0) * ESA_CCI_CELLS_PER_DEG))
    ny, nx = len(y), len(x)
    codes = (
        ds["lccs_class"]
        .isel(
            time=0,
            lat=slice(i0, i0 + ny * BLOCK),
            lon=slice(j0, j0 + nx * BLOCK),
        )
        .values
    )  # uint8, (ny*90, nx*90); ESA lat axis is descending
    ds.close()

    frac = fraction_lut(tech)[codes]
    frac = frac.reshape(ny, BLOCK, nx, BLOCK).mean(axis=(1, 3))
    return frac[::-1]  # flip to ascending latitude to match the y axis


def build_region(
    region: str,
    out_dir: Path,
    path_config: PathConfig,
    apply_lulc: bool = False,
    lulc_path: Path | None = None,
    pv_density: float = CAPACITY_DENSITY_MW_PER_KM2["pv"],
    wind_density: float = CAPACITY_DENSITY_MW_PER_KM2["wind"],
    year: int = ERA5_DATA_YEAR,
) -> Path:
    """Build one region's ceiling; `year` only stamps the filename — the data
    depends solely on the LULC map and the densities."""
    log.info(f"Building max_capacity for {region} (lulc={'on' if apply_lulc else 'off'})")
    t0 = time.perf_counter()
    y, x = target_grid(region, path_config)
    areas = pixel_area(y)[:, None]
    density = {"pv": pv_density, "wind": wind_density}

    data = {}
    for tech in ["pv", "wind"]:
        cap = np.broadcast_to(areas * density[tech], (len(y), len(x))).astype("float64")
        if apply_lulc:
            if lulc_path is None:
                raise ValueError("apply_lulc=True requires lulc_path")
            cap = cap * usable_fraction(y, x, tech, lulc_path)
        data[tech] = xr.DataArray(cap, coords={"y": y, "x": x}, dims=["y", "x"])

    ds = xr.Dataset(data)
    ds.attrs = {
        "description": "Maximum installable capacity per pixel (MW)",
        "method": f"pixel_area(lat, R={EARTH_RADIUS_KM} km) x density"
        + (" x ESA-CCI usable land fraction" if apply_lulc else " (no land-use term)"),
        "density_mw_per_km2": str(density),
        "lulc_source": (lulc_path.name if apply_lulc else "none"),
        "lulc_codes": (str(LULC_CODES) if apply_lulc else "n/a"),
        "generated_by": "boa.cds.max_capacity",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    nc_dir = out_dir / "cav"
    nc_dir.mkdir(parents=True, exist_ok=True)
    out = nc_dir / (max_cap_store_stem(region, year) + ".nc")
    ds.to_netcdf(out)
    log.info(f"  wrote {out} in {time.perf_counter() - t0:.1f}s")

    # Zarr twin next to the profile stores in staging; what `install` promotes.
    zarr_path = out_dir / (max_cap_store_stem(region, year) + ".zarr")
    if zarr_path.exists():
        shutil.rmtree(zarr_path)
    ds.chunk(MAX_CAP_CHUNKS).to_zarr(zarr_path, mode="w", consolidated=True, zarr_format=ZARR_FORMAT)
    log.info(f"  wrote {zarr_path}")
    return out
