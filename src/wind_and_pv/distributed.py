from pathlib import Path

import atlite  # type: ignore
import xarray as xr
from time import perf_counter

from steelo.config import settings
from .availability import calculate_available_capacity_per_pixel
from .chunking import GeoChunker
from .processing import create_offline_cutout


def write_cutout(weather_data, chunk, prefix, features, overwrite=False, engine="h5netcdf") -> Path:
    chunk_path = chunk.get_path(prefix)
    if chunk_path.exists() and not overwrite:
        # don't overwrite existing chunks
        return chunk_path
    cutout = create_offline_cutout(weather_data, x=chunk.x, y=chunk.y, time=chunk.time)
    cutout.prepare(features=features)
    cutout.data.to_netcdf(chunk_path, engine=engine)
    return chunk_path


def chunk_to_offline_cutout(weather_data_path, chunk) -> tuple[Path, Path]:
    with xr.open_dataset(weather_data_path) as weather_data:
        wind_path = write_cutout(weather_data, chunk, "wind", ["wind"])
        pv_path = write_cutout(weather_data, chunk, "pv", ["temperature", "influx"])
    return wind_path, pv_path


def chunks_dir_from_weather_data_path(weather_data_path):
    weather_data = settings.project_root / "data" / "weather_data"
    chunks_dir = weather_data / "chunks" / weather_data_path.stem
    chunks_dir.mkdir(parents=True, exist_ok=True)
    return chunks_dir


def calc_wind_potential(chunk, overwrite=False, engine="h5netcdf") -> Path:
    result_path = chunk.get_path("wind_potential")
    if result_path.exists() and not overwrite:
        # return early
        return result_path

    chunk_path = chunk.get_path("wind")
    with xr.open_dataset(chunk_path, engine=engine) as data:
        cutout = atlite.Cutout(data=data, path="/tmp/foo.nc")
        wind = cutout.wind(turbine="NREL_ReferenceTurbine_2020ATB_7MW", capacity_factor_timeseries=True)

    wind.to_netcdf(result_path, engine=engine)
    return result_path


def calc_pv_potential(chunk, overwrite=False, engine="h5netcdf") -> Path:
    result_path = chunk.get_path("wind_potential")
    if result_path.exists() and not overwrite:
        # return early
        return result_path

    chunk_path = chunk.get_path("pv")
    with xr.open_dataset(chunk_path, engine="h5netcdf") as data:
        cutout = atlite.Cutout(data=data, path="/tmp/bar.nc")
        pv = cutout.pv(panel="CSi", orientation={"slope": 30.0, "azimuth": 180.0}, capacity_factor_timeseries=True)

    pv.to_netcdf(result_path, engine=engine)
    return result_path


def combine_availability_and_potential(chunk, tech: str, overwrite=False, engine="h5netcdf") -> Path:
    tech_supply_path = chunk.get_tech_supply_path(tech)
    if tech_supply_path.exists() and not overwrite:
        return tech_supply_path

    cav_path = calculate_available_capacity_per_pixel(chunk, tech)
    potential_path = calc_wind_potential(chunk) if tech == "wind" else calc_pv_potential(chunk)
    with xr.open_dataarray(cav_path, engine=engine) as cav, xr.open_dataset(potential_path, engine=engine) as potential:
        supply_value = cav * potential

    supply_value.to_netcdf(tech_supply_path, engine=engine)
    return tech_supply_path


def interpolate_to_hourly_resolution(chunk, overwrite=False, engine="h5netcdf") -> Path:
    interpolated_path = chunk.get_path("interpolated")
    if interpolated_path.exists() and not overwrite:
        return interpolated_path

    wind_supply_path = chunk.get_tech_supply_path("wind")
    pv_supply_path = chunk.get_tech_supply_path("pv")

    supply = xr.Dataset()
    with xr.open_dataset(wind_supply_path) as wind_supply, xr.open_dataset(pv_supply_path) as pv_supply:
        supply["wind"] = wind_supply.to_array()
        supply["pv"] = pv_supply.to_array()

    # Remove superfluous dimensions
    supply = supply.squeeze(dim="variable", drop=True)

    # Interpolate supply time series to hourly resolution
    supply_hourly = supply.resample(time="1H").interpolate("linear")

    # Remove superfluous dimensions
    supply_hourly = supply_hourly.drop_vars(["lat", "lon", "spatial_ref"])

    # Save to netCDF
    supply_hourly.to_netcdf(interpolated_path, engine=engine)
    return interpolated_path


