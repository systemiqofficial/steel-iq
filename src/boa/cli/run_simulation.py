#!/usr/bin/env python
"""
Baseload Optimisation Atlas (BOA) simulation runner: the `boa-run` console script.

Finds optimal renewable energy configurations (solar, wind, battery) to meet a
fixed baseload demand while minimising LCOE. Runs are always GLOBAL (all 9
regions); the one exception is the single-point mode.

Subcommands:
    boa-run                  full simulation: build frontier caches if missing, then query every year
    boa-run build-cache      year-independent frontier caches only (all regions)
    boa-run query            optimal-solution NetCDFs from pre-built caches (all regions, all years)
    boa-run point            single-point run at --lat/--lon (region auto-derived)

`--promote-lcoe` combines the per-year GLOBAL NetCDFs into the single LCOE file the
steel simulation reads; `boa-promote-lcoe` does the same for a finished run.

The weather year is never passed in: it is read off the profile-store filenames
of the selected input set (`--weather-input`), so the stores are the single
source of truth. Input data is prepared separately — `boa-cds-prepare` for the
profile + max-capacity stores, `boa-data-prepare` for the cost workbook — and a
preflight check points at the right command when something is missing. Both can
also be run inline via `--cds-prepare <year>` / `--data-prepare <xlsx> <scenario>`.

Examples:
    boa-run --demand 1000 --coverage 0.95
    boa-run --demand 1000 --coverage 0.95 --promote-lcoe
    boa-run --weather-input cds-2023 --cost-input xlsx-rev3 --dry-run
    boa-run --cds-prepare 2024 --data-prepare master.xlsx test_scenario
    boa-run build-cache --workers fast
    boa-run query --start-year 2030 --end-year 2030 --force
    boa-run point --lat 52.5 --lon 13.4
"""

import argparse
import logging
import os
import sys
from typing import List

import xarray as xr

from boa.cli import reconfigure_streams_utf8
from boa.config.paths import DEFAULT_SET, PathConfig
from boa.config.settings import REGION_COORDS
from boa.model.anchors import anchor_cost_coefficients
from boa.model.global_extension import (
    build_frontier_cache_for_region,
    combine_regional_datasets_into_global_dataset,
    query_frontier_cache_for_region,
)
from boa.model.lcoe_promotion import promote_lcoe
from boa.model.diagnostics import (
    plot_global_optimum_baseload_power_simulation_map,
    plot_regional_optimum_baseload_power_simulation_map,
)
from boa.inputs.costs import process_global_baseload_simulation_costs
from boa.config import run_manifest
from boa.inputs.profiles import DataKind, dataset_path, detect_weather_year, open_regional_dataset
from boa.model.single_point_run import execute_single_point_baseload_power_simulation
from boa.geo.iso3_finder import (
    load_subregion_polygons,
    validate_subregion_coverage,
    validate_subregion_keys,
)

