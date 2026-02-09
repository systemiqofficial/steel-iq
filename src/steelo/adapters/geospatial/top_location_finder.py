import logging
import time
from contextlib import contextmanager
import xarray as xr
from typing import TYPE_CHECKING, Optional, Any

if TYPE_CHECKING:
    from steelo.simulation import GeoConfig
    from steelo.domain.models import GeoDataPaths

from steelo.domain.constants import GEO_RESOLUTION
from steelo.adapters.geospatial.geospatial_layers import (
    add_iso3_codes,
    add_feasibility_mask,
    add_power_price,
    add_capped_hydrogen_price,
    add_capex_proxy_for_steel_and_iron_making_tech,
    add_cost_of_infrastructure,
    add_transportation_costs,
    add_landtype_factor,
)
from steelo.adapters.geospatial.geospatial_calculations import get_baseload_coverage
from steelo.adapters.geospatial.priority_kpi import calculate_priority_location_kpi


@contextmanager
def time_step(step_name: str, logger: Optional[logging.Logger] = None, skip: bool = False):
    """
    Context manager to time a code block and log the duration.

    Args:
        step_name: Name of the step being timed
        logger: Logger to use for output (defaults to steelo.geospatial.timing)
        skip: If True, log as SKIPPED and don't execute the block

    Side Effects:
        Logs timing information to the specified logger
    """
    if logger is None:
        logger = logging.getLogger("steelo.geospatial.timing")

    if skip:
        logger.debug(f"[GEO TIMING] {step_name}: SKIPPED")
        yield
        return

    start = time.time()
    yield
    elapsed = time.time() - start
    logger.debug(f"[GEO TIMING] {step_name}: {elapsed:.3f} seconds")


def get_candidate_locations_for_opening_new_plants(
    uow, env, geo_config: "GeoConfig", geo_paths: "GeoDataPaths"
) -> tuple[dict[str, list[dict[Any, Any]]], xr.Dataset]:
    """
    Execute the geospatial layers pipeline to determine the best places to build new iron and steel plants.

    Combines all input layers (feasibility mask, power price, plant CAPEX, cost of infrastructure, transportation costs,
    land type factor) to calculate a global priority location KPI.

    Layer types:
        - Static: iso3, feasibility mask, cost of infrastructure (assuming negligible changes in time), land type factor.
          The first three layers are saved to temp files in the first simulation year and reused in subsequent years to
          reduce runtime - temp files are cleaned up after simulation. The landtype factor only pre-calculates a part
          which is independent from the user input.
        - Dynamic, PAM-independent: power price (both baseload and grid), hydrogen price, and some transportation costs
          (iron feedstock and steel demand). The hydrogen price is not used for the priority location KPI, but needed
          for the full NPV calculation later on.
        - Dynamic, PAM-dependent: plant CAPEX, some transportation costs (iron demand and steel feedstock)

    Args:
        uow: Unit of work containing repository access to plants, demand centers, and suppliers
        env: Environment containing year, input costs, configuration, and cost curves
        geo_config: Configuration for geospatial calculations (power mix, hydrogen ceiling, transport costs, etc.)
        geo_paths: Paths to geospatial data files (terrain, land cover, shapefiles, etc.)

    Returns:
        top_locations: Dictionary with the top X% locations for steel and iron production, their (lat,lon) coordinates
            and the corresponding railway cost, LCOE, and LCOH (required to calculate the NPV).
        energy_prices: Dataset with the power price and LCOH for the given year for all locations.

    Note:
        Even if the power price and railway cost are not selected for the priority location KPI, they are still
        calculated since they are needed for downstream calculations.
    """
    # Create dedicated logger for geospatial timing
    geo_timer_logger = logging.getLogger("steelo.geospatial.timing")
    start = time.time()
    geo_timer_logger.info(f"[GEO TIMING] ========== Starting Geospatial Layers Pipeline for Year {env.year} ==========")

    # Calculate all GEO layers with timing
    with time_step("get_baseload_coverage", geo_timer_logger):
        baseload_coverage = get_baseload_coverage(geo_config.included_power_mix)

    with time_step("add_iso3_codes", geo_timer_logger):
        # Always added; required as a basis for other layers
        global_ds = add_iso3_codes(resolution=GEO_RESOLUTION, geo_paths=geo_paths)

    with time_step("add_feasibility_mask", geo_timer_logger):
        # Always added; required as a basis for other layers
        global_ds = add_feasibility_mask(global_ds, geo_config, geo_paths=geo_paths)

    with time_step("add_power_price", geo_timer_logger):
        # Always added; required as for the energy costs of new plants. New plants' energy costs usually correspond to the power mix used for
        # location prioritization, with one exception: If the power mix is set to "Not included" for location prioritization, the grid power
        # price is used to set the energy costs of new plants. Reason: A plant cannot operate with zero energy costs.
        global_ds = add_power_price(global_ds, env.year, env.input_costs, baseload_coverage, geo_paths=geo_paths)

    with time_step("add_capped_hydrogen_price", geo_timer_logger):
        # Always added; required as for the energy costs of new plants
        global_ds = add_capped_hydrogen_price(
            global_ds,
            env.year,
            env.hydrogen_efficiency,
            env.hydrogen_capex_opex,
            env.country_mappings,
            baseload_coverage,
            geo_config,
            geo_paths=geo_paths,
        )

    with time_step("add_capex_proxy_for_steel_and_iron_making_tech", geo_timer_logger):
        # Always added; required as a basis for other layers
        capex = add_capex_proxy_for_steel_and_iron_making_tech(env.name_to_capex["greenfield"])
    if capex is None:
        raise ValueError("CAPEX could not be determined in GEO layers.")

    with time_step("add_cost_of_infrastructure", geo_timer_logger):
        # Always added; required as infrastructure costs for new plants
        global_ds = add_cost_of_infrastructure(global_ds, environment=env, geo_paths=geo_paths)

    with time_step("add_transportation_costs", geo_timer_logger, skip=not geo_config.include_transport_cost):
        if geo_config.include_transport_cost:
            global_ds = add_transportation_costs(
                global_ds,
                uow.repository,
                env.year,
                env.config.active_statuses,
                geo_config,
                geo_paths=geo_paths,
            )

    with time_step("add_landtype_factor", geo_timer_logger, skip=not geo_config.include_lulc_cost):
        if geo_config.include_lulc_cost:
            global_ds = add_landtype_factor(global_ds, geo_config, geo_paths=geo_paths)

    # Extract the top locations based on the priority location KPI
    with time_step("calculate_priority_location_kpi", geo_timer_logger):
        top_locations = calculate_priority_location_kpi(
            global_ds,
            capex,
            env.year,
            baseload_coverage,
            env.config.expanded_capacity,
            env.config.plant_lifetime,
            geo_config,
            geo_paths=geo_paths,
        )

    # Extract energy prices for all locations; needed for NPV calculation later on
    # Include iso3 codes for country-level aggregation and overbuild factors if available
    with time_step("extract_energy_prices", geo_timer_logger):
        fields_to_extract = ["iso3", "power_price", "capped_lcoh"]
        # Add overbuild factors if they exist (only present when baseload_coverage > 0)
        if "solar_factor" in global_ds:
            fields_to_extract.extend(["solar_factor", "wind_factor", "battery_factor"])
        energy_prices = global_ds[fields_to_extract]

    # Show time taken
    end = time.time()
    total_time = end - start
    geo_timer_logger.info(
        f"[GEO TIMING] ========== Total Geospatial Layers Pipeline Time: {total_time:.3f} seconds ({total_time / 60:.2f} minutes) =========="
    )
    return top_locations, energy_prices
