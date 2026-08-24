"""Per-pixel ISO3 attribution — single source of truth across BOA.

The public lookup API is ``iso3_at`` (single point) and ``iso3_at_batch``
(vectorised). Both read ``data/iso3_grid.nc`` (built by
``boa.geo.iso3_grid_builder``) and disambiguate multi-country cells via the
reverse_geocoder cities kd-tree, validated against the cell's candidate set.

For full-globe consumers (summaries, plotting) ``load_iso3_dataarray``
returns the resolved grid as an ``xr.DataArray``.
"""

import gc
import inspect
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pycountry
import reverse_geocoder as rg
import xarray as xr
from shapely.geometry import Point

logger = logging.getLogger(__name__)

# reverse_geocoder kd-tree singleton (cities-only). Lazy-initialised on first
# multi-country cell lookup; do not access directly — use _cities_pick_iso3.
REVERSE_GEOCODER: "rg.RGeocoder | None" = None

# Subregion polygon set keyed by iso3 (loaded by callers per process). Each entry
# is the GeoDataFrame of polygons for that iso3; columns: iso3, subregion, geometry.
SUBREGION_POLYGONS: dict[str, "gpd.GeoDataFrame"] = {}


def reset_singleton(singleton_class) -> None:
    """Clear ``rg.RGeocoder``'s @singleton decorator state so a new instance can be built."""
    decorator_closure = inspect.getclosurevars(singleton_class).nonlocals
    if "instances" in decorator_closure:
        instances_dict = decorator_closure["instances"]
        for key in list(instances_dict.keys()):
            del instances_dict[key]
    else:
        for obj in gc.get_objects():
            if isinstance(obj, dict) and singleton_class in obj:
                obj.pop(singleton_class, None)
                break


def set_global_reverse_geocoder(geocoder: "rg.RGeocoder") -> None:
    reset_singleton(rg.RGeocoder)
    global REVERSE_GEOCODER
    REVERSE_GEOCODER = geocoder


def get_global_reverse_geocoder() -> "rg.RGeocoder | None":
    return REVERSE_GEOCODER


_ISO3_GRID_CACHE: dict[Path, "_Iso3Grid"] = {}


class _Iso3Grid:
    """Cached arrays for a single iso3_grid.nc file."""

    __slots__ = ("lat_axis", "lon_axis", "code_grid", "multi", "candidates")

    def __init__(self, iso3_grid_path: Path) -> None:
        import json

        ds = xr.open_dataset(iso3_grid_path)
        lookup = np.array(json.loads(ds.attrs["iso3_lookup"]), dtype="<U3")
        self.code_grid: np.ndarray = lookup[ds["iso3_code"].values]
        self.lat_axis: np.ndarray = ds.lat.values.astype(float)
        self.lon_axis: np.ndarray = ds.lon.values.astype(float)
        if "multi_country" in ds.data_vars:
            self.multi: np.ndarray = ds["multi_country"].values.astype(bool)
            self.candidates: dict[tuple[int, int], list[str]] = {
                tuple(int(p) for p in k.split(",")): v
                for k, v in json.loads(ds.attrs.get("multi_country_candidates", "{}")).items()
            }
        else:
            self.multi = np.zeros_like(self.code_grid, dtype=bool)
            self.candidates = {}


def _load_iso3_grid(iso3_grid_path: Path) -> _Iso3Grid:
    """Return the cached ``_Iso3Grid`` for ``iso3_grid_path``."""
    cached = _ISO3_GRID_CACHE.get(iso3_grid_path)
    if cached is None:
        cached = _Iso3Grid(iso3_grid_path)
        _ISO3_GRID_CACHE[iso3_grid_path] = cached
    return cached


def _snap_to_grid(grid: _Iso3Grid, lat: float, lon: float) -> tuple[int, int]:
    iy = int(np.argmin(np.abs(grid.lat_axis - lat)))
    ix = int(np.argmin(np.abs(grid.lon_axis - lon)))
    return iy, ix


_CITIES_KDTREE_READY: bool = False


def init_cities_kdtree() -> None:
    """Lazy-init the reverse_geocoder kd-tree with cities only (no polygon seed).

    Idempotent and process-local. The default rg cities file (~28k entries) is
    enough to disambiguate border cells; polygon-cell intersection at build
    time handles single-country cells without a kd-tree, so the legacy 1 deg
    NE custom-raster seed is no longer needed.

    Public for explicit pre-warming (e.g. Celery prefork workers, where eager
    init lets all forked children share the kd-tree via copy-on-write).
    """
    global _CITIES_KDTREE_READY
    if _CITIES_KDTREE_READY and get_global_reverse_geocoder() is not None:
        return
    reset_singleton(rg.RGeocoder)
    set_global_reverse_geocoder(rg.RGeocoder(mode=1))
    _CITIES_KDTREE_READY = True