# Input set assumed when --weather-input is not given (matches boa-cds-prepare's
# automatic `cds-<year>` tagging).
DEFAULT_WEATHER_INPUT = "cds-2024"


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
reconfigure_streams_utf8()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("distributed").setLevel(logging.WARNING)


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Auto-append each argument's default to its help text while preserving the raw,
    pre-formatted description block (examples, line breaks)."""

    def _get_help_string(self, action):
        # Arguments defaulting to None resolve their real default later; "(default: None)" is noise.
        if action.default is None:
            return action.help
        return super()._get_help_string(action)


def add_data_args(parser: argparse.ArgumentParser) -> None:
    """``--weather-input/--cost-input/--run`` select which provenance slots a command reads and writes."""
    group = parser.add_argument_group("Data Selection")
    group.add_argument(
        "--weather-input",
        default=None,
        help="Input set under <root>/inputs/ (profile + max-capacity stores, design cache). "
        f"Default: {DEFAULT_WEATHER_INPUT}, or cds-<year> when --cds-prepare is given.",
    )
    group.add_argument(
        "--cost-input",
        default=None,
        help=f"Cost set under <root>/costs/ (boa_cost_data.xlsx, cost cache). "
        f"Default: {DEFAULT_SET}, or the scenario given to --data-prepare.",
    )
    group.add_argument(
        "--run", default=None, help="Run name under <root>/runs/ for outputs. Default: <weather-input>__<cost-input>."
    )
    group.add_argument(
        "--cds-prepare",
        type=int,
        metavar="YEAR",
        default=None,
        help="Run boa-cds-prepare for YEAR first, building the weather-input set's missing stores.",
    )
    group.add_argument(
        "--data-prepare",
        nargs=2,
        metavar=("XLSX", "SCENARIO"),
        default=None,
        help="Run boa-data-prepare first, extracting the cost workbook XLSX into cost set SCENARIO.",
    )


def resolve_data_sets(args: argparse.Namespace) -> None:
    """Fill in data-set names left unset on the command line, honouring the inline prepare flags."""
    if args.weather_input is None:
        args.weather_input = f"cds-{args.cds_prepare}" if args.cds_prepare is not None else DEFAULT_WEATHER_INPUT
    if args.cost_input is None:
        args.cost_input = args.data_prepare[1] if args.data_prepare is not None else DEFAULT_SET


def run_prepare_flags(args: argparse.Namespace) -> int:
    """Run the inline --cds-prepare / --data-prepare steps ahead of the simulation."""
    if args.cds_prepare is not None:
        from boa.cli.run_cds import main_prepare

        rc = main_prepare(["--weather_year", str(args.cds_prepare), "--inputs", args.weather_input])
        if rc:
            return rc
    if args.data_prepare is not None:
        try:
            from steelo.entrypoints.boa_data_cli import boa_data_prepare
        except ImportError:
            logging.error(
                "--data-prepare needs the steelo package; run "
                f"`boa-data-prepare --input-file {args.data_prepare[0]} --scenario {args.data_prepare[1]}` instead."
            )
            return 1
        boa_data_prepare(["--input-file", args.data_prepare[0], "--scenario", args.data_prepare[1]])
    if args.cds_prepare is not None or args.data_prepare is not None:
        # The prepare CLIs reconfigure logging onto a rich handler; restore the runner's format.
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            force=True,
        )
    return 0


def add_scenario_args(parser: argparse.ArgumentParser) -> None:
    """Scenario parameters; together with the input set they identify the design cache."""
    group = parser.add_argument_group("Scenario Parameters")
    group.add_argument(
        "-d",
        "--demand",
        type=float,
        default=1000.0,
        help="Baseload demand in MW. Typical range: 150-1000 MW",
    )
    group.add_argument(
        "-c",
        "--coverage",
        type=float,
        default=0.85,
        help="Required demand coverage fraction (0-1). E.g., 0.85 means RE must cover demand 85%% of the time",
    )
    group.add_argument(
        "-n",
        "--samples",
        type=int,
        default=1000,
        help="Number of random designs to sample. Higher values increase accuracy but also runtime",
    )


def add_temporal_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Temporal Parameters")
    group.add_argument("-s", "--start-year", type=int, default=2025, help="Starting investment year")
    group.add_argument("-e", "--end-year", type=int, default=2060, help="Ending investment year")
    group.add_argument("-f", "--frequency", type=int, default=1, help="Frequency in years between simulations")


def add_workers_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-w",
        "--workers",
        type=parse_workers,
        default="fast",
        help="Number of threads for parallel grid-point optimization. Integer or preset "
        "(small ~25%% of cores, normal ~50%%, fast cpu_count-2).",
    )


def add_promote_lcoe_arg(parser: argparse._ActionsContainer) -> None:
    """Takes a parser or an argument group, so the flag lands beside the other optional ones."""
    parser.add_argument(
        "--promote-lcoe",
        action="store_true",
        help="After the query, combine the per-year GLOBAL NetCDFs into one LCOE file for the steel simulation "
        "(same as running boa-promote-lcoe afterwards).",
    )


def run_promotion(path_config: PathConfig, baseload_demand: float, coverage: float) -> int:
    """Promote this scenario's LCOE, reporting a failure without discarding the completed query."""
    try:
        promote_lcoe(path_config, baseload_demand, coverage)
    except (FileNotFoundError, ValueError) as e:
        logging.error(f"LCOE promotion failed: {e}")
        return 1
    return 0


