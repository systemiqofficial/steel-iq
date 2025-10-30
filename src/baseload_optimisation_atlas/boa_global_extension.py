import numpy as np
import xarray as xr
import dask
from dask import delayed
from dask.distributed import Client
import time
import logging

from steelo.adapters.dataprocessing.preprocessing.iso3_finder import derive_iso3
from baseload_optimisation_atlas.boa_geospatial_utils import (
    worker_init_geocoder,
    convert_resolution_to_string,
    choose_land_points_in_cutout,
)
from baseload_optimisation_atlas.boa_config import (
    BaseloadPowerConfig,
    REGION_COORDS,
    ERA5_DATA_RESOLUTION,
    ERA5_DATA_YEAR,
)
from baseload_optimisation_atlas.boa_input_preprocessing import (
    process_global_baseload_simulation_costs,
)
from baseload_optimisation_atlas.boa_logic import (
    capacity_sampling,
    return_global_average_costs,
    filter_designs_according_to_coverage_and_calculate_costs,
)
from baseload_optimisation_atlas.boa_plotting import (
    plot_regional_optimum_baseload_power_simulation_map,
    plot_global_optimum_baseload_power_simulation_map,
)


def extract_costs_for_point(
    lat: float,
    lon: float,
    costs: xr.Dataset,
) -> tuple[dict, dict, float]:
    """
    Extract costs for a given location from cost dataset, using global average or neighboring country if country code not found.

    Args:
        lat: Latitude
        lon: Longitude
        costs: Cost dataset containing CAPEX, OPEX, and cost of capital indexed by ISO3 country codes

    Returns:
        Tuple containing:
            - CAPEX dictionary for solar and wind
            - OPEX percentage dictionary for solar, wind, and battery
            - Cost of capital

    Note:
        - Geocoder must be initialized before calling this function
        - Manual mappings apply for specific countries without cost data (e.g., GUF→SUR, AND→ESP, MTQ→DOM, SGS→ARG, FRO→DNK, ALA→FIN)
    """
    # Derive the country code from the latitude and longitude
    # Note: The geocoder is initialized in each worker before this function is called
    try:
        country_code = derive_iso3(lat, lon, max_distance_km=400)
    except ValueError:
        logging.warning(f"Unknown country code for ({lat}, {lon}). Setting global average costs.")
        return return_global_average_costs(costs)

    # Handle special cases: Assign country code from neighbouring country if the country is not in the
    # costs dataset (manual mappings)
    # Set French Guiana (GUF) to Suriname (SUR)
    if country_code == "GUF":
        country_code = "SUR"
    # Set Andorra (AND) to Spain (ESP)
    elif country_code == "AND":
        country_code = "ESP"
    # Set Martinique (MTQ) to Dominican Republic (DOM)
    elif country_code == "MTQ":
        country_code = "DOM"
    # Set South Georgia and the South Sandwich Islands (SGS) to Argentina (ARG)
    elif country_code == "SGS":
        country_code = "ARG"
    # Set Faroe Islands (FRO) to Denmark (DNK)
    elif country_code == "FRO":
        country_code = "DNK"
    # Set Aland Islands (ALA) to Finland (FIN)
    elif country_code == "ALA":
        country_code = "FIN"

    # Check if the country code exists in the costs dataset
    if country_code not in costs["iso3"].values:
        logging.warning(f"Country code {country_code} not found in costs data. Setting global average costs.")
        return return_global_average_costs(costs)
    else:
        # Extract costs for the country code
        capex = {}
        for tech in ["solar", "wind"]:
            capex[tech] = costs["Capex " + tech].sel(iso3=country_code).values
        opex_pct = {}
        for tech in ["solar", "wind", "battery"]:
            opex_pct[tech] = costs["Opex " + tech].sel(iso3=country_code).values
        cost_of_capital = float(costs["Cost of capital"].sel(iso3=country_code).values)

        return capex, opex_pct, cost_of_capital


