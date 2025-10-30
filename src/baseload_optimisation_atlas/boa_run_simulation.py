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
from typing import List

from steelo.config import settings
from baseload_optimisation_atlas.boa_config import BaseloadPowerConfig, REGION_COORDS
from baseload_optimisation_atlas.boa_global_extension import execute_baseload_power_simulation

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("distributed").setLevel(logging.WARNING)


def parse_arguments() -> argparse.Namespace:
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
            python boa_run_simulation.py --region EU --baseload-demand 1000 --coverage 0.95
            
            # Run single year with high coverage requirement
            python boa_run_simulation.py --start-year 2030 --end-year 2030
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Temporal parameters
    temporal_group = parser.add_argument_group("Temporal Parameters")
    temporal_group.add_argument(
        "--start-year", type=int, default=2025, help="Starting year for simulation (default: 2025)"
    )
    temporal_group.add_argument("--end-year", type=int, default=2050, help="Ending year for simulation (default: 2050)")
    temporal_group.add_argument(
        "--frequency", type=int, default=5, help="Frequency in years between simulations (default: 5)"
    )

    # Spatial parameters
    spatial_group = parser.add_argument_group("Spatial Parameters")
    spatial_group.add_argument(
        "--region",
        type=str,
        default="GLOBAL",
        choices=["GLOBAL"] + list(REGION_COORDS.keys()),
        help="Region to simulate. Use GLOBAL to run all regions (default: GLOBAL)",
    )

    # Technical parameters
    technical_group = parser.add_argument_group("Technical Parameters")
    technical_group.add_argument(
        "--baseload-demand",
        type=float,
        default=500.0,
        help="Baseload demand in MW. Typical range: 150-1000 MW (default: 500.0)",
    )
    technical_group.add_argument(
        "--coverage",
        type=float,
        default=0.85,
        help="Required demand coverage fraction (0-1). E.g., 0.85 means RE must cover demand 85%% of the time (default: 0.85)",
    )
    technical_group.add_argument(
        "--samples",
        type=int,
        default=1000,
        help="Number of random designs to sample. Higher values increase accuracy but also runtime (default: 1000)",
    )

    # Optional parameters
    optional_group = parser.add_argument_group("Optional Parameters")
    optional_group.add_argument("--verbose", action="store_true", help="Enable verbose logging output")
    optional_group.add_argument("--dry-run", action="store_true", help="Print configuration without running simulation")

    return parser.parse_args()


def validate_arguments(args: argparse.Namespace) -> None:
    """
    Validate command-line arguments for logical consistency.

    Args:
        args: Parsed command-line arguments

    Raises:
        ValueError: If arguments are invalid or inconsistent
    """
    if args.start_year > args.end_year:
        raise ValueError(f"Start year ({args.start_year}) must be <= end year ({args.end_year})")

    if args.frequency <= 0:
        raise ValueError(f"Frequency must be positive, got {args.frequency}")

    if not 0 < args.coverage <= 1:
        raise ValueError(f"Coverage must be between 0 and 1, got {args.coverage}")

    if args.baseload_demand <= 0:
        raise ValueError(f"Baseload demand must be positive, got {args.baseload_demand}")

    if args.samples <= 0:
        raise ValueError(f"Number of samples must be positive, got {args.samples}")


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


def main():
    """
    Main entry point for the baseload power simulation.

    Runs the Baseload Optimisation Atlas simulation to find optimal renewable
    energy configurations for steel plants across specified regions and years.
    """
    # Parse and validate arguments
    args = parse_arguments()

    try:
        validate_arguments(args)
    except ValueError as e:
        logging.error(f"Invalid arguments: {e}")
        return 1

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Calculate coverage percentile (p = percentage of time NOT covered)
    p = int((1 - args.coverage) * 100)

    # Get simulation years
    years = get_simulation_years(args.start_year, args.end_year, args.frequency)

    # Print configuration
    logging.info("=" * 60)
    logging.info("Baseload Optimisation Atlas (BOA) Configuration")
    logging.info("=" * 60)
    logging.info(f"Region: {args.region}")
    logging.info(f"Years to simulate: {years}")
    logging.info(f"Baseload demand: {args.baseload_demand} MW")
    logging.info(f"Coverage requirement: {args.coverage * 100:.1f}% (p={p})")
    logging.info(f"Number of samples: {args.samples}")
    logging.info("=" * 60)

    if args.dry_run:
        logging.info("Dry run - exiting without running simulation")
        return 0

    # Set up configuration
    config = BaseloadPowerConfig.from_project_root(settings.project_root)

    # Run simulations
    for year in years:
        logging.info(f"\nStarting simulation for year {year}")
        try:
            execute_baseload_power_simulation(
                year=year,
                region=args.region,
                baseload_demand=args.baseload_demand,
                p=p,
                n=args.samples,
                config=config,
            )
            logging.info(f"Completed simulation for year {year}")
        except Exception as e:
            logging.error(f"Failed to run simulation for year {year}: {e}")
            if args.verbose:
                logging.exception("Detailed error:")
            return 1

    logging.info("\nAll simulations completed successfully!")
    return 0


if __name__ == "__main__":
    exit(main())