def validate_scenario_args(args: argparse.Namespace) -> None:
    """Raise ValueError on logically inconsistent parameters."""
    if not 0 < args.coverage <= 1:
        raise ValueError(f"Coverage must be between 0 and 1, got {args.coverage}")
    if args.demand <= 0:
        raise ValueError(f"Baseload demand must be positive, got {args.demand}")
    if args.samples <= 0:
        raise ValueError(f"Number of samples must be a positive integer, got {args.samples}")


def validate_temporal_args(args: argparse.Namespace) -> None:
    if args.start_year > args.end_year:
        raise ValueError(f"Start year ({args.start_year}) must be <= end year ({args.end_year})")
    if args.frequency <= 0:
        raise ValueError(f"Frequency must be a positive integer, got {args.frequency}")


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


def resolved_parameters(args: argparse.Namespace, years: List[int] | None = None) -> dict:
    """Fully resolved scenario settings for the run manifest, so defaults are recorded too."""
    params: dict = {
        "demand_mw": args.demand,
        "coverage": args.coverage,
        "samples": args.samples,
    }
    if years is not None:
        params["years"] = years
    if hasattr(args, "workers"):
        params["workers"] = args.workers
    if hasattr(args, "lat"):
        params["lat"], params["lon"] = args.lat, args.lon
    return params


def build_path_config(args: argparse.Namespace) -> PathConfig:
    """Resolve the PathConfig for the selected sets and log where everything lives."""
    path_config = PathConfig.from_auto_detect(input_set=args.weather_input, cost_set=args.cost_input, run=args.run)
    logging.info(f"Data root: {path_config.root}")
    logging.info(f"Inputs: {path_config.input_set}; costs: {path_config.cost_set}; run: {path_config.run}")
    return path_config


def preflight(path_config: PathConfig, require_all_stores: bool = True) -> int:
    """
    Fail fast, before any compute, if the selected sets are incomplete.

    Returns the weather year detected from the input set's profile stores.
    Raises FileNotFoundError/ValueError with the exact preparation command to run.
    """
    weather_year = detect_weather_year(path_config)
    if require_all_stores:
        kinds: tuple[DataKind, ...] = ("profile", "max_cap")
        missing = [
            (kind, region)
            for region in REGION_COORDS
            for kind in kinds
            if not dataset_path(kind, region, path_config, weather_year).exists()
        ]
        if missing:
            regions = sorted({region for _, region in missing})
            raise FileNotFoundError(
                f"Input set '{path_config.input_set}' is missing {len(missing)} store(s) "
                f"for regions {regions} (weather year {weather_year}) — build them with "
                f"`boa-cds-prepare --weather_year {weather_year} --inputs {path_config.input_set}`."
            )
    if not path_config.input_data_path.exists():
        raise FileNotFoundError(
            f"Cost workbook {path_config.input_data_path} not found — build the cost set with "
            f"`boa-data-prepare --scenario {path_config.cost_set}` "
            f"(or pass `--data-prepare <master.xlsx> {path_config.cost_set}` to boa-run)."
        )
    logging.info(f"Preflight OK: weather year {weather_year}, cost set '{path_config.cost_set}'.")
    return weather_year