def run_baseload_optimization_for_point(
    lat: float,
    lon: float,
    profile: xr.Dataset,
    max_cap_full: xr.Dataset,
    baseload_demand: float,
    costs: xr.Dataset,
    storage_costs: dict[str, np.ndarray],
    investment_horizon: int,
    p: int,
    n: int,
) -> tuple[dict, float, float]:
    """
    Optimize renewable energy system design for a grid point to minimize LCOE while meeting baseload demand coverage threshold.

    Steps:
        1. Extract renewable energy profile for grid point and skip if zero potential
        2. Calculate physical capacity limits based on grid area and minimum spacing (land use not considered)
        3. Sample system designs and filter by hourly coverage threshold to obtain accepted designs
        4. Calculate installation cost and LCOE for accepted designs
        5. Select optimal design: accepted design with lowest LCOE

    Args:
        lat: Latitude
        lon: Longitude
        profile: Renewable energy profile dataset
        max_cap_full: Maximum capacity dataset
        baseload_demand: Baseload demand (MW)
        costs: Cost dataset
        storage_costs: Battery storage cost parameters
        investment_horizon: Investment horizon in years
        p: Percentile threshold for demand coverage (e.g., 15 means 85% coverage required)
        n: Number of random design samples

    Returns:
        Tuple containing:
            - Optimal design dictionary with solar, wind, and battery overscale factors
            - LCOE (USD/MWh)
            - Installation cost (USD)
    """
    # Get the profile for the current grid point; skipping grid points with zero potential
    profile_grid_point_ds = profile.sel(x=lon, y=lat, method="nearest")
    if np.sum(profile_grid_point_ds.solar.values) == 0 and np.sum(profile_grid_point_ds.wind.values) == 0:
        logging.debug(f"Skipping grid point {lat}, {lon} due to zero potential.")
        return {"solar": 0, "wind": 0, "battery": 0}, 0, 0
    profile_grid_point = {}
    for tech in ["solar", "wind"]:
        profile_grid_point[tech] = profile_grid_point_ds[tech].values.flatten()

    # Capacity limit (physical constraints)
    # Assumption: The renewable installation built to power a steel plant fits within a single grid point (0.25 deg ~ 15-30 km)
    max_cap = max_cap_full.sel(x=lon, y=lat, method="nearest")
    overbuild_limit = {tech: float(max_cap[tech].values) / baseload_demand for tech in ["pv", "wind"]}
    overbuild_limit = {key.replace("pv", "solar"): value for key, value in overbuild_limit.items()}
    logging.debug(f"Physical capacity limit for grid point {lat}, {lon}: {overbuild_limit}")

    # Create a sample of feasible designs (installed solar, wind, and battery capacity) given a certain RE profile
    feasible_designs = capacity_sampling(profile_grid_point, p, limit=overbuild_limit, n_samples=n, seed=12)

    # Calculate the installation cost and LCOE for each accepted design (accepted designs are those that meet the demand MOST of the time)
    capex, opex_pct, cost_of_capital = extract_costs_for_point(lat, lon, costs)
    accepted_designs, installation_costs, lcoes = filter_designs_according_to_coverage_and_calculate_costs(
        feasible_designs,
        baseload_demand,
        capex,
        storage_costs,
        opex_pct,
        profile_grid_point,
        cost_of_capital,
        investment_horizon,
        p,
    )

    # Find the optimal design (the one with the lowest LCOE)
    if len(accepted_designs) == 0:
        logging.debug(f"No accepted designs for grid point {lat}, {lon}.")
        return {"solar": 0, "wind": 0, "battery": 0}, 0, 0

    opt_idx = np.argmin(lcoes)
    optimal_design = accepted_designs[opt_idx]
    optimal_lcoe = lcoes[opt_idx]
    optimal_cost = installation_costs[opt_idx]

    return optimal_design, optimal_lcoe, optimal_cost


