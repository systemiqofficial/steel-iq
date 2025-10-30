import pytest
import geopandas as gpd
from shapely.geometry import Polygon, LineString
from steelo.domain.environment import EnvironmentScorer
from steelo.adapters.geospatial.geospatial_toolbox import GeoSpatialAdapter


@pytest.fixture
def country_boundary():
    return gpd.GeoDataFrame(
        {"country": ["TestLand"]},
        geometry=[Polygon([(10, 50), (11, 50), (11, 51), (10, 51), (10, 50)])],
        crs="EPSG:4326",
    )


@pytest.fixture
def infrastructure():
    line = LineString([(10.5, 50.5), (10.7, 50.7)])
    return {
        "highway": gpd.GeoDataFrame({"type": ["highway"]}, geometry=[line], crs="EPSG:4326"),
    }


@pytest.fixture
def adapter():
    return GeoSpatialAdapter()


def test_create_grid(country_boundary, adapter):
    grid = adapter.create_grid(country_boundary, cell_size=0.5)
    assert len(grid) > 0
    for cell in grid:
        assert "geometry" in cell


def test_compute_distances(country_boundary, infrastructure, adapter):
    grid = adapter.create_grid(country_boundary, cell_size=0.5)
    distances = adapter.compute_distances(grid, infrastructure)
    for cell in distances:
        assert "distance_to_highway_meters" in cell
        assert cell["distance_to_highway_meters"] >= 0


def test_normalize_and_score(adapter):
    grid = [
        {"distance_to_highway_meters": 10, "distance_to_rail_meters": 5, "distance_to_powerline_meters": 2},
        {"distance_to_highway_meters": 100, "distance_to_rail_meters": 50, "distance_to_powerline_meters": 20},
    ]
    scaler = adapter.get_scaler()
    scored = EnvironmentScorer.normalize_and_score(
        grid, ["distance_to_highway_meters", "distance_to_rail_meters", "distance_to_powerline_meters"], scaler
    )
    for cell in scored:
        assert "proximity_score" in cell
        assert 0 <= cell["proximity_score"] <= 1
