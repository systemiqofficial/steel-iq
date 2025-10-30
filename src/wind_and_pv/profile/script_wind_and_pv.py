import pstats
import warnings

import numpy as np
import pandas as pd

import atlite  # type: ignore

from atlite.datasets import era5  # type: ignore
from atlite.pv.solar_position import SolarPosition  # type: ignore

import xarray as xr
from pathlib import Path
from numpy import atleast_1d

from pstats import SortKey


features = {
    "height": ["height"],
    "wind": ["wnd100m", "wnd_azimuth", "roughness"],
    "influx": [
        "influx_toa",
        "influx_direct",
        "influx_diffuse",
        "albedo",
        "solar_altitude",
        "solar_azimuth",
    ],
    "temperature": ["temperature", "soil temperature", "dewpoint temperature"],
    "runoff": ["runoff"],
}


def maybe_swap_spatial_dims(ds, namex="x", namey="y"):
    """
    Swap order of spatial dimensions according to atlite concention.
    """
    swaps = {}
    lx, rx = ds.indexes[namex][[0, -1]]
    ly, uy = ds.indexes[namey][[0, -1]]

    if lx > rx:
        swaps[namex] = slice(None, None, -1)
    if uy < ly:
        swaps[namey] = slice(None, None, -1)

    return ds.isel(**swaps) if swaps else ds


def _rename_and_clean_coords(ds, add_lon_lat=True):
    """
    Rename 'longitude' and 'latitude' columns to 'x' and 'y' and fix roundings.

    Optionally (add_lon_lat, default:True) preserves latitude and
    longitude columns as 'lat' and 'lon'.
    """
    ds = ds.rename({"longitude": "x", "latitude": "y"})
    if "valid_time" in ds.sizes:
        ds = ds.rename({"valid_time": "time"}).unify_chunks()
    # round coords since cds coords are float32 which would lead to mismatches
    ds = ds.assign_coords(x=np.round(ds.x.astype(float), 5), y=np.round(ds.y.astype(float), 5))
    ds = maybe_swap_spatial_dims(ds)
    if add_lon_lat:
        ds = ds.assign_coords(lon=ds.coords["x"], lat=ds.coords["y"])

    # Combine ERA5 and ERA5T data into a single dimension.
    # See https://github.com/PyPSA/atlite/issues/190
    if "expver" in ds.coords:
        unique_expver = np.unique(ds["expver"].values)
        if len(unique_expver) > 1:
            expver_dim = xr.DataArray(unique_expver, dims=["expver"], coords={"expver": unique_expver})
            ds = (
                ds.assign_coords({"expver_dim": expver_dim})
                .drop_vars("expver")
                .rename({"expver_dim": "expver"})
                .set_index(expver="expver")
            )
            for var in ds.data_vars:
                ds[var] = ds[var].expand_dims("expver")
            # expver=1 is ERA5 data, expver=5 is ERA5T data This combines both
            # by filling in NaNs from ERA5 data with values from ERA5T.
            ds = ds.sel(expver="0001").combine_first(ds.sel(expver="0005"))
    ds = ds.drop_vars(["expver", "number"], errors="ignore")

    return ds


def get_data_wind_offline(ds):
    """
    Get wind data for the supplied xarray ds.
    """

    ds = _rename_and_clean_coords(ds)

    ds["wnd100m"] = np.sqrt(ds["u100"] ** 2 + ds["v100"] ** 2).assign_attrs(
        units=ds["u100"].attrs["units"], long_name="100 metre wind speed"
    )
    # span the whole circle: 0 is north, π/2 is east, -π is south, 3π/2 is west
    azimuth = np.arctan2(ds["u100"], ds["v100"])
    ds["wnd_azimuth"] = azimuth.where(azimuth >= 0, azimuth + 2 * np.pi)
    ds = ds.rename({"fsr": "roughness"})
    ds = ds.drop_vars([v for v in ds if v not in features["wind"]])

    return ds


def get_data_influx_offline(ds):
    """
    Get influx data for given for the supplied data ds
    """

    ds = _rename_and_clean_coords(ds)

    ds = ds.rename({"fdir": "influx_direct", "tisr": "influx_toa"})
    ds["albedo"] = (
        ((ds["ssrd"] - ds["ssr"]) / ds["ssrd"].where(ds["ssrd"] != 0))
        .fillna(0.0)
        .assign_attrs(units="(0 - 1)", long_name="Albedo")
    )
    ds["influx_diffuse"] = (ds["ssrd"] - ds["influx_direct"]).assign_attrs(
        units="J m**-2", long_name="Surface diffuse solar radiation downwards"
    )
    ds = ds.drop_vars([v for v in ds if v not in features["influx"]])

    # Convert from energy to power J m**-2 -> W m**-2 and clip negative fluxes
    for a in ("influx_direct", "influx_diffuse", "influx_toa"):
        ds[a] = ds[a] / (60.0 * 60.0)
        ds[a].attrs["units"] = "W m**-2"

    # ERA5 variables are mean values for previous hour, i.e. 13:01 to 14:00 are labelled as "14:00"
    # account by calculating the SolarPosition for the center of the interval for aggregation happens
    # see https://github.com/PyPSA/atlite/issues/158
    # Do not show DeprecationWarning from new SolarPosition calculation (#199)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        time_shift = pd.to_timedelta("-30 minutes")
        sp = SolarPosition(ds, time_shift=time_shift)
    sp = sp.rename({v: f"solar_{v}" for v in sp.data_vars})

    ds = xr.merge([ds, sp])

    return ds


