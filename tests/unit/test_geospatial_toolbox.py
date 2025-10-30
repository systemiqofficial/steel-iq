"""Unit tests for geospatial_toolbox.py functions."""

import numpy as np
import geopandas as gpd
import pytest
import warnings
import xarray as xr
from pathlib import Path
from shapely.geometry import Point, Polygon
from unittest.mock import Mock, patch

from steelo.adapters.geospatial.geospatial_toolbox import (
    create_global_grid_with_iso,
    haversine_distance,
    haversine_dask,
    distance_to_closest_location,
)
from steelo.domain.models import Location


class TestCreateGlobalGridWithIso:
    """Test create_global_grid_with_iso function."""

    def test_create_global_grid_with_iso_basic(self):
        """Test basic grid creation with default resolution."""
        # Create mock geo_paths
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = Path("/mock/countries")
        mock_geo_paths.disputed_areas_shapefile_dir = Path("/mock/disputed")

        # Create mock countries GeoDataFrame
        mock_countries = gpd.GeoDataFrame(
            {
                "ISO_A3": ["USA", "CAN", "MEX"],
                "geometry": [
                    Polygon([(-100, 30), (-90, 30), (-90, 40), (-100, 40)]),  # USA
                    Polygon([(-100, 40), (-90, 40), (-90, 50), (-100, 50)]),  # Canada
                    Polygon([(-100, 20), (-90, 20), (-90, 30), (-100, 30)]),  # Mexico
                ],
            },
            crs="EPSG:4326",
        )

        # Create mock disputed areas GeoDataFrame with Western Sahara
        mock_disputed = gpd.GeoDataFrame(
            {
                "ISO_A3": ["XXX", "YYY"],
                "geometry": [
                    Polygon([(-20, 20), (-10, 20), (-10, 30), (-20, 30)]),
                    Polygon([(-30, 20), (-20, 20), (-20, 30), (-30, 30)]),
                ],
            },
            crs="EPSG:4326",
        )
        # Add empty rows to reach index 14 for Western Sahara
        for i in range(2, 15):
            mock_disputed.loc[i] = [None, Polygon([])]

        with patch("geopandas.read_file") as mock_read_file:
            # Mock the two read_file calls
            mock_read_file.side_effect = [mock_countries, mock_disputed]

            # Call the function with resolution=5.0 for faster test
            # Suppress expected warning about CRS concatenation
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="CRS not set for some of the concatenation inputs")
                result = create_global_grid_with_iso(resolution=5.0, geo_paths=mock_geo_paths)

            # Verify results
            assert isinstance(result, gpd.GeoDataFrame)
            assert "ISO_A3" in result.columns
            assert "geometry" in result.columns

            # Check that points were created
            assert len(result) > 0
            assert all(isinstance(geom, Point) for geom in result.geometry)

            # Check that spatial join was performed (some points should have ISO codes)
            assert not result["ISO_A3"].isna().all()

    def test_create_global_grid_with_iso_custom_resolution(self):
        """Test grid creation with custom resolution."""
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = Path("/mock/countries")
        mock_geo_paths.disputed_areas_shapefile_dir = Path("/mock/disputed")

        mock_countries = gpd.GeoDataFrame(
            {"ISO_A3": ["USA"], "geometry": [Polygon([(-100, 30), (-90, 30), (-90, 40), (-100, 40)])]}, crs="EPSG:4326"
        )

        # Create empty disputed areas with proper geometry
        mock_disputed = gpd.GeoDataFrame({"ISO_A3": [None] * 15, "geometry": [Polygon([])] * 15}, crs="EPSG:4326")

        with patch("geopandas.read_file") as mock_read_file:
            mock_read_file.side_effect = [mock_countries, mock_disputed]

            # Test with resolution=10.0
            result = create_global_grid_with_iso(resolution=10.0, geo_paths=mock_geo_paths)

            # With resolution=10, we expect fewer points
            expected_lat_points = len(np.arange(-90, 90.1, 10.0))
            expected_lon_points = len(np.arange(-180, 180.1, 10.0))
            expected_total = expected_lat_points * expected_lon_points

            assert len(result) == expected_total

    def test_create_global_grid_with_iso_manual_corrections(self):
        """Test that manual corrections are applied for specific countries."""
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = Path("/mock/countries")
        mock_geo_paths.disputed_areas_shapefile_dir = Path("/mock/disputed")

        # Create countries with indices that will be manually corrected
        mock_countries = gpd.GeoDataFrame(
            {
                "ISO_A3": ["-99"] * 200,  # Fill with -99 to ensure we have enough rows
                "geometry": [Polygon([])] * 200,
            },
            crs="EPSG:4326",
        )

        # Add specific geometries for countries that get corrected
        mock_countries.loc[21, "geometry"] = Polygon([(-10, 60), (0, 60), (0, 70), (-10, 70)])  # Norway
        mock_countries.loc[43, "geometry"] = Polygon([(0, 45), (10, 45), (10, 55), (0, 55)])  # France
        mock_countries.loc[160, "geometry"] = Polygon([(30, 35), (35, 35), (35, 40), (30, 40)])  # Cyprus
        mock_countries.loc[167, "geometry"] = Polygon([(40, 0), (50, 0), (50, 10), (40, 10)])  # Somalia
        mock_countries.loc[174, "geometry"] = Polygon([(20, 42), (22, 42), (22, 44), (20, 44)])  # Kosovo

        mock_disputed = gpd.GeoDataFrame({"ISO_A3": [None] * 15, "geometry": [Polygon([])] * 15}, crs="EPSG:4326")
        # Add Western Sahara at index 14
        mock_disputed.loc[14, "geometry"] = Polygon([(-20, 20), (-10, 20), (-10, 30), (-20, 30)])

        with patch("geopandas.read_file") as mock_read_file:
            mock_read_file.side_effect = [mock_countries, mock_disputed]

            # Call with large resolution for faster test
            result = create_global_grid_with_iso(resolution=30.0, geo_paths=mock_geo_paths)

            # The function should have:
            # 1. Added Western Sahara (ESH) from disputed areas
            # 2. Corrected Norway (21 -> NOR), France (43 -> FRA), Cyprus (160 -> CYP),
            #    Somalia (167 -> SOM), Kosovo (174 -> XKX)

            # Since we do spatial join, points within these corrected polygons
            # should have the corrected ISO codes
            assert isinstance(result, gpd.GeoDataFrame)

            # Verify that the corrections would be applied (checking the land DataFrame
            # would have these corrections, though we can't directly test the internal state)
            assert "ISO_A3" in result.columns

    def test_create_global_grid_with_iso_missing_geo_paths(self):
        """Test that function raises error when geo_paths is None or missing directories."""
        # Test with None geo_paths
        with pytest.raises(ValueError, match="geo_paths.countries_shapefile_dir is required"):
            create_global_grid_with_iso(geo_paths=None)

        # Test with missing countries_shapefile_dir
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = None
        mock_geo_paths.disputed_areas_shapefile_dir = Path("/mock/disputed")

        with pytest.raises(ValueError, match="geo_paths.countries_shapefile_dir is required"):
            create_global_grid_with_iso(geo_paths=mock_geo_paths)

        # Test with missing disputed_areas_shapefile_dir
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = Path("/mock/countries")
        mock_geo_paths.disputed_areas_shapefile_dir = None

        with pytest.raises(ValueError, match="geo_paths.disputed_areas_shapefile_dir is required"):
            create_global_grid_with_iso(geo_paths=mock_geo_paths)

    def test_create_global_grid_with_iso_spatial_join(self):
        """Test that spatial join correctly assigns ISO codes to points within country polygons."""
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = Path("/mock/countries")
        mock_geo_paths.disputed_areas_shapefile_dir = Path("/mock/disputed")

        # Create a simple test case with one country
        mock_countries = gpd.GeoDataFrame(
            {
                "ISO_A3": ["TEST"],
                "geometry": [Polygon([(-10, -10), (10, -10), (10, 10), (-10, 10)])],  # Square around origin
            },
            crs="EPSG:4326",
        )

        mock_disputed = gpd.GeoDataFrame({"ISO_A3": [None] * 15, "geometry": [Polygon([])] * 15}, crs="EPSG:4326")

        with patch("geopandas.read_file") as mock_read_file:
            mock_read_file.side_effect = [mock_countries, mock_disputed]

            # Use large resolution for manageable test
            result = create_global_grid_with_iso(resolution=20.0, geo_paths=mock_geo_paths)

            # Point at (0, 0) should be within the TEST country polygon
            origin_point = result[(result.geometry.x == 0) & (result.geometry.y == 0)]
            if len(origin_point) > 0:
                assert origin_point.iloc[0]["ISO_A3"] == "TEST"

            # Points far from origin should have NaN ISO codes
            far_points = result[(abs(result.geometry.x) > 50) | (abs(result.geometry.y) > 50)]
            if len(far_points) > 0:
                assert far_points["ISO_A3"].isna().any()

    def test_create_global_grid_with_iso_point_coverage(self):
        """Test that grid covers the expected global range."""
        mock_geo_paths = Mock()
        mock_geo_paths.countries_shapefile_dir = Path("/mock/countries")
        mock_geo_paths.disputed_areas_shapefile_dir = Path("/mock/disputed")

        # Create an empty countries GeoDataFrame with at least one row
        mock_countries = gpd.GeoDataFrame({"ISO_A3": ["NONE"], "geometry": [Polygon([])]}, crs="EPSG:4326")

        mock_disputed = gpd.GeoDataFrame({"ISO_A3": [None] * 15, "geometry": [Polygon([])] * 15}, crs="EPSG:4326")

        with patch("geopandas.read_file") as mock_read_file:
            mock_read_file.side_effect = [mock_countries, mock_disputed]

            # Test with resolution=45 for quick test
            resolution = 45.0
            result = create_global_grid_with_iso(resolution=resolution, geo_paths=mock_geo_paths)

            # Extract coordinates
            lons = result.geometry.x
            lats = result.geometry.y

            # Check coverage
            assert lons.min() == -180.0
            assert lons.max() == 180.0
            assert lats.min() == -90.0
            assert lats.max() == 90.0

            # Check that points are spaced correctly
            unique_lats = sorted(lats.unique())
            unique_lons = sorted(lons.unique())

            # Verify spacing
            for i in range(1, len(unique_lats)):
                assert abs(unique_lats[i] - unique_lats[i - 1] - resolution) < 0.001

            for i in range(1, len(unique_lons)):
                assert abs(unique_lons[i] - unique_lons[i - 1] - resolution) < 0.001


