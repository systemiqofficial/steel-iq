"""Baseload LCOE per geo_key for pricing green hydrogen at the scenario power mix.

The country-level hydrogen price prices electrolysis power as ``(1 - c) * grid + c * LCOE``,
``c`` being the baseload share of the power mix. This module supplies the LCOE half: a
configurable percentile of the BOA pixel LCOE distribution for every geo_key that carries an
electricity price — a country, or a declared sub-national unit such as ``CHN:CN-NM`` — read
off the same BOA files the geo siting layer prices power from.

A geo_key without any BOA pixel (microstates, small islands) takes the mean of the nearest
finite pixels around its location, so every priced geo_key gets a green-power price.
"""

import json
import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import xarray as xr
from scipy.spatial import cKDTree

from steelo.domain.constants import GEO_RESOLUTION, PERMWh_TO_PERkWh, Year

from .geo_unit_lookup import ADMIN1_LAYER_NAME, _load_admin1_layer
from .geospatial_layers import _lcoe_from_yearly_files, add_iso3_codes

if TYPE_CHECKING:
    import geopandas as gpd

    from steelo.domain.models import GeoDataPaths

NEIGHBOUR_PIXELS = 8  # finite pixels averaged around each location of a geo_key that has none of its own


def build_geo_key_grid(geo_paths: "GeoDataPaths") -> xr.DataArray:
    """
    Per-pixel geo_key on the global grid: iso3, refined to ``ISO3:CODE`` where geo_hierarchy
    declares the admin-1 unit.

    Uses the same admin-1 geometry and hierarchy gate that tags plant locations, so a pixel and
    a plant in the same province resolve to the same key. Without a hierarchy or admin-1 layer
    (older geo-data packages) every pixel stays at country level.
    """
    logger = logging.getLogger(f"{__name__}.build_geo_key_grid")
    iso3 = add_iso3_codes(resolution=GEO_RESOLUTION, geo_paths=geo_paths)["iso3"]
    keys = iso3.values.astype(str).astype(object)
    hierarchy_path = geo_paths.data_dir / "fixtures" / "geo_hierarchy.json"
    admin1 = (
        _load_admin1_layer(str(geo_paths.admin1_shapefile_dir / f"{ADMIN1_LAYER_NAME}.shp"))
        if geo_paths.admin1_shapefile_dir
        else None
    )
    if not hierarchy_path.exists() or admin1 is None:
        logger.info("No geo_hierarchy or admin-1 layer available; baseload LCOE resolves at country level only.")
        return xr.DataArray(keys, coords=iso3.coords, dims=iso3.dims, name="geo_key")

    import geopandas as gpd
    from shapely.geometry import Point

    declared: dict[str, set[str]] = {}
    for row in json.loads(hierarchy_path.read_text()):
        declared.setdefault(row["iso3"], set()).add(row["geo_unit"])
    lat, lon = iso3["lat"].values, iso3["lon"].values
    for country, codes in declared.items():
        ii, jj = np.nonzero(keys == country)
        units = admin1[(admin1["adm0_a3"] == country) & admin1["iso_3166_2"].isin(codes)][["iso_3166_2", "geometry"]]
        if ii.size == 0 or units.empty:
            continue
        points = gpd.GeoDataFrame(
            {"i": ii, "j": jj}, geometry=[Point(lon[j], lat[i]) for i, j in zip(ii, jj)], crs=admin1.crs
        )
        hits = gpd.sjoin(points, units, how="inner", predicate="within")
        hits = hits[~hits.index.duplicated()]
        keys[hits["i"].to_numpy(), hits["j"].to_numpy()] = [f"{country}:{code}" for code in hits["iso_3166_2"]]
        logger.info(
            "[H2 PRICE] geo_key grid: %s refined %d of %d pixels to %d of %d declared units",
            country,
            len(hits),
            ii.size,
            hits["iso_3166_2"].nunique(),
            len(codes),
        )
    return xr.DataArray(keys, coords=iso3.coords, dims=iso3.dims, name="geo_key")