def get_data_temperature_offline(ds):
    """
    Get wind temperature for given given data ds.
    """
    ds = _rename_and_clean_coords(ds)
    ds = ds.rename({"t2m": "temperature", "stl4": "soil temperature"})
    ds = ds.drop_vars([v for v in ds if v not in features["temperature"]])

    return ds


def _area(coords):
    # North, West, South, East. Default: global
    x0, x1 = coords["x"].min().item(), coords["x"].max().item()
    y0, y1 = coords["y"].min().item(), coords["y"].max().item()
    return [y1, x0, y0, x1]


def non_bool_dict(d):
    """
    Convert bool to int for netCDF4 storing.
    """
    return {k: v if not isinstance(v, bool) else int(v) for k, v in d.items()}


def get_era5_features_offline(cutout, features=["wind", "tempearture", "influx"], tmpdir=None, weather_data=None):
    datasets = []
    ymax, xmin, ymin, xmax = _area(cutout.data.coords)
    print("bulk path: ", cutout.data.attrs["bulk_path"])

    if weather_data is None:
        data = xr.open_dataset(cutout.data.attrs["bulk_path"])
    else:
        data = weather_data

    feature_to_function = {
        "wind": get_data_wind_offline,
        "temperature": get_data_temperature_offline,
        "influx": get_data_influx_offline,
    }

    ds = data.sel(
        longitude=slice(xmin, xmax),
        latitude=slice(ymax, ymin),
    )

    for feature in features:
        # func = globals().get(f"get_data_{feature}_offline")
        # # Pass ds_bulk if available; otherwise, fallback to original behavior
        # feature_data = delayed(func)(
        #     data.sel(
        #         longitude=slice(xmin, xmax),
        #         latitude=slice(ymax, ymin),
        #     )
        # )

        feature_data = feature_to_function[feature](ds)
        datasets.append(feature_data)
    # datasets = compute(*datasets, scheduler="processes")
    ds = xr.merge(datasets, compat="equals")

    for v in ds:
        ds[v].attrs["module"] = "era5"
        fd = era5.features.items()
        ds[v].attrs["feature"] = [k for k, l in fd if v in l].pop()  # noqa
    return ds


def cutout_prepare_era5_offline(
    cutout,
    features=None,
    tmpdir=None,
    overwrite=False,
    compression={"zlib": True, "complevel": 9, "shuffle": True},
    weather_data=None,
):
    if cutout.prepared and not overwrite:
        # wtf?
        return cutout

    features = atleast_1d(features) if features else slice(None)

    # target is series of all available variables for given module and features
    cutout.data.attrs

    print("has the error been thrown at this point? line 311")

    # we short-cut to go quickly to the offline data preparation
    missing_features = features

    print("missing features: ", missing_features)
    ds = get_era5_features_offline(cutout, missing_features, tmpdir=tmpdir, weather_data=weather_data)
    print("has the error been thrown at this point? line 319")

    cutout.data.attrs.update(dict(prepared_features=features))
    attrs = non_bool_dict(cutout.data.attrs)
    attrs.update(ds.attrs)
    ds = ds.assign_attrs(**attrs)
    cutout.data = ds

    return cutout


def get_cutout():
    CDS_DIR = Path.cwd() / "data" / "weather_data" / "cds"
    global_weather_path = str(CDS_DIR / "GBR_2024_025_deg.nc")
    cutout = atlite.Cutout(
        path="da.nc",
        module="era5",
        # bounds= [-15., 47., 4., 64.],#convert_bound_dict_to_list(bounds_dict),
        x=slice(-14.0, 2),
        y=slice(61.0, 50),
        time="2024",
        bulk_path=global_weather_path,
    )
    weather_data = xr.open_dataset(global_weather_path, chunks=None)
    return cutout, weather_data


def prepare_data(cutout, weather_data):
    cutout_prepare_era5_offline(
        cutout, features=["wind", "temperature", "influx"], tmpdir=None, weather_data=weather_data
    )
    return cutout


def calc_pv(cutout):
    pv = cutout.pv(
        panel="CSi",
        orientation={"slope": 30.0, "azimuth": 180.0},
    )
    return pv


def calc_wind(cutout):
    wind = cutout.wind(turbine="Vestas_V90_3MW")
    return wind


def main():
    cutout, weather_data = get_cutout()
    import cProfile

    profiler = cProfile.Profile()
    profiler.enable()
    prepare_data(cutout, weather_data)  # Run the function normally
    profiler.disable()
    profiler.dump_stats("profile_results.prof")
    with open("profile_stats.txt", "w") as f:
        ps = pstats.Stats("profile_results.prof", stream=f)
        ps.strip_dirs().sort_stats(SortKey.CUMULATIVE).print_stats(100)

    # cutout = prepare_data(cutout)
    # pv = calc_pv(cutout)
    # wind = calc_wind(cutout)


if __name__ == "__main__":
    main()
    # import cProfile
    # cProfile.run('main()', 'profile_results.prof')
