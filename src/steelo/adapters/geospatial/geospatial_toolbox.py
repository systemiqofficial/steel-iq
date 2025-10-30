from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from steelo.domain.models import GeoDataPaths

import numpy as np
import pandas as pd
import math
import geopandas as gpd  # type: ignore
import shapely.geometry as shp_geom
import osmnx as ox
from sklearn.preprocessing import MinMaxScaler  # type: ignore
from sklearn.neighbors import BallTree
import xarray as xr
from shapely.geometry import Point
import dask.array as da
from steelo.domain.constants import EARTH_RADIUS, MIN_CAPACITY_FOR_DISTANCE_CALCULATION


class GeoSpatialAdapter:
    """
    Adapter for geospatial operations including country boundaries, grid creation, and distance calculations.
    """

    @staticmethod
    def get_country_boundary(country_name: str) -> gpd.GeoDataFrame:
        """
        Get the boundary of a country by name using OpenStreetMap data.

        Args:
            country_name: Name of the country to retrieve boundary for

        Returns:
            GeoDataFrame containing the country boundary in EPSG:4326 CRS
        """
        boundary = ox.geocode_to_gdf(country_name)
        return boundary.to_crs("EPSG:4326")

    @staticmethod
    def create_grid(boundary: gpd.GeoDataFrame, cell_size: float) -> list[dict[str, float]]:
        """
        Create a grid of cells within a boundary and clip to the boundary extent.

        Args:
            boundary: GeoDataFrame containing the boundary polygon to clip the grid to
            cell_size: Size of each grid cell in degrees

        Returns:
            List of dictionaries, each containing 'geometry' and bounding box coordinates (minx, miny, maxx, maxy)
        """
        minx, miny, maxx, maxy = boundary.total_bounds

        # Use numpy.arange to handle floating-point cell sizes
        x_coords = np.arange(minx, maxx, cell_size)
        y_coords = np.arange(miny, maxy, cell_size)

        grid_polygons = [shp_geom.box(x, y, x + cell_size, y + cell_size) for x in x_coords for y in y_coords]
        grid = gpd.GeoDataFrame({"geometry": grid_polygons}, crs="EPSG:4326")

        # Ensure valid intersection
        clipped_grid = gpd.overlay(grid, boundary, how="intersection")
        clipped_grid = clipped_grid[clipped_grid.is_valid]  # Filter invalid geometries

        result = []
        for geometry in clipped_grid.geometry:
            if geometry.is_empty:  # Skip empty geometries
                continue
            bounds = geometry.bounds  # Extract numeric bounds directly
            result.append(
                {
                    "geometry": geometry,
                    "minx": float(bounds[0]),
                    "miny": float(bounds[1]),
                    "maxx": float(bounds[2]),
                    "maxy": float(bounds[3]),
                }
            )
        return result

    @staticmethod
    def compute_distances(
        grid: list[dict[str, float]], infrastructure: dict[str, gpd.GeoDataFrame]
    ) -> list[dict[str, float]]:
        """
        Compute distances from each grid cell to the nearest infrastructure of each type.

        Args:
            grid: List of grid cells, each a dictionary with geometry and bounds
            infrastructure: Dictionary mapping infrastructure type names to GeoDataFrames containing infrastructure geometries

        Returns:
            Updated grid list with added distance fields (e.g., 'distance_to_{infra_type}_meters')

        Side Effects:
            Modifies the input grid dictionaries in-place by adding distance fields
        """
        for infra_type, gdf in infrastructure.items():
            # Re-project the infrastructure to a suitable CRS (e.g., EPSG:3857 for meters)
            projected_gdf = gdf.to_crs("EPSG:3857")

            for row in grid:
                # Re-project the grid point to match the projected CRS
                point = shp_geom.Point((row["minx"], row["miny"]))
                projected_point = gpd.GeoSeries([point], crs="EPSG:4326").to_crs("EPSG:3857").iloc[0]

                # Calculate distance in meters
                row[f"distance_to_{infra_type}_meters"] = projected_gdf.geometry.distance(projected_point).min()

        return grid

    @staticmethod
    def get_scaler() -> Callable[[list[list[float]]], list[list[float]]]:
        """
        Provide a scaling function using scikit-learn's MinMaxScaler.

        Returns:
            Callable function that takes a 2D list of floats and returns a scaled 2D list using MinMaxScaler
        """
        scaler = MinMaxScaler()

        def scale(data: list[list[float]]) -> list[list[float]]:
            return scaler.fit_transform(data).tolist()

        return scale