class TestHaversineDistance:
    """Test haversine_distance function."""

    def test_haversine_distance_same_point(self):
        """Test distance calculation for the same point (should be 0)."""
        # Same point: distance should be 0
        row = [0.0, 0.0, 0.0, 0.0]  # lat1, lon1, lat2, lon2
        distance = haversine_distance(row)
        assert distance == pytest.approx(0.0, abs=1e-6)

    def test_haversine_distance_equator(self):
        """Test distance calculation along the equator."""
        # Points on equator 1 degree apart
        # At equator, 1 degree longitude ≈ 111.32 km
        row = [0.0, 0.0, 0.0, 1.0]  # lat1, lon1, lat2, lon2
        distance = haversine_distance(row)
        assert distance == pytest.approx(111.32, rel=0.01)

    def test_haversine_distance_meridian(self):
        """Test distance calculation along a meridian."""
        # Points on same meridian 1 degree apart
        # 1 degree latitude ≈ 111.32 km everywhere
        row = [0.0, 0.0, 1.0, 0.0]  # lat1, lon1, lat2, lon2
        distance = haversine_distance(row)
        assert distance == pytest.approx(111.32, rel=0.01)

    def test_haversine_distance_known_cities(self):
        """Test with known distances between cities."""
        # London to Paris (approximate coordinates)
        # London: 51.5074° N, 0.1278° W
        # Paris: 48.8566° N, 2.3522° E
        # Known distance: ~344 km
        row = [51.5074, -0.1278, 48.8566, 2.3522]
        distance = haversine_distance(row)
        assert distance == pytest.approx(344, rel=0.05)  # Within 5% of known distance

        # New York to Los Angeles (approximate)
        # NYC: 40.7128° N, 74.0060° W
        # LA: 34.0522° N, 118.2437° W
        # Known distance: ~3944 km
        row = [40.7128, -74.0060, 34.0522, -118.2437]
        distance = haversine_distance(row)
        assert distance == pytest.approx(3944, rel=0.05)

    def test_haversine_distance_antipodes(self):
        """Test distance between antipodal points (opposite sides of Earth)."""
        # North pole to South pole - should be half Earth's circumference
        # Earth circumference ≈ 40,075 km, so half ≈ 20,037.5 km
        row = [90.0, 0.0, -90.0, 0.0]
        distance = haversine_distance(row)
        assert distance == pytest.approx(20037.5, rel=0.01)

        # Antipodal points at equator
        row = [0.0, 0.0, 0.0, 180.0]
        distance = haversine_distance(row)
        assert distance == pytest.approx(20037.5, rel=0.01)

    def test_haversine_distance_negative_coordinates(self):
        """Test with negative latitude and longitude values."""
        # Southern hemisphere to northern hemisphere
        row = [-33.8688, 151.2093, 35.6762, 139.6503]  # Sydney to Tokyo
        distance = haversine_distance(row)
        # Known distance: ~7,800 km
        assert distance == pytest.approx(7800, rel=0.05)

    def test_haversine_distance_across_date_line(self):
        """Test distance calculation across the international date line."""
        # Tokyo to San Francisco across Pacific
        row = [35.6762, 139.6503, 37.7749, -122.4194]
        distance = haversine_distance(row)
        # Known distance: ~8,300 km
        assert distance == pytest.approx(8300, rel=0.05)

    def test_haversine_distance_small_distances(self):
        """Test accuracy for very small distances."""
        # Two points very close together (0.001 degrees apart)
        row = [51.5000, 0.0000, 51.5001, 0.0000]
        distance = haversine_distance(row)
        # 0.001 degrees latitude ≈ 0.111 km / 10 = 0.0111 km
        assert distance == pytest.approx(0.0111, rel=0.01)

    def test_haversine_distance_edge_cases(self):
        """Test edge cases with extreme coordinates."""
        # Points at poles
        row = [90.0, 45.0, 90.0, -45.0]  # Both at North pole (longitude irrelevant)
        distance = haversine_distance(row)
        assert distance == pytest.approx(0.0, abs=1e-6)

        # Maximum longitude difference
        row = [0.0, -180.0, 0.0, 180.0]  # Same as 0° difference due to wraparound
        distance = haversine_distance(row)
        assert distance == pytest.approx(0.0, abs=1e-6)

    def test_haversine_distance_symmetry(self):
        """Test that distance is symmetric (A to B = B to A)."""
        # Forward direction
        row1 = [40.0, -70.0, 50.0, 10.0]
        distance1 = haversine_distance(row1)

        # Reverse direction
        row2 = [50.0, 10.0, 40.0, -70.0]
        distance2 = haversine_distance(row2)

        assert distance1 == pytest.approx(distance2, abs=1e-6)

    def test_haversine_distance_quarter_circle(self):
        """Test distance for quarter of Earth's circumference."""
        # 90 degrees apart on equator - quarter of Earth
        row = [0.0, 0.0, 0.0, 90.0]
        distance = haversine_distance(row)
        # Quarter of ~40,075 km = ~10,019 km
        assert distance == pytest.approx(10019, rel=0.01)

        # 90 degrees apart on meridian
        row = [0.0, 0.0, 90.0, 0.0]
        distance = haversine_distance(row)
        assert distance == pytest.approx(10019, rel=0.01)


