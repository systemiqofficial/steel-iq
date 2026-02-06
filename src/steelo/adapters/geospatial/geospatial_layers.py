import xarray as xr
import numpy as np
import pandas as pd
import logging

# Set up SSL for bundled environments - do this BEFORE any imports that might use HTTPS
import os
import ssl
import sys

# Detect if we're in a bundled environment
if getattr(sys, "frozen", False) or "steelo-electron" in sys.executable or "STEEL-IQ" in sys.executable:
    try:
        # Try certifi first
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    except ImportError:
        # Fallback: disable SSL verification (less secure but works)
        ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[assignment]
        os.environ["PYTHONHTTPSVERIFY"] = "0"
        logging.warning("Warning: Running in bundled environment with SSL verification disabled")

from steelo.adapters.geospatial.geospatial_toolbox import create_global_grid_with_iso
from steelo.adapters.geospatial.geospatial_calculations import (
    calculate_lcoh_from_power_price,
    calculate_regional_hydrogen_ceiling,
    apply_hydrogen_price_cap,
    calculate_distance_to_demand_and_feedstock,
)
from steelo.adapters.repositories.interface import Repository
from steelo.domain.models import CountryMappingService, Environment, PlotPaths
from steelo.utilities.variable_matching import LULC_LABELS_TO_NUM
from steelo.utilities.plotting import (
    plot_screenshot,
    plot_value_histogram,
    plot_global_grid_with_iso3,
)
from typing import Optional, Any, cast, TYPE_CHECKING

if TYPE_CHECKING:
    from steelo.domain.models import GeoDataPaths
    from steelo.simulation import GeoConfig
from steelo.domain.constants import (
    Year,
    GRAVITY_ACCELERATION,
    PERMWh_TO_PERkWh,
    PERkWh_TO_PERMWh,
    MioUSD_TO_USD,
    USD_TO_MioUSD,
    rad_TO_deg,
)


def add_iso3_codes(resolution: float, geo_paths: "GeoDataPaths") -> xr.Dataset:
    """
    Create a global grid with ISO3 codes assigned to each (lat,lon) point.

    Args:
        resolution: Grid resolution in degrees
        geo_paths: Paths to geospatial data files (shapefiles, static layers directory)

    Returns:
        Dataset with 'iso3' variable containing ISO3 country codes for each grid point

    Side Effects:
        - Generates and saves plot of global grid with ISO3 codes
        - Saves ISO3 grid to NetCDF file in static_layers_dir for reuse
    """
    logger = logging.getLogger(f"{__name__}.add_iso3_codes")
    # Ensure required paths are provided
    if not geo_paths or not geo_paths.static_layers_dir:
        raise ValueError(
            "The static_layers_dir is required to save the global grid with ISO3 codes. "
            "Ensure geo_paths is provided and contains static_layers_dir."
        )
    iso3_grid_path = geo_paths.static_layers_dir / "global_grid_with_iso3.nc"

    # Check if the output file already exists
    if iso3_grid_path.exists():
        logger.info(f"[GEO LAYERS] Global grid with ISO3 codes already exists at {iso3_grid_path}.")
        try:
            # Try netcdf4 first (preferred)
            ds = xr.open_dataset(iso3_grid_path, engine="netcdf4")
        except (ImportError, ValueError):
            try:
                # Fallback to h5netcdf
                ds = xr.open_dataset(iso3_grid_path, engine="h5netcdf")
            except (ImportError, ValueError):
                # Last resort: scipy engine for basic NetCDF3 files
                ds = xr.open_dataset(iso3_grid_path, engine="scipy")
    else:
        logger.info(f"[GEO LAYERS] Creating global grid with ISO3 codes at {resolution} degree resolution.")

        # Initialize empty global grid
        lat_global = np.arange(-90, 90.1, resolution)
        lon_global = np.arange(-180, 180.1, resolution)
        ds = xr.Dataset(
            coords={"lat": lat_global, "lon": lon_global},
            data_vars={
                "iso3": (("lat", "lon"), np.zeros((len(lat_global), len(lon_global)))),
            },
        )

        # Add ISO3 codes to the global grid
        iso3_grid = create_global_grid_with_iso(resolution, geo_paths=geo_paths)[["ISO_A3", "geometry"]]
        iso3_grid["geometry"] = iso3_grid["geometry"].apply(lambda geom: geom.representative_point())
        iso3_grid["lon"] = iso3_grid["geometry"].x
        iso3_grid["lat"] = iso3_grid["geometry"].y
        iso3_grid = iso3_grid.drop_duplicates(subset=["lat", "lon"], keep="first")
        iso3_pivot = iso3_grid.pivot(index="lat", columns="lon", values="ISO_A3")
        ds["iso3"] = (("lat", "lon"), iso3_pivot.values.astype(str))

        # Plot the global grid with ISO3 codes
        plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
        plot_global_grid_with_iso3(ds, plot_paths=plot_paths_obj)

        # Save to file
        geo_paths.static_layers_dir.mkdir(parents=True, exist_ok=True)
        try:
            ds["iso3"].to_netcdf(iso3_grid_path, mode="w", format="NETCDF4")
        except (ImportError, ValueError):
            ds["iso3"].to_netcdf(iso3_grid_path, mode="w", engine="scipy")
    return ds