def create_global_grid_with_iso(
    resolution: float = 1.0, geo_paths: Optional["GeoDataPaths"] = None
) -> gpd.GeoDataFrame:
    """
    Create a global grid with ISO3 codes for each point.

    Args:
        resolution: Grid resolution in degrees (default: 1.0)
        geo_paths: Paths to geospatial data files containing country shapefiles and disputed areas

    Returns:
        GeoDataFrame with Point geometries and ISO_A3 codes for each grid point

    Note:
        Some countries (Western Sahara, Norway, France, Cyprus, Somalia, Kosovo) are manually corrected due to
        issues with the original shapefile.
    """

    # Create lat/lon grid with given resolution
    lats = np.arange(-90, 90.1, resolution)
    lons = np.arange(-180, 180.1, resolution)
    points = [Point(float(lon), float(lat)) for lat in lats for lon in lons]

    # Load land polygons with ISO3 codes
    if not geo_paths or not geo_paths.countries_shapefile_dir:
        raise ValueError("geo_paths.countries_shapefile_dir is required")
    if not geo_paths.disputed_areas_shapefile_dir:
        raise ValueError("geo_paths.disputed_areas_shapefile_dir is required")

    countries_shapefile_path = geo_paths.countries_shapefile_dir / "ne_110m_admin_0_countries.shp"
    disputed_areas_shapefile_path = geo_paths.disputed_areas_shapefile_dir / "ne_10m_admin_0_disputed_areas.shp"
    countries = gpd.read_file(countries_shapefile_path)[["ISO_A3", "geometry"]]
    disputed_areas = gpd.read_file(disputed_areas_shapefile_path)[["ISO_A3", "geometry"]]

    # Add Western Sahara disputed area
    # Assign 14 to Western Sahara
    disputed_areas.loc[14, "ISO_A3"] = "ESH"
    western_sahara = disputed_areas[disputed_areas.ISO_A3 == "ESH"]
    land = gpd.GeoDataFrame(pd.concat([countries, western_sahara], ignore_index=True), crs=countries.crs)

    # Manually correct wrongly assigned polygons in source data (ISO_A3 = -99)
    # Assign 21 to Norway
    land.loc[21, "ISO_A3"] = "NOR"
    # Assign 43 to France
    land.loc[43, "ISO_A3"] = "FRA"
    # Assign 160 to Cyprus
    land.loc[160, "ISO_A3"] = "CYP"
    # Assign 167 to Somalia
    land.loc[167, "ISO_A3"] = "SOM"
    # Assign 174 to Kosovo
    land.loc[174, "ISO_A3"] = "XKX"

    # Assign each point to a country code
    gdf = gpd.GeoDataFrame(geometry=points, crs=land.crs)
    labelled_points = gpd.sjoin(gdf, land, how="left", predicate="within").drop(columns="index_right")

    return labelled_points


def haversine_distance(row):
    """
    Calculate distance between two points on Earth using the Haversine formula.

    Args:
        row: Array-like containing [lat1, lon1, lat2, lon2] in degrees

    Returns:
        Distance between the two points in kilometers
    """
    lat1, lon1, lat2, lon2 = map(math.radians, row)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return c * EARTH_RADIUS


