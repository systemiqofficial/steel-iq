import numpy as np
import xarray as xr
import logging
import time
import argparse
import sys
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from boa.config.paths import PathConfig
from boa.config.settings import OVERSCALE_SAMPLING_MEANS, RANDOM_SEED
from boa.geo.iso3_finder import (
    derive_subregion,
    iso3_at,
    load_subregion_polygons,
    validate_subregion_coverage,
    validate_subregion_keys,
)
from boa.inputs.costs import process_global_baseload_simulation_costs
from boa.inputs.profiles import open_regional_dataset
from boa.model.diagnostics import plot_time_series, plot_design_distributions, plot_state_of_charge, plot_cost_scatter
from boa.geo.geospatial import build_region_selection, select_region, wrap_lon_to_grid
from boa.model.logic import (
    min_survivors_required,
    optimize_point,
    return_global_average_costs,
    calculate_net_energy_production,
    state_of_charge,
)


def _resolve_min_survivors(n: int, min_survivor_fraction: float | None) -> int:
    """Survivor threshold for `n` samples; `None` uses the model default (MIN_SURVIVOR_FRACTION)."""
    if min_survivor_fraction is None:
        return min_survivors_required(n)
    return min_survivors_required(n, min_survivor_fraction)


@lru_cache(maxsize=4)
def _iso3_to_subregions(iso3_keys: tuple[str, ...]) -> dict[str, list[str]]:
    """Parse a costs dataset's iso3 dim into {iso3: [cost_key, ...]} by splitting on ':'."""
    out: dict[str, list[str]] = {}
    for key in iso3_keys:
        out.setdefault(key.split(":", 1)[0], []).append(key)
    return out


def build_cost_lookup_indices(costs: xr.Dataset) -> tuple[dict[str, list[str]], set[str]]:
    """Pre-compute `(iso3_to_subregions, full_cost_key_set)` for a costs dataset.

    Returned once per query and reused across thousands of per-point ``cost_key_for_point``
    calls, replacing the previous in-loop ``tuple(costs["iso3"].values)`` rebuild + repeated
    membership scans of the full iso3 dim.
    """
    iso3_to_subregions = _iso3_to_subregions(tuple(str(k) for k in costs["iso3"].values))
    full_cost_key_set = set(k for v in iso3_to_subregions.values() for k in v)
    return iso3_to_subregions, full_cost_key_set


def cost_key_for_point(
    lat: float,
    lon: float,
    iso3_to_subregions: dict[str, list[str]],
    full_cost_key_set: set[str],
    country_code: str | None = None,
    iso3_grid_path: Path | None = None,
) -> str:
    """Resolve a (lat, lon) to its cost-dataset key — string only, no ``.sel()`` calls.

    Hot-path companion to ``extract_costs_for_point``: callers that need the cost VALUES
    (capex/opex/coc) typically batch them via a precomputed ``{cost_key: (capex, opex, coc)}``
    lookup table built once per query instead of per-point. The two-step pattern avoids the
    ~6 xarray ``.sel`` calls per point that dominated the gridded query phase.

    Returns the resolved cost_key string. The special markers ``"UNKNOWN:GLOBAL_AVG"`` and
    ``"{iso3}:GLOBAL_AVG"`` signal that the caller should use the global-average fallback.
    """
    if country_code is None and iso3_grid_path is not None:
        country_code = iso3_at(lat, lon, iso3_grid_path)
    if country_code is None:
        logging.warning(f"[FALLBACK] Unknown country code for ({lat}, {lon}); using global-average costs.")
        return "UNKNOWN:GLOBAL_AVG"

    # Iso3s that don't survive the cost-dataset build need a manual neighbour:
    # ALA has no Country mapping row; SGS has no irena_region so it's dropped
    # during the CAPEX merge.
    if country_code == "ALA":
        country_code = "FIN"
    elif country_code == "SGS":
        country_code = "ARG"

    # Resolve country_code to the cost-key used in the dataset. Most iso3s map to themselves;
    # multi-subregion iso3s (e.g. CHN) need per-point shapefile lookup; whole-iso3 subregions
    # (e.g. TWN) have a single declared cost-key known at load time.
    subregions = iso3_to_subregions.get(country_code, [])
    if len(subregions) > 1:
        cost_key = derive_subregion(lat, lon, country_code) or country_code
    elif len(subregions) == 1:
        cost_key = subregions[0]
    else:
        cost_key = country_code

    if cost_key not in full_cost_key_set:
        # Sub-national key with no cost row → fall back to the national iso3 (e.g. an
        # un-authored CHN province uses the CHN row) before the global-average marker.
        if ":" in cost_key and country_code in full_cost_key_set:
            logging.info(f"[FALLBACK] Subregion cost key {cost_key} not in costs data; using national {country_code}.")
            return country_code
        logging.warning(
            f"[FALLBACK] Cost key {cost_key} (from {country_code}) not found in costs data; using global-average costs."
        )
        return f"{country_code}:GLOBAL_AVG"
    return cost_key