def execute_baseload_optimization_for_region(
    year: int,
    region: str,
    baseload_demand: float,
    p: int,
    profile: xr.Dataset,
    costs: xr.Dataset,
    storage_costs: dict[str, np.ndarray],
    investment_horizon: int,
    n: int,
    config: BaseloadPowerConfig,
) -> xr.Dataset:
    """
    Execute baseload optimization for all land grid points in a region using parallel Dask processing.

    Args:
        year: Investment year
        region: Region name
        baseload_demand: Baseload demand (MW)
        p: Percentile threshold for demand coverage
        profile: Renewable energy profile dataset
        costs: Cost dataset
        storage_costs: Battery storage cost parameters
        investment_horizon: Investment horizon in years
        n: Number of random design samples per grid point
        config: Baseload power configuration

    Returns:
        Dataset containing optimal solution with variables: lcoe, installation_cost, solar_factor, wind_factor, battery_factor

    Side Effects:
        - Saves optimal solution to NetCDF file
        - Initializes Dask client with 10 workers
        - Initializes geocoder in each worker

    Note:
        If optimal solution already exists in output directory, it is loaded instead of recalculated
    """
    # Check if the file exists already
    optimal_sol_path = (
        config.geo_output_dir
        / "baseload_power_simulation"
        / f"p{str(p)}"
        / region
        / f"optimal_sol_{region}_{str(year)}_p{str(p)}.nc"
    )
    if optimal_sol_path.exists():
        logging.info(f"Optimal solution for {region} already exists. Loading from {optimal_sol_path}.")
        return xr.open_dataset(optimal_sol_path)
    else:
        logging.info(f"Running optimization for {region}.")

        # Load max capacity data
        max_cap_path = (
            config.cav_dir
            / f"max_capacity_{region}_{ERA5_DATA_YEAR}_{convert_resolution_to_string(ERA5_DATA_RESOLUTION)}_deg.nc"
        )
        max_cap = xr.open_dataset(max_cap_path)

        # Run optimization for all land grid points
        land_points, all_lats, all_lons = choose_land_points_in_cutout(profile, config.terrain_nc_path)
        zeroes = np.zeros((len(all_lats), len(all_lons)))
        optimal_sol = xr.Dataset(
            coords={
                "lat": all_lats,
                "lon": all_lons,
            },
            data_vars={
                "lcoe": (("lat", "lon"), zeroes),
                "installation_cost": (("lat", "lon"), zeroes),
                "solar_factor": (("lat", "lon"), zeroes),
                "wind_factor": (("lat", "lon"), zeroes),
                "battery_factor": (("lat", "lon"), zeroes),
            },
        )
        client = Client(n_workers=10)
        # Initialize geocoder in each worker
        client.run(worker_init_geocoder, config)
        results = dask.compute(
            *[
                delayed(run_baseload_optimization_for_point)(
                    lat,
                    lon,
                    profile,
                    max_cap,
                    baseload_demand,
                    costs,
                    storage_costs,
                    investment_horizon,
                    p,
                    n,
                )
                for lat, lon in land_points
            ]
        )
        client.close()

        # Convert to an xarray dataset and save to file
        for i, (lat, lon) in enumerate(land_points):
            optimal_design, optimal_lcoe, optimal_cost = results[i]
            if np.isnan(optimal_lcoe):
                continue
            temp_lcoe = optimal_sol["lcoe"].copy()
            temp_installation_cost = optimal_sol["installation_cost"].copy()
            temp_solar_factor = optimal_sol["solar_factor"].copy()
            temp_wind_factor = optimal_sol["wind_factor"].copy()
            temp_battery_factor = optimal_sol["battery_factor"].copy()

            temp_lcoe.loc[dict(lat=lat, lon=lon)] = optimal_lcoe
            temp_solar_factor.loc[dict(lat=lat, lon=lon)] = optimal_design["solar"]
            temp_wind_factor.loc[dict(lat=lat, lon=lon)] = optimal_design["wind"]
            temp_battery_factor.loc[dict(lat=lat, lon=lon)] = optimal_design["battery"]

            optimal_sol["lcoe"] = temp_lcoe
            optimal_sol["installation_cost"] = temp_installation_cost
            optimal_sol["solar_factor"] = temp_solar_factor
            optimal_sol["wind_factor"] = temp_wind_factor
            optimal_sol["battery_factor"] = temp_battery_factor
        optimal_sol_path.parent.mkdir(parents=True, exist_ok=True)
        optimal_sol.to_netcdf(optimal_sol_path, mode="w", format="NETCDF4")

        return optimal_sol


def combine_regional_datasets_into_global_dataset(year: int, p: int, config: BaseloadPowerConfig) -> xr.Dataset | None:
    """
    Combine all regional datasets into a single global dataset by interpolating onto a common grid and merging.

    Args:
        year: Investment year
        p: Percentile threshold for demand coverage
        config: Baseload power configuration

    Returns:
        Global dataset with merged regional data, or None if any region is missing

    Side Effects:
        Saves global dataset to NetCDF file

    Note:
        If global dataset already exists in output directory, it is loaded instead of recalculated
    """
    regions = list(REGION_COORDS.keys())
    regional_datasets = {}

    # Check if the global dataset already exists
    global_output_path = (
        config.geo_output_dir
        / "baseload_power_simulation"
        / f"p{str(p)}"
        / "GLOBAL"
        / f"optimal_sol_GLOBAL_{year}_p{str(p)}.nc"
    )
    if global_output_path.exists():
        logging.info(f"Global optimal solution already exists at {global_output_path}.")
        return xr.open_dataset(global_output_path)
    else:
        logging.info(f"Combining regional datasets into global dataset for {year}.")
        # Load all regional datasets
        for region in regions:
            optimal_sol_path = (
                config.geo_output_dir
                / "baseload_power_simulation"
                / f"p{str(p)}"
                / region
                / f"optimal_sol_{region}_{year}_p{str(p)}.nc"
            )
            if not optimal_sol_path.exists():
                logging.warning(f"Optimal solution for {region} not found. Please check processing.")
                return None
            regional_datasets[region] = xr.open_dataset(optimal_sol_path)

        logging.info("Generating global maps from regional datasets.")

        # Define global grid
        lat_global = np.arange(-90, 90.1, 0.25)  # Adjust resolution if needed
        lon_global = np.arange(-180, 180.1, 0.25)

        # Interpolate regional datasets onto the global grid
        interpolated_datasets = {
            region: ds.interp(lat=lat_global, lon=lon_global, method="nearest")
            for region, ds in regional_datasets.items()
        }

        # Initialize global dataset with NaN values
        global_ds = xr.full_like(next(iter(interpolated_datasets.values())), fill_value=np.nan)

        # Merge interpolated datasets into the global dataset
        for region, ds in interpolated_datasets.items():
            for var in ds.data_vars:
                if var not in global_ds:
                    global_ds[var] = xr.full_like(ds[var], fill_value=np.nan)
                global_ds[var] = xr.where(global_ds[var].isnull(), ds[var], global_ds[var])

        # Remove zero values
        global_ds = global_ds.where(global_ds != 0)

        # Save the global dataset
        global_output_path.parent.mkdir(parents=True, exist_ok=True)
        global_ds.to_netcdf(global_output_path)

        return global_ds


