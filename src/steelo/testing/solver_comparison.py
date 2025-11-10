"""
Solver comparison and validation framework.

This module provides tools for comparing different solver implementations
and validating that optimized solvers produce accurate results.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from steelo.benchmarking.performance_profiler import BenchmarkResults, SolverBenchmark
from steelo.config.optimization_config import OptimizationConfig
from steelo.domain.trade_modelling import trade_lp_modelling as tlp
from steelo.domain.trade_modelling.solver_factory import SolverFactory

logger = logging.getLogger(__name__)


@dataclass
class SolverComparisonResult:
    """Results from comparing two solver implementations."""

    baseline_mode: str
    optimized_mode: str

    # Timing
    baseline_time_ms: float
    optimized_time_ms: float
    speedup: float

    # Memory
    baseline_memory_mb: float
    optimized_memory_mb: float
    memory_reduction: float

    # Accuracy
    max_relative_diff: float
    violations: int
    tolerance: float
    accuracy_passed: bool

    # Solution quality
    baseline_objective: Optional[float] = None
    optimized_objective: Optional[float] = None
    objective_diff: Optional[float] = None

    # Problem size
    problem_size: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    timestamp: float = field(default_factory=time.time)

    def get_summary(self) -> str:
        """Get human-readable summary of comparison."""
        lines = [
            f"Solver Comparison: {self.baseline_mode} vs {self.optimized_mode}",
            f"",
            f"Performance:",
            f"  Baseline:  {self.baseline_time_ms:.1f} ms",
            f"  Optimized: {self.optimized_time_ms:.1f} ms",
            f"  Speedup:   {self.speedup:.2f}x",
            f"",
            f"Memory:",
            f"  Baseline:  {self.baseline_memory_mb:.1f} MB",
            f"  Optimized: {self.optimized_memory_mb:.1f} MB",
            f"  Reduction: {self.memory_reduction:.1f}%",
            f"",
            f"Accuracy:",
            f"  Max diff:  {self.max_relative_diff:.4f} ({self.max_relative_diff*100:.2f}%)",
            f"  Tolerance: {self.tolerance:.4f} ({self.tolerance*100:.2f}%)",
            f"  Violations: {self.violations}",
            f"  Passed:    {'✓ Yes' if self.accuracy_passed else '✗ No'}",
        ]

        if self.baseline_objective is not None and self.optimized_objective is not None:
            lines.extend([
                f"",
                f"Objective:",
                f"  Baseline:  {self.baseline_objective:.2f}",
                f"  Optimized: {self.optimized_objective:.2f}",
                f"  Diff:      {self.objective_diff:.6f}",
            ])

        return "\n".join(lines)


class SolverComparison:
    """
    Framework for comparing different solver implementations.

    Provides comprehensive comparison including timing, memory usage,
    and accuracy validation between baseline and optimized solvers.

    Usage:
        comparison = SolverComparison()

        # Compare baseline vs parallel
        result = comparison.compare_solvers(
            trade_lp=model,
            baseline_config=OptimizationConfig(solver_mode="baseline"),
            optimized_config=OptimizationConfig(solver_mode="parallel"),
        )

        print(result.get_summary())
    """

    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize solver comparison framework.

        Args:
            output_dir: Directory for saving comparison results
        """
        self.output_dir = output_dir or Path("solver_comparisons")
        self.output_dir.mkdir(exist_ok=True, parents=True)

        self.benchmark = SolverBenchmark(output_dir=self.output_dir)
        self.results: List[SolverComparisonResult] = []

    def compare_solvers(
        self,
        trade_lp: tlp.TradeLPModel,
        baseline_config: OptimizationConfig,
        optimized_config: OptimizationConfig,
        tolerance: Optional[float] = None,
    ) -> SolverComparisonResult:
        """
        Compare two solver implementations.

        Args:
            trade_lp: Trade LP model to solve
            baseline_config: Configuration for baseline solver
            optimized_config: Configuration for optimized solver
            tolerance: Accuracy tolerance (uses config default if None)

        Returns:
            SolverComparisonResult with detailed comparison
        """
        logger.info(
            f"Comparing solvers: {baseline_config.solver_mode} vs {optimized_config.solver_mode}"
        )

        tolerance = tolerance or optimized_config.tolerance

        # Get problem size
        problem_size = self._get_problem_size(trade_lp)
        logger.info(f"Problem size: {problem_size}")

        # Solve with baseline
        logger.info(f"Solving with baseline ({baseline_config.solver_mode})...")
        baseline_solver = SolverFactory(baseline_config).create_solver()
        baseline_result = self.benchmark.profile(
            func=baseline_solver,
            component="solver_comparison",
            operation="baseline_solve",
            args=(trade_lp,),
            problem_size=problem_size,
            config={"mode": baseline_config.solver_mode},
        )

        # Extract baseline solution
        trade_lp.extract_solution()
        baseline_allocations = self._extract_allocations(trade_lp)
        baseline_objective = self._get_objective_value(trade_lp)

        # Solve with optimized
        logger.info(f"Solving with optimized ({optimized_config.solver_mode})...")
        optimized_solver = SolverFactory(optimized_config).create_solver()
        optimized_result = self.benchmark.profile(
            func=optimized_solver,
            component="solver_comparison",
            operation="optimized_solve",
            args=(trade_lp,),
            problem_size=problem_size,
            config={"mode": optimized_config.solver_mode},
        )

        # Extract optimized solution
        trade_lp.extract_solution()
        optimized_allocations = self._extract_allocations(trade_lp)
        optimized_objective = self._get_objective_value(trade_lp)

        # Compare solutions
        accuracy = self._compare_allocations(
            baseline_allocations, optimized_allocations, tolerance
        )

        # Calculate speedup and memory reduction
        speedup = baseline_result.duration_ms / max(optimized_result.duration_ms, 0.001)
        memory_reduction = (
            (baseline_result.peak_memory_mb - optimized_result.peak_memory_mb)
            / max(baseline_result.peak_memory_mb, 0.001)
            * 100
        )

        # Calculate objective difference
        objective_diff = None
        if baseline_objective is not None and optimized_objective is not None:
            objective_diff = abs(optimized_objective - baseline_objective)

        # Create comparison result
        comparison_result = SolverComparisonResult(
            baseline_mode=baseline_config.solver_mode,
            optimized_mode=optimized_config.solver_mode,
            baseline_time_ms=baseline_result.duration_ms,
            optimized_time_ms=optimized_result.duration_ms,
            speedup=speedup,
            baseline_memory_mb=baseline_result.peak_memory_mb,
            optimized_memory_mb=optimized_result.peak_memory_mb,
            memory_reduction=memory_reduction,
            max_relative_diff=accuracy["max_relative_diff"],
            violations=accuracy["violations"],
            tolerance=tolerance,
            accuracy_passed=accuracy["passed"],
            baseline_objective=baseline_objective,
            optimized_objective=optimized_objective,
            objective_diff=objective_diff,
            problem_size=problem_size,
        )

        self.results.append(comparison_result)

        # Log summary
        logger.info(f"\n{comparison_result.get_summary()}\n")

        return comparison_result

    def _get_problem_size(self, trade_lp: tlp.TradeLPModel) -> Dict[str, Any]:
        """Extract problem size metrics from trade LP model."""
        try:
            return {
                "process_centers": len(trade_lp.process_centers),
                "commodities": len(trade_lp.commodities),
                "legal_allocations": len(trade_lp.legal_allocations) if hasattr(trade_lp, "legal_allocations") else 0,
            }
        except Exception as e:
            logger.warning(f"Could not extract problem size: {e}")
            return {}

    def _extract_allocations(self, trade_lp: tlp.TradeLPModel) -> Dict[Tuple, float]:
        """Extract allocation values from solved trade LP model."""
        allocations = {}

        if trade_lp.allocations is None:
            logger.warning("No allocations found in trade LP model")
            return allocations

        try:
            for (from_pc, to_pc, comm), value in trade_lp.allocations.allocations.items():
                # Create hashable key
                key = (from_pc.name, to_pc.name, comm.name)
                allocations[key] = float(value)
        except Exception as e:
            logger.warning(f"Error extracting allocations: {e}")

        return allocations

    def _get_objective_value(self, trade_lp: tlp.TradeLPModel) -> Optional[float]:
        """Get objective value from solved trade LP model."""
        try:
            if hasattr(trade_lp, "lp_model") and hasattr(trade_lp.lp_model, "objective"):
                import pyomo.environ as pyo

                return pyo.value(trade_lp.lp_model.objective)
        except Exception as e:
            logger.warning(f"Could not extract objective value: {e}")

        return None

    def _compare_allocations(
        self,
        baseline: Dict[Tuple, float],
        optimized: Dict[Tuple, float],
        tolerance: float,
    ) -> Dict[str, Any]:
        """
        Compare allocation dictionaries for accuracy validation.

        Args:
            baseline: Baseline allocations
            optimized: Optimized allocations
            tolerance: Relative tolerance for comparison

        Returns:
            Dictionary with comparison results
        """
        baseline_keys = set(baseline.keys())
        optimized_keys = set(optimized.keys())

        missing_in_optimized = baseline_keys - optimized_keys
        extra_in_optimized = optimized_keys - baseline_keys

        max_relative_diff = 0.0
        violations = 0

        # Compare common keys
        for key in baseline_keys & optimized_keys:
            baseline_val = baseline[key]
            optimized_val = optimized[key]

            # Skip near-zero values
            if abs(baseline_val) < 1e-6 and abs(optimized_val) < 1e-6:
                continue

            # Calculate relative difference
            if abs(baseline_val) > 1e-10:
                rel_diff = abs(optimized_val - baseline_val) / abs(baseline_val)
            else:
                rel_diff = abs(optimized_val - baseline_val)

            max_relative_diff = max(max_relative_diff, rel_diff)

            if rel_diff > tolerance:
                violations += 1

        # Log mismatches
        if missing_in_optimized:
            logger.warning(
                f"{len(missing_in_optimized)} allocations missing in optimized solution"
            )
        if extra_in_optimized:
            logger.warning(
                f"{len(extra_in_optimized)} extra allocations in optimized solution"
            )

        # Determine if validation passed
        passed = (
            len(missing_in_optimized) == 0
            and len(extra_in_optimized) == 0
            and max_relative_diff <= tolerance
        )

        return {
            "passed": passed,
            "max_relative_diff": max_relative_diff,
            "violations": violations,
            "missing_in_optimized": len(missing_in_optimized),
            "extra_in_optimized": len(extra_in_optimized),
        }

    def export_results(self, filename: str = "solver_comparison_results.csv") -> Path:
        """
        Export comparison results to CSV.

        Args:
            filename: Output filename

        Returns:
            Path to exported file
        """
        if not self.results:
            logger.warning("No results to export")
            return None

        # Convert to DataFrame
        data = []
        for result in self.results:
            row = {
                "baseline_mode": result.baseline_mode,
                "optimized_mode": result.optimized_mode,
                "baseline_time_ms": result.baseline_time_ms,
                "optimized_time_ms": result.optimized_time_ms,
                "speedup": result.speedup,
                "baseline_memory_mb": result.baseline_memory_mb,
                "optimized_memory_mb": result.optimized_memory_mb,
                "memory_reduction_pct": result.memory_reduction,
                "max_relative_diff": result.max_relative_diff,
                "violations": result.violations,
                "tolerance": result.tolerance,
                "accuracy_passed": result.accuracy_passed,
                "baseline_objective": result.baseline_objective,
                "optimized_objective": result.optimized_objective,
                "objective_diff": result.objective_diff,
                "timestamp": result.timestamp,
                **result.problem_size,
            }
            data.append(row)

        df = pd.DataFrame(data)

        # Export
        output_path = self.output_dir / filename
        df.to_csv(output_path, index=False)

        logger.info(f"Exported {len(self.results)} comparison results to {output_path}")

        return output_path

    def generate_report(self, filename: str = "solver_comparison_report.md") -> Path:
        """
        Generate markdown report with comparison results.

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
            f.write("# Steel-IQ Solver Comparison Report\n\n")
            f.write(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            # Summary table
            f.write("## Summary\n\n")
            f.write("| Baseline | Optimized | Speedup | Memory Reduction | Accuracy | Status |\n")
            f.write("|----------|-----------|---------|------------------|----------|--------|\n")

            for result in self.results:
                status = "✓ Pass" if result.accuracy_passed else "✗ Fail"
                f.write(
                    f"| {result.baseline_mode} | {result.optimized_mode} | "
                    f"{result.speedup:.2f}x | {result.memory_reduction:.1f}% | "
                    f"{result.max_relative_diff*100:.2f}% | {status} |\n"
                )

            f.write("\n")

            # Detailed results
            f.write("## Detailed Results\n\n")
            for i, result in enumerate(self.results, 1):
                f.write(f"### Comparison {i}: {result.baseline_mode} vs {result.optimized_mode}\n\n")
                f.write("```\n")
                f.write(result.get_summary())
                f.write("\n```\n\n")

        logger.info(f"Generated comparison report: {output_path}")

        return output_path


def compare_all_solvers(
    trade_lp: tlp.TradeLPModel,
    tolerance: float = 0.01,
    output_dir: Optional[Path] = None,
) -> List[SolverComparisonResult]:
    """
    Compare all available solver implementations against baseline.

    This is a convenience function that compares baseline against all
    optimized solver modes (parallel, gasplan, parallel_gasplan).

    Args:
        trade_lp: Trade LP model to solve
        tolerance: Accuracy tolerance for validation (default 1%)
        output_dir: Directory for saving results

    Returns:
        List of SolverComparisonResult objects
    """
    comparison = SolverComparison(output_dir=output_dir)

    baseline_config = OptimizationConfig(solver_mode="baseline")

    # Compare each optimized mode
    optimized_modes = ["parallel", "gasplan", "parallel_gasplan"]
    results = []

    for mode in optimized_modes:
        logger.info(f"\n{'='*60}")
        logger.info(f"Comparing baseline vs {mode}")
        logger.info(f"{'='*60}\n")

        optimized_config = OptimizationConfig(
            solver_mode=mode,  # type: ignore
            check_accuracy=True,
            tolerance=tolerance,
        )

        try:
            result = comparison.compare_solvers(
                trade_lp=trade_lp,
                baseline_config=baseline_config,
                optimized_config=optimized_config,
                tolerance=tolerance,
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Error comparing {mode}: {e}")

    # Export results
    comparison.export_results()
    comparison.generate_report()

    return results


def generate_comparison_report(
    results: List[SolverComparisonResult],
    output_path: Path,
) -> None:
    """
    Generate standalone comparison report from results.

    Args:
        results: List of comparison results
        output_path: Path to save report
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("# Steel-IQ Solver Comparison Report\n\n")
        f.write(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        # Summary
        f.write("## Executive Summary\n\n")
        f.write("| Solver | Speedup | Memory | Accuracy | Status |\n")
        f.write("|--------|---------|--------|----------|--------|\n")

        for result in results:
            status = "✓" if result.accuracy_passed else "✗"
            f.write(
                f"| {result.optimized_mode} | {result.speedup:.2f}x | "
                f"{result.memory_reduction:.1f}% | {result.max_relative_diff*100:.2f}% | {status} |\n"
            )

        f.write("\n")

        # Recommendations
        f.write("## Recommendations\n\n")

        # Find best solver
        passing = [r for r in results if r.accuracy_passed]
        if passing:
            best = max(passing, key=lambda r: r.speedup)
            f.write(f"**Recommended solver**: `{best.optimized_mode}`\n")
            f.write(f"- Speedup: {best.speedup:.2f}x\n")
            f.write(f"- Memory reduction: {best.memory_reduction:.1f}%\n")
            f.write(f"- Accuracy: {best.max_relative_diff*100:.2f}%\n\n")
        else:
            f.write("**Warning**: No optimized solvers passed accuracy validation.\n\n")

        # Details
        f.write("## Detailed Results\n\n")
        for result in results:
            f.write(f"### {result.optimized_mode}\n\n")
            f.write("```\n")
            f.write(result.get_summary())
            f.write("\n```\n\n")

    logger.info(f"Generated comparison report: {output_path}")
