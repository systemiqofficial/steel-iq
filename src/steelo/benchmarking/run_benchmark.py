"""
CLI tool for running comprehensive benchmarks on Steel-IQ solvers.

Usage:
    # Run baseline benchmark
    python -m steelo.benchmarking.run_benchmark --years 2020-2025

    # Compare all solver modes
    python -m steelo.benchmarking.run_benchmark --years 2020-2025 --compare-all

    # Specific solver mode
    python -m steelo.benchmarking.run_benchmark --years 2020-2025 --mode parallel
"""

import argparse
import logging
import sys
from pathlib import Path

from steelo.benchmarking.hardware_detector import detect_apple_silicon
from steelo.benchmarking.performance_profiler import SolverBenchmark

logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Benchmark Steel-IQ optimization implementations"
    )

    parser.add_argument(
        "--years",
        type=str,
        default="2020-2025",
        help="Year range to simulate (format: YYYY-YYYY)",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["baseline", "parallel", "gasplan", "parallel_gasplan"],
        default="baseline",
        help="Solver mode to benchmark",
    )

    parser.add_argument(
        "--compare-all",
        action="store_true",
        help="Run all solver modes and generate comparison report",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks"),
        help="Directory for benchmark results",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--track-memory",
        action="store_true",
        default=True,
        help="Track memory usage during benchmarking",
    )

    parser.add_argument(
        "--validate-accuracy",
        action="store_true",
        default=True,
        help="Validate solution accuracy against baseline",
    )

    return parser.parse_args()


def setup_logging(verbose: bool):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def parse_year_range(year_str: str) -> tuple[int, int]:
    """Parse year range string."""
    try:
        start, end = year_str.split("-")
        return int(start), int(end)
    except (ValueError, AttributeError):
        logger.error(f"Invalid year range: {year_str}. Expected format: YYYY-YYYY")
        sys.exit(1)


def main():
    """Main benchmark execution."""
    args = parse_args()
    setup_logging(args.verbose)

    # Detect hardware
    hw = detect_apple_silicon()
    logger.info(f"Hardware detected: {hw}")
    logger.info(f"Optimal workers for parallel mode: {hw.optimal_workers('parallel')}")

    # Create benchmark framework
    benchmark = SolverBenchmark(output_dir=args.output_dir)

    # Parse years
    start_year, end_year = parse_year_range(args.years)
    years = list(range(start_year, end_year + 1))

    logger.info(f"Benchmarking years: {start_year}-{end_year} ({len(years)} years)")

    # Import simulation components (lazy import to avoid dependencies)
    try:
        from steelo.simulation import run_simulation
        from steelo.config import load_config
    except ImportError as e:
        logger.error(f"Failed to import simulation components: {e}")
        logger.error("Make sure Steel-IQ is properly installed")
        sys.exit(1)

    # Determine which modes to run
    modes_to_run = []
    if args.compare_all:
        modes_to_run = ["baseline", "parallel", "gasplan", "parallel_gasplan"]
    else:
        modes_to_run = [args.mode]

    # Run benchmarks
    baseline_result = None
    all_results = {}

    for mode in modes_to_run:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Benchmarking mode: {mode}")
        logger.info(f"{'=' * 60}\n")

        # Configure solver mode
        config = {
            "solver_mode": mode,
            "years": years,
            "benchmark_mode": True,
        }

        # Run benchmark
        # NOTE: This is a placeholder - actual implementation needs to be connected
        # to the real simulation runner once it's available
        logger.warning(
            "Benchmark integration with full simulation pending implementation"
        )
        logger.info("For now, demonstrating benchmark framework structure")

        # Placeholder for demonstration
        result = benchmark.profile(
            func=lambda: {"placeholder": "result"},
            component="lp_solver",
            operation=f"solve_{mode}",
            config={"mode": mode, "years": len(years)},
            problem_size={"years": len(years)},
        )

        all_results[mode] = result

        # Store baseline for comparison
        if mode == "baseline":
            baseline_result = result

    # Validate accuracy if requested
    if args.validate_accuracy and len(all_results) > 1 and baseline_result:
        logger.info("\n" + "=" * 60)
        logger.info("Accuracy Validation")
        logger.info("=" * 60 + "\n")

        for mode, result in all_results.items():
            if mode == "baseline":
                continue

            # NOTE: Placeholder for actual solution comparison
            logger.info(f"Comparing {mode} against baseline...")
            logger.warning("Solution comparison pending full integration")

    # Export results
    csv_path = benchmark.export_results(filename=f"benchmark_{args.mode}.csv")
    logger.info(f"\nResults exported to: {csv_path}")

    # Generate report
    report_path = benchmark.generate_report(
        filename=f"benchmark_{args.mode}_report.md"
    )
    logger.info(f"Report generated: {report_path}")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Benchmark Summary")
    logger.info("=" * 60)

    for mode, result in all_results.items():
        duration = result.avg_duration_ms()
        memory = result.peak_memory_mb

        if baseline_result and mode != "baseline":
            baseline_duration = baseline_result.avg_duration_ms()
            speedup = baseline_duration / duration if duration > 0 else 0
            logger.info(
                f"{mode:20s}: {duration:8.2f}ms | {memory:6.1f}MB | {speedup:5.1f}x speedup"
            )
        else:
            logger.info(f"{mode:20s}: {duration:8.2f}ms | {memory:6.1f}MB | baseline")

    logger.info("\nBenchmarking complete!")


if __name__ == "__main__":
    main()
