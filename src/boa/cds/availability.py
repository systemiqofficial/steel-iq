"""
Layered land availability behind the per-pixel capacity ceiling.

    max_capacity(tech) = pixel_area(lat) x density(tech) x PROD(layers(tech))

Each layer is a function of `(y, x, tech)` returning a factor in [0, 1] on the model
grid, and the layers multiply. Two are implemented:

  * `lulc` -- fractional, from the ESA-CCI 300 m land-cover class map. A 0.25 deg cell
    is exactly a 90x90 block of ESA-CCI pixels, so aggregation is an exact block mean
    with no reprojection. Classes absent from `LULC_CODES` get 0, which is what makes
    forest, wetland, water and snow hard exclusions.
  * `cds_exclusion` -- binary, from the Copernicus combined exclusion masks. Seven
    criteria unioned: protected areas, polar, urban, water, slope, elevation and
    distance to shore. Notably it has no forest criterion, so the two layers are
    complementary rather than redundant.

The registry is deliberately narrow. `latitude_weighting_coefficients` ships in the same
mask bundle and is cos(lat), which `pixel_area` already contains -- multiplying it in
would apply cos twice, so it must never become a layer.

Everything here also carries provenance. `availability_signature` hashes the ordered
layers together with the densities, and `availability_tag` names the set for the input-set
directory. Both exist because a max-capacity store records what it was built from nowhere
else, so a changed layer set or density would otherwise be reused in silence.

The layers are only as good as their parameters, and those are not settled: see the
warning on `settings.LULC_CODES`, whose fractions and the densities beside them set how
much of a cell is usable and currently leave most European land unable to host a 500 MW
baseload at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Any, Callable

import numpy as np
import xarray as xr

from boa.cds.spec import EXCLUSION_MASK_FILES, EXCLUSION_MASK_VARS
from boa.config.constants import ESA_CCI_CELLS_PER_DEG
from boa.config.settings import ERA5_DATA_RESOLUTION, LULC_CODES

# Side of the ESA-CCI block that tiles one model cell: 0.25 deg x 360 cells/deg.
BLOCK = int(ERA5_DATA_RESOLUTION * ESA_CCI_CELLS_PER_DEG)

# Class bytes held at once while reading LULC. The delivered raster is tiled 2025 x 2025,
# so a region read is already cheap in time; this only bounds the working set, which the
# class-to-fraction expansion multiplies by four on top.
READ_BAND_BYTES = 64 << 20

# Canonical order. Layers commute, so this does not change the composed factor -- it
# fixes the provenance string, which would otherwise depend on argument order.
LAYER_ORDER = ("lulc", "cds_exclusion")

# Short, filesystem-safe token per layer, for the input-set name.
LAYER_TAGS = {"lulc": "lulc", "cds_exclusion": "excl"}
NO_LAYERS_TAG = "geom"


@dataclass(frozen=True)
class LayerSpec:
    """One configured layer: what to call, where its data is, and what to record."""

    name: str
    path: Path
    source: str  # provider filename(s), for the store attrs
    params: dict[str, Any]  # everything else that changes the numbers


def fraction_lut(tech: str) -> np.ndarray:
    """256-entry class -> fraction table. Unlisted classes stay 0, i.e. fully excluded."""
    lut = np.zeros(256, dtype=np.float32)
    for code, frac in LULC_CODES[tech].items():
        lut[code] = frac
    return lut


@contextmanager
def _open_lccs(path: Path) -> Iterator[xr.DataArray]:
    """
    The 2-D `lccs_class` array, still lazy, from either a NetCDF or a Zarr store.

    `mask_and_scale=False` is the load-bearing part: it stops xarray promoting the class
    codes to float, which would make them unusable as indices into the fraction table.
    """
    path = Path(path)
    if path.suffix == ".zarr":
        ds = xr.open_zarr(path, consolidated=True, mask_and_scale=False)
    else:
        ds = xr.open_dataset(path, mask_and_scale=False)
    with ds:
        codes = ds["lccs_class"]
        yield codes.isel(time=0) if "time" in codes.dims else codes


def _as_class_bytes(values: np.ndarray, name: str) -> np.ndarray:
    """
    Refuse anything that cannot index the 256-entry fraction table, by name.

    The dtype fails in two different ways: a float decode makes the index raise, and an
    int8 decode makes codes at or above 128 index from the wrong end, which is silently
    wrong rather than a crash.
    """
    if values.dtype == np.uint8:
        return values
    if np.issubdtype(values.dtype, np.integer) and values.min() >= 0 and values.max() <= 255:
        return values.astype(np.uint8)
    raise ValueError(
        f"{name}: lccs_class decoded as {values.dtype}, which cannot index the class "
        "lookup table. Check the file for a scale_factor, add_offset or _FillValue that "
        "would need handling deliberately."
    )


def read_lulc_codes(
    path: Path,
    lat: slice = slice(None),
    lon: slice = slice(None),
) -> np.ndarray:
    """
    ESA-CCI class codes as uint8, optionally windowed.

    `lat`/`lon` window the read. The full raster is 64800 x 129600, so production always
    passes slices; the default exists for small files and tests.
    """
    with _open_lccs(path) as codes:
        return _as_class_bytes(codes.isel(lat=lat, lon=lon).values, Path(path).name)


def lulc_fraction(y: np.ndarray, x: np.ndarray, tech: str, lulc_path: Path) -> np.ndarray:
    """
    Mean usable land fraction per model cell, as an exact block mean of ESA-CCI classes.

    The cell for node `(y, x)` spans `+/- ERA5_DATA_RESOLUTION / 2`, and the ESA-CCI axes
    are cell-centred at half-steps of 1/360 deg, so each node's block starts at a
    computable integer index and no interpolation is involved.

    `y` is expected ascending and the ESA-CCI latitude axis descends, hence the flip on
    the way out.

    The read is banded over latitude, which is purely about memory. A whole region is
    hundreds of millions of ESA-CCI pixels, and the class-to-fraction step expands each
    byte to a float32, so an unbanded read peaks at five times the window -- gigabytes for
    the larger regions, for an output of a few hundred kilobytes. Banding costs nothing:
    the same bytes are read either way, and the block mean is per-cell so bands are
    independent.
    """
    i0 = int(round((90.0 - (y.max() + ERA5_DATA_RESOLUTION / 2)) * ESA_CCI_CELLS_PER_DEG))
    j0 = int(round((x.min() - ERA5_DATA_RESOLUTION / 2 + 180.0) * ESA_CCI_CELLS_PER_DEG))
    ny, nx = len(y), len(x)
    name = Path(lulc_path).name
    lut = fraction_lut(tech)
    rows_per_band = max(1, READ_BAND_BYTES // (nx * BLOCK * BLOCK))
    out = np.empty((ny, nx), dtype=np.float64)

    with _open_lccs(lulc_path) as codes:
        n_lat, n_lon = codes.sizes["lat"], codes.sizes["lon"]
        # Bounds are checked here rather than left to the slice: an out-of-range slice
        # comes back short and only fails later, in a reshape naming neither axis nor cell.
        if i0 < 0 or j0 < 0 or i0 + ny * BLOCK > n_lat or j0 + nx * BLOCK > n_lon:
            raise ValueError(
                f"requested cells fall outside {name}: "
                f"lat {y.min()}..{y.max()}, lon {x.min()}..{x.max()} maps to rows "
                f"{i0}..{i0 + ny * BLOCK} of {n_lat} and columns {j0}..{j0 + nx * BLOCK} of {n_lon}"
            )

        for top in range(0, ny, rows_per_band):
            rows = min(rows_per_band, ny - top)
            band = codes.isel(
                lat=slice(i0 + top * BLOCK, i0 + (top + rows) * BLOCK),
                lon=slice(j0, j0 + nx * BLOCK),
            ).values
            # The expansion stays float32 because it is the memory peak -- one float per
            # ESA-CCI pixel in the band -- but the mean accumulates 8100 of them per cell,
            # so the sum is taken in float64. In float32 that accumulation drifts by ~1e-6
            # relative, which is coarser than the fractions are meaningful to.
            #
            # Kept as one expression on purpose: naming the expanded array would hold it
            # past the end of the iteration, so it would still be alive while the next
            # band was read and expanded, doubling the peak this banding exists to bound.
            out[top : top + rows] = (
                lut[_as_class_bytes(band, name)].reshape(rows, BLOCK, nx, BLOCK).mean(axis=(1, 3), dtype=np.float64)
            )
    return out[::-1]


def cds_exclusion_factor(y: np.ndarray, x: np.ndarray, tech: str, masks_dir: Path) -> np.ndarray:
    """
    Availability factor from the Copernicus combined exclusion mask, as `1 - mask`.

    The convention is 1 = excluded, 0 = suitable. Inverting it the wrong way zeroes
    exactly the cells that should survive, and the only downstream symptom is a world
    that looks uniformly infeasible, so the direction is asserted in the tests.

    The two technologies read different files -- the wind mask omits the water layer, so
    offshore stays assessable -- and the delivered arrays carry descending latitude on a
    0..360 longitude, which is folded to the model's ascending / -180..180 grid here.
    """
    path = Path(masks_dir) / EXCLUSION_MASK_FILES[tech]
    with xr.open_dataset(path) as ds:
        mask = ds[EXCLUSION_MASK_VARS[tech]]
        mask = mask.assign_coords(longitude=(mask.longitude + 180) % 360 - 180)
        mask = mask.sortby("longitude").sortby("latitude")
        selected = mask.sel(latitude=y, longitude=x, method="nearest").values
    return 1.0 - selected.astype(np.float64)


LAYER_FUNCS: dict[str, Callable[[np.ndarray, np.ndarray, str, Path], np.ndarray]] = {
    "lulc": lulc_fraction,
    "cds_exclusion": cds_exclusion_factor,
}


def layer_specs(
    names: list[str],
    *,
    lulc_path: Path | None = None,
    masks_dir: Path | None = None,
) -> list[LayerSpec]:
    """
    Configure the named layers, in canonical order, with the sources they need.

    Each spec carries what its numbers depend on, because that is what
    `availability_signature` hashes -- the layer's own data file and, for LULC, the class
    table, which is a settings value that can change without any file changing.
    """
    unknown = sorted(set(names) - set(LAYER_FUNCS))
    if unknown:
        raise ValueError(f"unknown availability layer(s): {unknown}; known: {sorted(LAYER_FUNCS)}")

    specs = []
    for name in LAYER_ORDER:
        if name not in names:
            continue
        if name == "lulc":
            if lulc_path is None:
                raise ValueError("the 'lulc' layer needs lulc_path")
            specs.append(LayerSpec(name, Path(lulc_path), Path(lulc_path).name, {"lulc_codes": LULC_CODES}))
        else:
            if masks_dir is None:
                raise ValueError("the 'cds_exclusion' layer needs masks_dir")
            specs.append(
                LayerSpec(
                    name,
                    Path(masks_dir),
                    ", ".join(EXCLUSION_MASK_FILES[t] for t in sorted(EXCLUSION_MASK_FILES)),
                    {"variables": EXCLUSION_MASK_VARS},
                )
            )
    return specs


def availability_factor(y: np.ndarray, x: np.ndarray, tech: str, specs: list[LayerSpec]) -> np.ndarray:
    """
    The composed factor on the model grid: the product of every configured layer.

    An empty spec list gives ones, which leaves the ceiling exactly as geometry gives it
    -- the current production behaviour, and the baseline any layered run is compared to.
    """
    factor = np.ones((len(y), len(x)), dtype=np.float64)
    for spec in specs:
        factor = factor * LAYER_FUNCS[spec.name](y, x, tech, spec.path)
    return factor


def availability_signature(specs: list[LayerSpec], densities: dict[str, float]) -> str:
    """
    Stable digest of everything that determines a max-capacity store's numbers.

    Stored on the store and compared before reuse. Without it the rebuild check is
    presence-only, so a changed layer set or a changed `--pv-density` silently reuses
    ceilings built against the old values -- and because the ceilings are baked into the
    design cache, nothing downstream can notice either.

    Stability matters as much as sensitivity: an unstable hash would rebuild every store
    on every run, so keys are sorted and the encoding is fixed.
    """
    payload = {
        "layers": [{"name": s.name, "source": s.source, "params": s.params} for s in specs],
        "densities": {k: densities[k] for k in sorted(densities)},
    }
    return sha256(dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def availability_tag(names: list[str]) -> str:
    """
    Short token naming a layer set, for the input-set directory (`cds-2024-lulc+excl`).

    The input set separates `zarr_dir` and, through it, `design_cache_dir`, so putting the
    layer set in the name is what stops a design cache built against one ceiling from
    being reused by a run with another. The signature catches a mismatch; the tag stops
    it arising.
    """
    tags = [LAYER_TAGS[name] for name in LAYER_ORDER if name in names]
    return "+".join(tags) if tags else NO_LAYERS_TAG
