import numpy as np
import pandas as pd
import xarray as xr
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from steelo.domain.models import GeoDataPaths
    from steelo.simulation import GeoConfig

from steelo.adapters.repositories.interface import Repository
from steelo.domain.constants import Year, GEO_RESOLUTION
from steelo.domain.models import CountryMappingService, Location, PlotPaths, Volumes
from steelo.utilities.variable_matching import POWER_MIX_TO_COVERAGE_MAP
from steelo.adapters.geospatial.geospatial_toolbox import distance_to_closest_location, generate_grid
from steelo.domain.constants import MWH_TO_KWH, T_TO_MT
import logging


def get_baseload_coverage(included_power_mix: str) -> float:
    """
    Get baseload power coverage percentage based on power mix type.

    Args:
        included_power_mix: Power mix type (e.g., "Grid only", "85% baseload + 15% grid", "95% baseload + 5% grid", "Not included")

    Returns:
        Baseload coverage percentage as a float (0.0 to 1.0)
    """
    if included_power_mix not in POWER_MIX_TO_COVERAGE_MAP:
        raise ValueError(
            f"Invalid value for included_power_mix: {included_power_mix}. "
            "Choose from 'Grid only', '85% baseload + 15% grid', '95% baseload + 5% grid', or 'Not included'."
        )
    return POWER_MIX_TO_COVERAGE_MAP[included_power_mix]


# -----------------------------------------  Hydrogen ------------------------------------------------------
# Note: A country-level version of the functions below  can be found in calculate_costs.py and is used by the
# PlantAgentsModel to follow the same logic to get hydrogen costs from electricity prices. For consistency,
# any changes to the logic below should be reflected in there as well.


def calculate_lcoh_from_power_price(
    ds: xr.Dataset,
    year: int,
    hydrogen_efficiency: dict[Year, float],
    hydrogen_capex_opex: dict[str, dict[Year, float]],
) -> xr.Dataset:
    """
    Calculate the LCOH (Levelized Cost of Hydrogen) from the power price for the given year.

    Formula: LCOH (USD/kg) = electrolyser energy consumption (MWh/kg) * power price (USD/kWh) + CAPEX and OPEX
    components for each country and year (USD/kg)

    Args:
        ds: Dataset with power prices (baseload and grid combined)
        year: Simulation year
        hydrogen_efficiency: Dictionary mapping years to efficiency values (MWh/kg)
        hydrogen_capex_opex: Dictionary mapping country codes to year->value dictionaries for CAPEX and OPEX (USD/kg)

    Returns:
        Dataset with added 'lcoh' variable containing levelized cost of hydrogen for each grid point
    """
    logger = logging.getLogger(f"{__name__}.calculate_lcoh_from_power_price")
    logger.info("[GEO LAYERS] Calculating LCOH from power price.")

    # Energy consumption of the electrolyser
    energy_consumption = hydrogen_efficiency.get(Year(year))
    if energy_consumption is None:
        raise ValueError(f"Hydrogen efficiency not found for year {year}. Check input data.")
    energy_consumption_kwh = energy_consumption * MWH_TO_KWH  # Convert MWh to kWh

    # LCOH CAPEX and O&M component
    country_lcoh = {}
    for country, values in hydrogen_capex_opex.items():
        capex_opex_value = values.get(Year(year))
        if capex_opex_value is None:
            raise ValueError(f"Missing hydrogen CAPEX/OPEX value for country '{country}' and year {year}.")
        country_lcoh[country] = capex_opex_value

    # Mask where power price is NaN - ignore these points for speed
    valid_mask = ~pd.isna(ds["power_price"].values)
    lat_vals = ds["lat"].values
    lon_vals = ds["lon"].values
    lcoh_result = np.full(ds["power_price"].shape, np.nan)
    if np.any(valid_mask):
        valid_indices = np.where(valid_mask)
        valid_lats = lat_vals[valid_indices[0]]
        valid_lons = lon_vals[valid_indices[1]]

        # Vectorized calculation of LCOH
        ds["lcoh"] = xr.full_like(ds["power_price"], fill_value=float("nan"))
        power_price_vals = (
            ds["power_price"]
            .sel(lat=xr.DataArray(valid_lats, dims="z"), lon=xr.DataArray(valid_lons, dims="z"), method="nearest")
            .values
        )
        iso3_vals = (
            ds["iso3"]
            .sel(lat=xr.DataArray(valid_lats, dims="z"), lon=xr.DataArray(valid_lons, dims="z"), method="nearest")
            .values
        )
        result = np.full_like(power_price_vals, np.nan, dtype=float)
        for idx, (power_price_pixel, iso3_pixel) in enumerate(zip(power_price_vals, iso3_vals)):
            if iso3_pixel == "nan" or pd.isna(power_price_pixel) or pd.isna(iso3_pixel):
                continue
            lcoh_capex_opex_component_pixel = country_lcoh.get(iso3_pixel)
            if lcoh_capex_opex_component_pixel is None or pd.isna(lcoh_capex_opex_component_pixel):
                continue
            result[idx] = energy_consumption_kwh * power_price_pixel + lcoh_capex_opex_component_pixel
        lcoh_result[valid_indices] = result
        ds["lcoh"].values[:] = lcoh_result
    else:
        raise ValueError("No valid input points found for LCOH calculation. Revise power price layer.")
    return ds


