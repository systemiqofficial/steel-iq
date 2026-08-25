"""
Convert CDS C3S Energy capacity factors to regional profile Zarr stores.

Reads the monthly global NetCDFs fetched by `boa_cds download` (dataset
sis-energy-global-reanalysis) and writes per-region Zarr stores
schema-identical to the ones src/boa/inputs/profiles.py serves (variables
solar + wind, dims (time, y, x), per-unit CF, consolidated Zarr v2, full-time
x 12x12 spatial chunks).

Stores are written to the staging dir; promote them to the live dir with
`boa_cds install`.

Conventions handled here (verified against the raw CDS NetCDFs):
  - CDS latitude is descending and longitude is 0..360 -> converted to
    ascending / -180..180 to match the profile grid.
  - Ocean pixels are zeroed, not NaN. They are written through UNCHANGED —
    reconciling them with the non-zero atlite coastal pixels is still open.
  - CDS ships float64; stores are written as float32.

The raw NetCDFs are chunked (1, 721, 1440) — one compressed chunk per global
hour — so ANY regional read decompresses every file in full. Converting
multiple regions therefore goes through a shared global intermediate Zarr
(`build_global_store`): one decompression pass over the monthly files, after
which each region is a cheap chunk-aligned slice. The direct monthly path
remains for single-region conversions without the intermediate.
"""

import datetime
import logging
import shutil
import time
from pathlib import Path

import numpy as np
import xarray as xr

from boa.cds.spec import CDS_DATASET, CDS_TECH_SPEC, CDS_VARS, cf_extract_dir_name
from boa.config.paths import PathConfig
from boa.config.settings import ERA5_DATA_RESOLUTION, ERA5_DATA_YEAR, REGION_COORDS
from boa.conversions import convert_resolution_to_string
from boa.store_schema import PROFILE_CHUNKS, ZARR_FORMAT, profile_store_stem

log = logging.getLogger(__name__)

# Intermediate global store: month-sized time chunks, coarse spatial blocks.
# Not the model layout — regions are rechunked to PROFILE_CHUNKS when cut out.
GLOBAL_CHUNKS = {"time": 732, "y": 121, "x": 120}


def global_store_path(cds_dir: Path, year: int) -> Path:
    res = convert_resolution_to_string(ERA5_DATA_RESOLUTION)
    return cds_dir / "global_zarr" / f"cds_cf_global_{year}_{res}_deg.zarr"


def cds_month_files(cds_dir: Path, tech: str, year: int) -> list[Path]:
    folder = cds_dir / cf_extract_dir_name(tech, year)
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} not found — download/extract it first (boa_cds download --year {year})")
    files = sorted(folder.glob("*.nc"))
    if len(files) != 12:
        raise FileNotFoundError(f"{folder} holds {len(files)} NetCDFs, expected 12 monthly files")
    return files


def region_grid(region: str, path_config: PathConfig) -> tuple[np.ndarray, np.ndarray]:
    """Target (y, x) coordinates for a region.

    Prefer the exact grid of an existing live store so the new store is a
    coordinate-identical drop-in; fall back to the REGION_COORDS box for
    regions without one.
    """
    existing = path_config.zarr_dir / (profile_store_stem(region, ERA5_DATA_YEAR) + ".zarr")
    if existing.exists():
        ds = xr.open_zarr(existing, consolidated=True)
        y, x = ds.y.values.copy(), ds.x.values.copy()
        ds.close()
        log.info(f"  grid from existing store: {len(y)}x{len(x)} pixels")
        return y, x
    if region not in REGION_COORDS:
        raise KeyError(f"{region}: no existing store and no REGION_COORDS entry")
    north, west, south, east = REGION_COORDS[region]
    step = ERA5_DATA_RESOLUTION
    y = np.arange(south, north + step / 2, step)
    x = np.arange(west, min(east, 180.0 - step) + step / 2, step)
    log.info(f"  grid from REGION_COORDS: {len(y)}x{len(x)} pixels")
    return y, x


def load_cds_tech(files: list[Path], tech: str, y: np.ndarray, x: np.ndarray) -> xr.DataArray:
    """Concatenate monthly global CDS files, subset to the region grid.

    Contiguous slices, not pointwise nearest-selection: the CDS grid nodes
    coincide exactly with the profile grid (asserted below), and per-point
    indexing through the lazy NetCDF backend is orders of magnitude slower.
    """
    half = ERA5_DATA_RESOLUTION / 2
    months = []
    for f in files:
        ds = xr.open_dataset(f)
        ds = ds.sel(latitude=slice(y.max() + half, y.min() - half))  # latitude is descending
        ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
        ds = ds.sortby("longitude").sortby("latitude")
        da = ds[CDS_VARS[tech]].sel(longitude=slice(x.min() - half, x.max() + half))
        np.testing.assert_allclose(da.latitude.values, y, atol=1e-6)
        np.testing.assert_allclose(da.longitude.values, x, atol=1e-6)
        months.append(da.astype("float32").load())
        ds.close()
    out = xr.concat(months, dim="time")
    out = out.rename({"latitude": "y", "longitude": "x"}).assign_coords(y=y, x=x)
    if not out.get_index("time").is_monotonic_increasing:
        raise ValueError(f"{tech}: concatenated time axis is not monotonic")
    return out