def calculate_wind_and_pv_potentials(weather_data_path) -> tuple[dict, dict]:
    from dask import delayed, compute

    elapsed = {}  # keep track of time spent in each step
    results = {}

    # split the weather data into chunks and write them to disk
    chunks_dir = chunks_dir_from_weather_data_path(weather_data_path)
    chunker = GeoChunker.from_weather_data_path(
        weather_data_path, x_chunk_size=25.0, y_chunk_size=25.0, time="2024", chunks_dir=chunks_dir
    )
    chunks = chunker.chunks
    data_prep_start = perf_counter()
    data_preparation_tasks = []
    for chunk in chunks:
        data_preparation_tasks.append(delayed(chunk_to_offline_cutout)(weather_data_path, chunk))
    compute(*data_preparation_tasks)
    elapsed["preparation"] = perf_counter() - data_prep_start

    # Calculate availability based on LULC codes and weights for wind
    cav_wind_start = perf_counter()
    cav_wind_tasks = []
    for chunk in chunks:
        cav_wind_tasks.append(delayed(calculate_available_capacity_per_pixel)(chunk, "wind"))
    results["cav_wind"] = compute(*cav_wind_tasks)
    elapsed["cav_wind"] = perf_counter() - cav_wind_start

    # Calculate availability based on LULC codes and weights for pv
    cav_pv_start = perf_counter()
    cav_pv_tasks = []
    for chunk in chunks:
        cav_pv_tasks.append(delayed(calculate_available_capacity_per_pixel)(chunk, "pv"))
    results["cav_pv"] = compute(*cav_pv_tasks)
    elapsed["cav_pv"] = perf_counter() - cav_pv_start

    # calculate the wind potential for each chunk
    wind_start = perf_counter()
    wind_tasks, pv_tasks = [], []
    for chunk in chunks:
        wind_tasks.append(delayed(calc_wind_potential)(chunk))
        pv_tasks.append(delayed(calc_pv_potential)(chunk))
    results["wind"] = compute(*wind_tasks)
    elapsed["wind"] = perf_counter() - wind_start

    # calculate the pv potential for each chunk
    pv_start = perf_counter()
    results["pv"] = compute(*pv_tasks)
    elapsed["pv"] = perf_counter() - pv_start

    # Calculate the supply for renewable energy technologies
    supply_wind_start = perf_counter()
    supply_wind_tasks, supply_pv_tasks = [], []
    for chunk in chunks:
        supply_wind_tasks.append(delayed(combine_availability_and_potential)(chunk, "wind"))
        supply_pv_tasks.append(delayed(combine_availability_and_potential)(chunk, "pv"))
    results["supply_wind"] = compute(*supply_wind_tasks)
    elapsed["supply_wind"] = perf_counter() - supply_wind_start

    supply_pv_start = perf_counter()
    results["supply_pv"] = compute(*supply_pv_tasks)
    elapsed["supply_pv"] = perf_counter() - supply_pv_start

    # interpolate to hourly resolution
    interpolate_start = perf_counter()
    interpolate_tasks = []
    for chunk in chunks:
        interpolate_tasks.append(delayed(interpolate_to_hourly_resolution)(chunk))
    results["interpolate"] = compute(*interpolate_tasks)
    elapsed["interpolate"] = perf_counter() - interpolate_start

    return elapsed, results


def calculate_wind_and_pv_potentials_for_regions(regions):
    elapsed, results = {}, {}
    all_start = perf_counter()
    for name, path in regions.items():
        elapsed_for_path, results_for_path = calculate_wind_and_pv_potentials(path)
        elapsed[name] = elapsed_for_path
        results[name] = results_for_path
    elapsed["all"] = perf_counter() - all_start
    return elapsed, results
