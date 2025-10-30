import numpy as np
import pandas as pd
import xarray as xr
import pytest

from wind_and_pv.processing import extract_area, clean_coordinates, process_wind, process_influx, process_temperature


@pytest.fixture
def dummy_coords():
    ds = xr.Dataset(coords={"x": ("x", np.array([1, 2, 3])), "y": ("y", np.array([4, 5, 6]))})
    return ds.coords


def test_extract_area(dummy_coords):
    # Expected order: [North, West, South, East]
    # For x: [1, 2, 3] => min=1, max=3; for y: [4, 5, 6] => min=4, max=6.
    # So expected: [1, 3, 4, 6]
    area = extract_area(dummy_coords)
    assert area == (1, 3, 4, 6)


def test_clean_coordinates():
    # Create a dummy dataset with 'longitude' and 'latitude'
    ds = xr.Dataset(
        {"var": (("x", "y"), np.ones((2, 2)))},
        coords={"longitude": ("x", [1.234567, 2.345678]), "latitude": ("y", [3.456789, 4.567891])},
    )
    ds_clean = clean_coordinates(ds, add_lon_lat=True)
    # Check renaming and rounding
    np.testing.assert_allclose(ds_clean.x.values, [1.23457, 2.34568])
    np.testing.assert_allclose(ds_clean.y.values, [3.45679, 4.56789])
    assert "lon" in ds_clean.coords and "lat" in ds_clean.coords


def test_process_wind():
    # Create a dummy dataset with required variables and attributes.
    ds = xr.Dataset(
        {
            "u100": (("x", "y"), np.array([[3, 4], [0, 0]])),
            "v100": (("x", "y"), np.array([[4, 3], [0, 0]])),
            "fsr": (("x", "y"), np.ones((2, 2))),
        }
    )
    ds["u100"].attrs["units"] = "m/s"
    ds_processed = process_wind(ds)
    # Test wind speed calculation (Pythagoras): first element sqrt(3^2 + 4^2)=5.
    np.testing.assert_allclose(ds_processed["wnd100m"].values[0, 0], 5)
    # Check azimuth is in the correct range (>= 0)
    assert (ds_processed["wnd_azimuth"].values >= 0).all()


def test_process_influx():
    # Create a dummy dataset with required solar radiation variables and a time coordinate.
    ds = xr.Dataset(
        {
            "fdir": (("x", "y"), np.array([[300]])),
            "tisr": (("x", "y"), np.array([[400]])),
            "ssrd": (("x", "y"), np.array([[1000]])),
            "ssr": (("x", "y"), np.array([[100]])),
        },
        coords={"time": pd.date_range("2024-06-01", periods=1), "x": [10], "y": [50]},
    )
    # Add required 'lon' and 'lat' coordinates.
    ds = ds.assign_coords(lon=ds["x"], lat=ds["y"])
    ds_processed = process_influx(ds)
    # Verify that expected keys are present.
    for key in ["influx_toa", "influx_direct", "influx_diffuse", "albedo", "solar_altitude", "solar_azimuth"]:
        assert key in ds_processed
    # Check that original variables are renamed (i.e. 'fdir' is gone).
    assert "fdir" not in ds_processed
    # Verify numerical values: values are divided by 3600.
    np.testing.assert_allclose(ds_processed["influx_direct"].values, np.array([[300 / 3600]]))
    np.testing.assert_allclose(ds_processed["influx_toa"].values, np.array([[400 / 3600]]))
    np.testing.assert_allclose(ds_processed["influx_diffuse"].values, np.array([[700 / 3600]]))
    # Albedo should be computed as (ssrd - ssr)/ssrd = (1000 - 100) / 1000 = 0.9.
    np.testing.assert_allclose(ds_processed["albedo"].values, np.array([[0.9]]))
    # Check attributes for unit conversions.
    assert ds_processed["influx_direct"].attrs["units"] == "W m**-2"
    assert ds_processed["albedo"].attrs["units"] == "(0 - 1)"


def test_process_temperature():
    # Create a dummy dataset with temperature variables.
    ds = xr.Dataset(
        {
            "t2m": (("x", "y"), np.array([[280, 285], [290, 295]])),
            "stl4": (("x", "y"), np.array([[270, 275], [280, 285]])),
        }
    )
    ds_processed = process_temperature(ds)
    # Ensure that the keys have been renamed.
    assert "temperature" in ds_processed
    assert "soil temperature" in ds_processed
    # Check that the values are unchanged.
    np.testing.assert_array_equal(ds_processed["temperature"].values, np.array([[280, 285], [290, 295]]))
    np.testing.assert_array_equal(ds_processed["soil temperature"].values, np.array([[270, 275], [280, 285]]))
