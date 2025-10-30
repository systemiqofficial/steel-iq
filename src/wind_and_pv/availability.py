from pathlib import Path
from typing import cast
import numpy as np
import rasterio  # type: ignore
import rioxarray as rio
import xarray as xr

from steelo.domain.constants import EARTH_RADIUS


CAPACITY_PER_AREA = capacity_per_area = {
    "pv": 141.9,  # MW/km^2
    "wind": 10.42,  # MW/km^2
}

LULC_CODES = {
    "pv": {  # pass only non-zero, saves time
        10: 0.02,  # Cropland, rainfed
        11: 0.02,  # Herbaceous cover
        20: 0.02,  # Cropland
        30: 0.02,  # Mosaic cropland (>50%) / natural vegetation (tree, shrub, herbaceous cover) (<50%)
        40: 0.02,  # Mosaic natural vegetation (tree, shrub, herbaceous cover) (>50%) / cropland (<50%)
        110: 0.02,  # mosaic herbaceous cover
        120: 0.02,  # Shrubland
        121: 0.02,  # Shrubland evergreen
        122: 0.02,  # Shrubland deciduous
        130: 0.02,  # Grassland
        150: 0.33,  # Sparse vegetation (tree, shrub, herbaceous cover) (<15%)
        151: 0.33,  # Sparse tree (<15%)
        152: 0.33,  # Sparse shrub (<15%)
        153: 0.33,  # Sparse herbaceous cover (<15%)
        180: 0.02,  # shrub or herbaceous cover
        190: 0.024,  # Urban areas
        200: 0.33,  # Bare areas
        201: 0.33,  # Consolidated bare areas
        202: 0.33,  # Unconsolidated bare areas
    },
    "wind": {
        10: 0.15,  # Cropland, rainfed
        11: 0.15,  # Herbaceous cover
        20: 0.15,  # Cropland
        30: 0.15,  # Mosaic cropland (>50%) / natural vegetation (tree, shrub, herbaceous cover) (<50%)
        40: 0.15,  # Mosaic natural vegetation (tree, shrub, herbaceous cover) (>50%) / cropland (<50%)
        110: 0.15,  # mosaic herbaceous cover
        120: 0.15,  # Shrubs
        121: 0.15,  # Shrubland evergreen
        122: 0.15,  # Shrubland deciduous
        130: 0.15,  # Grassland
        150: 0.33,  # sparse vegetation
        151: 0.33,  # sparse tree
        152: 0.33,  # sparse shrub
        153: 0.33,  # sparse herbaceous cover
        180: 0.15,  # shrub or herbaceous cover
        200: 0.33,  # Bare areas
        201: 0.33,  # Consolidated bare areas
        202: 0.33,  # Unconsolidated bare areas
    },
}


def calculate_area_of_single_pixel(resolution: float, lat: float) -> float:
    """
    Calculate the area of a single pixel in km^2 depending of the latitude and the resolution.
    Latitude and resolution in degrees. Assumes a spherical Earth - 'cause we don't hire flat-Earthers at Systemiq.
    """
    earth_perimeter = 2 * np.pi * EARTH_RADIUS
    y_size = resolution * earth_perimeter / 360
    x_size = y_size * np.cos(np.radians(lat))

    return y_size * x_size


def calculate_area_of_pixels_in_box(resolution: float, latitudes: np.ndarray, longitudes: np.ndarray) -> xr.DataArray:
    """
    Calculate the area of all pixels within a bounding box in km^2.
    """

    # Calculate the area of a circle along the latitude dimension
    areas_y = xr.DataArray(
        [calculate_area_of_single_pixel(resolution, lat) for lat in latitudes], coords=[latitudes], dims=["lat"]
    )

    # Exapand to the longitude dimension
    areas = xr.DataArray(
        np.outer(areas_y, np.ones(len(longitudes))),
        coords=[latitudes, longitudes],
        dims=["y", "x"],
    )

    return areas


def calculate_available_capacity_per_pixel(chunk, tech, overwrite=False, engine="h5netcdf") -> Path:
    capacity_per_area = CAPACITY_PER_AREA[tech]
    cav_reprojected_path = chunk.get_path(f"{tech}_avail_capacity_{chunk.time}")
    if cav_reprojected_path.exists() and not overwrite:
        # use cached values -> return early
        return cav_reprojected_path

    lulc_path = str(chunk.chunks_dir.parent.parent / "ESACCI-LC-L4-LCCS-Map-300m-P1Y-2015-v2.0.7.tif")
    with cast(xr.DataArray, rio.open_rasterio(lulc_path)) as lulc_data:
        lulc_data_resolution = lulc_data.rio.resolution()[0]
        lulc_data_ = lulc_data.drop_vars(["spatial_ref", "band"]).squeeze("band", drop=True)

    lulc_data_cutout = lulc_data_.sel(x=chunk.x, y=chunk.y)

    lulc_codes_cutout = xr.apply_ufunc(
        lambda x: LULC_CODES[tech].get(x, 0),
        lulc_data_cutout,
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
    )

    # Area of all pixels in cutout (km^2)
    cutout_lats = lulc_data_cutout.y.values
    cutout_lons = lulc_data_cutout.x.values

    area_path = chunk.get_path(f"{tech}_area")
    if area_path.exists():
        with xr.open_dataarray(area_path) as areas:
            # Calculate available capacity per pixel (MW)
            available_areas = areas * lulc_codes_cutout
            available_capacity = capacity_per_area * available_areas
    else:
        areas = calculate_area_of_pixels_in_box(lulc_data_resolution, cutout_lats, cutout_lons)
        areas.to_netcdf(area_path, engine=engine)

        # Calculate available capacity per pixel (MW)
        available_areas = areas * lulc_codes_cutout
        available_capacity = capacity_per_area * available_areas

    # Adapt resolution to match ERA5 data
    available_capacity = available_capacity.rio.write_crs("EPSG:4326")

    with xr.open_dataset(chunk.get_path(tech), engine=engine) as chunk_data:
        chunk_data = chunk_data.rio.write_crs("EPSG:4326")
        cav_reprojected = available_capacity.rio.reproject_match(chunk_data, resampling=rasterio.enums.Resampling.sum)

    # Save
    cav_reprojected = cav_reprojected.to_dataset(name="available_capacity").to_array()
    cav_reprojected.to_netcdf(cav_reprojected_path, engine=engine)

    return cav_reprojected_path