def add_feasibility_mask(ds: xr.Dataset, geo_config: "GeoConfig", geo_paths: "GeoDataPaths") -> xr.Dataset:
    """
    Create a binary feasibility mask based on terrain and latitude constraints.

    Sea points, high and steep locations, and extreme latitudes are not feasible for steel and iron production.

    Steps:
        1. Load terrain data (land-sea mask, geopotential, slope)
        2. Create binary masks for land (vs. sea), altitude, and slope
        3. Combine masks and apply latitude filter
        4. Interpolate to match dataset grid resolution

    Formula:
        Altitude (m) = geopotential / gravity acceleration

    Args:
        ds: Dataset to add feasibility mask to
        geo_config: Configuration containing max_altitude (m), max_slope (degrees), max_latitude (degrees)
        geo_paths: Paths to geospatial data files (terrain NetCDF file, static layers directory)

    Returns:
        Dataset with added 'feasibility_mask' variable (binary: 1=feasible, 0=not feasible)

    Side Effects:
        - Generates and saves plots of land-sea mask, altitude, slope, and final feasibility mask
        - Saves feasibility mask to NetCDF file in static_layers_dir for reuse
    """
    logger = logging.getLogger(f"{__name__}.add_feasibility_mask")
    # Ensure required paths are provided
    if not geo_paths or not geo_paths.terrain_nc_path:
        raise ValueError(
            "The terrain_nc_path is required to calculate the feasibility mask. "
            "Ensure geo_paths is provided and contains terrain_nc_path."
        )
    terrain_path = geo_paths.terrain_nc_path
    if not geo_paths or not geo_paths.static_layers_dir:
        raise ValueError(
            "The static_layers_dir is required to save the feasibility mask. "
            "Ensure geo_paths is provided and contains static_layers_dir."
        )
    feasibility_mask_path = geo_paths.static_layers_dir / "feasibility_mask.nc"

    # Check if the output file already exists
    if feasibility_mask_path.exists():
        logger.info(f"[GEO LAYERS] Feasibility mask already exists at {feasibility_mask_path}. Loading from file.")
        try:
            ds["feasibility_mask"] = xr.open_dataset(feasibility_mask_path, engine="netcdf4")["feasibility_mask"]
        except (ImportError, ValueError):
            try:
                ds["feasibility_mask"] = xr.open_dataset(feasibility_mask_path, engine="h5netcdf")["feasibility_mask"]
            except (ImportError, ValueError):
                ds["feasibility_mask"] = xr.open_dataset(feasibility_mask_path, engine="scipy")["feasibility_mask"]
    else:
        logger.info("[GEO LAYERS] Adding feasibility mask.")
        try:
            terrain = xr.open_dataset(terrain_path, engine="netcdf4").isel(valid_time=0).drop_vars("valid_time")
        except (ImportError, ValueError):
            try:
                terrain = xr.open_dataset(terrain_path, engine="h5netcdf").isel(valid_time=0).drop_vars("valid_time")
            except (ImportError, ValueError):
                terrain = xr.open_dataset(terrain_path, engine="scipy").isel(valid_time=0).drop_vars("valid_time")
        terrain = terrain.rename({"latitude": "lat", "longitude": "lon"})

        # Binary land sea mask
        landsea_mask_bin = (terrain["lsm"] > 0.5).astype(int)
        plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
        plot_screenshot(
            landsea_mask_bin,
            title="Binary Land Sea Mask",
            var_type="binary",
            save_name="landsea_mask_bin",
            plot_paths=plot_paths_obj,
        )

        # Altitude
        geopotential = terrain["z"]
        altitude = geopotential / GRAVITY_ACCELERATION  # Convert geopotential to altitude in meters
        altitude_pos = altitude.where(altitude > 0)
        altitude_bin = xr.where(altitude_pos < geo_config.max_altitude, 1, 0)
        plot_screenshot(
            altitude_bin,
            title="Binary Altitude",
            var_type="binary",
            save_name="altitude_bin",
            plot_paths=plot_paths_obj,
        )

        # Slope (calculated at subgrid level)
        slope_rad = terrain["slor"]
        slope = slope_rad * rad_TO_deg
        slope_bin = xr.where(slope < geo_config.max_slope, 1, 0)
        plot_screenshot(
            slope_bin,
            title="Binary Slope",
            var_type="binary",
            save_name="slope_bin",
            plot_paths=plot_paths_obj,
        )

        # Combine into a single feasibility mask
        feasibility_mask = landsea_mask_bin * altitude_bin * slope_bin
        feasibility_mask = feasibility_mask.fillna(0)
        feasibility_mask_newcoords = feasibility_mask.assign_coords(
            lon=np.where(feasibility_mask.lon > 180, feasibility_mask.lon - 360, feasibility_mask.lon)
        )
        feasibility_mask_interp = feasibility_mask_newcoords.interp(lat=ds.lat, lon=ds.lon, method="nearest")

        # Remove high latitudes - using absolute value for clarity and including boundaries
        feasibility_mask_corrected = xr.where(
            abs(feasibility_mask_interp.lat) <= geo_config.max_latitude,
            feasibility_mask_interp,
            0,
        )

        # Log effect of feasibility mask
        n_feasible_cells = int((feasibility_mask_corrected > 0).sum())
        n_total_cells = int(feasibility_mask_corrected.size)
        logger.info(
            f"[GEO LAYERS] Feasibility mask filter applied (max_altitude={geo_config.max_altitude}, max_slope={geo_config.max_slope}°, max_latitude={geo_config.max_latitude}°): "
            f"feasible cells {n_feasible_cells:,} / {n_total_cells:,}, "
            f"reduction={(1 - n_feasible_cells / max(n_total_cells, 1)) * 100:.1f}%"
        )

        # Plot and save feasibility mask
        ds["feasibility_mask"] = feasibility_mask_corrected
        plot_screenshot(
            ds["feasibility_mask"],
            title="Feasibility Mask",
            var_type="binary",
            save_name="feasibility_mask",
            plot_paths=plot_paths_obj,
        )
        ds["feasibility_mask"].to_netcdf(feasibility_mask_path, mode="w", format="NETCDF4")
    return ds


