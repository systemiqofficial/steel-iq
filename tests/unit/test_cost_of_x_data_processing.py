import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from steelo.adapters.dataprocessing.preprocessing.cost_of_x_data_processing import merge_geographical_data


def test_merge_geographical_data_success(mocker):
    # Create mock geodataframe
    mock_geo_df = gpd.GeoDataFrame(
        {"iso_a3": ["USA", "CAN", "MEX"], "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]}
    )

    # Mock gpd.read_file to return our mock geodataframe
    mocker.patch("geopandas.read_file", return_value=mock_geo_df)

    data = {"country_code": ["USA", "CAN", "MEX"], "value": [100, 200, 300]}
    data_df = pd.DataFrame(data)

    result = merge_geographical_data(data_df, "country_code")

    assert "geometry" in result.columns
    assert "iso_a3" in result.columns
    assert result.shape[0] == 3


def test_merge_geographical_data_invalid_iso3(mocker):
    """
    Test handling of invalid ISO3 country codes in the input data.
    """
    # Create mock geodataframe
    mock_geo_df = gpd.GeoDataFrame(
        {"iso_a3": ["USA", "CAN", "MEX"], "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]}
    )

    # Mock gpd.read_file to return our mock geodataframe
    mocker.patch("geopandas.read_file", return_value=mock_geo_df)

    data = {
        "country_code": ["US", "CAN", "MEX"],  # 'US' is not a valid ISO3 code
        "value": [100, 200, 300],
    }
    data_df = pd.DataFrame(data)

    with pytest.raises(ValueError, match="The column 'country_code' does not contain valid ISO3 country codes."):
        merge_geographical_data(data_df, "country_code")