def _validate_cost_set(path_config: PathConfig, investment_year: int) -> tuple[xr.Dataset, int]:
    """Load one year's costs and check subregion coverage/keys against the shapefile."""
    costs, horizon = process_global_baseload_simulation_costs(
        investment_year=investment_year,
        input_data_path=path_config.input_data_path,
        cost_cache_dir=path_config.cost_cache_dir,
    )
    load_subregion_polygons(path_config.admin1_10m_shapefile_path)
    validate_subregion_coverage(costs)
    validate_subregion_keys(costs, path_config.admin1_10m_shapefile_path)
    return costs, horizon


def build_all_caches(
    path_config: PathConfig,
    coverage: float,
    years: List[int],
    n_workers: int,
    force: bool = False,
) -> None:
    """
    Build the year- and baseload-independent frontier cache for every region.

    Skip-if-exists unless `force`. `years` is the horizon the run will query, and it is used
    only to choose anchors: seed placement is the one part of the build that reads a cost, so
    the anchors must cover every (cost key, year) a later query can ask for. The stored values
    are pure dispatch and serve every year regardless.
    """
    anchors = anchor_cost_coefficients(years, lambda year: _validate_cost_set(path_config, year))
    for region in REGION_COORDS:
        logging.info(f"\nBuilding frontier cache for {region}")
        profile = open_regional_dataset("profile", region, path_config)
        build_frontier_cache_for_region(
            region=region,
            coverage=coverage,
            profile=profile,
            anchors=anchors,
            path_config=path_config,
            n_workers=n_workers,
            force=force,
        )


def query_all_years(
    path_config: PathConfig,
    years: List[int],
    baseload_demand: float,
    coverage: float,
    n_workers: int,
    force: bool = False,
    generate_plots: bool = True,
) -> None:
    """
    Derive optimal-solution NetCDFs for every (region, year) from the frontier caches.

    No profiles are opened. The sampler needed them at query time to re-run dispatch on its
    winner; the frontier store already holds every dispatch result the pricing needs, so a
    year costs four cost scalars and an argmin per pixel.
    """
    for year in years:
        costs, horizon = _validate_cost_set(path_config, year)
        for region in REGION_COORDS:
            logging.info(f"\nQuerying frontier cache for {region} y{year}")
            query_frontier_cache_for_region(
                year=year,
                region=region,
                baseload_demand=baseload_demand,
                coverage=coverage,
                costs=costs,
                investment_horizon=horizon,
                path_config=path_config,
                n_workers=n_workers,
                force=force,
            )
            if generate_plots:
                plot_regional_optimum_baseload_power_simulation_map(
                    year, region, coverage, baseload_demand, path_config
                )
        global_optimal_sol = combine_regional_datasets_into_global_dataset(
            year,
            coverage,
            baseload_demand,
            path_config,
            force=force,
        )
        if generate_plots and global_optimal_sol is not None:
            plot_global_optimum_baseload_power_simulation_map(
                global_optimal_sol, year, coverage, baseload_demand, path_config
            )