def add_baseload_power_price(
    ds: xr.Dataset, baseload_coverage: float, target_year: int, geo_paths: "GeoDataPaths"
) -> xr.Dataset:
    """
    Add pre-calculated baseload power price to the dataset.

    The baseload power price is the LCOE (Levelized Cost of Energy) of the optimal renewable energy solution
    for the given coverage percentage. LCOE values are pre-calculated for select years and linearly interpolated
    for in-between years to reduce computational cost.

    Args:
        ds: Dataset to add baseload power price to
        baseload_coverage: Baseload power coverage percentage (0.0 to 1.0)
        target_year: Simulation year for which to retrieve/interpolate LCOE
        geo_paths: Paths to geospatial data files (baseload power simulation directory)

    Returns:
        Dataset with added 'lcoe' variable containing baseload power price (USD/kWh)

    Side Effects:
        - Generates and saves plot of optimal LCOE and histogram
    """
    logger = logging.getLogger(f"{__name__}.add_baseload_power_price")
    # Ensure required paths are provided
    if not geo_paths or not geo_paths.baseload_power_sim_dir:
        raise ValueError(
            "baseload_power_sim_dir is required for baseload power cost calculations. "
            "Ensure geo_paths is provided and contains baseload_power_sim_dir."
        )
    baseload_dir = geo_paths.baseload_power_sim_dir

    # Find all available LCOE files and their years
    p = int((1 - baseload_coverage) * 100)
    lcoe_dir = baseload_dir / f"p{str(p)}" / "GLOBAL"
    lcoe_files = list(lcoe_dir.glob(f"optimal_sol_GLOBAL_*_p{str(p)}.nc"))
    available_years = []
    file_map = {}
    for f in lcoe_files:
        try:
            year_str = f.name.split("_")[3]
            year = int(year_str)
            available_years.append(year)
            file_map[year] = f
        except Exception:
            continue
    if not available_years:
        raise FileNotFoundError(f"No LCOE files found in {lcoe_dir}")

    if target_year in available_years:
        # Choose the data for the target year if available
        logger.info(f"[GEO LAYERS] Explicitly calculated LCOE is available for year {target_year}.")
        baseload_lcoe_path = file_map[target_year]
        baseload_lcoe_year = xr.open_dataset(baseload_lcoe_path)["lcoe"]
    else:
        # If not, interpolate among the two closest years to the target year (below and above)
        years_sorted = sorted(available_years)
        low_year = max([y for y in years_sorted if y <= target_year], default=years_sorted[0])
        high_year = min([y for y in years_sorted if y >= target_year], default=years_sorted[-1])
        logger.info(
            f"[GEO LAYERS] Interpolating baseload LCOE for year {target_year} from years {low_year} and {high_year}."
        )
        baseload_lcoe = []
        for ref_year in sorted(set([low_year, high_year])):
            baseload_lcoe_path = file_map[ref_year]
            baseload_lcoe.append(xr.open_dataset(baseload_lcoe_path)["lcoe"].expand_dims(year=[ref_year]))
        baseload_lcoe_concat = xr.concat(baseload_lcoe, dim="year")
        baseload_lcoe_year = baseload_lcoe_concat.interp(year=target_year, method="linear").drop_vars("year")
    baseload_lcoe_year = baseload_lcoe_year * PERMWh_TO_PERkWh  # USD/MWh to USD/kWh (BOA in USD/MWh, PAM in USD/kWh)
    ds = xr.merge([ds, baseload_lcoe_year])

    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    plot_screenshot(
        ds["lcoe"].where(ds["feasibility_mask"] > 0) * PERkWh_TO_PERMWh,  # USD/kWh to USD/MWh
        title=f"Optimal LCOE for {100 - p}% coverage in {target_year} (USD/MWh)",
        var_type="sequential",
        max_val=200,
        save_name=f"optimal_lcoe_{str(target_year)}_p{str(p)}",
        plot_paths=plot_paths_obj,
    )
    plot_value_histogram(ds, var_name="lcoe", bins=100, log_scale=True, plot_paths=plot_paths_obj)

    return ds


