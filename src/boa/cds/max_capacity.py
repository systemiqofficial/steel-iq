"""
Build max_capacity stores: spherical pixel area x density x availability layers.

Regenerates the per-region max-capacity ceilings. The original generator was
never committed, but the shipped files reproduce exactly as
pixel_area(lat, R=6371.0) x density, with no availability signal in them.

The layers scale ONLY the capacity ceiling — capacity factors are never touched
(CF = per-machine efficiency, availability = how many machines fit). They live in
boa.cds.availability; this module composes them and records which set was used.

Geometry-only (no layers) is the production default and installs under the plain
store name, reproducing the shipped stores bit-for-bit. A layered build belongs to
a different input set, since the ceiling it produces is not interchangeable.

Every store carries an `availability_signature` over the layer set and the
densities. It is what makes a stale store detectable: the ceilings are baked into
the design cache, so reusing one built from different parameters is wrong with no
downstream symptom.

Output is a NetCDF (for inspection and the legacy local_nc backend) plus a
Zarr twin, which is what `boa_cds install` promotes to the live dir.
"""

import datetime
import json
import logging
import shutil
import time
from pathlib import Path

import numpy as np
import xarray as xr

from boa.cds.availability import LayerSpec, availability_factor, availability_signature
from boa.config.constants import EARTH_RADIUS_KM
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

SIGNATURE_ATTR = "availability_signature"


def pixel_area(lat: np.ndarray) -> np.ndarray:
    y_size = ERA5_DATA_RESOLUTION * 2 * np.pi * EARTH_RADIUS_KM / 360
    return y_size * (y_size * np.cos(np.radians(lat)))


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


def store_attrs(layers: list[LayerSpec], density: dict[str, float]) -> dict[str, str]:
    """
    Self-describing provenance for a max-capacity store.

    `availability_signature` is the load-bearing entry: it is what a later run compares
    against to decide whether a store on disk was built from the parameters now being
    asked for. The per-layer source and params are for a human reading the store; the
    signature is for the machine.

    `lulc_source` and `lulc_codes` are kept as deprecated aliases for one release, so a
    reader written against the pre-layer stores still finds what it expects.
    """
    names = [spec.name for spec in layers]
    attrs = {
        "description": "Maximum installable capacity per pixel (MW)",
        "method": f"pixel_area(lat, R={EARTH_RADIUS_KM} km) x density"
        + (" x " + " x ".join(names) if names else " (no availability layers)"),
        "density_mw_per_km2": str(density),
        "availability_layers": ",".join(names),
        SIGNATURE_ATTR: availability_signature(layers, density),
        "generated_by": "boa.cds.max_capacity",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    for spec in layers:
        attrs[f"layer_{spec.name}_source"] = spec.source
        attrs[f"layer_{spec.name}_params"] = json.dumps(spec.params, sort_keys=True)

    lulc = next((spec for spec in layers if spec.name == "lulc"), None)
    attrs["lulc_source"] = lulc.source if lulc else "none"
    attrs["lulc_codes"] = str(LULC_CODES) if lulc else "n/a"
    return attrs


def build_region(
    region: str,
    out_dir: Path,
    path_config: PathConfig,
    layers: list[LayerSpec] | None = None,
    pv_density: float = CAPACITY_DENSITY_MW_PER_KM2["pv"],
    wind_density: float = CAPACITY_DENSITY_MW_PER_KM2["wind"],
    year: int = ERA5_DATA_YEAR,
) -> Path:
    """Build one region's ceiling; `year` only stamps the filename — the data depends
    solely on the availability layers and the densities."""
    layers = layers or []
    log.info(f"Building max_capacity for {region} (layers: {','.join(s.name for s in layers) or 'none'})")
    t0 = time.perf_counter()
    y, x = target_grid(region, path_config)
    areas = pixel_area(y)[:, None]
    density = {"pv": pv_density, "wind": wind_density}

    data = {}
    for tech in ["pv", "wind"]:
        cap = np.broadcast_to(areas * density[tech], (len(y), len(x))).astype("float64")
        if layers:
            cap = cap * availability_factor(y, x, tech, layers)
        data[tech] = xr.DataArray(cap, coords={"y": y, "x": x}, dims=["y", "x"])

    ds = xr.Dataset(data)
    ds.attrs = store_attrs(layers, density)
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