def main_run(argv: list[str]) -> int:
    """
    Bare `boa-run`: full GLOBAL simulation. Composes build-cache (once, skip-if-exists)
    with query (per year), so the year-independent cache is never rebuilt per year.
    Use the subcommands with --force for targeted rebuilds.
    """
    parser = argparse.ArgumentParser(
        prog="boa-run",
        description=__doc__,
        formatter_class=_HelpFormatter,
    )
    add_temporal_args(parser)
    add_scenario_args(parser)
    add_workers_arg(parser)
    optional_group = parser.add_argument_group("Optional Parameters")
    optional_group.add_argument("--verbose", action="store_true", help="Enable verbose logging output")
    optional_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve paths and run the preflight check without simulating",
    )
    optional_group.add_argument(
        "--plots",
        action="store_true",
        help="Generate per-region and global map plots during the run (off by default; plots can be regenerated later from saved NetCDFs)",
    )
    add_promote_lcoe_arg(optional_group)
    add_data_args(parser)
    args = parser.parse_args(argv)

    try:
        validate_temporal_args(args)
        validate_scenario_args(args)
    except ValueError as e:
        logging.error(f"Invalid arguments: {e}")
        return 1

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    years = get_simulation_years(args.start_year, args.end_year, args.frequency)

    logging.info("=" * 60)
    logging.info("BOA: full GLOBAL simulation")
    logging.info("=" * 60)
    logging.info(f"Years to simulate: {years}")
    logging.info(f"Baseload demand: {args.demand} MW")
    logging.info(f"Coverage requirement: {args.coverage * 100:.1f}%")
    logging.info(f"Worker threads: {args.workers}")
    logging.info(f"Generate plots: {args.plots}")
    logging.info("=" * 60)

    resolve_data_sets(args)
    if (rc := run_prepare_flags(args)) != 0:
        return rc
    path_config = build_path_config(args)
    try:
        preflight(path_config)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 1

    if args.dry_run:
        logging.info(f"Frontier caches: {path_config.frontier_cache_dir(detect_weather_year(path_config))}")
        logging.info(f"Outputs: {path_config.outputs_dir}")
        logging.info("Dry run - exiting without running simulation")
        return 0

    run_manifest.record_invocation(path_config, "run", list(argv), parameters=resolved_parameters(args, years))
    build_all_caches(path_config, args.coverage, years, args.workers)
    query_all_years(
        path_config,
        years,
        args.demand,
        args.coverage,
        args.workers,
        generate_plots=args.plots,
    )
    if args.promote_lcoe and run_promotion(path_config, args.demand, args.coverage) != 0:
        return 1
    logging.info("\nAll simulations completed successfully!")
    return 0