def add_grid_power_price(
    ds: xr.Dataset,
    input_costs: dict[str, dict[int, dict[str, float]]],
    year: int,
    geo_paths: "GeoDataPaths",
) -> xr.Dataset:
    """
    Assign country-level grid power prices to each grid cell based on ISO3 codes.

    Args:
        ds: Dataset to add grid prices to (must contain 'iso3' and 'feasibility_mask' variables)
        input_costs: Dictionary mapping ISO3 codes to year-specific costs including electricity prices
        year: Simulation year
        geo_paths: Paths to geospatial data files for plotting outputs

    Returns:
        Dataset with added 'grid_price' variable containing grid power prices (USD/kWh)

    Side Effects:
        - Generates and saves plot of grid power prices

    Note:
        For grid cells without a matching ISO3 code, the maximum grid price is used as a fallback.
    """
    grid_price = pd.Series({iso3: input_costs[iso3][year]["electricity"] for iso3 in input_costs})
    ds["grid_price"] = xr.apply_ufunc(
        lambda iso3: grid_price.loc[iso3]
        if (isinstance(iso3, str) and iso3 in grid_price.index and not pd.isna(iso3))
        else grid_price.max(),
        ds["iso3"],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
    )

    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    plot_screenshot(
        ds["grid_price"].where(ds["feasibility_mask"] > 0) * PERkWh_TO_PERMWh,  # USD/kWh to USD/MWh
        title="Grid Power Price (USD/MWh)",
        var_type="sequential",
        save_name=f"grid_power_price_{str(year)}",
        plot_paths=plot_paths_obj,
    )
    return ds


