"""
Open regional renewable-profile + max-capacity datasets.

Backend is selected by PROFILE_DATA_SOURCE:
  - "local_zarr" (default): spatially chunked Zarr stores in zarr_dir
                  (both kinds; produced by the boa_cds pipeline)
  - "local_nc":  legacy unchunked atlite NetCDF files in atlite_output_dir / cav_dir

Zarr is dramatically faster for single-point reads because the data is
chunked spatially — one (lat, lon) read pulls one ~5 MB chunk instead of
scanning the whole regional NetCDF.
"""

import logging
import os
from typing import Literal

import xarray as xr

from boa.config.paths import PathConfig
from boa.config.settings import ERA5_DATA_YEAR
from boa.store_schema import max_cap_store_stem, profile_store_stem

logger = logging.getLogger(__name__)

DataKind = Literal["profile", "max_cap"]


def open_regional_dataset(kind: DataKind, region: str, path_config: PathConfig) -> xr.Dataset:
    """
    Return an open xr.Dataset for (kind, region).

    For "profile" datasets, the source NetCDFs use the variable name "pv" and
    the Zarr stores use "solar". This helper normalizes to "solar" either way
    so callers can use a single name.
    """
    source = os.getenv("PROFILE_DATA_SOURCE", "local_zarr")
    if source == "local_zarr":
        ds = _open_local_zarr(kind, region, path_config)
    elif source == "local_nc":
        ds = _open_local_nc(kind, region, path_config)
    else:
        raise ValueError(f"Unknown PROFILE_DATA_SOURCE={source!r} (expected 'local_nc' or 'local_zarr')")

    if kind == "profile" and "pv" in ds.data_vars:
        ds = ds.rename({"pv": "solar"})

    logger.debug("Opened %s dataset for %s from %s", kind, region, source)
    return ds


def _filename_stem(kind: DataKind, region: str) -> str:
    if kind == "profile":
        return profile_store_stem(region, ERA5_DATA_YEAR)
    return max_cap_store_stem(region, ERA5_DATA_YEAR)


def _open_local_nc(kind: DataKind, region: str, path_config: PathConfig) -> xr.Dataset:
    name = _filename_stem(kind, region) + ".nc"
    if kind == "profile":
        path = path_config.atlite_output_dir / name
    else:
        path = path_config.cav_dir / name
    return xr.open_dataset(path)


def _open_local_zarr(kind: DataKind, region: str, path_config: PathConfig) -> xr.Dataset:
    name = _filename_stem(kind, region) + ".zarr"
    path = path_config.zarr_dir / name
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — populate the live store dir (see docs/cds-data-pipeline.md, "
            f"e.g. `boa_cds install`), or set PROFILE_DATA_SOURCE=local_nc for the legacy "
            f"atlite NetCDFs."
        )
    return xr.open_zarr(path, consolidated=True)
