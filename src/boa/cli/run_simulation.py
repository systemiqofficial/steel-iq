#!/usr/bin/env python
"""
Baseload Optimisation Atlas (BOA) Simulation Runner

This script runs the baseload power simulation to find optimal renewable energy
configurations (solar, wind, battery) for steel plants across different regions.

The simulation optimizes the sizing of renewable energy installations to meet
baseload demand while minimizing the Levelized Cost of Electricity (LCOE).
"""

import argparse
import logging
import os
import sys
from typing import List

import xarray as xr

from boa.config.paths import DEFAULT_SET, PathConfig
from boa.config.settings import REGION_COORDS
from boa.model.global_extension import (
    build_design_cache_for_region,
    combine_regional_datasets_into_global_dataset,
    execute_baseload_power_simulation,
    query_design_cache_for_region,
)
from boa.inputs.costs import process_global_baseload_simulation_costs
from boa.config import run_manifest
from boa.inputs.profiles import open_regional_dataset
from boa.model.single_point_run import execute_single_point_baseload_power_simulation
from boa.geo.iso3_finder import (
    load_subregion_polygons,
    validate_subregion_coverage,
    validate_subregion_keys,
)
from boa.conversions import coverage_to_percentile


def parse_workers(value: str) -> int:
    """
    Accept either an explicit integer or a performance preset for --workers.

    Presets (computed from os.cpu_count()):
      - small:  ~25% of cores (laptop-friendly, leaves room for other apps)
      - normal: ~50% of cores (balanced)
      - fast:   cpu_count - 2 (max throughput, leaves 2 cores for OS)
    """
    try:
        n = int(value)
        if n < 1:
            raise argparse.ArgumentTypeError(f"--workers must be >= 1, got {n}")
        return n
    except ValueError:
        pass
    cores = os.cpu_count() or 4
    presets = {
        "small": max(1, cores // 4),
        "normal": max(2, cores // 2),
        "fast": max(2, cores - 2),
    }
    if value in presets:
        return presets[value]
    raise argparse.ArgumentTypeError(f"--workers must be an integer or one of {sorted(presets)}, got {value!r}")


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("distributed").setLevel(logging.WARNING)


def add_data_args(parser: argparse.ArgumentParser) -> None:
    """``--inputs/--costs/--run`` select which provenance slots a command reads and writes."""
    group = parser.add_argument_group("Data Selection")
    group.add_argument(
        "--inputs",
        default=DEFAULT_SET,
        help="Input set under <root>/inputs/ (profile + max-capacity stores, design cache).",
    )
    group.add_argument(
        "--costs", default=DEFAULT_SET, help="Cost set under <root>/costs/ (boa_cost_data.xlsx, cost cache)."
    )
    group.add_argument(
        "--run", default=None, help="Run name under <root>/runs/ for outputs. Default: <inputs>__<costs>."
    )


def resolve_paths(args: argparse.Namespace, command: str, argv: list[str]) -> PathConfig:
    """Build the PathConfig for the selected sets and record this invocation in run.json."""
    path_config = PathConfig.from_auto_detect(input_set=args.inputs, cost_set=args.costs, run=args.run)
    logging.info(f"Data root: {path_config.root}")
    logging.info(f"Inputs: {path_config.input_set}; costs: {path_config.cost_set}; run: {path_config.run}")
    run_manifest.record_invocation(path_config, command, argv)
    return path_config


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Auto-append each argument's default to its help text while preserving the raw,
    pre-formatted description block (examples, line breaks)."""

    pass


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse command-line arguments for the baseload power simulation.

    Returns:
        Parsed command-line arguments with simulation parameters.
    """
    parser = argparse.ArgumentParser(
        description="""
        Baseload Optimisation Atlas (BOA) - Find optimal renewable energy configurations
        for steel plants to meet baseload demand while minimizing LCOE.
        
        Example usage:
            # Run global simulation for 2025-2050 with default parameters
            python boa_run_simulation.py
            
            # Run for specific region with custom demand
            python boa_run_simulation.py --region EU --baseload-demand 1000
            
            # Run single year with high coverage requirement
            python boa_run_simulation.py --start-year 2030 --end-year 2030 --coverage 0.95

            # Query a different cost set against the same CDS inputs
            run_boa query --inputs cds-2024 --costs xlsx-rev3
        """,
        formatter_class=_HelpFormatter,
    )

    # Temporal parameters
    temporal_group = parser.add_argument_group("Temporal Parameters")
    temporal_group.add_argument("--start-year", type=int, default=2025, help="Starting year for simulation")
    temporal_group.add_argument("--end-year", type=int, default=2050, help="Ending year for simulation")
    temporal_group.add_argument("--frequency", type=int, default=5, help="Frequency in years between simulations")

    # Spatial parameters
    ## Required for global and regional runs
    spatial_group = parser.add_argument_group("Spatial Parameters")
    spatial_group.add_argument(
        "--region",
        type=str,
        default="GLOBAL",
        choices=["GLOBAL"] + list(REGION_COORDS.keys()),
        help="Region to simulate. Use GLOBAL to run all regions.",
    )
    ## Required for single-point runs
    spatial_group.add_argument(
        "--lat",
        type=float,
        default=None,
        help="Latitude for specific location simulation.",
    )
    spatial_group.add_argument(
        "--lon",
        type=float,
        default=None,
        help="Longitude for specific location simulation.",
    )

    # Technical parameters
    technical_group = parser.add_argument_group("Technical Parameters")
    technical_group.add_argument(
        "--baseload-demand",
        type=float,
        default=500.0,
        help="Baseload demand in MW. Typical range: 150-1000 MW",
    )
    technical_group.add_argument(
        "--coverage",
        type=float,
        default=0.85,
        help="Required demand coverage fraction (0-1). E.g., 0.85 means RE must cover demand 85%% of the time",
    )
    technical_group.add_argument(
        "--samples",
        type=int,
        default=1000,
        help="Number of random designs to sample. Higher values increase accuracy but also runtime",
    )
    technical_group.add_argument(
        "--workers",
        type=parse_workers,
        default="fast",
        help="Number of threads for parallel grid-point optimization. Integer or preset "
        "(small ~25%% of cores, normal ~50%%, fast cpu_count-2).",
    )

    # Optional parameters
    optional_group = parser.add_argument_group("Optional Parameters")
    optional_group.add_argument("--verbose", action="store_true", help="Enable verbose logging output")
    optional_group.add_argument("--dry-run", action="store_true", help="Print configuration without running simulation")
    optional_group.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip per-region and global map plotting during the run (saves time for long batch runs; plots can be regenerated later from saved NetCDFs)",
    )
    optional_group.add_argument(
        "--force",
        action="store_true",
        help="Re-derive every artifact (design cache, regional/GLOBAL NetCDFs) "
        "even if it already exists on disk. Default is "
        "skip-if-exists: cached artifacts are reused, making interrupted "
        "runs resumable.",
    )

    add_data_args(parser)

    return parser.parse_args(argv)


def validate_arguments(args: argparse.Namespace) -> None:
    """
    Validate command-line arguments for logical consistency.

    Args:
        args: Parsed command-line arguments

    Raises:
        ValueError: If arguments are invalid or inconsistent
    """
    # Validate general arguments (all runs)
    if args.start_year > args.end_year:
        raise ValueError(f"Start year ({args.start_year}) must be <= end year ({args.end_year})")

    if not isinstance(args.frequency, int) or args.frequency <= 0:
        raise ValueError(f"Frequency must be a positive integer, got {args.frequency}")

    if not 0 < args.coverage <= 1:
        raise ValueError(f"Coverage must be between 0 and 1, got {args.coverage}")

    if args.baseload_demand <= 0:
        raise ValueError(f"Baseload demand must be positive, got {args.baseload_demand}")

    if not isinstance(args.samples, int) or args.samples <= 0:
        raise ValueError(f"Number of samples must be a positive integer, got {args.samples}")

    # Validate lat/lon arguments (single run)
    if (args.lat is None) != (args.lon is None):
        raise ValueError("Both --lat and --lon must be provided together, or neither")

    if args.lat is not None:
        if not -90 <= args.lat <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got {args.lat}")

    if args.lon is not None:
        if not -180 <= args.lon <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got {args.lon}")

    # Don't allow both region and lat/lon to be specified explicitly
    if args.lat is not None and args.lon is not None and args.region != "GLOBAL":
        raise ValueError(
            f"Cannot specify both --region ({args.region}) and --lat/--lon ({args.lat}, {args.lon}). "
            f"Please use either --region for regional simulation OR --lat/--lon for single-point simulation "
            f"(region will be auto-derived from coordinates)."
        )


def get_simulation_years(start_year: int, end_year: int, frequency: int) -> List[int]:
    """
    Generate list of simulation years based on frequency.

    Args:
        start_year: First year to simulate
        end_year: Last year to simulate
        frequency: Years between simulations

    Returns:
        List of years to simulate, rounded to multiples of frequency
    """
    years = []
    for year in range(start_year, end_year + 1, frequency):
        # Round to nearest multiple of frequency for consistency with other models
        rounded_year = round(year / frequency) * frequency
        if start_year <= rounded_year <= end_year:
            years.append(rounded_year)
    return years


def main_run(argv: list[str] | None = None):
    """
    Default `run` entry point: full build-if-missing + query for each requested
    year. Same UX as the pre-cache CLI; the design cache speedup is transparent
    to callers.
    """
    # Parse and validate arguments
    args = parse_arguments(argv)

    try:
        validate_arguments(args)
    except ValueError as e:
        logging.error(f"Invalid arguments: {e}")
        return 1

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Calculate coverage percentile (p = percentage of time NOT covered)
    p = coverage_to_percentile(args.coverage)

    # Get simulation years
    years = get_simulation_years(args.start_year, args.end_year, args.frequency)

    # Print configuration
    logging.info("=" * 60)
    logging.info("Baseload Optimisation Atlas (BOA) Configuration")
    logging.info("=" * 60)
    if args.lat is not None and args.lon is not None:
        logging.info("Mode: Single-point simulation")
        logging.info(f"Location: Lat={args.lat}, Lon={args.lon}")
        logging.info("Region: Auto-derived from coordinates")
    else:
        logging.info("Mode: Regional simulation")
        logging.info(f"Region: {args.region}")
    logging.info(f"Years to simulate: {years}")
    logging.info(f"Baseload demand: {args.baseload_demand} MW")
    logging.info(f"Coverage requirement: {args.coverage * 100:.1f}% (p={p})")
    logging.info(f"Number of samples: {args.samples}")
    logging.info(f"Worker threads: {args.workers}")
    is_single_point = args.lat is not None and args.lon is not None
    logging.info(f"Generate plots: {not args.no_plots}")
    logging.info("=" * 60)

    if args.dry_run:
        logging.info("Dry run - exiting without running simulation")
        return 0

    path_config = resolve_paths(args, "run", list(argv or sys.argv[1:]))

    # Run simulations and collect results
    results = {}
    for year in years:
        logging.info(f"\nStarting simulation for year {year}")
        try:
            if is_single_point:
                # Single-location simulation
                logging.info(f"Running single-location simulation at ({args.lat}, {args.lon})")
                optimal_sol = execute_single_point_baseload_power_simulation(
                    path_config=path_config,
                    year=year,
                    lat=args.lat,
                    lon=args.lon,
                    baseload_demand=args.baseload_demand,
                    p=p,
                    n=args.samples,
                )
                results[year] = optimal_sol
            else:
                # Regional simulation (default)
                logging.info(f"Running regional simulation for region: {args.region}")
                optimal_sol = execute_baseload_power_simulation(
                    path_config=path_config,
                    year=year,
                    region=args.region,
                    baseload_demand=args.baseload_demand,
                    p=p,
                    n=args.samples,
                    generate_plots=not args.no_plots,
                    n_workers=args.workers,
                    force=args.force,
                )
                results[year] = optimal_sol
            logging.info(f"Completed simulation for year {year}")
        except Exception as e:
            logging.error(f"Failed to run simulation for year {year}: {e}")
            if args.verbose:
                logging.exception("Detailed error:")
            return None

    logging.info("\nAll simulations completed successfully!")
    return results


def _resolve_regions(region_arg: str) -> List[str]:
    """Expand GLOBAL → all 9 regions; otherwise return [region_arg]."""
    if region_arg == "GLOBAL":
        return list(REGION_COORDS.keys())
    return [region_arg]


def main_build_cache(argv: list[str]) -> int | None:
    """
    `build-cache` subcommand: build the year-independent design cache for a region
    (or all regions if --region GLOBAL). No NetCDF output; only the per-region Zarr
    store is written. Idempotent — existing caches at the hashed path are skipped.
    """
    p = argparse.ArgumentParser(
        prog="boa.cli.run_simulation build-cache",
        description="Build the design cache(s) without producing optimal-solution NetCDFs.",
        formatter_class=_HelpFormatter,
    )
    p.add_argument(
        "--region",
        type=str,
        default="GLOBAL",
        choices=["GLOBAL"] + list(REGION_COORDS.keys()),
        help="Region to build the cache for (GLOBAL = all 9 regions).",
    )
    p.add_argument("--baseload-demand", type=float, default=500.0, help="Baseload demand in MW.")
    p.add_argument("--coverage", type=float, default=0.85, help="Required demand coverage fraction.")
    p.add_argument("--samples", type=int, default=1000, help="Number of Monte Carlo samples per point.")
    p.add_argument("--workers", type=parse_workers, default="fast", help="Number of threads (integer or preset).")
    p.add_argument(
        "--cost-anchor-year",
        type=int,
        default=2025,
        help="Year used only to load a costs dataset for cost_key derivation; "
        "any year works since iso3 dim is year-independent.",
    )
    p.add_argument(
        "--force", action="store_true", help="Rebuild the design cache even if it already exists at the hashed path."
    )
    p.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    add_data_args(p)
    args = p.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    p_pct = coverage_to_percentile(args.coverage)
    regions = _resolve_regions(args.region)

    logging.info("=" * 60)
    logging.info("BOA: build-cache")
    logging.info("=" * 60)
    logging.info(f"Regions: {regions}")
    logging.info(
        f"Baseload: {args.baseload_demand} MW; coverage p={p_pct}; samples={args.samples}; workers={args.workers}"
    )

    path_config = resolve_paths(args, "build-cache", list(argv))
    costs, _ = process_global_baseload_simulation_costs(
        investment_year=args.cost_anchor_year,
        input_data_path=path_config.input_data_path,
        cost_cache_dir=path_config.cost_cache_dir,
    )
    load_subregion_polygons(path_config.admin1_10m_shapefile_path)
    validate_subregion_coverage(costs)
    validate_subregion_keys(costs, path_config.admin1_10m_shapefile_path)

    for region in regions:
        logging.info(f"\nBuilding design cache for {region}")
        profile = open_regional_dataset("profile", region, path_config)
        build_design_cache_for_region(
            region=region,
            baseload_demand=args.baseload_demand,
            p=p_pct,
            n=args.samples,
            profile=profile,
            costs=costs,
            path_config=path_config,
            n_workers=args.workers,
            force=args.force,
        )
    logging.info("\nbuild-cache: all regions complete.")
    return 0


def main_query(argv: list[str]) -> int | None:
    """
    `query` subcommand: re-derive optimal-solution NetCDFs from pre-built design
    caches for one or more years. Requires the cache to exist; will not build.
    """
    p = argparse.ArgumentParser(
        prog="boa.cli.run_simulation query",
        description="Run the LCOE-only query against pre-built design caches.",
        formatter_class=_HelpFormatter,
    )
    p.add_argument(
        "--region",
        type=str,
        default="GLOBAL",
        choices=["GLOBAL"] + list(REGION_COORDS.keys()),
        help="Region to query (GLOBAL = all 9 regions).",
    )
    p.add_argument("--start-year", type=int, default=2025, help="Starting year.")
    p.add_argument("--end-year", type=int, default=2050, help="Ending year.")
    p.add_argument("--frequency", type=int, default=5, help="Years between queries.")
    p.add_argument("--baseload-demand", type=float, default=500.0, help="Baseload demand in MW.")
    p.add_argument("--coverage", type=float, default=0.85, help="Required demand coverage fraction.")
    p.add_argument("--samples", type=int, default=1000, help="Number of Monte Carlo samples per point.")
    p.add_argument("--workers", type=parse_workers, default="fast", help="Number of threads (integer or preset).")
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-derive every artifact (regional NetCDFs, GLOBAL combine) even if it already exists on disk.",
    )
    p.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    add_data_args(p)
    args = p.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    p_pct = coverage_to_percentile(args.coverage)
    regions = _resolve_regions(args.region)
    years = get_simulation_years(args.start_year, args.end_year, args.frequency)

    logging.info("=" * 60)
    logging.info("BOA: query (design-cache → NetCDF)")
    logging.info("=" * 60)
    logging.info(f"Regions: {regions}; years: {years}")
    logging.info(
        f"Baseload: {args.baseload_demand} MW; coverage p={p_pct}; samples={args.samples}; workers={args.workers}"
    )

    path_config = resolve_paths(args, "query", list(argv))
    # Profiles depend only on region; load once per region and reuse across years.
    profiles: dict[str, xr.Dataset] = {
        region: open_regional_dataset("profile", region, path_config) for region in regions
    }
    for year in years:
        costs, horizon = process_global_baseload_simulation_costs(
            investment_year=year,
            input_data_path=path_config.input_data_path,
            cost_cache_dir=path_config.cost_cache_dir,
        )
        load_subregion_polygons(path_config.admin1_10m_shapefile_path)
        validate_subregion_coverage(costs)
        validate_subregion_keys(costs, path_config.admin1_10m_shapefile_path)
        for region in regions:
            logging.info(f"\nQuerying design cache for {region} y{year}")
            query_design_cache_for_region(
                year=year,
                region=region,
                baseload_demand=args.baseload_demand,
                p=p_pct,
                profile=profiles[region],
                costs=costs,
                investment_horizon=horizon,
                n=args.samples,
                path_config=path_config,
                n_workers=args.workers,
                force=args.force,
            )
        # Consolidated GLOBAL NetCDF for the year. No-op (warns) when not all 9
        # regions are in place — i.e. when the user queried a single region.
        combine_regional_datasets_into_global_dataset(
            year,
            p_pct,
            args.baseload_demand,
            path_config,
            force=args.force,
        )
    logging.info("\nquery: all (region, year) pairs complete.")
    return 0


_SUBCOMMANDS = {"build-cache": main_build_cache, "query": main_query}


def main() -> int:
    """
    Top-level dispatcher. `python -m boa.cli.run_simulation [build-cache|query] ...`
    routes to the named subcommand; bare `python -m boa.cli.run_simulation ...` keeps
    the legacy "run" path (build-if-missing + query for the requested year range).

    Returns a process exit code (0 success, 1 failure) so the `run_boa` console
    entry point — which does `sys.exit(main())` — reports correctly. The run
    itself returns its results object; a None result signals failure.
    """
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        result = _SUBCOMMANDS[sys.argv[1]](sys.argv[2:])
    else:
        result = main_run(sys.argv[1:])
    if result is None:
        return 1
    if isinstance(result, int):
        return result
    return 0


if __name__ == "__main__":
    sys.exit(main())