def add_power_price(
    ds: xr.Dataset,
    year: int,
    input_costs: dict[str, dict[int, dict[str, float]]],
    baseload_coverage: float,
    geo_paths: "GeoDataPaths",
) -> xr.Dataset:
    """
    Calculate combined power price from grid and baseload sources.

    Formula:
        Full power price = (1-p)% from grid + p% from own installation (baseload)
        where p = baseload_coverage

    Args:
        ds: Dataset to add power prices to
        year: Simulation year
        input_costs: Dictionary mapping ISO3 codes to year-specific costs including electricity prices
        baseload_coverage: Percentage of power requirement covered by own baseload installation (0.0 to 1.0)
        geo_paths: Paths to geospatial data files

    Returns:
        Dataset with added 'power_price' variable containing combined power price (USD/kWh)

    Side Effects:
        - Generates and saves plot of power prices and histogram

    Note:
        The baseload LCOE is already calculated for the specified coverage percentage, so no additional
        multiplication by (1-p) is needed.
    """
    logger = logging.getLogger(f"{__name__}.add_power_price")
    logger.info(
        f"[GEO LAYERS] Adding power price as a combination of baseload and grid power price "
        f"at {baseload_coverage * 100}% baseload coverage."
    )
    ds = add_grid_power_price(ds, input_costs, year, geo_paths=geo_paths)
    if baseload_coverage > 0:
        ds = add_baseload_power_price(ds, baseload_coverage, year, geo_paths=geo_paths)
        ds["power_price"] = ds["grid_price"] * (1 - baseload_coverage) + ds["lcoe"]
        title = "Power Price - combination of baseload and grid for full coverage (USD/MWh)"
    else:
        ds["power_price"] = ds["grid_price"]
        title = "Power Price - grid only (USD/MWh)"

    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    plot_screenshot(
        ds["power_price"].where(ds["feasibility_mask"] > 0) * PERkWh_TO_PERMWh,  # USD/kWh to USD/MWh
        title=title,
        var_type="sequential",
        max_val=200,
        save_name=f"power_price_100cov_{str(year)}",
        plot_paths=plot_paths_obj,
    )
    plot_value_histogram(ds, var_name="power_price", bins=100, log_scale=True, plot_paths=plot_paths_obj)

    return ds


def add_capped_hydrogen_price(
    ds: xr.Dataset,
    year: int,
    hydrogen_efficiency: dict[Year, float],
    hydrogen_capex_opex: dict[str, dict[Year, float]],
    country_mappings: CountryMappingService,
    baseload_coverage: float,
    geo_config: "GeoConfig",
    geo_paths: "GeoDataPaths",
) -> xr.Dataset:
    """
    Calculate and apply regional ceilings to hydrogen prices for each grid cell.

    Steps:
        1. Calculate the LCOH per grid cell based on the power price
        2. Calculate the hydrogen ceiling for each interconnected region
        3. Apply regional ceilings and intraregional trade options to set capped hydrogen price

    Args:
        ds: Dataset with power prices (must contain 'power_price' and 'iso3' variables)
        year: Simulation year
        hydrogen_efficiency: Dictionary mapping years to electrolyser efficiency values (MWh/kg)
        hydrogen_capex_opex: Dictionary mapping country codes to year-specific CAPEX and OPEX values (USD/kg)
        country_mappings: CountryMappingService with region mappings for hydrogen trade
        baseload_coverage: Baseload power coverage percentage (0.0 to 1.0)
        geo_config: Configuration containing hydrogen ceiling percentile and trade settings
        geo_paths: Paths to geospatial data files for plotting outputs

    Returns:
        Dataset with added 'capped_lcoh' variable containing capped hydrogen prices (USD/kg)

    Side Effects:
        - Generates and saves plot of capped LCOH
    """
    logger = logging.getLogger(f"{__name__}.add_capped_hydrogen_price")
    logger.info("[GEO LAYERS] Adding (capped) LCOH (Levelized Cost of Hydrogen) to the dataset.")

    ds = calculate_lcoh_from_power_price(ds, year, hydrogen_efficiency, hydrogen_capex_opex)
    regional_ceiling = calculate_regional_hydrogen_ceiling(ds, country_mappings, geo_config.hydrogen_ceiling_percentile)
    ds = apply_hydrogen_price_cap(ds, regional_ceiling, geo_config)

    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    plot_screenshot(
        ds["capped_lcoh"].where(ds["feasibility_mask"] > 0),
        title=f"Optimal (capped) LCOH in {year} (USD/kg)",
        var_type="sequential",
        min_val=1,
        max_val=7,
        save_name=f"lcoh_{str(year)}_p{str(int((1 - baseload_coverage) * 100))}",
        plot_paths=plot_paths_obj,
    )

    return ds