def haversine_dask(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two points on Earth using the Haversine formula with Dask parallelization.

    Args:
        lat1: Latitude of first point(s) in degrees (array-like)
        lon1: Longitude of first point(s) in degrees (array-like)
        lat2: Latitude of second point(s) in degrees (array-like)
        lon2: Longitude of second point(s) in degrees (array-like)

    Returns:
        Dask array of distances in kilometers
    """
    lat1 = da.radians(lat1)[:, None]
    lon1 = da.radians(lon1)[:, None]
    lat2 = da.radians(lat2)[None, :]
    lon2 = da.radians(lon2)[None, :]
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = da.sin(dlat / 2) ** 2 + da.cos(lat1) * da.cos(lat2) * da.sin(dlon / 2) ** 2
    c = 2 * da.arcsin(da.sqrt(a))
    return c * EARTH_RADIUS


def generate_grid(bbox: dict, resolution: float) -> gpd.GeoSeries:
    """
    Generate a grid of points within a bounding box.

    Args:
        bbox: Bounding box dictionary with keys 'minx', 'miny', 'maxx', 'maxy' in degrees
        resolution: Grid spacing in degrees

    Returns:
        GeoSeries of Point geometries representing the grid points (CRS: EPSG:4326)
    """

    x = np.arange(bbox["minx"] + resolution / 2, bbox["maxx"] - resolution / 2, resolution)
    y = np.arange(bbox["miny"] + resolution / 2, bbox["maxy"] - resolution / 2, resolution)
    xs, ys = np.meshgrid(x, y)

    coords = np.asarray((np.ravel(xs), np.ravel(ys))).T
    return gpd.GeoSeries(gpd.points_from_xy(coords[:, 0], coords[:, 1]), crs="EPSG:4326")


def distance_to_closest_location(
    weighted_locations: dict,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
    batch_size: int = 100_000,
) -> xr.DataArray:
    """
    Compute distance from each target point to the closest qualifying location using BallTree for efficiency.

    Steps:
        1. Filter locations by minimum capacity threshold (locations with capacity <= MIN_CAPACITY_FOR_DISTANCE_CALCULATION are excluded)
        2. Check for potential unit mismatches between capacities and threshold
        3. Build BallTree with haversine metric for efficient nearest-neighbor queries
        4. Query nearest neighbor for each target point in batches

    Args:
        weighted_locations: Dictionary mapping Location objects to capacity values (tonnes)
        target_lats: 1D array of target latitudes in degrees
        target_lons: 1D array of target longitudes in degrees
        batch_size: Number of points to process per batch for memory efficiency (default: 100,000)

    Returns:
        DataArray with dimensions (lat, lon) containing distances in kilometers to the closest qualifying location
    """
    # 1) Early exit
    if not weighted_locations:
        raise ValueError("No weighted locations provided for distance calculation.")

    # 2) Check for potential unit mismatch
    # Raise an error if there's a potential order of magnitude issue suggesting wrong units
    capacities = [cap for cap in weighted_locations.values() if cap > 0]
    if capacities:
        # Use percentiles to be robust to outliers
        sorted_caps = sorted(capacities)
        p5_cap = sorted_caps[int(len(sorted_caps) * 0.05)] if len(sorted_caps) > 1 else sorted_caps[0]
        p95_cap = sorted_caps[int(len(sorted_caps) * 0.95)] if len(sorted_caps) > 1 else sorted_caps[0]
        median_cap = sorted_caps[len(sorted_caps) // 2]
        min_cap = min(capacities)
        max_cap = max(capacities)

        # Check for two types of unit mismatches using percentiles to avoid outlier influence:
        # Case 1: 95% of capacities are way ABOVE threshold (e.g., 100x or more)
        # This suggests capacities might be in a larger unit than expected
        # Example: capacities in tonnes but threshold in ttpa (tonnes per annum)
        if p5_cap > MIN_CAPACITY_FOR_DISTANCE_CALCULATION * 100:
            raise ValueError(
                f"Potential unit mismatch detected: 95% of capacities are significantly above the threshold.\n"
                f"MIN_CAPACITY_FOR_DISTANCE_CALCULATION: {MIN_CAPACITY_FOR_DISTANCE_CALCULATION:.0f}\n"
                f"Capacity range: {min_cap:.0f} - {max_cap:.0f} (median: {median_cap:.0f})\n"
                f"5th percentile: {p5_cap:.0f} ({p5_cap / MIN_CAPACITY_FOR_DISTANCE_CALCULATION:.1f}x threshold)\n"
                f"95th percentile: {p95_cap:.0f} ({p95_cap / MIN_CAPACITY_FOR_DISTANCE_CALCULATION:.1f}x threshold)\n"
                f"If capacities are in tonnes but threshold is in ttpa, divide capacities by 1000."
            )

        # Case 2: 95% of capacities are way BELOW threshold (e.g., less than 0.001x)
        # This suggests capacities might be in a smaller unit than expected
        # Example: capacities in ktpa but threshold in tonnes
        if p95_cap < MIN_CAPACITY_FOR_DISTANCE_CALCULATION * 0.001:
            raise ValueError(
                f"Potential unit mismatch detected: 95% of capacities are significantly below the threshold.\n"
                f"MIN_CAPACITY_FOR_DISTANCE_CALCULATION: {MIN_CAPACITY_FOR_DISTANCE_CALCULATION:.0f}\n"
                f"Capacity range: {min_cap:.2f} - {max_cap:.2f} (median: {median_cap:.2f})\n"
                f"5th percentile: {p5_cap:.2f} ({p5_cap / MIN_CAPACITY_FOR_DISTANCE_CALCULATION:.6f}x threshold)\n"
                f"95th percentile: {p95_cap:.2f} ({p95_cap / MIN_CAPACITY_FOR_DISTANCE_CALCULATION:.6f}x threshold)\n"
                f"Consider if capacities need to be scaled up (e.g., multiply by 1000 if in kt instead of t)."
            )

    # 3) Filter by capacity before any heavy work
    locs = [
        (loc.lat, loc.lon, cap)
        for loc, cap in weighted_locations.items()
        if cap > MIN_CAPACITY_FOR_DISTANCE_CALCULATION
    ]
    if not locs:
        raise ValueError(
            "Not a single location meets the minimum capacity requirement for the distance calculation. "
            "Decrease minimum capacity or verify input data."
        )

    # 3) Build a BallTree (haversine metric) and query in batches
    loc_rad = np.radians(np.array([(lat, lon) for lat, lon, _ in locs]))
    tree = BallTree(loc_rad, metric="haversine")

    # Prepare target points (meshgrid -> list of points)
    lat_grid, lon_grid = np.meshgrid(target_lats, target_lons, indexing="ij")
    pts = np.stack([lat_grid.ravel(), lon_grid.ravel()], axis=1)
    pts_rad = np.radians(pts)

    # Query nearest neighbor (k=1) in batches
    out = np.empty(pts_rad.shape[0], dtype=np.float32)
    for start in range(0, len(pts_rad), batch_size):
        end = start + batch_size
        dist_rad, _ = tree.query(pts_rad[start:end], k=1)
        out[start:end] = (dist_rad[:, 0] * EARTH_RADIUS).astype(np.float32)

    dist2d = out.reshape(lat_grid.shape)
    return xr.DataArray(dist2d, coords={"lat": target_lats, "lon": target_lons}, dims=["lat", "lon"])
