from pathlib import Path
import pandas as pd
import numpy as np
import xarray as xr
import reverse_geocoder as rg  # type: ignore
import logging

from steelo.adapters.geospatial.geospatial_toolbox import create_global_grid_with_iso
from steelo.domain.models import GeoDataPaths
from steelo.adapters.dataprocessing.preprocessing.iso3_finder import (
    derive_iso3,
    Coordinate,
    reset_reverse_geocoder,
)
from baseload_optimisation_atlas.boa_config import BaseloadPowerConfig


def combine_custom_with_default_raster(custom_raster, master_data_path: Path | None = None) -> pd.DataFrame:
    """
    Combine custom global grid with default city coordinates for geocoding.

    Steps:
        1. Adapt custom raster to default format (extract centroids, rename columns)
        2. Replace ISO2 codes with ISO3 codes in default raster using master mapping
        3. Concatenate custom and default rasters

    Args:
        custom_raster: Global grid with ISO3 codes (GeoDataFrame)
        master_data_path: Path to master Excel file containing ISO2 to ISO3 mapping (default: None)

    Returns:
        Combined DataFrame with columns: lat, lon, name, admin1, admin2, cc (ISO3 code)

    Note:
        Default raster contains 1000 cities from the reverse_geocoder package
    """

    # Adapt custom raster to default format
    custom_raster = custom_raster.dropna(subset=["ISO_A3"]).copy()
    projected_crs = custom_raster.to_crs("EPSG:3857")
    centroids = projected_crs.geometry.centroid.to_crs(custom_raster.crs)
    custom_raster["lat"] = centroids.y.round(3)
    custom_raster["lon"] = centroids.x.round(3)
    custom_raster["name"] = ""
    custom_raster["admin1"] = ""
    custom_raster["admin2"] = ""
    custom_raster = custom_raster.rename(columns={"ISO_A3": "cc"})
    sorted_custom_raster = custom_raster[["lat", "lon", "name", "admin1", "admin2", "cc"]]

    # Replace iso2 with iso3 in default raster
    default_raster_path = Path(rg.__path__[0]) / rg.RG_FILE
    default_raster = pd.read_csv(default_raster_path)
    if not master_data_path:
        raise ValueError("master_data_path must be provided")
    iso2_to_iso3 = pd.read_excel(master_data_path, sheet_name="Country mapping")[
        ["ISO 2-letter code", "ISO 3-letter code"]
    ]
    default_raster_iso3 = default_raster.copy()
    for iso2, iso3 in zip(iso2_to_iso3["ISO 2-letter code"], iso2_to_iso3["ISO 3-letter code"]):
        default_raster_iso3["cc"] = default_raster_iso3["cc"].replace(iso2, iso3)

    # Combine the custom and the default raster
    return pd.concat([sorted_custom_raster, default_raster_iso3], ignore_index=True)


def worker_init_geocoder(config: BaseloadPowerConfig) -> None:
    """
    Initialize reverse geocoder in each Dask worker by loading custom global grid data and combining it with default city coordinates.

    Args:
        config: Baseload power configuration containing paths to shapefiles and master data

    Side Effects:
        - Initializes reverse geocoder in the worker process
        - Logs debug message when initialization is complete

    Note:
        This function is called once per worker when the Dask client starts
    """
    # Reset any existing geocoder first
    reset_reverse_geocoder()

    # Create a minimal GeoDataPaths with only the required fields
    # The create_global_grid_with_iso function only uses countries_shapefile_dir and disputed_areas_shapefile_dir
    geo_paths = GeoDataPaths(
        countries_shapefile_dir=config.countries_shapefile_path.parent,
        disputed_areas_shapefile_dir=config.disputed_areas_shapefile_path.parent,
        terrain_nc_path=config.terrain_nc_path,
        # Set dummy values for required fields that aren't used
        data_dir=config.countries_shapefile_path.parent.parent,
        atlite_dir=config.atlite_output_dir.parent,
        geo_plots_dir=config.geo_plots_dir,
        rail_distance_nc_path=config.terrain_nc_path,  # dummy
        railway_capex_csv_path=config.terrain_nc_path.parent / "dummy.csv",  # dummy
        lcoh_capex_csv_path=config.terrain_nc_path.parent / "dummy.csv",  # dummy
        regional_energy_prices_xlsx=config.master_input_path,  # dummy
        baseload_power_sim_dir=config.geo_output_dir / "baseload_power_simulation",
        static_layers_dir=config.geo_output_dir,  # directory for static layers
        landtype_percentage_path=config.terrain_nc_path,  # dummy
    )

    # Load custom grid data
    custom_raster = create_global_grid_with_iso(1.0, geo_paths=geo_paths)
    combined_coords = combine_custom_with_default_raster(custom_raster, master_data_path=config.master_input_path)

    # Convert to Coordinate objects
    coord_objects = [Coordinate(lat=row.lat, lon=row.lon, iso3=row.cc) for _, row in combined_coords.iterrows()]

    # Initialize geocoder by making a dummy call. Set max_distance_km to a large value to ensure all coordinates are covered.
    derive_iso3(0, 0, coordinates=coord_objects, max_distance_km=1000)
    logging.debug("Geocoder initialized in worker")