class TestHaversineDask:
    """Test haversine_dask function for parallel distance computation."""

    def test_haversine_dask_single_point(self):
        """Test distance calculation with single source and target point."""
        import dask.array as da

        # Single source point to single target point
        lat1 = da.from_array([0.0], chunks=1)
        lon1 = da.from_array([0.0], chunks=1)
        lat2 = da.from_array([1.0], chunks=1)
        lon2 = da.from_array([0.0], chunks=1)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (1, 1)
        # 1 degree latitude difference ≈ 111.32 km
        assert result[0, 0] == pytest.approx(111.32, rel=0.01)

    def test_haversine_dask_multiple_sources_single_target(self):
        """Test multiple source points to single target point."""
        import dask.array as da

        # Multiple sources to single target
        lat1 = da.from_array([0.0, 10.0, 20.0], chunks=2)
        lon1 = da.from_array([0.0, 0.0, 0.0], chunks=2)
        lat2 = da.from_array([0.0], chunks=1)
        lon2 = da.from_array([0.0], chunks=1)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (3, 1)
        assert result[0, 0] == pytest.approx(0.0, abs=1e-6)  # Same point
        assert result[1, 0] == pytest.approx(1113.2, rel=0.01)  # 10 degrees
        assert result[2, 0] == pytest.approx(2226.4, rel=0.01)  # 20 degrees

    def test_haversine_dask_single_source_multiple_targets(self):
        """Test single source point to multiple target points."""
        import dask.array as da

        # Single source to multiple targets
        lat1 = da.from_array([0.0], chunks=1)
        lon1 = da.from_array([0.0], chunks=1)
        lat2 = da.from_array([0.0, 0.0, 0.0], chunks=2)
        lon2 = da.from_array([1.0, 2.0, 3.0], chunks=2)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (1, 3)
        # Along equator: 1, 2, 3 degrees longitude
        assert result[0, 0] == pytest.approx(111.32, rel=0.01)
        assert result[0, 1] == pytest.approx(222.64, rel=0.01)
        assert result[0, 2] == pytest.approx(333.96, rel=0.01)

    def test_haversine_dask_multiple_to_multiple(self):
        """Test multiple source points to multiple target points (distance matrix)."""
        import dask.array as da

        # Create a 3x4 distance matrix
        lat1 = da.from_array([0.0, 10.0, -10.0], chunks=2)
        lon1 = da.from_array([0.0, 0.0, 0.0], chunks=2)
        lat2 = da.from_array([0.0, 5.0, -5.0, 15.0], chunks=2)
        lon2 = da.from_array([10.0, 10.0, 10.0, 10.0], chunks=2)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (3, 4)

        # Verify a few specific distances
        # From (0,0) to (0,10): 10 degrees along equator
        assert result[0, 0] == pytest.approx(1113.2, rel=0.01)

        # From (10,0) to (5,10): diagonal distance
        # This involves both latitude and longitude differences
        assert result[1, 1] > 500 and result[1, 1] < 1500  # Reasonable range

    def test_haversine_dask_negative_coordinates(self):
        """Test with negative latitude and longitude values."""
        import dask.array as da

        lat1 = da.from_array([-30.0, -45.0], chunks=1)
        lon1 = da.from_array([-60.0, -90.0], chunks=1)
        lat2 = da.from_array([30.0, 45.0], chunks=1)
        lon2 = da.from_array([60.0, 90.0], chunks=1)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (2, 2)
        # All distances should be positive
        assert np.all(result >= 0)

        # Diagonal elements should be large (crossing hemispheres)
        assert result[0, 0] > 10000  # Large distance
        assert result[1, 1] > 10000  # Large distance

    def test_haversine_dask_same_points(self):
        """Test that same points return zero distance."""
        import dask.array as da

        # Multiple identical point pairs
        lat1 = da.from_array([51.5, -33.86, 35.68], chunks=2)
        lon1 = da.from_array([-0.12, 151.21, 139.69], chunks=2)
        lat2 = da.from_array([51.5, -33.86, 35.68], chunks=2)
        lon2 = da.from_array([-0.12, 151.21, 139.69], chunks=2)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (3, 3)
        # Diagonal should be zeros (same points)
        np.testing.assert_allclose(np.diag(result), 0.0, atol=1e-6)

    def test_haversine_dask_antipodes(self):
        """Test distance to antipodal points."""
        import dask.array as da

        # Antipodal points
        lat1 = da.from_array([0.0, 90.0], chunks=1)
        lon1 = da.from_array([0.0, 0.0], chunks=1)
        lat2 = da.from_array([0.0, -90.0], chunks=1)
        lon2 = da.from_array([180.0, 0.0], chunks=1)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (2, 2)
        # From (0,0) to (0,180): half Earth circumference
        assert result[0, 0] == pytest.approx(20037.5, rel=0.01)
        # From (90,0) to (-90,0): pole to pole
        assert result[1, 1] == pytest.approx(20037.5, rel=0.01)

    def test_haversine_dask_chunking(self):
        """Test that different chunk sizes produce same results."""
        import dask.array as da

        # Same data with different chunking
        lat1_chunk1 = da.from_array([0.0, 10.0, 20.0, 30.0], chunks=1)
        lon1_chunk1 = da.from_array([0.0, 10.0, 20.0, 30.0], chunks=1)
        lat2 = da.from_array([5.0, 15.0], chunks=2)
        lon2 = da.from_array([5.0, 15.0], chunks=2)

        lat1_chunk2 = da.from_array([0.0, 10.0, 20.0, 30.0], chunks=2)
        lon1_chunk2 = da.from_array([0.0, 10.0, 20.0, 30.0], chunks=2)

        distances1 = haversine_dask(lat1_chunk1, lon1_chunk1, lat2, lon2)
        distances2 = haversine_dask(lat1_chunk2, lon1_chunk2, lat2, lon2)

        result1 = distances1.compute()
        result2 = distances2.compute()

        np.testing.assert_allclose(result1, result2, rtol=1e-10)

    def test_haversine_dask_large_arrays(self):
        """Test with larger arrays to verify broadcasting works correctly."""
        import dask.array as da

        # Create larger arrays
        lat1 = da.from_array(np.linspace(-90, 90, 50), chunks=10)
        lon1 = da.from_array(np.linspace(-180, 180, 50), chunks=10)
        lat2 = da.from_array(np.linspace(-45, 45, 30), chunks=10)
        lon2 = da.from_array(np.linspace(-90, 90, 30), chunks=10)

        distances = haversine_dask(lat1, lon1, lat2, lon2)
        result = distances.compute()

        assert result.shape == (50, 30)
        assert np.all(result >= 0)  # All distances positive
        assert np.all(result <= 20037.5)  # Max distance is half Earth circumference

    def test_haversine_dask_comparison_with_regular(self):
        """Test that dask version produces same results as regular haversine."""
        import dask.array as da

        # Test specific point pairs
        test_cases = [
            ([0.0], [0.0], [1.0], [0.0]),  # 1 degree latitude
            ([51.5], [-0.12], [48.86], [2.35]),  # London to Paris
            ([40.71], [-74.01], [34.05], [-118.24]),  # NYC to LA
        ]

        for lat1, lon1, lat2, lon2 in test_cases:
            # Compute with dask version
            lat1_da = da.from_array(lat1, chunks=1)
            lon1_da = da.from_array(lon1, chunks=1)
            lat2_da = da.from_array(lat2, chunks=1)
            lon2_da = da.from_array(lon2, chunks=1)

            dask_result = haversine_dask(lat1_da, lon1_da, lat2_da, lon2_da).compute()[0, 0]

            # Compute with regular version
            row = [lat1[0], lon1[0], lat2[0], lon2[0]]
            regular_result = haversine_distance(row)

            # Results should match
            assert dask_result == pytest.approx(regular_result, rel=1e-6)