def main_build_cache(argv: list[str]) -> int:
    """
    `build-cache` subcommand: build the year-independent design caches for all regions.
    No NetCDF output; only the per-region Zarr stores are written. Idempotent —
    existing caches at the parameterised path are skipped.
    """
    parser = argparse.ArgumentParser(
        prog="boa-run build-cache",
        description="Build the design caches (all regions) without producing optimal-solution NetCDFs.",
        formatter_class=_HelpFormatter,
    )
    add_scenario_args(parser)
    add_workers_arg(parser)
    parser.add_argument("--force", action="store_true", help="Rebuild each design cache even if it already exists.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    add_data_args(parser)
    args = parser.parse_args(argv)

    try:
        validate_scenario_args(args)
    except ValueError as e:
        logging.error(f"Invalid arguments: {e}")
        return 1
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logging.info("=" * 60)
    logging.info("BOA: build-cache")
    logging.info("=" * 60)
    logging.info(f"Coverage {args.coverage:g}; workers={args.workers} (caches are baseload- and year-independent)")

    resolve_data_sets(args)
    if (rc := run_prepare_flags(args)) != 0:
        return rc
    path_config = build_path_config(args)
    try:
        preflight(path_config)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 1
    run_manifest.record_invocation(path_config, "build-cache", list(argv), parameters=resolved_parameters(args))
    build_all_caches(
        path_config,
        args.coverage,
        get_simulation_years(args.start_year, args.end_year, args.frequency),
        args.workers,
        force=args.force,
    )
    logging.info("\nbuild-cache: all regions complete.")
    return 0


def main_query(argv: list[str]) -> int:
    """
    `query` subcommand: re-derive optimal-solution NetCDFs from pre-built design
    caches for the requested years. Requires the caches to exist; will not build.
    """
    parser = argparse.ArgumentParser(
        prog="boa-run query",
        description="Run the LCOE-only query against pre-built design caches (all regions).",
        formatter_class=_HelpFormatter,
    )
    add_temporal_args(parser)
    add_scenario_args(parser)
    add_workers_arg(parser)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-derive every artifact (regional NetCDFs, GLOBAL combine) even if it already exists on disk.",
    )
    parser.add_argument("--plots", action="store_true", help="Generate per-region and global map plots.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    add_promote_lcoe_arg(parser)
    add_data_args(parser)
    args = parser.parse_args(argv)

    try:
        validate_temporal_args(args)
        validate_scenario_args(args)
    except ValueError as e:
        logging.error(f"Invalid arguments: {e}")
        return 1
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    years = get_simulation_years(args.start_year, args.end_year, args.frequency)

    logging.info("=" * 60)
    logging.info("BOA: query (design-cache → NetCDF)")
    logging.info("=" * 60)
    logging.info(f"Years: {years}")
    logging.info(
        f"Baseload: {args.demand} MW; coverage {args.coverage:g}; samples={args.samples}; workers={args.workers}"
    )

    resolve_data_sets(args)
    if (rc := run_prepare_flags(args)) != 0:
        return rc
    path_config = build_path_config(args)
    try:
        preflight(path_config)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 1
    run_manifest.record_invocation(path_config, "query", list(argv), parameters=resolved_parameters(args, years))
    query_all_years(
        path_config,
        years,
        args.demand,
        args.coverage,
        args.workers,
        force=args.force,
        generate_plots=args.plots,
    )
    if args.promote_lcoe and run_promotion(path_config, args.demand, args.coverage) != 0:
        return 1
    logging.info("\nquery: all (region, year) pairs complete.")
    return 0


def main_point(argv: list[str]) -> int:
    """
    `point` subcommand: single-point simulation at (--lat, --lon); the region and
    country are auto-derived from the coordinates.
    """
    parser = argparse.ArgumentParser(
        prog="boa-run point",
        description="Single-point simulation; region auto-derived from coordinates.",
        formatter_class=_HelpFormatter,
    )
    parser.add_argument("--lat", type=float, required=True, help="Latitude of the point.")
    parser.add_argument("--lon", type=float, required=True, help="Longitude of the point.")
    add_temporal_args(parser)
    add_scenario_args(parser)
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    add_data_args(parser)
    args = parser.parse_args(argv)

    try:
        validate_temporal_args(args)
        validate_scenario_args(args)
        if not -90 <= args.lat <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got {args.lat}")
        if not -180 <= args.lon <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got {args.lon}")
    except ValueError as e:
        logging.error(f"Invalid arguments: {e}")
        return 1
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    years = get_simulation_years(args.start_year, args.end_year, args.frequency)

    logging.info("=" * 60)
    logging.info("BOA: single-point simulation")
    logging.info("=" * 60)
    logging.info(f"Location: Lat={args.lat}, Lon={args.lon} (region auto-derived)")
    logging.info(f"Years: {years}; baseload: {args.demand} MW; coverage {args.coverage:g}; samples={args.samples}")

    resolve_data_sets(args)
    if (rc := run_prepare_flags(args)) != 0:
        return rc
    path_config = build_path_config(args)
    try:
        # The point's region is derived downstream, so only the year + cost set are preflighted.
        preflight(path_config, require_all_stores=False)
    except (FileNotFoundError, ValueError) as e:
        logging.error(str(e))
        return 1
    run_manifest.record_invocation(path_config, "point", list(argv), parameters=resolved_parameters(args, years))

    for year in years:
        logging.info(f"\nRunning single-point simulation for year {year}")
        try:
            execute_single_point_baseload_power_simulation(
                path_config=path_config,
                year=year,
                lat=args.lat,
                lon=args.lon,
                baseload_demand=args.demand,
                coverage=args.coverage,
                n=args.samples,
            )
        except Exception as e:
            logging.error(f"Failed to run simulation for year {year}: {e}")
            if args.verbose:
                logging.exception("Detailed error:")
            return 1
    logging.info("\npoint: all years complete.")
    return 0


_SUBCOMMANDS = {
    "build-cache": main_build_cache,
    "query": main_query,
    "point": main_point,
}


def main() -> int:
    """
    Top-level dispatcher for `boa-run`. A recognised first argument routes to the
    named subcommand; anything else is parsed by the bare full-run command.
    Returns a process exit code (0 success, 1 failure).
    """
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        return _SUBCOMMANDS[sys.argv[1]](sys.argv[2:]) or 0
    return main_run(sys.argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
