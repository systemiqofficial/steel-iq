"""
Promote staged Zarr stores into the live dir the model reads (zarr_dir).

Nothing else in the pipeline writes to the live dir; this is the single,
explicit switch point. Each store is validated (openable, expected variables
and dims) before promotion, and moved into place under a temporary name then
renamed, so a crash never leaves a half-written live store.
"""

import logging
import os
import shutil
from pathlib import Path

import numpy as np
import xarray as xr

from boa.cds.max_capacity import SIGNATURE_ATTR
from boa.config.settings import ERA5_DATA_YEAR
from boa.store_schema import max_cap_store_stem, profile_store_stem

log = logging.getLogger(__name__)

EXPECTED = {
    "profile": {"vars": {"solar", "wind"}, "dims": ("time", "y", "x")},
    "max-cap": {"vars": {"pv", "wind"}, "dims": ("y", "x")},
}


def staged_store_path(staging_dir: Path, kind: str, region: str, year: int) -> Path:
    stem = profile_store_stem(region, year) if kind == "profile" else max_cap_store_stem(region, year)
    return staging_dir / (stem + ".zarr")


def validate_store(path: Path, kind: str) -> None:
    """Raise ValueError if the staged store is not a model-ready store of `kind`."""
    ds = xr.open_zarr(path, consolidated=True)
    try:
        expected = EXPECTED[kind]
        if set(ds.data_vars) != expected["vars"]:
            raise ValueError(f"{path.name}: data vars {sorted(ds.data_vars)} != expected {sorted(expected['vars'])}")
        for var in ds.data_vars:
            if tuple(ds[var].dims) != expected["dims"]:
                raise ValueError(f"{path.name}: {var} dims {tuple(ds[var].dims)} != expected {expected['dims']}")
        if kind == "max-cap":
            _validate_max_cap(ds, path)
    finally:
        ds.close()


def _validate_max_cap(ds: xr.Dataset, path: Path) -> None:
    """
    A ceiling store must say what built it, and must hold plausible ceilings.

    The signature is required rather than optional because it is what a later run
    compares to decide whether a store can be reused. A store without one cannot be
    checked at all, and the ceilings it holds get baked into the design cache where
    nothing downstream can notice they came from different parameters.

    The value check exists because an availability layer is one sign away from
    catastrophe: a mask inverted the wrong way produces negative ceilings, which would
    otherwise surface only as a world that had become uniformly infeasible.
    """
    if not ds.attrs.get(SIGNATURE_ATTR):
        raise ValueError(
            f"{path.name}: no {SIGNATURE_ATTR} attr. It is written by boa.cds.max_capacity; "
            "a store without one predates the availability layers and must be rebuilt."
        )
    for var in ds.data_vars:
        values = ds[var].values
        if not np.isfinite(values).all():
            raise ValueError(f"{path.name}: {var} holds non-finite ceilings")
        if (values < 0).any():
            raise ValueError(f"{path.name}: {var} holds negative ceilings (an inverted availability layer?)")


def stored_signature(path: Path) -> str | None:
    """The availability signature a max-capacity store was built with, or None."""
    if not path.exists():
        return None
    with xr.open_zarr(path, consolidated=True) as ds:
        return ds.attrs.get(SIGNATURE_ATTR)


def install_store(
    staged: Path, dest_dir: Path, force: bool = False, keep_staged: bool = False, dry_run: bool = False
) -> Path:
    """Promote one validated store from staging into `dest_dir`."""
    dest = dest_dir / staged.name
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} exists — pass --force to overwrite")
    if dry_run:
        log.info(f"[dry-run] would install {staged.name} -> {dest}")
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".{staged.name}.installing"
    if tmp.exists():
        shutil.rmtree(tmp)
    if keep_staged:
        shutil.copytree(staged, tmp)
    else:
        shutil.move(str(staged), str(tmp))
    if dest.exists():
        shutil.rmtree(dest)
    os.rename(tmp, dest)
    log.info(f"Installed {dest}")
    return dest


def install_regions(
    regions: list[str],
    year: int,
    kinds: list[str],
    staging_dir: Path,
    dest_dir: Path,
    force: bool = False,
    keep_staged: bool = False,
    dry_run: bool = False,
    kind_explicit: bool = False,
) -> list[Path]:
    """Validate and promote the staged stores for each region.

    With both kinds requested, a region missing either staged store is refused
    up front (unless `kind_explicit`), preventing a half-installed region pair.
    """
    if year != ERA5_DATA_YEAR:
        log.warning(
            f"Installing {year} stores, but the model reads {ERA5_DATA_YEAR} until "
            f"ERA5_DATA_YEAR in src/boa/config/settings.py is changed."
        )

    plan: list[tuple[str, Path]] = []
    for region in regions:
        for kind in kinds:
            staged = staged_store_path(staging_dir, kind, region, year)
            if not staged.exists():
                if kind_explicit:
                    raise FileNotFoundError(f"{staged} not found — build it first")
                raise FileNotFoundError(
                    f"{staged} not found — a region installs as a profile + max-cap pair; "
                    f"build the missing store or pass an explicit --kind"
                )
            validate_store(staged, kind)
            plan.append((region, staged))

    installed = []
    for region, staged in plan:
        installed.append(install_store(staged, dest_dir, force, keep_staged, dry_run))
    for path in installed:
        log.info(f"  {'would install' if dry_run else 'installed'}: {path.name}")
    return installed