def add_capex_proxy_for_steel_and_iron_making_tech(capex_dict: dict) -> Optional[float]:
    """
    Calculate average greenfield CAPEX across all steel and iron making technologies.

    Args:
        capex_dict: Nested dictionary containing CAPEX values for different regions, technologies, and field types

    Returns:
        Average greenfield CAPEX value (USD/t), or None if no greenfield values found
    """
    logger = logging.getLogger(f"{__name__}.add_capex_proxy_for_steel_and_iron_making_tech")
    logger.info("[GEO LAYERS] Adding average greenfield CAPEX for steel and iron making technologies.")

    def extract_greenfield_values(d, parent_key=None):
        """
        Extract only greenfield values from the nested dictionary.

        Args:
            d: Dictionary or value to extract from
            parent_key: Parent key to track if we're in a brownfield branch (for exclusion)

        Returns:
            List of greenfield CAPEX values found in the dictionary
        """
        values = []
        if isinstance(d, dict):
            for k, v in d.items():
                # If we have both greenfield and brownfield, only take greenfield
                if k == "greenfield":
                    if isinstance(v, (float, int)):
                        values.append(v)
                    else:
                        # Recursively extract if greenfield contains nested structure
                        values.extend(extract_greenfield_values(v))
                elif k != "brownfield":
                    # Continue searching in nested structures, but skip brownfield
                    values.extend(extract_greenfield_values(v, k))
        elif isinstance(d, (float, int)) and parent_key != "brownfield":
            # If it's a direct number (not in a greenfield/brownfield dict), include it
            # This handles cases where capex is specified directly without field type
            values.append(d)
        return values

    all_values = extract_greenfield_values(capex_dict)
    if not all_values:
        return None
    return sum(all_values) / len(all_values)


def add_cost_of_infrastructure(ds: xr.Dataset, environment: "Environment", geo_paths: "GeoDataPaths") -> xr.Dataset:
    """
    Add railway infrastructure buildout costs to the dataset, based on distance to existing rail lines
    and country-specific construction costs per kilometer.

    Steps:
        1. Load pre-calculated rail distance data
        2. Map country-specific rail construction costs per km to each grid cell
        3. Calculate total rail cost = distance × cost per km

    Args:
        ds: Dataset to add infrastructure costs to (must contain 'iso3' and 'feasibility_mask' variables)
        environment: Environment containing railway cost data by country
        geo_paths: Paths to geospatial data files (rail distance NetCDF, static layers directory)

    Returns:
        Dataset with added 'rail_cost' variable containing railway buildout costs (USD)

    Side Effects:
        - Generates and saves plots of rail distance and rail cost
        - Saves rail cost to NetCDF file in static_layers_dir for reuse

    Note:
        Distance to existing rail is calculated as a straight line from any grid cell to the nearest rail line.
    """
    logger = logging.getLogger(f"{__name__}.add_cost_of_infrastructure")
    # Ensure required paths are provided
    if not geo_paths or not geo_paths.rail_distance_nc_path:
        raise ValueError(
            "rail_distance_nc_path is required for rail cost calculations. "
            "Ensure geo_paths is provided and contains rail_distance_nc_path."
        )
    if not geo_paths or not geo_paths.static_layers_dir:
        raise ValueError(
            "The static_layers_dir is required to save the rail cost. "
            "Ensure geo_paths is provided and contains static_layers_dir."
        )

    # Check if the output file already exists
    rail_cost_path = geo_paths.static_layers_dir / "rail_cost.nc"
    if rail_cost_path.exists():
        logger.info(f"[GEO LAYERS] Rail cost already exists at {rail_cost_path}. Loading from file.")
        ds["rail_cost"] = xr.open_dataset(rail_cost_path)["rail_cost"]
    else:
        logger.info("[GEO LAYERS] Adding cost of building new infrastructure (rail only).")

        # Rail distance
        rail_dist = xr.open_dataarray(geo_paths.rail_distance_nc_path)
        rail_dist = rail_dist.rename({"x": "lon", "y": "lat"})
        rail_dist_interp = rail_dist.interp(lat=ds.lat, lon=ds.lon, method="nearest")
        ds["rail_distance"] = rail_dist_interp

        plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
        plot_screenshot(
            ds["rail_distance"].where(ds["feasibility_mask"] > 0),
            title="Rail Distance (km)",
            var_type="sequential",
            max_val=1500,
            save_name="rail_distance",
            plot_paths=plot_paths_obj,
        )

        # Railway costs per km
        if environment.railway_costs is None:
            raise ValueError("The railway costs per km are required. Check the environment.")
        railway_costs = environment.railway_costs
        ## Map rail cost per km to each point based on ISO3 codes, setting max as default for missing countries
        railway_costs_dict = {cost.iso3: cost for cost in railway_costs}
        max_cost = max(cost.cost_per_km for cost in railway_costs)

        def get_rail_cost_per_km(iso3: Any) -> float:
            """
            Get railway construction cost per km for a given ISO3 country code.

            Args:
                iso3: ISO3 country code

            Returns:
                Railway cost per kilometer (USD/km)

            Note:
                For countries without specific cost data, the maximum cost across all countries is used.
            """
            cost = railway_costs_dict.get(iso3)
            if cost:
                return cost.get_cost_in_usd_per_km()  # Already converts from Mio USD to USD
            else:
                return max_cost * MioUSD_TO_USD  # Convert from Mio USD/km to USD/km

        rail_cost_per_km_array = xr.apply_ufunc(
            get_rail_cost_per_km,
            ds["iso3"],
            vectorize=True,
            dask="parallelized",
            output_dtypes=[float],
        )

        # Total rail cost per location = rail distance x rail cost per km
        rail_distance_array = cast(xr.DataArray, ds["rail_distance"]).astype(float)
        ds["rail_cost"] = rail_distance_array * rail_cost_per_km_array

        plot_screenshot(
            ds["rail_cost"].where(ds["feasibility_mask"] > 0) * USD_TO_MioUSD,  # Convert USD to Mio USD
            title="Rail Buildout Cost (Mio USD)",
            var_type="sequential",
            max_val=6000,
            save_name="rail_cost",
            plot_paths=plot_paths_obj,
        )
        ds["rail_cost"].to_netcdf(rail_cost_path, mode="w", format="NETCDF4")
    return ds