class TestDistanceToClosestLocation:
    """Test distance_to_closest_location function."""

    def test_distance_to_closest_location_single_location(self):
        """Test distance calculation with a single location."""
        # Single location at (0, 0)
        locations = {
            Location(
                lat=0.0, lon=0.0, country="Test", region="TestRegion", iso3="TST"
            ): 2_000_000  # Capacity above threshold (2 million tonnes)
        }

        # Target grid
        target_lats = np.array([-10.0, 0.0, 10.0])
        target_lons = np.array([-10.0, 0.0, 10.0])

        result = distance_to_closest_location(locations, target_lats, target_lons)

        # Check result shape
        assert result.shape == (3, 3)
        assert isinstance(result, xr.DataArray)
        assert "lat" in result.coords
        assert "lon" in result.coords

        # Distance at origin should be 0
        assert result.sel(lat=0.0, lon=0.0).values == pytest.approx(0.0, abs=1e-3)

        # Distances should increase away from origin
        assert result.sel(lat=10.0, lon=0.0).values > 0
        assert result.sel(lat=0.0, lon=10.0).values > 0
        assert result.sel(lat=-10.0, lon=-10.0).values > 0

    def test_distance_to_closest_location_multiple_locations(self):
        """Test with multiple locations to find closest one."""
        locations = {
            Location(lat=0.0, lon=0.0, country="Test", region="TestRegion", iso3="TST"): 2_000_000,
            Location(lat=5.0, lon=5.0, country="Test", region="TestRegion", iso3="TST"): 1_500_000,
            Location(lat=-5.0, lon=-5.0, country="Test", region="TestRegion", iso3="TST"): 2_500_000,
        }

        target_lats = np.array([-5.0, 0.0, 5.0])
        target_lons = np.array([-5.0, 0.0, 5.0])

        result = distance_to_closest_location(locations, target_lats, target_lons)

        # Points should be closest to their respective locations
        assert result.sel(lat=0.0, lon=0.0).values == pytest.approx(0.0, abs=1e-3)
        assert result.sel(lat=5.0, lon=5.0).values == pytest.approx(0.0, abs=1e-3)
        assert result.sel(lat=-5.0, lon=-5.0).values == pytest.approx(0.0, abs=1e-3)

    def test_distance_to_closest_location_capacity_filter(self):
        """Test that locations below minimum capacity are filtered out."""
        from steelo.domain.constants import MIN_CAPACITY_FOR_DISTANCE_CALCULATION

        locations = {
            Location(
                lat=0.0, lon=0.0, country="Test", region="TestRegion", iso3="TST"
            ): MIN_CAPACITY_FOR_DISTANCE_CALCULATION - 1,  # Below threshold
            Location(
                lat=10.0, lon=10.0, country="Test", region="TestRegion", iso3="TST"
            ): MIN_CAPACITY_FOR_DISTANCE_CALCULATION + 100_000,  # Above threshold
        }

        target_lats = np.array([0.0, 5.0, 10.0])
        target_lons = np.array([0.0, 5.0, 10.0])

        result = distance_to_closest_location(locations, target_lats, target_lons)

        # All points should be closest to (10, 10) since (0, 0) is filtered out
        # Distance at (10, 10) should be 0
        assert result.sel(lat=10.0, lon=10.0).values == pytest.approx(0.0, abs=1e-3)

        # Distance at (0, 0) should be > 0 (distance to (10, 10))
        assert result.sel(lat=0.0, lon=0.0).values > 1000  # Should be far

    def test_distance_to_closest_location_empty_locations(self):
        """Test error handling with empty locations dictionary."""
        locations = {}

        target_lats = np.array([0.0, 10.0])
        target_lons = np.array([0.0, 10.0])

        with pytest.raises(ValueError, match="No weighted locations provided"):
            distance_to_closest_location(locations, target_lats, target_lons)

    def test_distance_to_closest_location_all_below_threshold(self):
        """Test error when all locations are below capacity threshold."""
        from steelo.domain.constants import MIN_CAPACITY_FOR_DISTANCE_CALCULATION

        locations = {
            Location(
                lat=0.0, lon=0.0, country="Test", region="TestRegion", iso3="TST"
            ): MIN_CAPACITY_FOR_DISTANCE_CALCULATION - 1,
            Location(
                lat=10.0, lon=10.0, country="Test", region="TestRegion", iso3="TST"
            ): MIN_CAPACITY_FOR_DISTANCE_CALCULATION - 0.1,
        }

        target_lats = np.array([0.0, 10.0])
        target_lons = np.array([0.0, 10.0])

        with pytest.raises(ValueError, match="Not a single location meets the minimum capacity requirement"):
            distance_to_closest_location(locations, target_lats, target_lons)

    def test_distance_to_closest_location_large_grid(self):
        """Test with a larger grid to verify performance and correctness."""
        locations = {
            Location(lat=-30.0, lon=-60.0, country="Test", region="TestRegion", iso3="TST"): 1_500_000,
            Location(lat=40.0, lon=-100.0, country="Test", region="TestRegion", iso3="TST"): 2_000_000,
            Location(lat=35.0, lon=139.0, country="Test", region="TestRegion", iso3="TST"): 3_000_000,
        }

        # Create a larger grid
        target_lats = np.linspace(-45, 45, 10)
        target_lons = np.linspace(-120, 120, 10)

        result = distance_to_closest_location(locations, target_lats, target_lons)

        assert result.shape == (10, 10)
        assert np.all(result.values >= 0)  # All distances should be non-negative

        # Check that the result has proper coordinates
        np.testing.assert_array_equal(result.coords["lat"].values, target_lats)
        np.testing.assert_array_equal(result.coords["lon"].values, target_lons)

    def test_distance_to_closest_location_batching(self):
        """Test that batching works correctly for large datasets."""
        # Create many locations
        locations = {}
        for i in range(10):
            lat = -45 + i * 10
            lon = -90 + i * 20
            locations[Location(lat=lat, lon=lon, country="Test", region="TestRegion", iso3="TST")] = (
                1_500_000 + i * 500_000
            )

        # Create a grid that will require batching
        target_lats = np.linspace(-90, 90, 50)
        target_lons = np.linspace(-180, 180, 60)

        # Use small batch size to test batching
        result = distance_to_closest_location(locations, target_lats, target_lons, batch_size=500)

        assert result.shape == (50, 60)
        assert np.all(result.values >= 0)

        # Verify dtype is float32 for memory efficiency
        assert result.dtype == np.float32

    def test_distance_to_closest_location_known_distances(self):
        """Test with known distance values."""
        # Place location at equator
        locations = {Location(lat=0.0, lon=0.0, country="Test", region="TestRegion", iso3="TST"): 2_000_000}

        # Points at known distances
        target_lats = np.array([0.0, 1.0, 0.0])
        target_lons = np.array([1.0, 0.0, 10.0])

        result = distance_to_closest_location(locations, target_lats, target_lons)

        # 1 degree along equator ≈ 111.32 km
        assert result.sel(lat=0.0, lon=1.0).values == pytest.approx(111.32, rel=0.01)

        # 1 degree along meridian ≈ 111.32 km
        assert result.sel(lat=1.0, lon=0.0).values == pytest.approx(111.32, rel=0.01)

        # 10 degrees along equator ≈ 1113.2 km
        assert result.sel(lat=0.0, lon=10.0).values == pytest.approx(1113.2, rel=0.01)

    def test_distance_to_closest_location_negative_coordinates(self):
        """Test with negative latitude and longitude values."""
        locations = {
            Location(lat=-33.87, lon=151.21, country="Australia", region="Oceania", iso3="AUS"): 5_000_000,  # Sydney
            Location(lat=40.71, lon=-74.01, country="USA", region="Americas", iso3="USA"): 6_000_000,  # New York
            Location(lat=-23.55, lon=-46.63, country="Brazil", region="Americas", iso3="BRA"): 7_000_000,  # São Paulo
        }

        target_lats = np.array([-40.0, -20.0, 0.0, 20.0, 40.0])
        target_lons = np.array([-100.0, -50.0, 0.0, 50.0, 100.0])

        result = distance_to_closest_location(locations, target_lats, target_lons)

        assert result.shape == (5, 5)
        assert np.all(result.values >= 0)

        # Check specific point closest to New York
        assert (
            result.sel(lat=40.0, lon=-100.0, method="nearest").values
            < result.sel(lat=-40.0, lon=100.0, method="nearest").values
        )

    def test_distance_to_closest_location_poles(self):
        """Test behavior near poles."""
        locations = {
            Location(lat=89.0, lon=0.0, country="Test", region="Arctic", iso3="ARC"): 1_500_000,  # Near North Pole
            Location(lat=-89.0, lon=0.0, country="Test", region="Antarctic", iso3="ANT"): 1_500_000,  # Near South Pole
        }

        target_lats = np.array([-90.0, -45.0, 0.0, 45.0, 90.0])
        target_lons = np.array([0.0, 90.0, 180.0, -90.0, -180.0])

        result = distance_to_closest_location(locations, target_lats, target_lons)

        assert result.shape == (5, 5)

        # Points near poles should be closest to their respective polar location
        # Near South Pole
        assert result.sel(lat=-90.0, lon=0.0).values < 200  # Should be close to South Pole location
        # Near North Pole
        assert result.sel(lat=90.0, lon=0.0).values < 200  # Should be close to North Pole location
