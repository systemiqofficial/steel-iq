"""
Baseload LCOE per geo key, for pricing existing plants' green hydrogen at the scenario power mix.

The country-level hydrogen price prices electrolysis power as ``(1 - c) × grid + c × LCOE``, c being
the baseload coverage of the power mix. This module supplies the LCOE: a configurable percentile of
the LCOE of each priced geo key's cells, read from the promoted BOA file the run's power mix uses.
A geo key is a country, or a declared sub-national unit such as ``CHN:CN-NM``.

A geo key without a finite cell of its own (microstates, small islands) takes the mean of the
nearest finite cells around its location, so every priced geo key gets a baseload LCOE.
"""

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import xarray as xr
from scipy.spatial import cKDTree

from steelo.adapters.geospatial import boa_lcoe
from steelo.adapters.geospatial.geo_unit_lookup import ADMIN1_LAYER_NAME, _load_admin1_layer
from steelo.adapters.geospatial.geospatial_layers import add_iso3_codes
from steelo.domain.constants import GEO_RESOLUTION, PERMWh_TO_PERkWh, Year
from steelo.utilities import energy_trace

if TYPE_CHECKING:
    import geopandas as gpd

    from steelo.domain.models import GeoDataPaths

NEIGHBOUR_PIXELS = 8  # finite cells averaged around each location of a geo key that has none of its own


def build_geo_key_grid(iso3: xr.DataArray, geo_paths: "GeoDataPaths") -> xr.DataArray:
    """
    The geo key of every cell of the global grid: its iso3, refined to ``ISO3:CODE`` where
    ``geo_hierarchy.json`` declares the admin-1 unit.

    Args:
        iso3: The global iso3 grid (lat, lon), as ``add_iso3_codes`` returns it.
        geo_paths: Paths carrying ``data_dir`` (for ``fixtures/geo_hierarchy.json``) and
            ``admin1_shapefile_dir``.

    Returns:
        A DataArray of geo keys on the iso3 grid's coordinates, named ``geo_key``.

    Notes:
        Uses the same admin-1 geometry and hierarchy gate that tag plant locations, so a cell and a
        plant in the same province resolve to the same key. The spatial join covers only the cells
        of countries with declared units. Without a hierarchy or an admin-1 layer (older geo-data
        packages) every cell stays at country level.
    """
    logger = logging.getLogger(f"{__name__}.build_geo_key_grid")
    keys = iso3.values.astype(str).astype(object)
    hierarchy_path = geo_paths.data_dir / "fixtures" / "geo_hierarchy.json"
    admin1 = (
        _load_admin1_layer(str(geo_paths.admin1_shapefile_dir / f"{ADMIN1_LAYER_NAME}.shp"))
        if geo_paths.admin1_shapefile_dir
        else None
    )
    if not hierarchy_path.exists() or admin1 is None:
        logger.info("No geo_hierarchy or admin-1 layer available; cells resolve at country level only.")
        return xr.DataArray(keys, coords=iso3.coords, dims=iso3.dims, name="geo_key")

    import geopandas as gpd

    declared: dict[str, set[str]] = {}
    for row in json.loads(hierarchy_path.read_text()):
        declared.setdefault(row["iso3"], set()).add(row["geo_unit"])
    lat, lon = iso3["lat"].values, iso3["lon"].values
    for country, codes in declared.items():
        ii, jj = np.nonzero(keys == country)
        units = admin1[(admin1["adm0_a3"] == country) & admin1["iso_3166_2"].isin(codes)][["iso_3166_2", "geometry"]]
        if ii.size == 0 or units.empty:
            continue
        points = gpd.GeoDataFrame({"i": ii, "j": jj}, geometry=gpd.points_from_xy(lon[jj], lat[ii]), crs=admin1.crs)
        hits = gpd.sjoin(points, units, how="inner", predicate="within")
        hits = hits[~hits.index.duplicated()]
        keys[hits["i"].to_numpy(), hits["j"].to_numpy()] = [f"{country}:{code}" for code in hits["iso_3166_2"]]
        logger.info(
            "[GEO KEY] geo_key grid: %s refined %d of %d cells to %d of %d declared units",
            country,
            len(hits),
            ii.size,
            hits["iso_3166_2"].nunique(),
            len(codes),
        )
    return xr.DataArray(keys, coords=iso3.coords, dims=iso3.dims, name="geo_key")