def execute_baseload_power_simulation(
    year: int = 2023,
    region: str = "GLOBAL",
    baseload_demand: float = 500.0,
    p: int = 15,
    n: int = 1000,
    config: BaseloadPowerConfig | None = None,
) -> None:
    """
    Execute baseload power simulation for a region or globally, running optimization in parallel and plotting results.

    Args:
        year: Investment year (default: 2023)
        region: Region name, or "GLOBAL" to run for all regions and combine into global map (default: "GLOBAL")
        baseload_demand: Baseload demand in MW (default: 500.0)
        p: Percentile threshold for demand coverage (e.g., 15 means 85% coverage required) (default: 15)
        n: Number of random design samples per grid point, controls variability of overscale factor distributions (default: 1000)
        config: Configuration object containing all necessary paths for standalone baseload power simulation (default: None)

    Side Effects:
        - Preprocesses costs globally and saves to file
        - For each region: runs optimization, saves results to NetCDF, and plots regional map
        - If region is "GLOBAL": combines all regional datasets into global dataset and plots global map
        - Logs total runtime

    Note:
        Configuration object is required - raises ValueError if None or invalid
    """
    start = time.time()

    # Verify all required paths are set in the configuration
    if config is None or not isinstance(config, BaseloadPowerConfig):
        raise ValueError("Configuration object is required. Please provide a valid BaseloadPowerConfig instance.")

    # Preprocess costs globally and save to file
    projected_cost_per_country, storage_costs, investment_horizon = process_global_baseload_simulation_costs(
        investment_year=year,
        master_input_path=config.master_input_path,
        renewable_input_path=config.renewable_input_path,
        geo_output_dir=config.geo_output_dir,
    )

    if region == "GLOBAL":
        logging.info(f"Running global baseload optimization for year {year}.")
        # Run for all continents
        for continent in REGION_COORDS.keys():
            logging.info(f"Running baseload optimization for {continent}")
            # Profiles for REs
            profile_path = (
                config.atlite_output_dir
                / f"pv_and_wind_potential_{continent}_{ERA5_DATA_YEAR}_{convert_resolution_to_string(ERA5_DATA_RESOLUTION)}_deg.nc"
            )
            profile = xr.open_dataset(profile_path)
            profile = profile.rename({"pv": "solar"})
            # Optimization
            execute_baseload_optimization_for_region(
                year,
                continent,
                baseload_demand,
                p,
                profile,
                projected_cost_per_country,
                storage_costs,
                investment_horizon,
                n=n,
                config=config,
            )
            # Plot results for single continent
            plot_regional_optimum_baseload_power_simulation_map(year, continent, p, config)
        # Combine all regions into a single dataset and save to file
        global_optimal_sol = combine_regional_datasets_into_global_dataset(year, p, config)
        # Plot global results
        if global_optimal_sol is not None:
            plot_global_optimum_baseload_power_simulation_map(global_optimal_sol, year, p, config)

    else:
        logging.info(f"Running baseload optimization for {region} in {year}.")
        # Profiles for REs
        profile_path = (
            config.atlite_output_dir
            / f"pv_and_wind_potential_{region}_{ERA5_DATA_YEAR}_{convert_resolution_to_string(ERA5_DATA_RESOLUTION)}_deg.nc"
        )
        profile = xr.open_dataset(profile_path)
        profile = profile.rename({"pv": "solar"})
        # Optimization
        execute_baseload_optimization_for_region(
            year,
            region,
            baseload_demand,
            p,
            profile,
            projected_cost_per_country,
            storage_costs,
            investment_horizon,
            n=n,
            config=config,
        )
        # Plot results for selected region
        plot_regional_optimum_baseload_power_simulation_map(year, region, p, config)

    end = time.time()
    logging.info(f"Total runtime: {(end - start) / 60} minutes")