def extract_costs_for_point(
    lat: float,
    lon: float,
    costs: xr.Dataset,
    country_code: str | None = None,
    iso3_grid_path: Path | None = None,
) -> tuple[dict, dict, float, str]:
    """
    Extracts the costs for a given latitude and longitude from the cost dataset. If the country
    code is not found in the dataset, returns global-average values with a `{iso3}:GLOBAL_AVG`
    cost_key marker (or `UNKNOWN:GLOBAL_AVG` when iso3 resolution fails outright).

    Convenience wrapper around ``cost_key_for_point`` + ``costs_for_key`` for callers that
    only do one or a few lookups (single-point CLI, API). Gridded hot paths should call those
    two helpers directly with precomputed indices/lookup tables to skip the per-call setup.

    Returns:
        (capex_per_tech, opex_pct_per_tech, cost_of_capital, cost_key)
    """
    iso3_to_subregions, full_cost_key_set = build_cost_lookup_indices(costs)
    cost_key = cost_key_for_point(
        lat,
        lon,
        iso3_to_subregions,
        full_cost_key_set,
        country_code=country_code,
        iso3_grid_path=iso3_grid_path,
    )
    if cost_key.endswith(":GLOBAL_AVG"):
        capex, opex, coc = return_global_average_costs(costs)
        return capex, opex, coc, cost_key
    capex = {tech: costs["Capex " + tech].sel(iso3=cost_key).values for tech in ["solar", "wind", "battery"]}
    opex_pct = {tech: costs["Opex " + tech].sel(iso3=cost_key).values for tech in ["solar", "wind", "battery"]}
    cost_of_capital = float(costs["Cost of capital"].sel(iso3=cost_key).values)
    return capex, opex_pct, cost_of_capital, cost_key


def costs_for_key(cost_key: str, costs: xr.Dataset) -> tuple[dict, dict, float]:
    """
    Query-time cost lookup for a precomputed cost_key (no geocoder needed).

    Counterpart to `extract_costs_for_point` for the design-cache flow: build-time
    geocoding bakes `cost_key` into the cache; at query time we only need the
    year-specific .sel lookup. Returns (capex_per_tech, opex_pct_per_tech, coc),
    falling back to global averages for `*:GLOBAL_AVG` markers or keys missing
    from a (possibly-shifted) costs dataset.
    """
    if not cost_key or cost_key.endswith(":GLOBAL_AVG"):
        return return_global_average_costs(costs)
    cost_keys = set(str(k) for k in costs["iso3"].values)
    if cost_key not in cost_keys:
        # Sub-national key absent (e.g. a cached province key not in a shifted costs dataset)
        # → fall back to the national iso3 before the global average.
        national = cost_key.split(":", 1)[0] if ":" in cost_key else None
        if national and national in cost_keys:
            logging.info(f"[FALLBACK] cached cost_key {cost_key} not in costs dataset; using national {national}.")
            cost_key = national
        else:
            logging.warning(f"[FALLBACK] cached cost_key {cost_key} not in costs dataset; using global average.")
            return return_global_average_costs(costs)
    capex = {tech: costs["Capex " + tech].sel(iso3=cost_key).values for tech in ["solar", "wind", "battery"]}
    opex_pct = {tech: costs["Opex " + tech].sel(iso3=cost_key).values for tech in ["solar", "wind", "battery"]}
    cost_of_capital = float(costs["Cost of capital"].sel(iso3=cost_key).values)
    return capex, opex_pct, cost_of_capital


