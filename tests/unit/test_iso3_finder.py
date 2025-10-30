import pytest
import pandas as pd
from steelo.adapters.dataprocessing.preprocessing.iso3_finder import (
    derive_iso3,
    get_global_reverse_geocoder,
    search,
    reset_reverse_geocoder,
    Coordinate,
)


@pytest.fixture(autouse=True)
def reset_geocoder():
    """Reset the geocoder before and after each test."""
    reset_reverse_geocoder()
    yield
    reset_reverse_geocoder()


@pytest.fixture
def sample_coordinates():
    """Provide sample coordinates for testing."""
    return [
        Coordinate(lat=37.7749, lon=-122.4194, iso3="USA"),  # San Francisco
        Coordinate(lat=52.509669, lon=13.376294, iso3="DEU"),  # Berlin
        Coordinate(lat=51.5074, lon=-0.1278, iso3="GBR"),  # London
        Coordinate(lat=48.8566, lon=2.3522, iso3="FRA"),  # Paris
        Coordinate(lat=0, lon=0, iso3="XYZ"),  # Test point
    ]


def test_derive_iso3_valid_coordinates(sample_coordinates):
    # Initialize with sample coordinates
    result = derive_iso3(37.7749, -122.4194, coordinates=sample_coordinates)
    assert result == "USA"

    # Subsequent calls don't need coordinates
    result = derive_iso3(52.509669, 13.376294)
    assert result == "DEU"


def test_derive_iso3_value_error_on_invalid_coordinates(sample_coordinates):
    # Initialize geocoder first
    derive_iso3(37.7749, -122.4194, coordinates=sample_coordinates)

    # Invalid latitude and longitude should raise ValueError
    with pytest.raises(ValueError, match="Invalid coordinates"):
        derive_iso3(1000, 2000)


def test_derive_iso3_raises_error_when_too_far(sample_coordinates):
    # Initialize geocoder
    derive_iso3(37.7749, -122.4194, coordinates=sample_coordinates)

    # When result is too far from input coordinates, should raise ValueError
    with pytest.raises(ValueError, match="exceeds the maximum allowed distance"):
        derive_iso3(10, 10, max_distance_km=10)


def test_derive_iso3_returns_exact_series_with_dataframe_apply(sample_coordinates):
    # Initialize geocoder
    derive_iso3(52.509669, 13.376294, coordinates=sample_coordinates)

    df = pd.DataFrame({"lat": [52.509669], "lon": [13.376294]})
    # Apply the derive_iso3 function to the DataFrame
    result = df.apply(lambda x: derive_iso3(x["lat"], x["lon"]), axis=1)
    # Expected series
    expected_series = pd.Series(["DEU"])
    # Assert that the result matches the expected series exactly
    assert result.equals(expected_series)


def test_search_with_custom_coordinates():
    # Create custom coordinates
    custom_coords = [
        Coordinate(lat=42.57952, lon=1.65362, iso3="AND"),  # Andorra
        Coordinate(lat=0, lon=0, iso3="XYZ"),  # Custom test point
    ]

    # Initialize with custom coordinates
    point = (0, 0)
    result = search(point, coordinates=custom_coords)

    # Should get the expected country code
    assert len(result) == 1
    assert result[0]["cc"] == "XYZ"

    # Check Andorra point too
    result = search((42.57952, 1.65362))
    assert len(result) == 1
    assert result[0]["cc"] == "AND"


def test_geocoder_state_management():
    """Test that the geocoder state is properly managed."""
    # Initially, no geocoder should be set
    assert get_global_reverse_geocoder() is None

    # Initialize with coordinates
    coords = [Coordinate(lat=51.5074, lon=-0.1278, iso3="GBR")]
    search((51.5074, -0.1278), coordinates=coords)

    # Now geocoder should be set
    assert get_global_reverse_geocoder() is not None

    # Reset should clear it
    reset_reverse_geocoder()
    assert get_global_reverse_geocoder() is None


def test_cache_derive_iso3(sample_coordinates):
    """Test that cache_derive_iso3 works after initialization."""
    from steelo.adapters.dataprocessing.preprocessing.iso3_finder import cache_derive_iso3

    # Initialize geocoder
    derive_iso3(37.7749, -122.4194, coordinates=sample_coordinates)

    # Now cached version should work
    result = cache_derive_iso3(52.509669, 13.376294)
    assert result == "DEU"

    # Multiple calls should use cache
    result2 = cache_derive_iso3(52.509669, 13.376294)
    assert result2 == "DEU"