def provenance_attrs(source_files: str, year: int) -> dict:
    return {
        "source_dataset": CDS_DATASET,
        "source": "Copernicus Climate Change Service (C3S) Energy, ERA5-derived capacity factors",
        "technological_specification": CDS_TECH_SPEC,
        "source_files": source_files,
        "year": year,
        "units": "p.u.",
        "coastal_fill": "none (CDS ocean/excluded pixels remain zero)",
        "converted_by": "boa.cds.convert",
        "converted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "licence": "CC-BY 4.0 (C3S)",
    }


def build_global_store(cds_dir: Path, year: int, out_path: Path) -> Path:
    """One decompression pass over the monthly files -> global intermediate Zarr.

    Always built with both technologies so any later region cut can use it.
    Skipped when it already exists (delete it to rebuild).
    """
    if out_path.exists():
        log.info(f"{out_path} exists — reusing (delete it to rebuild)")
        return out_path

    log.info(f"Building global intermediate store for {year} -> {out_path}")
    t0 = time.perf_counter()
    data = {}
    source_files = []
    for tech in CDS_VARS:
        files = cds_month_files(cds_dir, tech, year)
        source_files += [f.name for f in files]
        # Time chunks of one day: each dask task decompresses 24 whole-globe
        # NetCDF chunks, the file's native unit.
        ds = xr.open_mfdataset(files, combine="by_coords", chunks={"time": 24})
        da = ds[CDS_VARS[tech]]
        lon = ((da.longitude.values + 180) % 360) - 180
        lat = da.latitude.values
        da = da.isel(
            longitude=np.argsort(lon, kind="stable"),
            latitude=np.argsort(lat, kind="stable"),
        ).assign_coords(longitude=np.sort(lon), latitude=np.sort(lat))
        if not da.get_index("time").is_monotonic_increasing:
            raise ValueError(f"{tech}: concatenated time axis is not monotonic")
        data[tech] = da.astype("float32").rename({"latitude": "y", "longitude": "x"})

    global_ds = xr.Dataset(data, attrs=provenance_attrs("; ".join(source_files), year))
    global_ds = global_ds.chunk(GLOBAL_CHUNKS)
    tmp = out_path.with_name(out_path.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    global_ds.to_zarr(tmp, mode="w", consolidated=True, zarr_format=ZARR_FORMAT)
    tmp.rename(out_path)
    log.info(f"  wrote {out_path} in {time.perf_counter() - t0:.1f}s")
    return out_path


def load_region_from_global(global_store: Path, tech: str, y: np.ndarray, x: np.ndarray) -> xr.DataArray:
    """Cut one region window out of the global intermediate store."""
    half = ERA5_DATA_RESOLUTION / 2
    ds = xr.open_zarr(global_store, consolidated=True)
    da = ds[tech].sel(y=slice(y.min() - half, y.max() + half), x=slice(x.min() - half, x.max() + half))
    np.testing.assert_allclose(da.y.values, y, atol=1e-6)
    np.testing.assert_allclose(da.x.values, x, atol=1e-6)
    return da.load()


def convert_region(
    region: str,
    year: int,
    techs: list[str],
    cds_dir: Path,
    out_dir: Path,
    path_config: PathConfig,
    global_store: Path | None = None,
) -> Path:
    log.info(f"Converting {region} ({year}, {'+'.join(techs)})")
    t0 = time.perf_counter()
    y, x = region_grid(region, path_config)

    data = {}
    if global_store is not None:
        for tech in techs:
            log.info(f"  cutting {tech} from {global_store.name}")
            data[tech] = load_region_from_global(global_store, tech, y, x)
        source_files = xr.open_zarr(global_store, consolidated=True).attrs.get("source_files", global_store.name)
    else:
        files_by_tech = {tech: cds_month_files(cds_dir, tech, year) for tech in techs}
        for tech in techs:
            log.info(f"  loading {tech} (12 monthly files)")
            data[tech] = load_cds_tech(files_by_tech[tech], tech, y, x)
        source_files = "; ".join(f.name for fs in files_by_tech.values() for f in fs)

    ds = xr.Dataset(data, attrs=provenance_attrs(source_files, year))
    # Arrays cut from the global store keep its chunk encoding; drop it so the
    # write derives chunks from PROFILE_CHUNKS instead of failing validation.
    for var in ds.variables.values():
        var.encoding.pop("chunks", None)
        var.encoding.pop("preferred_chunks", None)
    zarr_path = out_dir / (profile_store_stem(region, year) + ".zarr")
    if zarr_path.exists():
        log.info(f"  {zarr_path} exists — removing")
        shutil.rmtree(zarr_path)
    ds = ds.chunk(PROFILE_CHUNKS)
    ds.to_zarr(zarr_path, mode="w", consolidated=True, zarr_format=ZARR_FORMAT)
    log.info(
        f"  wrote {zarr_path} in {time.perf_counter() - t0:.1f}s "
        f"({ds.sizes['time']} hours, {ds.sizes['y']}x{ds.sizes['x']} pixels)"
    )
    return zarr_path