def run_baseload_optimization_for_point(
    lat: float,
    lon: float,
    profile: xr.Dataset,
    max_cap_full: xr.Dataset,
    baseload_demand: float,
    costs: xr.Dataset,
    investment_horizon: int,
    p: int,
    n: int,
    min_survivor_fraction: float | None = None,
) -> dict:
    """
    [REFERENCE IMPLEMENTATION — no current callers in production]

    Per-point baseload optimization, written as a readable spec for what one grid point goes
    through. The production paths that replaced this:
      - GLOBAL / regional runs: `build_design_cache_for_region` / `query_design_cache_for_region`
        in `src/boa/model/global_extension.py` split the work into year-independent precompute (cached to
        disk) + year-dependent LCOE (per-tile, thread-parallel).
      - Single-point API path:  `execute_single_point_baseload_power_simulation` below,
        which wraps the same per-point steps with plotting + progress callbacks.

    Preserved as the algorithmic reference for the per-point flow.

    Run the baseload optimization for a given grid point (lat, lon) using the provided renewable energy profile and cost data.
    The function returns a dict with the optimal design (solar, wind, and battery overscale factors), the LCOE, and the installation cost.

    Calculation steps:
        1. Get the renewable energy profile for the current grid point.
        2. Calculate the physical capacity limits for solar and wind at the grid point. Land use/cover is not considered here, so
        capacity limits are based on physical constraints only (like the area of the grid point at that latitude and the minimum
        spacing between installations).
        3. Sample feasible designs (solar, wind, and battery overscale factors) given the renewable energy profile.
        4. Filter the feasible designs according to their hourly coverage, which must be above a certain percentile (p) -> accepted designs.
        5. Calculate the installation cost and LCOE for each accepted design.
        6. Choose the optimal design (the accepted design with the lowest LCOE), provided at least
        `min_survivor_fraction * n` designs were accepted.

    The returned dict carries a `status` code (see boa.config.constants.STATUS_CODES) recording why an
    all-zero result came back: no resource (3), no design met coverage (2), or too few survivors
    for the optimum to be trusted (4).
    """
    # Get the profile for the current grid point; skipping grid points with zero potential.
    # Wrap the query longitude into the cutout's grid so a trans-antimeridian point snaps
    # to the near edge, not the far edge across the -180/180 seam.
    prof_x = profile.x.values
    profile_lon = wrap_lon_to_grid(lon, float(prof_x.min()), float(prof_x.max()))
    profile_grid_point_ds = profile.sel(x=profile_lon, y=lat, method="nearest")
    if np.sum(profile_grid_point_ds.solar.values) == 0 and np.sum(profile_grid_point_ds.wind.values) == 0:
        logging.debug(f"Skipping grid point {lat}, {lon} due to zero potential.")
        return {
            "design": {"solar": 0, "wind": 0, "battery": 0},
            "lcoe": 0,
            "lcoe_coverage_based": 0,
            "installation_cost": 0,
            "installation_cost_breakdown": {"solar": 0, "wind": 0, "battery": 0},
            "coverage": 0,
            "served_fraction": 0,
            "cost_of_capital": 0,
            "cost_key": "",
            "status": 3,
        }
    profile_grid_point = {}
    for tech in ["solar", "wind"]:
        profile_grid_point[tech] = profile_grid_point_ds[tech].values.flatten()

    # Capacity limit (physical constraints)
    # Assumption: The renewable installation built to power a steel plant fits within a single grid point (0.25 deg ~ 15-30 km)
    # Follow the profile's snapped cell so capacity and profile describe the same grid point.
    max_cap = max_cap_full.sel(
        x=float(profile_grid_point_ds.x.values), y=float(profile_grid_point_ds.y.values), method="nearest"
    )
    overbuild_limit = {tech: float(max_cap[tech].values) / baseload_demand for tech in ["pv", "wind"]}
    overbuild_limit = {key.replace("pv", "solar"): value for key, value in overbuild_limit.items()}
    logging.debug(f"Physical capacity limit for grid point {lat}, {lon}: {overbuild_limit}")

    # Sample, filter by coverage, and pick the minimum-LCOE design in one vectorised pass.
    capex, opex_pct, cost_of_capital, cost_key = extract_costs_for_point(lat, lon, costs)
    min_survivors = _resolve_min_survivors(n, min_survivor_fraction)
    optimum, intermediates = optimize_point(
        profile_grid_point,
        p,
        baseload_demand,
        capex,
        opex_pct,
        cost_of_capital,
        investment_horizon,
        n,
        limit=overbuild_limit,
        seed=RANDOM_SEED,
        mus=OVERSCALE_SAMPLING_MEANS,
        return_intermediates=True,
        min_survivors=min_survivors,
    )

    # Too few designs met the coverage threshold for the argmin to be trusted
    if optimum is None:
        n_accepted = int(intermediates["accepted_mask"].sum())
        logging.debug(
            f"No usable optimum for grid point {lat}, {lon}: {n_accepted} of {n} designs met "
            f"the coverage threshold (minimum {min_survivors})."
        )
        return {
            "design": {"solar": 0, "wind": 0, "battery": 0},
            "lcoe": 0,
            "lcoe_coverage_based": 0,
            "installation_cost": 0,
            "installation_cost_breakdown": {"solar": 0, "wind": 0, "battery": 0},
            "coverage": 0,
            "served_fraction": 0,
            "cost_of_capital": 0,
            "cost_key": cost_key,
            "status": 4 if n_accepted else 2,
        }

    optimum["cost_of_capital"] = cost_of_capital
    optimum["cost_key"] = cost_key
    optimum["status"] = 1
    return optimum


