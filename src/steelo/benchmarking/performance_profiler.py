"""
Performance profiling and benchmarking for Steel-IQ solvers.

Provides comprehensive timing, memory, and accuracy measurements for comparing
different solver implementations.
"""

import logging
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResults:
    """Results from a single benchmark run."""

    component: str  # e.g., "lp_solver", "geospatial", "plant_agents"
    operation: str  # e.g., "solve", "matrix_build", "grid_calculate"

    # Timing
    duration_ms: float
    iterations: int = 1

    # Memory
    peak_memory_mb: float = 0.0
    allocated_memory_mb: float = 0.0

    # Problem size
    problem_size: Dict[str, Any] = field(default_factory=dict)

    # Solution quality (for validation)
    objective_value: Optional[float] = None
    constraint_violations: int = 0

    # Solver-specific metrics
    solver_iterations: Optional[int] = None
    solver_status: Optional[str] = None

    # Hardware info
    hardware: Optional[str] = None

    # Metadata
    timestamp: float = field(default_factory=time.time)
    config: Dict[str, Any] = field(default_factory=dict)

    def avg_duration_ms(self) -> float:
        """Average duration per iteration."""
        return self.duration_ms / max(1, self.iterations)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for export."""
        return {
            "component": self.component,
            "operation": self.operation,
            "duration_ms": self.duration_ms,
            "avg_duration_ms": self.avg_duration_ms(),
            "iterations": self.iterations,
            "peak_memory_mb": self.peak_memory_mb,
            "allocated_memory_mb": self.allocated_memory_mb,
            "objective_value": self.objective_value,
            "constraint_violations": self.constraint_violations,
            "solver_iterations": self.solver_iterations,
            "solver_status": self.solver_status,
            "hardware": self.hardware,
            "timestamp": self.timestamp,
            **self.problem_size,
            **self.config,
        }


class SolverBenchmark:
    """
    Comprehensive benchmarking framework for Steel-IQ optimizations.

    Measures timing, memory usage, and solution quality for different
    solver implementations and optimization strategies.

    Usage:
        benchmark = SolverBenchmark()

        # Benchmark a function
        result = benchmark.profile(
            func=solve_lp_model,
            component="lp_solver",
            operation="solve",
            args=(model,),
        )

        # Compare multiple implementations
        results = benchmark.compare_solvers(
            baseline=solve_baseline,
            parallel=solve_parallel,
            gasplan=solve_gasplan,
        )
    """

    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize benchmark framework.

        Args:
            output_dir: Directory for saving benchmark results
        """
        self.output_dir = output_dir or Path("benchmarks")
        self.output_dir.mkdir(exist_ok=True, parents=True)

        self.results: List[BenchmarkResults] = []

    def profile(
        self,
        func: Callable,
        component: str,
        operation: str,
        args: tuple = (),
        kwargs: dict = None,
        iterations: int = 1,
        track_memory: bool = True,
        problem_size: dict = None,
        config: dict = None,
    ) -> BenchmarkResults:
        """
        Profile a function with timing and memory tracking.

        Args:
            func: Function to profile
            component: Component name (e.g., "lp_solver")
            operation: Operation name (e.g., "solve")
            args: Positional arguments for func
            kwargs: Keyword arguments for func
            iterations: Number of times to run (for averaging)
            track_memory: Whether to track memory usage
            problem_size: Dict of problem size metrics
            config: Configuration dict for this run

        Returns:
            BenchmarkResults with profiling data
        """
        kwargs = kwargs or {}
        problem_size = problem_size or {}
        config = config or {}

        # Memory tracking
        if track_memory:
            tracemalloc.start()
            tracemalloc.reset_peak()

        # Timing
        start_time = time.perf_counter()

        # Run function
        result = None
        for i in range(iterations):
            result = func(*args, **kwargs)

        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000

        # Memory stats
        peak_memory_mb = 0.0
        allocated_memory_mb = 0.0
        if track_memory:
            current, peak = tracemalloc.get_traced_memory()
            peak_memory_mb = peak / (1024 * 1024)
            allocated_memory_mb = current / (1024 * 1024)
            tracemalloc.stop()

        # Extract solution metrics if available
        objective_value = None
        solver_iterations = None
        solver_status = None
        constraint_violations = 0

        if hasattr(result, "objective_value"):
            objective_value = result.objective_value
        if hasattr(result, "iterations"):
            solver_iterations = result.iterations
        if hasattr(result, "status"):
            solver_status = str(result.status)
        if hasattr(result, "constraint_violations"):
            constraint_violations = result.constraint_violations

        # Create results
        benchmark_result = BenchmarkResults(
            component=component,
            operation=operation,
            duration_ms=duration_ms,
            iterations=iterations,
            peak_memory_mb=peak_memory_mb,
            allocated_memory_mb=allocated_memory_mb,
            problem_size=problem_size,
            objective_value=objective_value,
            constraint_violations=constraint_violations,
            solver_iterations=solver_iterations,
            solver_status=solver_status,
            config=config,
        )

        self.results.append(benchmark_result)

        logger.info(
            f"Benchmarked {component}.{operation}: "
            f"{benchmark_result.avg_duration_ms():.2f}ms/iter, "
            f"{peak_memory_mb:.1f}MB peak memory"
        )

        return benchmark_result

    def profile_lp_solver(
        self,
        solve_func: Callable,
        model: Any,
        config: dict = None,
    ) -> Dict[str, BenchmarkResults]:
        """
        Comprehensive profiling of LP solver.

        Profiles:
        - Model building time
        - Matrix construction time
        - Solve time
        - Solution extraction time
        - Total time

        Args:
            solve_func: Function that solves the LP model
            model: Trade LP model to solve
            config: Configuration for this solver

        Returns:
            Dict of BenchmarkResults for each stage
        """
        results = {}

        # Get problem size
        problem_size = {
            "variables": getattr(model.lp_model, "nvariables", lambda: 0)(),
            "constraints": getattr(model.lp_model, "nconstraints", lambda: 0)(),
        }

        # Profile solve
        results["solve"] = self.profile(
            func=solve_func,
            component="lp_solver",
            operation="solve",
            args=(model,),
            problem_size=problem_size,
            config=config or {},
        )

        return results

    def compare_solutions(
        self,
        baseline: Any,
        optimized: Any,
        tolerance: float = 0.01,  # 1% tolerance
    ) -> Dict[str, Any]:
        """
        Compare two solutions for accuracy validation.

        Args:
            baseline: Baseline solution (reference)
            optimized: Optimized solution to validate
            tolerance: Relative tolerance (default 1%)

        Returns:
            Dict with comparison metrics:
                - identical: Whether solutions are identical
                - max_relative_diff: Maximum relative difference
                - violations: Number of values exceeding tolerance
                - passed: Whether validation passed
        """
        # Extract allocations from solutions
        baseline_allocs = self._extract_allocations(baseline)
        optimized_allocs = self._extract_allocations(optimized)

        # Compare keys
        baseline_keys = set(baseline_allocs.keys())
        optimized_keys = set(optimized_allocs.keys())

        missing_in_optimized = baseline_keys - optimized_keys
        extra_in_optimized = optimized_keys - baseline_keys

        # Compare values for common keys
        max_relative_diff = 0.0
        violations = 0

        for key in baseline_keys & optimized_keys:
            baseline_val = baseline_allocs[key]
            optimized_val = optimized_allocs[key]

            if baseline_val == 0 and optimized_val == 0:
                continue
            elif baseline_val == 0:
                rel_diff = abs(optimized_val)
            else:
                rel_diff = abs(optimized_val - baseline_val) / abs(baseline_val)

            max_relative_diff = max(max_relative_diff, rel_diff)

            if rel_diff > tolerance:
                violations += 1

        # Determine if validation passed
        identical = (
            len(missing_in_optimized) == 0
            and len(extra_in_optimized) == 0
            and max_relative_diff == 0.0
        )

        passed = (
            len(missing_in_optimized) == 0
            and len(extra_in_optimized) == 0
            and max_relative_diff <= tolerance
        )

        return {
            "identical": identical,
            "passed": passed,
            "max_relative_diff": max_relative_diff,
            "violations": violations,
            "missing_in_optimized": len(missing_in_optimized),
            "extra_in_optimized": len(extra_in_optimized),
            "tolerance": tolerance,
        }

    def _extract_allocations(self, solution: Any) -> Dict[tuple, float]:
        """Extract allocation values from solution."""
        if isinstance(solution, dict):
            # Assume dict of allocations
            return {k: v for k, v in solution.items()}
        elif hasattr(solution, "allocations"):
            # Assume CommodityAllocations object
            allocs = {}
            for commodity, commodity_allocs in solution.items():
                if hasattr(commodity_allocs, "allocations"):
                    for key, value in commodity_allocs.allocations.items():
                        allocs[(commodity, *key)] = value
            return allocs
        else:
            logger.warning(f"Unknown solution format: {type(solution)}")
            return {}

    def export_results(self, filename: str = "benchmark_results.csv") -> Path:
        """
        Export benchmark results to CSV.

        Args:
            filename: Output filename

        Returns:
            Path to exported file
        """
        if not self.results:
            logger.warning("No results to export")
            return None

        # Convert to DataFrame
        data = [result.to_dict() for result in self.results]
        df = pd.DataFrame(data)

        # Export
        output_path = self.output_dir / filename
        df.to_csv(output_path, index=False)

        logger.info(f"Exported {len(self.results)} results to {output_path}")

        return output_path

    def generate_report(self, filename: str = "benchmark_report.md") -> Path:
        """
        Generate markdown report with comparison tables.

        Args:
            filename: Output filename

        Returns:
            Path to generated report
        """
        if not self.results:
            logger.warning("No results to report")
            return None

        output_path = self.output_dir / filename

        with open(output_path, "w") as f:
            f.write("# Steel-IQ Optimization Benchmark Report\n\n")
            f.write(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            # Group by component
            components = {}
            for result in self.results:
                if result.component not in components:
                    components[result.component] = []
                components[result.component].append(result)

            # Write tables for each component
            for component, results in components.items():
                f.write(f"## {component.replace('_', ' ').title()}\n\n")

                # Create comparison table
                f.write("| Operation | Duration (ms) | Memory (MB) | Speedup | Config |\n")
                f.write("|-----------|---------------|-------------|---------|--------|\n")

                # Find baseline for speedup calculation
                baseline = next(
                    (r for r in results if r.config.get("mode") == "baseline"), None
                )

                for result in results:
                    duration = result.avg_duration_ms()
                    memory = result.peak_memory_mb

                    speedup = "-"
                    if baseline and baseline != result and baseline.avg_duration_ms() > 0:
                        speedup = f"{baseline.avg_duration_ms() / duration:.1f}x"

                    config_str = result.config.get("mode", "default")

                    f.write(
                        f"| {result.operation} | {duration:.2f} | {memory:.1f} | "
                        f"{speedup} | {config_str} |\n"
                    )

                f.write("\n")

        logger.info(f"Generated report: {output_path}")

        return output_path


def profile_decorator(component: str, operation: str):
    """
    Decorator for automatic profiling of functions.

    Usage:
        @profile_decorator(component="lp_solver", operation="solve")
        def solve_lp(model):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            benchmark = SolverBenchmark()
            result = benchmark.profile(
                func=func,
                component=component,
                operation=operation,
                args=args,
                kwargs=kwargs,
            )
            return result
        return wrapper
    return decorator
