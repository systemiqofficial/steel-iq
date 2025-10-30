import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr
import warnings
import atlite  # type: ignore
from atlite.pv.solar_position import SolarPosition  # type: ignore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_area(coords: xr.core.coordinates.Coordinates) -> tuple[float, float, float, float]:
    """
    Extract spatial bounds [North, West, South, East] from cutout coordinates.

    Parameters:
        coords: xarray Coordinates with 'x' and 'y'.

    Returns:
        List of floats representing [North, West, South, East].
    """
    x_min, x_max = coords["x"].min().item(), coords["x"].max().item()
    y_min, y_max = coords["y"].min().item(), coords["y"].max().item()
    return x_min, x_max, y_min, y_max


def maybe_swap_spatial_dims(ds: xr.Dataset, namex: str = "x", namey: str = "y") -> xr.Dataset:
    """
    Swap the order of spatial dimensions if required by atlite's convention.

    Parameters:
        ds: xarray Dataset.
        namex: Name of the x coordinate.
        namey: Name of the y coordinate.

    Returns:
        Dataset with swapped dimensions if needed.
    """
    swaps: dict[str, slice] = {}
    lx, rx = ds.indexes[namex][[0, -1]]
    ly, uy = ds.indexes[namey][[0, -1]]
    if lx > rx:
        swaps[namex] = slice(None, None, -1)
    if uy < ly:
        swaps[namey] = slice(None, None, -1)
    return ds.isel(swaps) if swaps else ds


def clean_coordinates(ds: xr.Dataset, add_lon_lat: bool = True) -> xr.Dataset:
    """
    Standardize coordinate naming, rounding and dimension order.

    Parameters:
        ds: xarray Dataset with 'longitude' and 'latitude'.
        add_lon_lat: Whether to add 'lon' and 'lat' coordinates.

    Returns:
        Cleaned Dataset with standardized coordinate names.
    """
    warnings.simplefilter("ignore", UserWarning)
    ds = ds.rename({"longitude": "x", "latitude": "y"})
    if "valid_time" in ds.sizes:
        ds = ds.rename({"valid_time": "time"}).unify_chunks()
    ds = ds.assign_coords(x=np.round(ds.x.astype(float), 5), y=np.round(ds.y.astype(float), 5))
    ds = maybe_swap_spatial_dims(ds)
    if add_lon_lat:
        ds = ds.assign_coords(lon=ds.coords["x"], lat=ds.coords["y"])
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
            ds = ds.sel(expver="0001").combine_first(ds.sel(expver="0005"))
    return ds.drop_vars(["expver", "number"], errors="ignore")


def process_wind(ds: xr.Dataset) -> xr.Dataset:
    """
    Process wind data by calculating wind speed and azimuth.

    Parameters:
        ds: xarray Dataset containing 'u100', 'v100', and 'fsr'.

    Returns:
        Dataset with 'wnd100m', 'wnd_azimuth', and 'roughness'.
    """
    ds = ds.copy()
    ds["wnd100m"] = xr.DataArray(
        np.sqrt(ds["u100"] ** 2 + ds["v100"] ** 2), coords=ds["u100"].coords, dims=ds["u100"].dims
    ).assign_attrs(units=ds["u100"].attrs.get("units", "m/s"), long_name="100 metre wind speed")
    azimuth = xr.DataArray(np.arctan2(ds["u100"], ds["v100"]), coords=ds["u100"].coords, dims=ds["u100"].dims)
    ds["wnd_azimuth"] = azimuth.where(azimuth >= 0, azimuth + 2 * np.pi)
    ds = ds.rename({"fsr": "roughness"})
    return ds[["wnd100m", "wnd_azimuth", "roughness"]]


def process_influx(ds: xr.Dataset) -> xr.Dataset:
    """
    Process solar influx data by renaming variables, calculating albedo, and merging solar position.

    Parameters:
        ds: xarray Dataset containing solar radiation variables.

    Returns:
        Dataset with processed influx features.
    """
    ds = ds.rename({"fdir": "influx_direct", "tisr": "influx_toa"})
    ds["albedo"] = (
        ((ds["ssrd"] - ds["ssr"]) / ds["ssrd"].where(ds["ssrd"] != 0))
        .fillna(0.0)
        .assign_attrs(units="(0 - 1)", long_name="Albedo")
    )
    ds["influx_diffuse"] = (ds["ssrd"] - ds["influx_direct"]).assign_attrs(
        units="J m**-2", long_name="Surface diffuse solar radiation downwards"
    )
    for var in ("influx_direct", "influx_diffuse", "influx_toa"):
        ds[var] = ds[var] / (60.0 * 60.0)
        ds[var].attrs["units"] = "W m**-2"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        sp = SolarPosition(ds, time_shift=pd.to_timedelta("-30 minutes"))
    sp = sp.rename({v: f"solar_{v}" for v in sp.data_vars})
    merged = xr.merge([ds, sp])
    influx_vars = ["influx_toa", "influx_direct", "influx_diffuse", "albedo", "solar_altitude", "solar_azimuth"]
    return merged[influx_vars]


def process_temperature(ds: xr.Dataset) -> xr.Dataset:
    """
    Process temperature data by renaming temperature variables.

    Parameters:
        ds: xarray Dataset containing 't2m' and 'stl4'.

    Returns:
        Dataset with 'temperature' and 'soil temperature'.
    """
    ds = ds.rename({"t2m": "temperature", "stl4": "soil temperature"})
    return ds[["temperature", "soil temperature"]]


FEATURE_PROCESSORS: dict[str, Any] = {
    "wind": process_wind,
    "influx": process_influx,
    "temperature": process_temperature,
}