def execute_single_point_baseload_power_simulation(
    path_config: PathConfig,
    year: int,
    lat: float,
    lon: float,
    baseload_demand: float,
    p: int,
    n: int,
    min_survivor_fraction: float | None = None,
    generate_plots: bool = True,
    include_plot_data: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Execute the baseload power simulation for a single point (lat, lon).

    The full single-point workflow:
    1. Initialize geocoder
    2. Derive region from lat/lon via coordinate box selection (country is derived separately, for costs only)
    3. Process cost data
    4. Load renewable energy profiles for the derived region
    5. Load maximum capacity data for the region
    6. Run optimization for the specific point
    7. Generate and save plots
    8. Return optimal design, LCOE, and installation cost

    Args:
        path_config: Configuration object containing all necessary paths
        year: Investment year for the simulation
        lat: Latitude of the point
        lon: Longitude of the point
        baseload_demand: Baseload demand in MW
        p: Percentile of time where we don't cover the demand
        n: Number of random samples to generate
        min_survivor_fraction: Minimum share of the n samples that must clear the coverage
            filter before an optimum is returned; None uses the model default
            (boa.config.settings.MIN_SURVIVOR_FRACTION)
        progress_callback: Optional callable(percent: int, message: str) invoked
            at each stage so callers (e.g. the Celery task driving an API job)
            can report fine-grained progress to a UI.

    Returns:
        dict: optimal_sol with keys:
            - 'design': Dict with keys 'solar', 'wind', 'battery' (overscale factors)
            - 'lcoe': Optimal LCOE in $/MWh
            - 'installation_cost': Optimal installation cost in $
            - 'status': Feasibility status code (see boa.config.constants.STATUS_CODES)
    """
    start = time.time()

    def _progress(pct: int, msg: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(pct, msg)
            except Exception:
                logging.exception("progress_callback raised; continuing")

    # Create output directory for plots (if needed)
    if generate_plots:
        lat_str = f"{lat:.2f}".replace(".", "_").replace("-", "neg")
        lon_str = f"{lon:.2f}".replace(".", "_").replace("-", "neg")
        location_prefix = f"lat_{lat_str}_lon_{lon_str}"
        filename_prefix = f"{year}_{location_prefix}"
        output_dir = path_config.plots_dir / "single_point" / f"p{p}" / location_prefix
        output_dir.mkdir(parents=True, exist_ok=True)
        logging.info(f"Saving plots to {output_dir}")

    _progress(15, "Locating country and region...")

    # Derive country code from lat/lon via the single-source-of-truth grid.
    logging.info(f"Deriving country and region from coordinates ({lat}, {lon})...")
    country_code = iso3_at(lat, lon, path_config.iso3_grid_path)
    if country_code is None:
        raise ValueError(
            f"({lat}, {lon}) is not on a mapped land mass at 0.25 deg "
            "(open ocean, or inside one of the documented accepted iso3 gaps)."
        )

    # Choose the weather region from the coordinate itself (REGION_COORDS geometry),
    # independent of country. Snapped-cell metadata + any displacement warning are
    # added after the profile .sel below, once the grid cell is known.
    choice = select_region(lon, lat)
    region = choice.region
    logging.info(f"Location {country_code} -> region {region} (inside_box={choice.inside})")

    # Preprocess costs globally
    _progress(25, "Processing cost data...")
    logging.info("Processing cost data...")
    projected_cost_per_country, investment_horizon = process_global_baseload_simulation_costs(
        investment_year=year,
        input_data_path=path_config.input_data_path,
        cost_cache_dir=path_config.cost_cache_dir,
    )
    load_subregion_polygons(path_config.admin1_10m_shapefile_path)
    validate_subregion_coverage(projected_cost_per_country)
    validate_subregion_keys(projected_cost_per_country, path_config.admin1_10m_shapefile_path)

    # Load Atlite profile for the region. The backend (local NetCDF vs S3 Zarr)
    # is selected by PROFILE_DATA_SOURCE; the helper normalizes the variable
    # name to "solar" either way.
    _progress(40, f"Loading renewable energy profiles for region {region}...")
    logging.info(f"Loading renewable energy profiles for region {region}...")
    profile_full_region = open_regional_dataset("profile", region, path_config)
    # Wrap the query longitude into the cutout's grid so a trans-antimeridian point
    # (e.g. Samoa) snaps to the near edge, not the far edge across the -180/180 seam.
    prof_x = profile_full_region.x.values
    profile_lon = wrap_lon_to_grid(lon, float(prof_x.min()), float(prof_x.max()))
    profile_ds = profile_full_region.sel(x=profile_lon, y=lat, method="nearest")

    # Surface where the profile came from: the snapped grid cell, its box, and (inside
    # build_region_selection) a warning if the point is outside its box or snapped more
    # than one ERA5 cell away.
    region_selection = build_region_selection(lon, lat, float(profile_ds.x.values), float(profile_ds.y.values), choice)

    # Extract profile as dict for processing
    profile = {
        "solar": profile_ds["solar"].values.flatten(),
        "wind": profile_ds["wind"].values.flatten(),
    }

    # Plot 1: Time series of solar and wind profiles
    if generate_plots:
        logging.info("Generating time series plots...")
        plot_time_series(profile, output_path=output_dir / f"{filename_prefix}_time_series.png")

    # Load maximum capacity data
    _progress(50, "Loading capacity limits...")
    logging.info(f"Loading maximum capacity data for region {region}...")
    max_cap_full_region = open_regional_dataset("max_cap", region, path_config)
    # Follow the profile's already-wrapped, reported snap so capacity and profile
    # describe the same grid cell (the two regional cutouts share one grid).
    max_cap = max_cap_full_region.sel(x=float(profile_ds.x.values), y=float(profile_ds.y.values), method="nearest")
    overbuild_limit = {tech: float(max_cap[tech].values) / baseload_demand for tech in ["pv", "wind"]}
    overbuild_limit = {key.replace("pv", "solar"): value for key, value in overbuild_limit.items()}
    logging.info(f"Physical capacity limits: {overbuild_limit}")

    # Costs for this point (geocoded; falls back to global-average if iso3 not found).
    capex, opex_pct, cost_of_capital, cost_key = extract_costs_for_point(
        lat, lon, projected_cost_per_country, country_code
    )

    # Sample + filter + price in one vectorised pass (matches the GLOBAL path).
    # `return_intermediates=True` exposes the full design space so we can plot it and / or
    # build the API's plot-data payload without re-running the optimisation.
    _progress(60, f"Sampling {n:,} feasible designs + filtering by coverage...")
    logging.info(f"Sampling {n} feasible designs and computing optimum...")
    min_survivors = _resolve_min_survivors(n, min_survivor_fraction)
    optimum, intermediates = optimize_point(
        profile,
        p,
        baseload_demand,
        capex,
        opex_pct,
        cost_of_capital,
        investment_horizon,
        n,
        limit=overbuild_limit,
        seed=RANDOM_SEED,
        mus=OVERSCALE_SAMPLING_MEANS,
        return_intermediates=True,
        min_survivors=min_survivors,
    )

    feasible = intermediates["feasible_designs"]  # {"solar": (n,), "wind": (n,), "battery": (n,)}
    accepted_mask = intermediates["accepted_mask"]  # (n,)
    all_lcoes = intermediates["lcoes"]  # (n,)
    all_install_costs = intermediates["installation_costs"]  # (n,)

    # Plot 2: Feasible design distributions
    if generate_plots:
        logging.info("Plotting feasible design distributions...")
        feasible_list = [
            {
                "solar": float(feasible["solar"][i]),
                "wind": float(feasible["wind"][i]),
                "battery": float(feasible["battery"][i]),
            }
            for i in range(n)
        ]
        plot_design_distributions(feasible_list, output_path=output_dir / f"{filename_prefix}_feasible_designs.png")

    if optimum is None:
        n_accepted = int(accepted_mask.sum())
        logging.warning(
            f"No usable optimum for ({lat}, {lon}): {n_accepted} of {n} designs met the "
            f"coverage threshold (minimum {min_survivors})."
        )
        return {
            "design": {"solar": 0, "wind": 0, "battery": 0},
            "lcoe": 0,
            "lcoe_coverage_based": 0,
            "installation_cost": 0,
            "installation_cost_breakdown": {"solar": 0, "wind": 0, "battery": 0},
            "coverage": 0,
            "served_fraction": 0,
            "cost_of_capital": 0,
            "cost_key": cost_key,
            "status": 4 if n_accepted else 2,
            "region_selection": region_selection,
        }

    # Materialise the accepted design list once — both plot calls below need it (cheap at n~2000,
    # ~1 ms), and computing it here makes the type-narrowed value available everywhere.
    accepted_idx = np.where(accepted_mask)[0]
    accepted_list = [
        {
            "solar": float(feasible["solar"][i]),
            "wind": float(feasible["wind"][i]),
            "battery": float(feasible["battery"][i]),
        }
        for i in accepted_idx
    ]

    # Plot 3: Accepted design distributions
    if generate_plots:
        logging.info("Plotting accepted design distributions...")
        plot_design_distributions(accepted_list, output_path=output_dir / f"{filename_prefix}_accepted_designs.png")

    _progress(92, "Computing optimal design...")
    optimal_design = optimum["design"]
    optimal_lcoe = optimum["lcoe"]
    optimal_cost = optimum["installation_cost"]

    # Calculate state of charge and net energy for the OPTIMUM design (used for the SoC plot
    # and plot_data time series).
    opt_net_nrg = None
    opt_soc = None
    if generate_plots or include_plot_data:
        if generate_plots:
            logging.info("Generating state of charge plot...")
        opt_net_nrg = calculate_net_energy_production(
            optimal_design["solar"], profile["solar"], optimal_design["wind"], profile["wind"]
        )
        opt_soc = state_of_charge(opt_net_nrg, optimal_design["battery"])

        if generate_plots:
            plot_state_of_charge(opt_soc, output_path=output_dir / f"{filename_prefix}_state_of_charge.png")

    # Plot 5: Cost scatter plots
    if generate_plots:
        logging.info("Generating cost scatter plots...")
        plot_cost_scatter(
            all_lcoes[accepted_mask],
            accepted_list,
            optimal_design,
            installation_costs=all_install_costs[accepted_mask],
            output_path=output_dir / f"{filename_prefix}_costs.png",
        )

    # Log results
    logging.info(f"Optimization complete for ({lat}, {lon})")
    logging.info(f"  Optimal LCOE: {optimal_lcoe:.2f} $/MWh")
    logging.info(f"  Installation cost: {optimal_cost / 1e6:.2f} M$")
    logging.info(f"  Solar overscale factor: {optimal_design['solar']:.2f}")
    logging.info(f"  Wind overscale factor: {optimal_design['wind']:.2f}")
    logging.info(f"  Battery overscale factor: {optimal_design['battery']:.2f}")
    if generate_plots:
        logging.info(f"All plots saved to {output_dir}")

    end = time.time()
    logging.info(f"Total runtime: {(end - start):.2f} seconds ({(end - start) / 60:.2f} minutes)")

    result = {
        "design": optimal_design,
        "lcoe": optimal_lcoe,
        "lcoe_coverage_based": optimum["lcoe_coverage_based"],
        "installation_cost": optimal_cost,
        "installation_cost_breakdown": optimum["installation_cost_breakdown"],
        "coverage": optimum["coverage"],
        "served_fraction": optimum["served_fraction"],
        "cost_of_capital": cost_of_capital,
        "cost_key": cost_key,
        "status": 1,
        "region_selection": region_selection,
    }

    # Include plot data if requested
    if include_plot_data:
        # Derive battery flows and unmet/curtailed demand from the SoC trajectory.
        # opt_soc and opt_net_nrg are normalised (demand = 1 MW); multiply by
        # baseload_demand to land in absolute MW / MWh
        if opt_soc is not None and opt_net_nrg is not None and len(opt_soc) > 0:
            soc_prev = np.concatenate(([0.0], opt_soc[:-1]))
            delta_soc = opt_soc - soc_prev
            discharge_norm = np.maximum(0.0, -delta_soc)
            curtailed_norm = np.maximum(0.0, opt_net_nrg - delta_soc)
            unmet_norm = np.maximum(0.0, -opt_net_nrg - discharge_norm)
            net_nrg_series = (opt_net_nrg * baseload_demand).tolist()
            soc_series = (opt_soc * baseload_demand).tolist()
            discharge_series = (discharge_norm * baseload_demand).tolist()
            unmet_series = (unmet_norm * baseload_demand).tolist()
            curtailed_series = (curtailed_norm * baseload_demand).tolist()
        else:
            net_nrg_series = soc_series = discharge_series = unmet_series = curtailed_series = []

        # Columnar payload — intermediates are already arrays, so no list-of-dicts conversion needed.
        result["plot_data"] = {
            "time_series": {
                "solar_capacity_factor": profile["solar"].tolist(),
                "wind_capacity_factor": profile["wind"].tolist(),
                "net_energy_production_mw": net_nrg_series,
                "state_of_charge_mwh": soc_series,
                "battery_discharge_mw": discharge_series,
                "unmet_demand_mw": unmet_series,
                "curtailed_mw": curtailed_series,
            },
            "design_space": {
                "feasible_designs": {
                    "solar": feasible["solar"].tolist(),
                    "wind": feasible["wind"].tolist(),
                    "battery": feasible["battery"].tolist(),
                },
                "accepted_designs": {
                    "solar": feasible["solar"][accepted_mask].tolist(),
                    "wind": feasible["wind"][accepted_mask].tolist(),
                    "battery": feasible["battery"][accepted_mask].tolist(),
                },
            },
            "accepted_designs_costs": {
                "lcoe": all_lcoes[accepted_mask].tolist(),
                "installation_cost": all_install_costs[accepted_mask].tolist(),
            },
        }

    return result


def main():
    """
    Main function to run single point baseload optimization from command line.
    """
    parser = argparse.ArgumentParser(
        description="Run baseload optimization for a single geographic point",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--lat", type=float, required=True, help="Latitude of the point")
    parser.add_argument("--lon", type=float, required=True, help="Longitude of the point")
    parser.add_argument("--year", type=int, default=2030, help="Investment year for cost projections")
    parser.add_argument("--demand", type=float, default=100.0, help="Baseload demand in MW")
    parser.add_argument("--p", type=int, default=5, help="Percentile of time where we don't cover the demand")
    parser.add_argument("--n", type=int, default=10000, help="Number of random design samples to generate")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        # Initialize path configuration
        path_config = PathConfig.from_auto_detect()

        # Run the simulation
        logging.info(f"Running baseload optimization for coordinates ({args.lat}, {args.lon})")
        logging.info(f"Parameters: year={args.year}, demand={args.demand} MW, p={args.p}, n={args.n}")

        results = execute_single_point_baseload_power_simulation(
            path_config=path_config,
            year=args.year,
            lat=args.lat,
            lon=args.lon,
            baseload_demand=args.demand,
            p=args.p,
            n=args.n,
        )

        # Print results summary
        print("\n" + "=" * 60)
        print("OPTIMIZATION RESULTS")
        print("=" * 60)
        print(f"Location: ({args.lat}, {args.lon})")
        print(f"Optimal LCOE: {results['lcoe']:.2f} $/MWh")
        print(f"Installation cost: {results['installation_cost'] / 1e6:.2f} M$")
        breakdown = results.get("installation_cost_breakdown", {})
        if breakdown:
            total = sum(breakdown.values()) or 1.0
            print(
                f"  Solar:   {breakdown.get('solar', 0) / 1e6:>10.2f} M$  ({100 * breakdown.get('solar', 0) / total:5.1f}%)"
            )
            print(
                f"  Wind:    {breakdown.get('wind', 0) / 1e6:>10.2f} M$  ({100 * breakdown.get('wind', 0) / total:5.1f}%)"
            )
            print(
                f"  Battery: {breakdown.get('battery', 0) / 1e6:>10.2f} M$  ({100 * breakdown.get('battery', 0) / total:5.1f}%)"
            )
        print(f"Solar overscale factor: {results['design']['solar']:.2f}")
        print(f"Wind overscale factor: {results['design']['wind']:.2f}")
        print(f"Battery overscale factor: {results['design']['battery']:.2f}")
        print("=" * 60 + "\n")

        return results

    except Exception as e:
        logging.error(f"Error during execution: {e}", exc_info=True)
        return None


if __name__ == "__main__":
    results = main()
    # Exit with 0 on success, 1 on error
    if results is None:
        sys.exit(1)
    else:
        sys.exit(0)
