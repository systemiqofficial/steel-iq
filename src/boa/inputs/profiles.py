"""
Open regional renewable-profile + max-capacity datasets.

Backend is selected by PROFILE_DATA_SOURCE:
  - "local_zarr" (default): spatially chunked Zarr stores in zarr_dir
                  (both kinds; produced by the boa_cds pipeline)
  - "local_nc":  legacy unchunked atlite NetCDF files in atlite_output_dir / cav_dir

Zarr is dramatically faster for single-point reads because the data is
chunked spatially — one (lat, lon) read pulls one ~5 MB chunk instead of
scanning the whole regional NetCDF.

The weather year is not configured anywhere: it is read off the profile-store
filenames of the selected input set (``detect_weather_year``), so the stores
themselves are the single source of truth.
"""

import logging
import os
import re
from pathlib import Path
from typing import Literal

import xarray as xr

from boa.config.paths import PathConfig
from boa.store_schema import max_cap_store_stem, profile_store_stem

logger = logging.getLogger(__name__)

DataKind = Literal["profile", "max_cap"]

# Store stems are `pv_and_wind_potential_<REGION>_<year>_<res>_deg`; region names
# contain underscores, so anchor the year on the trailing `<res>_deg` instead.
_PROFILE_STEM_RE = re.compile(r"^pv_and_wind_potential_.+_(\d{4})_\d+_deg$")


def _source() -> str:
    source = os.getenv("PROFILE_DATA_SOURCE", "local_zarr")
    if source not in ("local_zarr", "local_nc"):
        raise ValueError(f"Unknown PROFILE_DATA_SOURCE={source!r} (expected 'local_nc' or 'local_zarr')")
    return source


def _profile_store_dir(path_config: PathConfig) -> tuple[Path, str]:
    """Directory holding the profile stores for the active backend, and their extension."""
    if _source() == "local_zarr":
        return path_config.zarr_dir, ".zarr"
    return path_config.atlite_output_dir, ".nc"


def detect_weather_year(path_config: PathConfig) -> int:
    """
    Weather year of the input set, read off its profile-store filenames.

    An input set holds stores for exactly one weather year; mixed years in one
    live dir are a misconfiguration and raise.
    """
    store_dir, suffix = _profile_store_dir(path_config)
    years = {
        int(m.group(1))
        for f in store_dir.glob(f"pv_and_wind_potential_*{suffix}")
        if (m := _PROFILE_STEM_RE.match(f.stem)) is not None
    }
    if not years:
        if m := re.fullmatch(r"cds-(\d{4})", path_config.input_set):
            hint = f"`boa-cds-prepare --weather_year {m.group(1)}` (or pass `--cds-prepare {m.group(1)}` to boa-run)"
        else:
            hint = f"`boa-cds-prepare --weather_year <year> --inputs {path_config.input_set}`"
        raise FileNotFoundError(
            f"No profile stores found in {store_dir} — build the input set first: {hint} "
            f"(see docs/cds-data-pipeline.md)."
        )
    if len(years) > 1:
        raise ValueError(
            f"Profile stores for multiple weather years {sorted(years)} found in {store_dir}; "
            f"an input set must hold exactly one year — move the extra stores to their own set."
        )
    return years.pop()


def dataset_path(kind: DataKind, region: str, path_config: PathConfig, year: int) -> Path:
    """Backend-resolved on-disk path of one (kind, region) store."""
    stem = profile_store_stem(region, year) if kind == "profile" else max_cap_store_stem(region, year)
    if _source() == "local_zarr":
        return path_config.zarr_dir / (stem + ".zarr")
    parent = path_config.atlite_output_dir if kind == "profile" else path_config.cav_dir
    return parent / (stem + ".nc")


def open_regional_dataset(kind: DataKind, region: str, path_config: PathConfig) -> xr.Dataset:
    """
    Return an open xr.Dataset for (kind, region).

    For "profile" datasets, the source NetCDFs use the variable name "pv" and
    the Zarr stores use "solar". This helper normalizes to "solar" either way
    so callers can use a single name.
    """
    source = _source()
    path = dataset_path(kind, region, path_config, detect_weather_year(path_config))
    if source == "local_zarr":
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found — populate the live store dir (see docs/cds-data-pipeline.md, "
                f"e.g. `boa-cds-prepare`), or set PROFILE_DATA_SOURCE=local_nc for the legacy "
                f"atlite NetCDFs."
            )
        ds = xr.open_zarr(path, consolidated=True)
    else:
        ds = xr.open_dataset(path)

    if kind == "profile" and "pv" in ds.data_vars:
        ds = ds.rename({"pv": "solar"})

    logger.debug("Opened %s dataset for %s from %s", kind, region, source)
    return ds