def calculate_regional_hydrogen_ceiling(
    ds: xr.Dataset, country_mappings: CountryMappingService, hydrogen_ceiling_percentile: float
) -> dict[str, float]:
    """
    Calculate the hydrogen ceiling for each interconnected region as the Xth percentile of LCOH values.

    Args:
        ds: Dataset with LCOH values
        country_mappings: CountryMappingService with region mappings
        hydrogen_ceiling_percentile: Percentile to use for hydrogen ceiling calculation (0-100)

    Returns:
        Dictionary mapping region names to their hydrogen ceiling prices (USD/kg)

    Note:
        If no LCOH data is available for a region, the ceiling is set to the global maximum LCOH (equivalent to no ceiling).
    """
    logger = logging.getLogger(f"{__name__}.calculate_regional_hydrogen_ceiling")
    logger.info("[GEO LAYERS] Calculating regional hydrogen ceiling based on LCOH values.")

    # Get connected regions for hydrogen trade and the countries within them
    tiam_ucl_region_to_iso3: dict[str, list[str]] = {}
    for mapping in country_mappings._mappings.values():
        region = mapping.tiam_ucl_region
        if region not in tiam_ucl_region_to_iso3:
            tiam_ucl_region_to_iso3[region] = []
        tiam_ucl_region_to_iso3[region].append(mapping.iso3)
    ds["tiam_ucl_region"] = (("lat", "lon"), np.full(ds["iso3"].shape, np.nan, dtype=object))
    for region, iso3_list in tiam_ucl_region_to_iso3.items():
        mask = np.isin(ds["iso3"].values, iso3_list)
        ds["tiam_ucl_region"].values[mask] = region

    # Calculate hydrogen ceiling
    regional_ceiling_dict = {}
    for region in tiam_ucl_region_to_iso3.keys():
        region_lcoh = ds["lcoh"].where(ds["tiam_ucl_region"] == region, drop=True)
        if region_lcoh.size == 0 or len(region_lcoh.values[~np.isnan(region_lcoh.values)]) == 0:
            if region != "Rest of World":  # Normal that RoW has no data
                logger.warning(
                    f"[GEO LAYERS] No LCOH data available for region {region}. Hydrogen ceiling cannot be calculated. LCOH set to global maximum."
                )
            regional_ceiling_dict[region] = np.nanmax(ds["lcoh"].values)
        else:
            regional_ceiling_dict[region] = np.nanpercentile(region_lcoh.values, hydrogen_ceiling_percentile)
            logger.debug(
                f"[GEO LAYERS] The {hydrogen_ceiling_percentile}th percentile of LCOH in {region} is: {regional_ceiling_dict[region]} USD/kg"
            )

    return regional_ceiling_dict