def convert_resolution_to_string(res):
    """
    Convert resolution to string and remove the dot.

    Args:
        res: Resolution value (e.g., 0.25)

    Returns:
        String representation without decimal point (e.g., "025")
    """
    return str(res).replace(".", "")


def convert_coordinates(dataset: xr.Dataset | xr.DataArray) -> xr.Dataset | xr.DataArray:
    """
    Convert coordinates from lon [0,360] to lon [-180,180] and lat [90,-90] to lat [-90,90].

    Args:
        dataset: Input dataset or data array with coordinates to be converted

    Returns:
        Dataset or data array with converted coordinates
    """
    # Convert longitude from 0,360 to -180,180
    dataset = dataset.assign_coords(
        longitude=np.where(dataset.longitude > 180, dataset.longitude - 360, dataset.longitude)
    )
    # Convert latitude from 90,-90 to -90,90
    dataset = dataset.assign_coords(latitude=np.where(dataset.latitude > 90, dataset.latitude - 180, dataset.latitude))
    return dataset


def choose_land_points_in_cutout(
    data: xr.Dataset,
    terrain_path: Path,
) -> tuple[
    np.ndarray[tuple[int, ...], np.dtype[np.float64]],
    np.ndarray[tuple[int, ...], np.dtype[np.float64]],
    np.ndarray[tuple[int, ...], np.dtype[np.float64]],
]:
    """
    Filter land points in the cutout area using land-sea mask and map to data grid.

    Args:
        data: Dataset containing the cutout area
        terrain_path: Path to terrain NetCDF file containing land-sea mask

    Returns:
        Tuple containing:
            - Array of land points (lat, lon coordinates)
            - Array of all latitudes in cutout
            - Array of all longitudes in cutout

    Side Effects:
        Logs the number and percentage of land grid points

    Note:
        The land-sea mask must be in the same resolution as the data
    """

    # Get the land-sea mask and filter for land points
    all_lats = data.y.values
    all_lons = data.x.values

    try:
        landsea_mask = xr.open_dataset(terrain_path, engine="netcdf4").drop_vars("valid_time")["lsm"]
    except (ImportError, ValueError):
        try:
            landsea_mask = xr.open_dataset(terrain_path, engine="h5netcdf").drop_vars("valid_time")["lsm"]
        except (ImportError, ValueError):
            landsea_mask = xr.open_dataset(terrain_path, engine="scipy").drop_vars("valid_time")["lsm"]
    landsea_mask_ = convert_coordinates(landsea_mask)
    landsea_mask_bin = (landsea_mask_ > 0.5).astype(int)
    landsea_mask_bin_cutout = landsea_mask_bin.sortby(["latitude", "longitude"]).sel(
        latitude=all_lats, longitude=all_lons, method="nearest"
    )
    all_points = np.array(np.meshgrid(landsea_mask_bin_cutout.latitude, landsea_mask_bin_cutout.longitude)).T.reshape(
        -1, 2
    )
    mask_values = np.array(landsea_mask_bin_cutout.values)
    land_points_lsm = all_points[mask_values.flatten() == 1]

    # Map nearest lat/lon to the data grid
    land_points_list = [
        (data.y.sel(y=lat, method="nearest").values.item(), data.x.sel(x=lon, method="nearest").values.item())
        for lat, lon in land_points_lsm
    ]
    land_points = np.array(sorted(set(land_points_list), key=lambda x: (x[0], x[1])))

    logging.info(
        f"Number of land grid points: {len(land_points)}; {len(land_points) / len(all_points) * 100:.2f}% of total"
    )

    return land_points, all_lats, all_lons
