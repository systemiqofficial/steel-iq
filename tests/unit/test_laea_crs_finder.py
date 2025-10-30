import pytest
from pyproj import CRS
from shapely.geometry import Point, Polygon, MultiPolygon
from steelo.adapters.dataprocessing.preprocessing.laea_crs_finder import get_laea_crs


@pytest.fixture
def point_geometry():
    # A simple Point over WGS84 (EPSG:4326).
    return Point(10, 45)  # lon=10, lat=45


@pytest.fixture
def polygon_geometry():
    # A Polygon over some arbitrary coordinates in WGS84.
    # This polygon is roughly a square around (2, 48)
    return Polygon([(1.5, 47.5), (1.5, 48.5), (2.5, 48.5), (2.5, 47.5)])


@pytest.fixture
def multi_polygon_geometry():
    # A MultiPolygon: one polygon near Europe and one near North America (WGS84, EPSG:4326)
    poly1 = Polygon([(1, 45), (1, 46), (2, 46), (2, 45)])
    poly2 = Polygon([(-75, 40), (-75, 41), (-74, 41), (-74, 40)])
    return MultiPolygon([poly1, poly2])


def test_get_laea_crs_return_type(point_geometry):
    """Test that the function returns a CRS object."""
    crs = get_laea_crs(point_geometry)
    assert isinstance(crs, CRS), "Expected a pyproj.CRS object."


def test_get_laea_crs_basic_params(point_geometry):
    """Test that the function returns an LAEA projection with correct center."""
    crs = get_laea_crs(point_geometry)
    proj_params = crs.to_proj4()

    # Check for required parameters
    assert "+proj=laea" in proj_params, "CRS should be Lambert Azimuthal Equal Area."
    assert "+ellps=WGS84" in proj_params, "CRS should use WGS84 ellipsoid."

    # Convert the proj string into a dictionary for easier access
    params = dict(param.split("=") for param in proj_params.split() if "=" in param)

    lat_0_val = float(params["+lat_0"])
    lon_0_val = float(params["+lon_0"])

    rep_point = point_geometry.representative_point()
    assert abs(lat_0_val - rep_point.y) < 1e-12, f"lat_0 {lat_0_val} does not match representative point {rep_point.y}"
    assert abs(lon_0_val - rep_point.x) < 1e-12, f"lon_0 {lon_0_val} does not match representative point {rep_point.x}"


def test_get_laea_crs_polygon(polygon_geometry):
    """Test with a polygon geometry."""
    crs = get_laea_crs(polygon_geometry)
    proj_params = crs.to_proj4()
    rep_point = polygon_geometry.representative_point()
    # Example of numeric parsing approach
    params_dict = dict(param.split("=") for param in proj_params.split() if "=" in param)
    lat_0_val = float(params_dict.get("+lat_0", "NaN"))
    lon_0_val = float(params_dict.get("+lon_0", "NaN"))

    # Now compare numerically with some tolerance
    assert abs(lat_0_val - rep_point.y) < 1e-9
    assert abs(lon_0_val - rep_point.x) < 1e-9


def test_get_laea_crs_multipolygon(multi_polygon_geometry):
    """Test with a multipolygon geometry."""
    crs = get_laea_crs(multi_polygon_geometry)
    proj_params = crs.to_proj4()
    rep_point = multi_polygon_geometry.representative_point()
    assert f"+lat_0={rep_point.y}" in proj_params
    assert f"+lon_0={rep_point.x}" in proj_params


def test_get_laea_crs_negative_coordinates():
    """Test geometry with negative coordinates (e.g., western hemisphere)."""
    geom = Point(-120, -10)  # lon=-120, lat=-10
    crs = get_laea_crs(geom)
    proj_params = crs.to_proj4()
    assert "+proj=laea" in proj_params
    assert "+ellps=WGS84" in proj_params
    assert "+lat_0=-10" in proj_params
    assert "+lon_0=-120" in proj_params
