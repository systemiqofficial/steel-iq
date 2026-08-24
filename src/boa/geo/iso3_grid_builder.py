"""Build the 0.25 deg per-pixel ISO3 grid from Natural Earth 1:50m polygons.

The grid (``data/iso3_grid.nc``) is the single source of truth for
``(lat, lon) -> iso3`` across BOA — used by the gridded optimisation, the
single-point CLI, and the summary CSVs. This module produces it via
polygon-cell intersection at 0.25 deg, so cells fully inside small countries
(Faroe Islands, Hong Kong, Singapore, ...) are tagged correctly instead of
being lost by point-in-polygon at the cell centre.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import xarray as xr
from shapely.geometry import box

logger = logging.getLogger(__name__)

NE_50M_SUBUNITS_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_map_subunits.zip"
NE_10M_ADMIN1_URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"

# NE subunit codes -> BOA Country mapping iso3. Applied at build time so the
# grid stores BOA-canonical codes and lookups don't need a runtime remap.
# Only needed for subunits whose ISO_A3 is "-99" (no standard ISO 3166-1
# code); subunits with a real ISO_A3 (ALA, ESH, SSD, CCK, CXR, ...) flow
# through unchanged.
NE_TO_BOA: dict[str, str] = {
    "KOS": "XKX",  # Kosovo
    "PSX": "PSE",  # Palestinian Territories (Gaza + West Bank subunits)
    "KAS": "IND",  # Siachen Glacier -> India (nearest city)
    "CYN": "CYP",  # Northern Cyprus -> Cyprus
    "SOL": "SOM",  # Somaliland -> Somalia
    "ATC": "AUS",  # Ashmore and Cartier Islands -> Australia
    "MAC": "CHN",  # Macao -> China (no MAC row in BOA Country mapping)
}


def ensure_ne_50m_shapefile(shapefile_path: Path, *, force: bool = False) -> Path:
    """Ensure the NE 1:50m map_subunits shapefile is unpacked at ``shapefile_path``.

    The map_subunits layer splits sovereigns into their constituent iso3s
    (e.g. France into FRA + GUF + MTQ + GLP + REU + MYT), which the
    admin_0_countries layer collapses into a single FRA polygon. Downloads
    the 836 KB ZIP from naciscdn.org if the .shp is missing.
    """
    if shapefile_path.exists() and not force:
        return shapefile_path
    target_dir = shapefile_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading NE 1:50m map_subunits shapefile from %s", NE_50M_SUBUNITS_URL)
    with urllib.request.urlopen(NE_50M_SUBUNITS_URL, timeout=60) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(target_dir)
    if not shapefile_path.exists():
        raise FileNotFoundError(
            f"After extracting NE 1:50m ZIP to {target_dir}, expected {shapefile_path} "
            "but it is still missing. Inspect the extracted files."
        )
    logger.info("Extracted NE 1:50m map_subunits to %s", target_dir)
    return shapefile_path


def ensure_ne_10m_admin1_shapefile(shapefile_path: Path, *, force: bool = False) -> Path:
    """Ensure the NE 1:10m admin-1 states/provinces shapefile is unpacked at ``shapefile_path``.

    This is the first-order (province/state) layer, source of sub-national
    geometry for the geo_hierarchy (China now, others later). Distinct from the
    50m country grid — see ``boa.config.paths`` for why it is not a replacement.
    Downloads the ZIP from naciscdn.org if the .shp is missing.
    """
    if shapefile_path.exists() and not force:
        return shapefile_path
    target_dir = shapefile_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading NE 1:10m admin-1 shapefile from %s", NE_10M_ADMIN1_URL)
    with urllib.request.urlopen(NE_10M_ADMIN1_URL, timeout=120) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(target_dir)
    if not shapefile_path.exists():
        raise FileNotFoundError(
            f"After extracting NE 1:10m admin-1 ZIP to {target_dir}, expected {shapefile_path} "
            "but it is still missing. Inspect the extracted files."
        )
    logger.info("Extracted NE 1:10m admin-1 to %s", target_dir)
    return shapefile_path


def _build_cell_grid(resolution: float) -> tuple[np.ndarray, np.ndarray, gpd.GeoDataFrame]:
    """Build a GeoDataFrame of (lat, lon)-indexed cell boxes at ``resolution``.

    Each cell is centred on (lat, lon) and spans ``[lon-r/2, lon+r/2] x [lat-r/2, lat+r/2]``.
    Returns (lat_axis, lon_axis, cells_gdf with columns iy, ix, geometry).
    """
    lats = np.arange(-90.0, 90.0 + 1e-9, resolution)
    lons = np.arange(-180.0, 180.0 + 1e-9, resolution)
    half = resolution / 2.0
    records = []
    for iy, lat in enumerate(lats):
        ymin = lat - half
        ymax = lat + half
        for ix, lon in enumerate(lons):
            records.append((iy, ix, box(lon - half, ymin, lon + half, ymax)))
    cells = gpd.GeoDataFrame(
        {"iy": [r[0] for r in records], "ix": [r[1] for r in records]},
        geometry=[r[2] for r in records],
        crs="EPSG:4326",
    )
    return lats, lons, cells


def _load_countries(shapefile_path: Path) -> gpd.GeoDataFrame:
    """Load NE 1:50m map_subunits, derive per-subunit iso3, apply NE_TO_BOA remap.

    Strategy: use ``ISO_A3`` (the dependency-level iso3 like ``GUF``, ``MYT``,
    ``SJM``), and where ``ISO_A3 == '-99'`` (i.e. the subunit has no separate
    ISO 3166-1 code — Corsica, Northern Cyprus, etc.) fall back to ``ADM0_A3``
    (the sovereign). This gives French overseas departments / Svalbard /
    Bonaire / similar territories their own polygon while keeping Corsica
    rolled into France.
    """
    gdf = gpd.read_file(shapefile_path)
    required = {"ISO_A3", "ADM0_A3", "geometry"}
    missing = required - set(gdf.columns)
    if missing:
        raise ValueError(f"{shapefile_path} missing columns: {missing}. Expected NE 1:50m map_subunits.")
    iso3 = gdf["ISO_A3"].astype(str)
    sovereign = gdf["ADM0_A3"].astype(str)
    iso3 = iso3.where(iso3 != "-99", sovereign)
    out = gpd.GeoDataFrame(
        {"iso3": iso3.values, "geometry": gdf.geometry.values},
        crs=gdf.crs,
    )
    out["iso3"] = out["iso3"].astype(str).replace(NE_TO_BOA)
    # Drop any remaining 3-char placeholders that wouldn't survive a real iso3 lookup.
    out = out[out["iso3"].astype(str).str.len() == 3]
    return out


def build_iso3_grid_from_shapefile(
    output_path: Path,
    *,
    shapefile_path: Path,
    resolution: float = 0.25,
    force: bool = False,
) -> Path:
    """Generate ``data/iso3_grid.nc`` from NE 1:50m via polygon-cell intersection.

    Algorithm:
      1. Load NE 1:50m map_subunits, derive iso3 from ISO_A3 (or ADM0_A3
         when ISO_A3 == "-99"), apply NE_TO_BOA remap.
      2. Build a 721x1441 grid of cell boxes at ``resolution``.
      3. sjoin(cells, countries, predicate='intersects') -> all (cell, polygon) pairs.
      4. Per cell:
         - 0 polygons    -> iso3='nan',  multi=0
         - 1 polygon     -> iso3=that,   multi=0
         - 2+ polygons   -> iso3=largest-area candidate, multi=1
      5. Encode uint8 codes + JSON lookup attr.
      6. Add a ``multi_country`` uint8 var (0/1).
      7. Stash a JSON attr ``multi_country_candidates`` mapping
         ``"iy,ix"`` to the candidate iso3 list (only for multi cells).

    Output schema:
      coords:    lat (721,), lon (1441,)
      data vars: iso3_code (uint8, lat,lon), multi_country (uint8, lat,lon)
      attrs:     iso3_lookup (JSON list[str]; index = code; last entry is 'nan'),
                 multi_country_candidates (JSON dict[str, list[str]]),
                 description, source

    Returns the output path.
    """
    if output_path.exists() and not force:
        logger.info("iso3 grid already exists at %s (use --force to rebuild)", output_path)
        return output_path

    logger.info("Loading NE 1:50m countries from %s", shapefile_path)
    countries = _load_countries(shapefile_path)
    logger.info("  %d polygons across %d iso3s", len(countries), countries["iso3"].nunique())

    logger.info("Building %g deg cell grid", resolution)
    lats, lons, cells = _build_cell_grid(resolution)
    n_lat, n_lon = len(lats), len(lons)
    logger.info("  %d cells (%d lat x %d lon)", n_lat * n_lon, n_lat, n_lon)

    logger.info("Spatial join (cells x countries, intersects)")
    joined = gpd.sjoin(cells, countries, predicate="intersects", how="inner").reset_index(drop=True)
    # Element-wise intersection area per (cell, polygon) — used to pick the
    # largest-area candidate stored as each cell's primary iso3_code. Lookup-time
    # disambiguation (cities kd-tree, in iso3_finder) falls back to this primary
    # only when the kd-tree's pick is not in the cell's candidate set.
    import shapely

    left_geoms = joined.geometry.values
    right_geoms = countries.geometry.iloc[joined["index_right"].values].values
    joined["_inter_area"] = shapely.area(shapely.intersection(left_geoms, right_geoms))
    logger.info("  %d (cell, country) intersection pairs", len(joined))

    # Aggregate per cell: largest-area iso3 + list of all candidates.
    by_cell = joined.groupby(["iy", "ix"], sort=False)
    primary_idx = by_cell["_inter_area"].idxmax()
    primary = joined.loc[primary_idx, ["iy", "ix", "iso3"]].set_index(["iy", "ix"])
    candidate_lists = by_cell["iso3"].apply(lambda s: sorted(set(s)))

    # Build the (lat, lon) string grid.
    iso3_grid = np.full((n_lat, n_lon), "nan", dtype="<U3")
    for (iy, ix), row in primary.iterrows():
        iso3_grid[int(iy), int(ix)] = str(row["iso3"])

    multi_grid = np.zeros((n_lat, n_lon), dtype=np.uint8)
    multi_candidates: dict[str, list[str]] = {}
    for (iy, ix), cands in candidate_lists.items():
        if len(cands) > 1:
            multi_grid[int(iy), int(ix)] = 1
            multi_candidates[f"{int(iy)},{int(ix)}"] = list(cands)

    n_multi = int(multi_grid.sum())
    n_tagged = int((iso3_grid != "nan").sum())
    logger.info(
        "  %d cells tagged, %d multi-country (%.2f%%)",
        n_tagged,
        n_multi,
        100.0 * n_multi / max(n_tagged, 1),
    )

    # Encode iso3 strings -> uint8 codes + JSON lookup. 'nan' is the last entry
    # so the (iso3_code == nan_code) check matches the legacy schema.
    distinct = sorted(set(iso3_grid.flatten()) - {"nan"})
    lookup = distinct + ["nan"]
    if len(lookup) > 255:
        raise RuntimeError(
            f"iso3 lookup has {len(lookup)} entries; uint8 codes max 256. Bump the encoding dtype if this is genuine."
        )
    code_for = {s: i for i, s in enumerate(lookup)}
    codes = np.vectorize(code_for.__getitem__, otypes=[np.uint8])(iso3_grid)

    ds = xr.Dataset(
        {
            "iso3_code": (("lat", "lon"), codes),
            "multi_country": (("lat", "lon"), multi_grid),
        },
        coords={"lat": lats, "lon": lons},
        attrs={
            "iso3_lookup": json.dumps(lookup),
            "multi_country_candidates": json.dumps(multi_candidates),
            "description": (
                "ISO3 country code per (lat, lon) pixel on the 0.25 deg ERA5 grid. "
                "Built from Natural Earth 1:50m ADM0_A3_US via polygon-cell intersection. "
                "Cells with >= 2 candidate countries set multi_country=1; the stored "
                "iso3 is the largest-area candidate."
            ),
            "source": (
                f"Natural Earth 1:50m countries ({shapefile_path.name}) -> "
                "build_iso3_grid_from_shapefile (NE_TO_BOA remap applied)"
            ),
        },
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = {
        "iso3_code": {"zlib": True, "complevel": 9, "shuffle": True},
        "multi_country": {"zlib": True, "complevel": 9, "shuffle": True},
    }
    ds.to_netcdf(output_path, encoding=encoding)
    logger.info(
        "  wrote %s (%d iso3 codes, %d bytes)",
        output_path,
        len(lookup),
        output_path.stat().st_size,
    )
    return output_path