def add_transportation_costs(
    ds: xr.Dataset,
    repository: Repository,
    year: int,
    active_statuses: list[str],
    geo_config: "GeoConfig",
    geo_paths: "GeoDataPaths",
) -> xr.Dataset:
    """
    Add transportation costs for feedstock and demand for both iron and steel production.

    Steps:
        1. Calculate distances to iron ore mines, iron plants, steel plants, and demand centers and interpolate to dataset grid
        2. Calculate transportation costs per ton by multiplying distances by cost rates

    Args:
        ds: Global dataset with feasibility mask and coordinates
        repository: Repository containing plants, suppliers, and demand centers
        year: Simulation year for which to calculate distances
        active_statuses: List of statuses considered as active (e.g., ["operating", "operating pre-retirement"])
        geo_config: Configuration containing transportation cost rates per km per ton
        geo_paths: Paths to geospatial data files for plotting outputs

    Returns:
        Dataset with added transportation cost variables:
            - feedstock_transportation_cost_per_ton_iron: Cost to transport iron ore to iron plant (USD/ton)
            - feedstock_transportation_cost_per_ton_steel: Cost to transport iron to steel plant (USD/ton)
            - demand_transportation_cost_per_ton_iron: Cost to transport iron to steel plants (USD/ton)
            - demand_transportation_cost_per_ton_steel: Cost to transport steel to demand centers (USD/ton)

    Side Effects:
        - Plots distance and transportation cost maps to geo_paths.geo_plots_dir
    """
    logger = logging.getLogger(f"{__name__}.add_transportation_costs")
    logger.info("[GEO LAYERS] Adding transportation costs to demand and feedstock for both iron and steel production.")

    # Calculate distances to ore mines, iron plants, steel plants, and demand centers
    dist_to_ore_mines, dist_to_iron_plants, dist_to_steel_plants, dist_to_demand_centers = (
        calculate_distance_to_demand_and_feedstock(repository, year, active_statuses, geo_paths)
    )
    ds_ = xr.merge(
        [
            ds,
            dist_to_ore_mines.interp(lat=ds.lat, lon=ds.lon, method="nearest").rename("feedstock_distance_iron"),
            dist_to_iron_plants.interp(lat=ds.lat, lon=ds.lon, method="nearest").rename("feedstock_distance_steel"),
            dist_to_steel_plants.interp(lat=ds.lat, lon=ds.lon, method="nearest").rename("demand_distance_iron"),
            dist_to_demand_centers.interp(lat=ds.lat, lon=ds.lon, method="nearest").rename("demand_distance_steel"),
        ]
    )

    # Plot distances
    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    for var in ["feedstock_distance_iron", "feedstock_distance_steel", "demand_distance_iron", "demand_distance_steel"]:
        plot_screenshot(
            ds_[var],
            title=f"{var} (km)",
            var_type="sequential",
            save_name=f"{var}_{str(year)}",
            plot_paths=plot_paths_obj,
        )

    # Calculate transportation costs per ton for each location
    ds["feedstock_transportation_cost_per_ton_iron"] = (
        ds_["feedstock_distance_iron"] * geo_config.transportation_cost_per_km_per_ton["iron_mine_to_plant"]
    )
    ds["feedstock_transportation_cost_per_ton_steel"] = (
        ds_["feedstock_distance_steel"] * geo_config.transportation_cost_per_km_per_ton["iron_to_steel_plant"]
    )
    ds["demand_transportation_cost_per_ton_iron"] = (
        ds_["demand_distance_iron"] * geo_config.transportation_cost_per_km_per_ton["iron_to_steel_plant"]
    )
    ds["demand_transportation_cost_per_ton_steel"] = (
        ds_["demand_distance_steel"] * geo_config.transportation_cost_per_km_per_ton["steel_to_demand"]
    )

    # Plot transportation costs
    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    for var in [
        "feedstock_transportation_cost_per_ton_iron",
        "feedstock_transportation_cost_per_ton_steel",
        "demand_transportation_cost_per_ton_iron",
        "demand_transportation_cost_per_ton_steel",
    ]:
        plot_screenshot(
            ds[var].where(ds["feasibility_mask"] > 0),
            title=f"{var} (USD)",
            var_type="sequential",
            save_name=f"{var}_{str(year)}",
            plot_paths=plot_paths_obj,
        )

    return ds