def lcoe_percentile_by_geo_key(lcoe: np.ndarray, geo_key: np.ndarray, percentile: float) -> dict[str, float]:
    """
    Percentile of the finite pixel LCOEs per geo_key.

    A country's value spans all its pixels, including those refined to its sub-national units;
    each declared unit also gets its own. geo_keys without a finite pixel are absent.
    """
    finite = np.isfinite(lcoe)
    df = pd.DataFrame({"geo_key": geo_key[finite].astype(str), "lcoe": lcoe[finite]})
    q = percentile / 100.0
    by_country = df.assign(geo_key=df["geo_key"].str.partition(":")[0]).groupby("geo_key")["lcoe"].quantile(q)
    by_unit = df[df["geo_key"].str.contains(":")].groupby("geo_key")["lcoe"].quantile(q)
    return {**by_country.to_dict(), **by_unit.to_dict()}


def _unit_sphere(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def admin1_rows_of_country(admin1: "gpd.GeoDataFrame", iso3: str) -> "gpd.GeoDataFrame":
    """
    Admin-1 features belonging to a country. Natural Earth files most dependencies under their
    own ``adm0_a3`` or geounit code (``gu_a3``: GLP, REU, TKL, …); the Caribbean Netherlands
    sit under NLD as ``NL-BQ*`` and Svalbard under NOR as geounit ``NSV``.
    """
    if iso3 == "BES":
        return admin1[admin1["iso_3166_2"].str.startswith("NL-BQ", na=False)]
    if iso3 == "SJM":
        return admin1[admin1["gu_a3"] == "NSV"]
    return admin1[(admin1["adm0_a3"] == iso3) | (admin1["gu_a3"] == iso3)]


def neighbour_fill_sources(
    missing: Iterable[str], grid: xr.DataArray, finite: np.ndarray, geo_paths: "GeoDataPaths"
) -> dict[str, np.ndarray]:
    """
    Flat pixel indices whose mean LCOE stands in for each geo_key that has no finite pixel.

    A geo_key's locations are its own grid pixels (present but unmodelled) or, for a territory too
    small to own a pixel, the representative point of its admin-1 geometry; each location contributes
    its NEIGHBOUR_PIXELS nearest finite pixels by great-circle distance.
    """
    missing = list(missing)
    if not missing:
        return {}
    lat2d, lon2d = np.meshgrid(grid["lat"].values, grid["lon"].values, indexing="ij")
    finite_idx = np.flatnonzero(finite.ravel())
    tree = cKDTree(_unit_sphere(lat2d.ravel()[finite_idx], lon2d.ravel()[finite_idx]))
    keys = grid.values
    admin1 = None
    sources: dict[str, np.ndarray] = {}
    for key in missing:
        own = np.nonzero(keys == key)
        if own[0].size:
            loc_lat, loc_lon = lat2d[own], lon2d[own]
        else:
            if admin1 is None and geo_paths.admin1_shapefile_dir is not None:
                admin1 = _load_admin1_layer(str(geo_paths.admin1_shapefile_dir / f"{ADMIN1_LAYER_NAME}.shp"))
            geometry = admin1_rows_of_country(admin1, key.partition(":")[0]) if admin1 is not None else None
            if geometry is None or geometry.empty:
                raise ValueError(f"No BOA pixel and no admin-1 geometry for {key}; cannot price its baseload LCOE")
            point = geometry.geometry.union_all().representative_point()
            loc_lat, loc_lon = np.array([point.y]), np.array([point.x])
        _, nearest = tree.query(_unit_sphere(loc_lat, loc_lon), k=min(NEIGHBOUR_PIXELS, finite_idx.size))
        sources[key] = finite_idx[np.unique(nearest)]
    return sources


def _year_loader(geo_paths: "GeoDataPaths", p: int) -> Callable[[int], xr.DataArray]:
    """Per-year LCOE (USD/MWh) reader: the combined BOA file when configured, else the per-year files."""
    logger = logging.getLogger(f"{__name__}.baseload_lcoe_by_geo_key_series")
    if geo_paths.baseload_lcoe_file is not None:
        ds = xr.open_dataset(geo_paths.baseload_lcoe_file)
        file_p = ds.attrs.get("p_percentile")
        if file_p is not None and int(file_p) != p:
            raise ValueError(
                f"Baseload LCOE file {geo_paths.baseload_lcoe_file.name} was run at p{int(file_p)}, but the hydrogen "
                f"power mix implies p{p}"
            )
        logger.info("[H2 PRICE] Baseload LCOE from %s (run '%s').", geo_paths.baseload_lcoe_file, ds.attrs.get("run"))
        available = [int(y) for y in ds["year"].values]

        def load(year: int) -> xr.DataArray:
            effective = min(max(year, min(available)), max(available))
            if effective in available:
                return ds["lcoe"].sel(year=effective).drop_vars("year")
            return ds["lcoe"].interp(year=effective, method="linear").drop_vars("year")

        return load
    if not geo_paths.baseload_power_sim_dir:
        raise ValueError("baseload_power_sim_dir or baseload_lcoe_file is required to price hydrogen baseload power")
    baseload_dir = geo_paths.baseload_power_sim_dir
    return lambda year: _lcoe_from_yearly_files(baseload_dir, p, year, logger)[0]


def baseload_lcoe_by_geo_key_series(
    geo_paths: "GeoDataPaths",
    coverage: float,
    percentile: float,
    years: Iterable[int],
    geo_keys: Iterable[str],
) -> dict[Year, dict[str, float]]:
    """
    Baseload LCOE (USD/kWh) per geo_key per year at the power mix's coverage.

    Each geo_key takes the ``percentile`` of its BOA pixel LCOE distribution, or the nearest-pixel
    mean when it has no pixel. Every requested geo_key is priced for every year, or a ValueError
    is raised — a missing price must never fall back to the grid silently.
    """
    from boa.conversions import coverage_to_percentile

    logger = logging.getLogger(f"{__name__}.baseload_lcoe_by_geo_key_series")
    if not 0 <= percentile <= 100:
        raise ValueError(f"hydrogen_lcoe_percentile must be within 0..100, got {percentile}")
    geo_keys, years = list(geo_keys), list(years)
    p = coverage_to_percentile(coverage)
    grid = build_geo_key_grid(geo_paths)
    keys_flat = grid.values.ravel()
    load = _year_loader(geo_paths, p)

    series: dict[Year, dict[str, float]] = {}
    fill_sources: dict[str, np.ndarray] | None = None
    for year in years:
        lcoe = load(year)
        if not (
            np.allclose(lcoe["lat"].values, grid["lat"].values) and np.allclose(lcoe["lon"].values, grid["lon"].values)
        ):
            raise ValueError("BOA LCOE grid does not match the iso3 grid; both must share the GEO_RESOLUTION lattice")
        flat = lcoe.values.ravel()
        values = lcoe_percentile_by_geo_key(flat, keys_flat, percentile)
        if fill_sources is None:
            missing = [k for k in geo_keys if k not in values]
            fill_sources = neighbour_fill_sources(missing, grid, np.isfinite(lcoe.values), geo_paths)
            logger.info(
                "[H2 PRICE] Baseload LCOE at p%d, percentile %g: %d geo_keys from own pixels, %d from nearest pixels",
                p,
                percentile,
                len(geo_keys) - len(missing),
                len(missing),
            )
            logger.debug("[H2 PRICE] geo_keys priced from nearest pixels: %s", ", ".join(missing))
        for key, idx in fill_sources.items():
            values[key] = float(np.mean(flat[idx]))
        try:
            series[Year(year)] = {k: values[k] * PERMWh_TO_PERkWh for k in geo_keys}
        except KeyError as exc:
            raise ValueError(f"No baseload LCOE for geo_key {exc} in {year}") from exc
    return series