def lcoe_percentile_by_geo_key(lcoe: np.ndarray, geo_key: np.ndarray, percentile: float) -> dict[str, float]:
    """
    A percentile of the finite cell LCOEs of every geo key.

    Args:
        lcoe: Flattened LCOE per cell.
        geo_key: Flattened geo key per cell, aligned with ``lcoe``.
        percentile: Percentile from 0 to 100, interpolated linearly between cells.

    Returns:
        Geo key to the percentile of its finite cells, in the unit of ``lcoe``.

    Notes:
        A country's value spans all its cells, those refined to its sub-national units included;
        each declared unit also gets its own. A geo key without a finite cell is absent.
    """
    finite = np.isfinite(lcoe)
    df = pd.DataFrame({"geo_key": geo_key[finite].astype(str), "lcoe": lcoe[finite]})
    q = percentile / 100.0
    by_country = df.assign(geo_key=df["geo_key"].str.partition(":")[0]).groupby("geo_key")["lcoe"].quantile(q)
    by_unit = df[df["geo_key"].str.contains(":")].groupby("geo_key")["lcoe"].quantile(q)
    return {**by_country.to_dict(), **by_unit.to_dict()}


def _unit_sphere(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """
    Points on the unit sphere, so Euclidean distance orders them as great-circle distance does.

    Args:
        lat: Latitudes in degrees.
        lon: Longitudes in degrees.

    Returns:
        One (x, y, z) row per point.
    """
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def admin1_rows_of_country(admin1: "gpd.GeoDataFrame", iso3: str) -> "gpd.GeoDataFrame":
    """
    The admin-1 features of a country or territory.

    Args:
        admin1: The Natural Earth admin-1 layer.
        iso3: ISO3 code.

    Returns:
        The features filed under the code as country (``adm0_a3``) or geounit (``gu_a3``).

    Notes:
        Natural Earth files most dependencies under their own ``adm0_a3`` or geounit code (GLP, REU,
        TKL, ...). The Caribbean Netherlands (BES) sit under NLD as ``NL-BQ*``, and Svalbard (SJM)
        under NOR as geounit ``NSV``.
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
    The cells whose mean LCOE stands in for each geo key without a finite cell of its own.

    Args:
        missing: The geo keys to fill.
        grid: The geo key grid (lat, lon), as ``build_geo_key_grid`` returns it.
        finite: Whether each cell of the grid has a finite LCOE, in the grid's shape.
        geo_paths: Paths carrying ``admin1_shapefile_dir``, read only for a key without a cell.

    Returns:
        Geo key to the flat indices of its source cells.

    Raises:
        ValueError: If a geo key has neither a cell of its own nor an admin-1 geometry.

    Notes:
        A geo key's locations are its own cells (present but without an LCOE) or, for a territory
        too small to own a cell, the representative point of its admin-1 geometry. Each location
        contributes its ``NEIGHBOUR_PIXELS`` nearest finite cells by great-circle distance.
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
                raise ValueError(f"No LCOE cell and no admin-1 geometry for {key}; cannot price its baseload LCOE")
            point = geometry.geometry.union_all().representative_point()
            loc_lat, loc_lon = np.array([point.y]), np.array([point.x])
        _, nearest = tree.query(_unit_sphere(loc_lat, loc_lon), k=min(NEIGHBOUR_PIXELS, finite_idx.size))
        sources[key] = finite_idx[np.unique(nearest)]
    return sources


def _same_lattice(lcoe: xr.DataArray, grid: xr.DataArray) -> bool:
    """
    Whether an LCOE grid and the geo key grid share their latitudes and longitudes.

    Args:
        lcoe: One year's LCOE grid.
        grid: The geo key grid.

    Returns:
        True when every dimension of ``grid`` is a dimension of ``lcoe`` with the same length and
        coordinates, in any order.
    """
    return all(
        dim in lcoe.dims and lcoe.sizes[dim] == grid.sizes[dim] and np.allclose(lcoe[dim].values, grid[dim].values)
        for dim in grid.dims
    )


def baseload_lcoe_by_geo_key_series(
    lcoe_file: Path,
    coverage: float,
    percentile: float,
    years: Iterable[int],
    geo_keys: Iterable[str],
    geo_paths: "GeoDataPaths",
) -> dict[Year, dict[str, float]]:
    """
    The baseload LCOE of every geo key in every year, at the power mix's coverage.

    Args:
        lcoe_file: The promoted LCOE file for ``coverage``, as ``bootstrap.check_baseload_lcoe_file`` returns it.
        coverage: Baseload coverage of the power mix, above zero.
        percentile: Percentile of a geo key's cells taken as its LCOE (``GeoConfig.hydrogen_lcoe_percentile``).
        years: The years to price.
        geo_keys: The geo keys to price, those of the input costs.
        geo_paths: Paths carrying ``static_layers_dir`` (the iso3 grid), ``data_dir`` (the hierarchy) and
            ``admin1_shapefile_dir``.

    Returns:
        Year to geo key to baseload LCOE in USD/kWh.

    Raises:
        ValueError: If the percentile is outside 0 to 100; if the file is refused (``boa_lcoe.open_lcoe_file``);
            if a year's LCOE grid is not on the iso3 grid's lattice; if a geo key has no LCOE in a year, or
            one that is not finite; or as ``neighbour_fill_sources``.

    Notes:
        Each geo key takes the percentile of its own finite cells, or the mean of its nearest finite cells
        when it has none in the first year priced; those source cells are kept for every later year. A
        missing price is never replaced by the grid's. Years outside the file's span reuse the nearest
        year (``boa_lcoe.lcoe_for_year``). Each year is traced as the ``hydrogen_lcoe`` stage.
    """
    logger = logging.getLogger(f"{__name__}.baseload_lcoe_by_geo_key_series")
    if not 0 <= percentile <= 100:
        raise ValueError(f"hydrogen_lcoe_percentile must be within 0 to 100, got {percentile}")
    priced_keys = list(geo_keys)
    iso3 = add_iso3_codes(resolution=GEO_RESOLUTION, geo_paths=geo_paths)["iso3"]
    grid = build_geo_key_grid(iso3, geo_paths)
    keys_flat = grid.values.ravel()

    series: dict[Year, dict[str, float]] = {}
    fill_sources: dict[str, np.ndarray] | None = None
    with boa_lcoe.open_lcoe_file(lcoe_file, coverage) as lcoe_ds:
        for year in years:
            lcoe = boa_lcoe.lcoe_for_year(lcoe_ds, year)
            if not _same_lattice(lcoe, grid):
                raise ValueError(
                    f"The LCOE grid of {lcoe_file} does not match the iso3 grid; both must share one lattice"
                )
            lcoe = lcoe.transpose(*grid.dims)
            flat = lcoe.values.ravel()
            values = lcoe_percentile_by_geo_key(flat, keys_flat, percentile)
            if fill_sources is None:
                missing = [key for key in priced_keys if key not in values]
                fill_sources = neighbour_fill_sources(missing, grid, np.isfinite(lcoe.values), geo_paths)
                logger.info(
                    "[H2 PRICE] Baseload LCOE from %s at coverage %g, percentile %g: %d geo keys from their own "
                    "cells, %d from their nearest cells",
                    lcoe_file,
                    coverage,
                    percentile,
                    len(priced_keys) - len(missing),
                    len(missing),
                )
                logger.debug("[H2 PRICE] Geo keys priced from their nearest cells: %s", ", ".join(missing))
            for key, idx in fill_sources.items():
                values[key] = float(np.mean(flat[idx]))
            unpriced = [key for key in priced_keys if key not in values]
            if unpriced:
                raise ValueError(f"No baseload LCOE for geo keys {', '.join(unpriced)} in {year}")
            not_finite = [key for key in priced_keys if not np.isfinite(values[key])]
            if not_finite:
                raise ValueError(f"Baseload LCOE not finite for geo keys {', '.join(not_finite)} in {year}")
            year_series = {key: values[key] * PERMWh_TO_PERkWh for key in priced_keys}
            energy_trace.hydrogen_lcoe(year, lcoe, iso3, percentile, year_series, fill_sources)
            series[Year(year)] = year_series
    return series