def _cities_pick_iso3(lat: float, lon: float) -> str | None:
    """Cities kd-tree lookup. Returns the iso3 of the nearest seeded city, or None."""
    init_cities_kdtree()
    geo = get_global_reverse_geocoder()
    if geo is None:
        return None
    result = geo.query([(lat, lon)])
    if not result:
        return None
    cc = str(result[0].get("cc", "")).upper()
    if not cc:
        return None
    if len(cc) == 3:
        return cc
    country = pycountry.countries.get(alpha_2=cc)
    return country.alpha_3 if country else None


def iso3_at(lat: float, lon: float, iso3_grid_path: Path) -> str | None:
    """Resolve (lat, lon) to its ISO3 via the single-source-of-truth grid.

    Single-country cells return the stored iso3 directly. Multi-country cells
    are disambiguated by the cities kd-tree (reverse_geocoder), validated
    against the cell's candidate set; the stored largest-area primary is the
    fallback when the kd-tree returns something outside that set. Returns
    ``None`` for cells with no land tag (open ocean or unmapped territory).
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(
            f"Invalid coordinates: ({lat}, {lon}). Latitude must be between -90 and 90, longitude between -180 and 180."
        )
    grid = _load_iso3_grid(iso3_grid_path)
    iy, ix = _snap_to_grid(grid, lat, lon)
    primary = str(grid.code_grid[iy, ix])
    if not primary or primary == "nan":
        return None
    if not grid.multi[iy, ix]:
        return primary
    candidates = grid.candidates.get((iy, ix), [primary])
    pick = _cities_pick_iso3(lat, lon)
    if pick in candidates:
        return pick
    return primary


def iso3_at_batch(
    lats: np.ndarray,
    lons: np.ndarray,
    iso3_grid_path: Path,
) -> np.ndarray:
    """Vectorised ``iso3_at`` for the gridded hot path.

    Returns a string array parallel to the inputs, with ``""`` where the cell
    has no land tag. Multi-country pixels go through the cities kd-tree one at
    a time (rare — ~3% of land cells globally — so the loop is fine).
    """
    grid = _load_iso3_grid(iso3_grid_path)
    iy = np.argmin(np.abs(grid.lat_axis[None, :] - np.asarray(lats)[:, None]), axis=1)
    ix = np.argmin(np.abs(grid.lon_axis[None, :] - np.asarray(lons)[:, None]), axis=1)
    out = grid.code_grid[iy, ix].astype("<U3").copy()
    out[out == "nan"] = ""
    multi_mask = grid.multi[iy, ix]
    if multi_mask.any():
        multi_idx = np.flatnonzero(multi_mask)
        for k in multi_idx:
            ki, kj = int(iy[k]), int(ix[k])
            candidates = grid.candidates.get((ki, kj))
            if not candidates:
                continue
            pick = _cities_pick_iso3(float(lats[k]), float(lons[k]))
            if pick in candidates:
                out[k] = pick
    return out


_ISO3_DA_CACHE: dict[Path, "xr.DataArray"] = {}


def load_iso3_dataarray(iso3_grid_path: Path) -> "xr.DataArray":
    """Full-globe ISO3 DataArray with multi-country cells disambiguated at cell centres.

    Cells with multi_country=1 are resolved via the cities kd-tree at the cell
    centre (the right disambiguation input for gridded callers like summaries
    and the regional batch optimisation). Result is cached per grid path so
    consumers can call this repeatedly without re-running the kd-tree.
    """
    cached = _ISO3_DA_CACHE.get(iso3_grid_path)
    if cached is not None:
        return cached
    grid = _load_iso3_grid(iso3_grid_path)
    resolved = grid.code_grid.copy()
    multi_iy, multi_ix = np.where(grid.multi)
    for k in range(multi_iy.size):
        iy, ix = int(multi_iy[k]), int(multi_ix[k])
        candidates = grid.candidates.get((iy, ix))
        if not candidates:
            continue
        pick = _cities_pick_iso3(float(grid.lat_axis[iy]), float(grid.lon_axis[ix]))
        if pick in candidates:
            resolved[iy, ix] = pick
    da = xr.DataArray(
        resolved,
        dims=("lat", "lon"),
        coords={"lat": grid.lat_axis, "lon": grid.lon_axis},
        name="iso3",
    )
    _ISO3_DA_CACHE[iso3_grid_path] = da
    return da


def load_subregion_polygons(admin1_shapefile_path: Path) -> None:
    """Build the sub-national polygon registry directly from the NE admin-1 dataset.

    Filters admin-1 to the canonical first-order units (``boa.geo.geo_hierarchy``) and keys each
    polygon by its ``geo_key`` (e.g. ``CHN:CN-HB``), registering it under its iso3 in
    ``SUBREGION_POLYGONS``. There is no derived shapefile — the geometry comes straight from NE
    admin-1, matching steel-iq's ``geo_unit_lookup``. A missing admin-1 file is tolerated: runs
    that declare no multi-subregion iso3 in the cost sheet never reach ``derive_subregion``, so
    the registry is genuinely optional."""
    from boa.geo.geo_hierarchy import province_polygons_from_admin1

    if not Path(admin1_shapefile_path).exists():
        logging.info(f"NE admin-1 shapefile {admin1_shapefile_path} not found; skipping subregion load.")
        return
    gdf = province_polygons_from_admin1(admin1_shapefile_path)
    for iso3, group in gdf.groupby("iso3"):
        SUBREGION_POLYGONS[str(iso3)] = group.reset_index(drop=True)


def validate_subregion_coverage(costs: xr.Dataset) -> None:
    """Raise if any iso3 declares multiple subregion keys but no polygons are loaded for it.
    Call after both the cost dataset is built and `load_subregion_polygons` has run."""
    sub_map: dict[str, list[str]] = {}
    for key in costs["iso3"].values:
        sub_map.setdefault(str(key).split(":", 1)[0], []).append(str(key))
    missing = sorted(iso3 for iso3, keys in sub_map.items() if len(keys) > 1 and iso3 not in SUBREGION_POLYGONS)
    if missing:
        raise ValueError(
            f"Cost dataset declares multiple subregions for iso3(s) {missing} but no polygons "
            f"are loaded. Ensure the NE admin-1 dataset (PathConfig.admin1_10m_shapefile_path) "
            f"is present and covers these countries (see boa.geo.geo_hierarchy_overrides)."
        )


def validate_subregion_keys(costs: xr.Dataset, admin1_shapefile_path: Path) -> None:
    """Raise if any authored sub-national cost key is not a recognised geo_key.

    Cross-checks every ``iso3:code`` key in the cost dataset against the canonical geo_hierarchy
    built from NE admin-1 (pycountry first-order ISO 3166-2 + overrides), catching typos, wrong
    levels, or unsupported units in the CAPEX ``Subregion`` column. Soft-degrades (skips the
    check) when the admin-1 source is absent, matching steel-iq's validator behaviour."""
    from boa.geo.geo_hierarchy import geo_hierarchy_from_admin1, valid_geo_keys

    if not Path(admin1_shapefile_path).exists():
        logging.info("NE admin-1 shapefile not found; skipping sub-national geo-key validation.")
        return
    valid = valid_geo_keys(geo_hierarchy_from_admin1(admin1_shapefile_path))
    if not valid:
        return
    declared_iso3s = {k.split(":", 1)[0] for k in valid}
    invalid = sorted(
        str(key)
        for key in costs["iso3"].values
        if ":" in str(key) and str(key).split(":", 1)[0] in declared_iso3s and str(key) not in valid
    )
    if invalid:
        raise ValueError(
            f"CAPEX Subregion declares sub-national cost key(s) {invalid} that are not recognised "
            f"first-order units in the geo_hierarchy (check the ISO 3166-2 code and level against "
            f"boa.geo.geo_hierarchy / the NE admin-1 dataset)."
        )


def derive_subregion(lat: float, lon: float, iso3: str) -> str | None:
    """
    Return the subregion cost-key for (lat, lon) within `iso3`, or None if `iso3` has no
    registered subregion polygons. Uses point-in-polygon; falls back to the nearest polygon
    edge for points just outside any polygon (e.g. coastal slack).
    """
    gdf = SUBREGION_POLYGONS.get(iso3)
    if gdf is None:
        return None
    pt = Point(float(lon), float(lat))
    hits = gdf[gdf.geometry.contains(pt)]
    if len(hits) > 0:
        return str(hits.iloc[0]["subregion"])
    # Nearest-edge fallback (projected CRS for honest distances); matches the
    # summaries' sjoin_nearest settling so both paths agree at coastal pixels.
    pt_proj = gpd.GeoSeries([pt], crs=gdf.crs).to_crs("EPSG:3857").iloc[0]
    dists = gdf.geometry.to_crs("EPSG:3857").distance(pt_proj)
    return str(gdf.iloc[int(dists.values.argmin())]["subregion"])