def apply_hydrogen_price_cap(
    ds: xr.Dataset,
    regional_ceiling: dict[str, float],
    geo_config: "GeoConfig",
) -> xr.Dataset:
    """
    Apply hydrogen price capping to the dataset based on regional ceilings and trade configuration.

    Options:
        a) If intraregional trade is NOT allowed, choose the minimum of the LCOH and the regional ceiling.
        Interregional trade is always allowed (or indirectly ignored via a hydrogen ceiling of the 100th percentile).
        b) If intraregional trade is allowed and there are regions to trade with, set the capped LCOH to the
        minimum of the LCOH, the regional ceiling, and the minimum regional ceiling of ANY of the regions in
        the cluster plus long distance transport costs per kg of hydrogen.

    Args:
        ds: Dataset with LCOH and tiam_ucl_region values
        regional_ceiling: Dictionary mapping regions to their hydrogen ceiling prices
        geo_config: Configuration object containing trade settings and transport costs

    Returns:
        Dataset with added 'capped_lcoh' variable
    """
    ds["capped_lcoh"] = xr.full_like(ds["lcoh"], fill_value=float("nan"))

    for region in regional_ceiling.keys():
        mask = ds["tiam_ucl_region"] == region
        trade_regions = geo_config.intraregional_trade_matrix[region]

        if geo_config.intraregional_trade_allowed and trade_regions is not None:
            best_intraregional_trade_value = (
                min([regional_ceiling[r] for r in trade_regions]) + geo_config.long_dist_pipeline_transport_cost
            )
            ceiling_value = min(best_intraregional_trade_value, regional_ceiling[region])
        else:
            ceiling_value = regional_ceiling[region]

        ds["capped_lcoh"].values[mask] = np.where(
            ds["lcoh"].values[mask] < ceiling_value,
            ds["lcoh"].values[mask],
            ceiling_value,
        )

    return ds


# -------------------------------- Distance to demand and feedstock ----------------------------------------------
def get_weighted_location_dict_from_plants(
    repository: Repository, product_type: str, active_statuses: list[str]
) -> dict[Location, float]:
    """
    Get a dictionary of plant locations and their total capacities for a given product type.

    Args:
        repository: Repository containing plant data
        product_type: Type of product to filter by (e.g., "steel", "iron")
        active_statuses: List of statuses considered as active (e.g., ["operating", "operating pre-retirement"])

    Returns:
        Dictionary mapping Location objects to total capacity (tonnes) for plants producing the specified product type

    Note:
        Capacities are accumulated for plants at the same location.
    """
    weighted_loc_dict: dict[Location, float] = {}
    for plant in repository.plants.list():
        cap_of_plant = 0.0
        for fg in plant.furnace_groups:
            if fg.technology.product == product_type and fg.status in active_statuses:
                cap_of_plant += fg.capacity

        # Skip plants with zero capacity or no location
        if cap_of_plant == 0:
            continue
        if plant.location is None:
            continue

        # Accumulate capacities for plants at the same location
        if plant.location in weighted_loc_dict:
            weighted_loc_dict[plant.location] += cap_of_plant
        else:
            weighted_loc_dict[plant.location] = cap_of_plant

    return weighted_loc_dict


def get_weighted_location_dict_from_demand_centers(repository: Repository, year: int) -> dict[Location, float]:
    """
    Get a dictionary of demand center locations and their demand volumes for the specified year.

    Args:
        repository: Repository containing demand center data
        year: Year for which to retrieve demand values

    Returns:
        Dictionary mapping Location objects to demand volumes (tonnes) for the specified year

    Note:
        Demand is accumulated for centers at the same location.
    """
    logger = logging.getLogger(f"{__name__}.get_weighted_location_dict_from_demand_centers")
    weighted_loc_dict: dict[Location, float] = {}
    demand_centers = repository.demand_centers.list()
    for demand_center in demand_centers:
        # Skip demand centers with no location or no demand for the given year
        if demand_center.center_of_gravity is None:
            logger.warning(f"[GEO LAYERS] Demand center {demand_center.demand_center_id} has no location.")
            continue
        year_obj = Year(year)
        if year_obj not in demand_center.demand_by_year:
            logger.warning(f"[GEO LAYERS] Demand center {demand_center.demand_center_id} has no demand for year {year}")
            continue

        # Accumulate demand for centers at the same location
        if demand_center.center_of_gravity in weighted_loc_dict:
            weighted_loc_dict[demand_center.center_of_gravity] += demand_center.demand_by_year[year_obj]
        else:
            weighted_loc_dict[demand_center.center_of_gravity] = demand_center.demand_by_year[year_obj]

    return weighted_loc_dict


