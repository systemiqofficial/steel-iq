import pytest
import warnings

from pathlib import Path
import xarray as xr

from steelo.config import settings

from wind_and_pv import get_wind_and_pv_potentials


@pytest.fixture
def fixture_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def gbr_weather_data_path():
    return settings.project_root / "data" / "weather_data" / "cds" / "GBR_2024_025_deg.nc"


def test_wind_and_pv_potentials(fixture_dir, gbr_weather_data_path):
    warnings.filterwarnings("ignore", message="numpy.ndarray size changed")
    # Given some expected PV and wind potentials and a GBR weather data file
    ds_pv_expected = xr.open_dataset(fixture_dir / "pv_gbr.nc")["specific generation"]
    ds_wind_expected = xr.open_dataset(fixture_dir / "wind_gbr.nc")["specific generation"]

    # When we calculate the actual potentials
    ds_wind_actual, ds_pv_actual = get_wind_and_pv_potentials(gbr_weather_data_path)

    # Then the data should be identical
    xr.testing.assert_identical(ds_pv_actual, ds_pv_expected)
    xr.testing.assert_identical(ds_wind_actual, ds_wind_expected)