def add_landtype_factor(ds: xr.Dataset, geo_config: "GeoConfig", geo_paths: "GeoDataPaths") -> xr.Dataset:
    """
    Add land type factor to the dataset as a CAPEX multiplication factor based on land cover and land use
    suitability.

    Steps:
        1. Load precomputed landtype percentages (e.g., 10% cropland, 90% forest). The original dataset has
        a resolution of 300 m and is aggregated to the grid resolution (ca. 50 km). This step is done upfront
        to save computation time.
        2. Create mapping from land cover labels to user-specified factors
        3. Calculate weighted landtype factor for each grid cell
        4. Fill zeros and missing values with 1.0 (neutral, minimum factor)

    Args:
        ds: Global dataset with coordinates
        geo_config: Configuration containing land_cover_factor mapping for each land type
        geo_paths: Paths to geospatial data files (landtype_percentage_path)

    Returns:
        Dataset with added 'landtype_factor' variable (values between 1.0 and 2.0)

    Side Effects:
        - Plots landtype factor map to geo_paths.geo_plots_dir

    Note:
        - Lower factors indicate more suitable land for steel and iron production
            - Bare areas and cropland are generally preferred (lower factors)
            - Trees and shrubs are less suitable for ecological reasons (higher factors)
        - Default factor is 1.0 for land types not specified in geo_config
    """
    logger = logging.getLogger(f"{__name__}.add_landtype_factor")
    logger.info("[GEO LAYERS] Adding land type factor.")

    # Load precomputed landtype percentages
    if not geo_paths or not geo_paths.landtype_percentage_path:
        raise ValueError(
            "landtype_percentage_path is required for landtype factor calculations. "
            "Ensure geo_paths is provided and contains landtype_percentage_path."
        )
    landtype_percentage = xr.open_dataarray(geo_paths.landtype_percentage_path)

    # Create a mapping from string labels to factors
    string_to_factor = {}
    for string_label in LULC_LABELS_TO_NUM:
        if string_label in geo_config.land_cover_factor:
            string_to_factor[string_label] = geo_config.land_cover_factor[string_label]
        else:
            # Default factor if not found
            string_to_factor[string_label] = 1.0

    # Apply the factors
    landtype_factors = np.zeros((len(ds.lat), len(ds.lon)), dtype=np.float32)
    for landtype_str in landtype_percentage.landtype.values:
        landtype_str = str(landtype_str)
        if landtype_str in string_to_factor:
            factor = string_to_factor[landtype_str]
            landtype_factors += landtype_percentage.sel(landtype=landtype_str).values * factor

    # Fill zeros with nans and ensure minimum value is 1
    landtype_factors = cast(
        np.ndarray[tuple[int, int], np.dtype[np.float32]],
        np.where(landtype_factors == 0, np.nan, landtype_factors).astype(np.float32),
    )
    landtype_factors = cast(
        np.ndarray[tuple[int, int], np.dtype[np.float32]],
        np.where(landtype_factors < 1, 1, landtype_factors).astype(np.float32),
    )

    # Add landtype factors to the global grid
    ds["landtype_factor"] = (("lat", "lon"), landtype_factors)
    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    plot_screenshot(
        ds["landtype_factor"],
        title="Landtype Factor",
        var_type="sequential",
        save_name="landtype_factor",
        plot_paths=plot_paths_obj,
    )

    return ds