def calculate_distance_to_demand_and_feedstock(
    repository: Repository,
    year: int,
    active_statuses: list[str],
    geo_paths: "GeoDataPaths",
) -> tuple[xr.DataArray, xr.DataArray, xr.DataArray, xr.DataArray]:
    """
    Calculate the distance to demand centers and feedstock sources for iron and steel plants.

    Distances calculated:
        - Iron ore mines to iron plants: Distance to closest mine with at least IRON_ORE_STEEL_RATIO x the steel plant's capacity.
        - Iron plants to steel plants: Distance to closest iron plant with at least the steel plant's capacity.
        - Steel plants to iron plants: Distance to closest steel plant with at least the iron plant's capacity.
        - Demand centers to steel plants: Distance to closest demand center with at least the steel plant's capacity.

    Args:
        repository: Repository containing plants, suppliers, and demand centers data
        year: Year for which to calculate distances
        active_statuses: List of statuses considered as active (e.g., ["operating", "operating pre-retirement"])
        geo_paths: Paths to geospatial data files for plotting outputs

    Returns:
        dist_to_ore_mines: DataArray of distances to iron ore mines (km)
        dist_to_iron_plants: DataArray of distances to iron plants (km)
        dist_to_steel_plants: DataArray of distances to steel plants (km)
        dist_to_demand_centers: DataArray of distances to demand centers (km)

    Note:
        Scrap is not considered as feedstock in this KPI for simplicity.
    """
    logger = logging.getLogger(f"{__name__}.calculate_distance_to_demand_and_feedstock")
    from steelo.utilities.plotting import plot_bubble_map

    # Create spatial grid
    bbox = {"minx": -180, "miny": -90, "maxx": 180, "maxy": 90}
    grid = generate_grid(bbox=bbox, resolution=GEO_RESOLUTION)
    lats = np.array(sorted(set(grid.y)))
    lons = np.array(sorted(set(grid.x)))

    # Iron ore mines
    logger.info("[GEO LAYERS] Calculating distances to iron ore mines.")
    iron_feedstock_locations_weight: dict[Location, float | int | Volumes] = {}
    iron_ore_suppliers = [
        supplier
        for supplier in repository.suppliers.list()
        if supplier.commodity.lower() in ["io_low", "io_mid", "io_high"]
    ]
    if not iron_ore_suppliers:
        logger.warning("[GEO LAYERS] No iron ore suppliers found in the repository.")
    for supplier in iron_ore_suppliers:
        if supplier.location is None:
            continue
        iron_feedstock_locations_weight[supplier.location] = float(supplier.capacity_by_year[Year(year)])
    plot_paths_obj = PlotPaths(geo_plots_dir=geo_paths.geo_plots_dir)
    plot_bubble_map(
        data=iron_feedstock_locations_weight,
        bubble_scaler=T_TO_MT,  # display in Mt
        title="Iron Ore Mines and Their Capacities",
        save_name="iron_ore_mines",
        plot_paths=plot_paths_obj,
    )
    dist_to_ore_mines = distance_to_closest_location(
        iron_feedstock_locations_weight,
        target_lats=lats,
        target_lons=lons,
    )

    # Iron plants
    logger.info("[GEO LAYERS] Calculating distances to iron plants.")
    iron_loc_dict = get_weighted_location_dict_from_plants(
        repository, product_type="iron", active_statuses=active_statuses
    )
    dist_to_iron_plants = distance_to_closest_location(
        iron_loc_dict,
        target_lats=lats,
        target_lons=lons,
    )

    # Steel plants
    logger.info("[GEO LAYERS] Calculating distances to steel plants.")
    steel_loc_dict = get_weighted_location_dict_from_plants(
        repository, product_type="steel", active_statuses=active_statuses
    )
    dist_to_steel_plants = distance_to_closest_location(
        steel_loc_dict,
        target_lats=lats,
        target_lons=lons,
    )

    # Demand centers
    logger.info("[GEO LAYERS] Calculating distances to demand centers.")
    demand_loc_dict = get_weighted_location_dict_from_demand_centers(repository, year)
    dist_to_demand_centers = distance_to_closest_location(
        demand_loc_dict,
        target_lats=lats,
        target_lons=lons,
    )

    return dist_to_ore_mines, dist_to_iron_plants, dist_to_steel_plants, dist_to_demand_centers