def process_features(ds: xr.Dataset, features: list[str]) -> dict[str, xr.Dataset]:
    """
    Process selected features using the appropriate processor functions.

    Parameters:
        ds: xarray Dataset to process.
        features: List of feature names to process.

    Returns:
        Dictionary mapping feature names to processed Datasets.
    """
    processed: dict[str, xr.Dataset] = {}
    unsupported: list[str] = []
    for feat in features:
        processor = FEATURE_PROCESSORS.get(feat)
        if processor:
            processed[feat] = processor(ds.copy())
        else:
            unsupported.append(feat)
    if unsupported:
        logger.warning(f"Features {unsupported} not supported for offline processing")
    return processed


def merge_features(processed: dict[str, xr.Dataset]) -> xr.Dataset:
    """
    Merge processed feature datasets into a single dataset and add metadata.

    Parameters:
        processed: Dictionary of processed Datasets keyed by feature name.

    Returns:
        Merged xarray Dataset with metadata for each variable.
    """
    merged = xr.merge(list(processed.values()), compat="equals")
    feature_map = {
        "wind": ["wnd100m", "wnd_azimuth", "roughness"],
        "influx": ["influx_toa", "influx_direct", "influx_diffuse", "albedo", "solar_altitude", "solar_azimuth"],
        "temperature": ["temperature", "soil temperature"],
    }
    for var in merged:
        merged[var].attrs["module"] = "era5"
        for feat, vars in feature_map.items():
            if var in vars:
                merged[var].attrs["feature"] = feat
                break
    return merged


def prepare_era5_offline(cutout: atlite.Cutout, *, features: list[str], weather_data: xr.Dataset) -> atlite.Cutout:
    """
    Prepare ERA5 weather data offline for the provided cutout and features.

    Parameters:
        cutout: atlite.Cutout instance to update.
        features: List of features to process.
        weather_data: Preloaded weather data as an xarray Dataset.

    Returns:
        Updated atlite.Cutout with processed data.
    """
    x_min, x_max, y_min, y_max = extract_area(cutout.data.coords)
    ds = weather_data.sel(longitude=slice(x_min, x_max), latitude=slice(y_max, y_min))
    ds = clean_coordinates(ds)
    processed = process_features(ds, features)
    if not processed:
        return cutout
    merged_ds = merge_features(processed)
    cutout.data.attrs.update(dict(prepared_features=features))
    cutout.data = merged_ds.assign_attrs(**cutout.data.attrs)
    return cutout


class OfflineCutout:
    """
    A wrapper around atlite.Cutout that provides offline processing capabilities.
    """

    def __init__(self, cutout_params: dict[str, Any], weather_data: xr.Dataset) -> None:
        """
        Initialize an OfflineCutout.

        Parameters:
            cutout_params: Parameters for atlite.Cutout.
            weather_data: Weather data as an xarray Dataset or a path to the file.
        """
        self.cutout = atlite.Cutout(**cutout_params)
        self.weather_data = weather_data

    def prepare(self, *, features: list[str]) -> "OfflineCutout":
        """
        Prepare the cutout for specified features using offline processing.

        Parameters:
            features: List of feature names to process ["wind", "temperature", "influx"].

        Returns:
            Self with updated data.
        """
        prepare_era5_offline(self.cutout, features=features, weather_data=self.weather_data)
        return self

    def pv(self, **kwargs: Any) -> xr.DataArray:
        """
        Calculate PV generation potential.

        Parameters:
            kwargs: Additional arguments for atlite.Cutout.pv.

        Returns:
            PV potential as an xarray DataArray.
        """
        return self.cutout.pv(**kwargs)

    def wind(self, **kwargs: Any) -> xr.DataArray:
        """
        Calculate wind generation potential.

        Parameters:
            kwargs: Additional arguments for atlite.Cutout.wind.

        Returns:
            Wind potential as an xarray DataArray.
        """
        return self.cutout.wind(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cutout, name)


def create_offline_cutout(
    weather_data: xr.Dataset,
    *,
    x: slice,
    y: slice,
    time: str,
    **kwargs: Any,
) -> OfflineCutout:
    """
    Create an OfflineCutout with the provided parameters.

    Parameters:
        weather_data: Weather data as an xarray Dataset.
        x: Longitude range slice.
        y: Latitude range slice.
        time: Time range.
        kwargs: Additional parameters for atlite.Cutout.

    Returns:
        An instance of OfflineCutout.
    """
    cutout_params: dict[str, Any] = {
        "path": kwargs.pop("path", "cutout.nc"),
        "module": kwargs.pop("module", "era5"),
        "x": x,
        "y": y,
        "time": time,
    }
    cutout_params.update(kwargs)
    return OfflineCutout(cutout_params, weather_data)


def get_wind_and_pv_potentials(weather_data_path: str | Path) -> tuple[xr.DataArray, xr.DataArray]:
    """
    Calculate wind and PV generation potentials from the given weather data file.

    Parameters:
        weather_data_path: Path to the weather data file.

    Returns:
        Tuple containing (wind_potential, pv_potential) as xarray DataArrays.
    """
    with xr.open_dataset(weather_data_path) as weather_data:
        cutout = create_offline_cutout(weather_data, x=slice(-14.0, 2), y=slice(61.0, 50), time="2024")
        cutout.prepare(features=["wind", "temperature", "influx"])
        pv = cutout.pv(panel="CSi", orientation={"slope": 30.0, "azimuth": 180.0})
        wind = cutout.wind(turbine="Vestas_V90_3MW")

    return wind, pv
