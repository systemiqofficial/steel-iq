"""
Solver factory for Steel-IQ trade optimization.

This module provides a factory for creating different solver implementations
based on configuration. It enables drop-in replacement of solvers for
performance optimization and experimentation.
"""

import logging
from typing import Any, Callable, Dict, Optional

from steelo.config.optimization_config import OptimizationConfig
from steelo.domain.trade_modelling import trade_lp_modelling as tlp

logger = logging.getLogger(__name__)


class SolverFactory:
    """
    Factory for creating optimized solver implementations.

    The SolverFactory provides a unified interface for creating different
    solver implementations based on configuration. This enables easy
    switching between baseline, parallel, gasplan, and GPU-accelerated
    variants without changing application code.

    Usage:
        config = OptimizationConfig(solver_mode="parallel")
        factory = SolverFactory(config)

        # Create solver function
        solve_func = factory.create_solver()

        # Use it to solve LP model
        result = solve_func(trade_lp_model)
    """

    def __init__(self, config: Optional[OptimizationConfig] = None):
        """
        Initialize SolverFactory.

        Args:
            config: Optimization configuration. If None, uses defaults.
        """
        self.config = config or OptimizationConfig()
        logger.info(f"Initialized SolverFactory with mode: {self.config.solver_mode}")

    def create_solver(self) -> Callable[[tlp.TradeLPModel], Any]:
        """
        Create solver function based on configuration.

        Returns:
            Callable that takes TradeLPModel and returns solver result

        Raises:
            ValueError: If solver mode is not supported
        """
        mode = self.config.solver_mode

        if mode == "baseline":
            return self._create_baseline_solver()
        elif mode == "parallel":
            return self._create_parallel_solver()
        elif mode == "gasplan":
            return self._create_gasplan_solver()
        elif mode == "parallel_gasplan":
            return self._create_parallel_gasplan_solver()
        else:
            raise ValueError(f"Unknown solver mode: {mode}")

    def _create_baseline_solver(self) -> Callable[[tlp.TradeLPModel], Any]:
        """
        Create baseline Pyomo/HiGHS solver.

        This is the standard solver implementation using Pyomo with HiGHS.
        It provides the reference implementation for accuracy validation.

        Returns:
            Baseline solver function
        """
        logger.info("Creating baseline solver (Pyomo/HiGHS)")

        def solve_baseline(trade_lp: tlp.TradeLPModel) -> Any:
            """
            Solve LP model using baseline implementation.

            Args:
                trade_lp: Trade LP model to solve

            Returns:
                Solver result object
            """
            logger.info("Solving with baseline solver...")
            result = trade_lp.solve_lp_model()
            logger.info(f"Baseline solver completed: {result.solver.termination_condition}")
            return result

        return solve_baseline

    def _create_parallel_solver(self) -> Callable[[tlp.TradeLPModel], Any]:
        """
        Create parallel LP solver.

        This solver uses parallel decomposition techniques to speed up
        large LP problems by solving subproblems in parallel.

        Note:
            This is a placeholder for future implementation (Phase 2-3).
            Currently delegates to baseline solver with a warning.

        Returns:
            Parallel solver function
        """
        logger.warning(
            "Parallel solver not yet implemented. "
            "Falling back to baseline solver. "
            "To implement: see docs/parallel_implementation.md"
        )

        # Future implementation would include:
        # - Benders decomposition for network structure
        # - Parallel subproblem solving
        # - Master problem coordination
        # - Worker pool management

        # For now, use baseline with parallel hint in options
        def solve_parallel(trade_lp: tlp.TradeLPModel) -> Any:
            """
            Solve LP model using parallel techniques (future).

            Args:
                trade_lp: Trade LP model to solve

            Returns:
                Solver result object
            """
            logger.info(f"Parallel solver requested with {self.config.workers} workers")
            logger.info("Using baseline solver (parallel not yet implemented)")

            # Set threading options if supported by solver
            # Note: HiGHS supports threads parameter
            result = trade_lp.solve_lp_model()
            return result

        return solve_parallel

    def _create_gasplan_solver(self) -> Callable[[tlp.TradeLPModel], Any]:
        """
        Create Gasplan network solver.

        Gasplan is a specialized network solver for minimum-cost flow
        problems. It's particularly efficient for trade network optimization
        due to the problem structure.

        Note:
            This is a placeholder for future implementation (Phase 4).
            Currently delegates to baseline solver with a warning.

        Returns:
            Gasplan solver function
        """
        logger.warning(
            "Gasplan solver not yet implemented. "
            "Falling back to baseline solver. "
            "To implement: see docs/gasplan_integration.md"
        )

        # Future implementation would include:
        # - Convert Pyomo model to Gasplan format
        # - Call Gasplan network solver
        # - Convert solution back to Pyomo format
        # - Handle basis information for warm starts

        def solve_gasplan(trade_lp: tlp.TradeLPModel) -> Any:
            """
            Solve LP model using Gasplan network solver (future).

            Args:
                trade_lp: Trade LP model to solve

            Returns:
                Solver result object
            """
            logger.info("Gasplan solver requested")
            logger.info("Using baseline solver (Gasplan not yet implemented)")
            result = trade_lp.solve_lp_model()
            return result

        return solve_gasplan

    def _create_parallel_gasplan_solver(self) -> Callable[[tlp.TradeLPModel], Any]:
        """
        Create combined parallel + Gasplan solver.

        This solver combines parallel decomposition with Gasplan's efficient
        network solving. It uses parallel decomposition for the outer problem
        and Gasplan for network subproblems.

        Note:
            This is a placeholder for future implementation (Phase 5).
            Currently delegates to baseline solver with a warning.

        Returns:
            Combined parallel+Gasplan solver function
        """
        logger.warning(
            "Parallel+Gasplan solver not yet implemented. "
            "Falling back to baseline solver. "
            "To implement: see docs/parallel_gasplan_implementation.md"
        )

        # Future implementation would include:
        # - Parallel decomposition strategy
        # - Gasplan for each subproblem
        # - Efficient coordination
        # - Load balancing across workers

        def solve_parallel_gasplan(trade_lp: tlp.TradeLPModel) -> Any:
            """
            Solve LP model using parallel decomposition + Gasplan (future).

            Args:
                trade_lp: Trade LP model to solve

            Returns:
                Solver result object
            """
            logger.info(
                f"Parallel+Gasplan solver requested with {self.config.workers} workers"
            )
            logger.info("Using baseline solver (parallel+Gasplan not yet implemented)")
            result = trade_lp.solve_lp_model()
            return result

        return solve_parallel_gasplan

    def validate_configuration(self) -> Dict[str, Any]:
        """
        Validate solver configuration and check hardware capabilities.

        Returns:
            Dictionary with validation results:
                - valid: Whether configuration is valid
                - warnings: List of warning messages
                - hardware: Detected hardware capabilities
        """
        warnings = []
        hardware = {}

        # Check for GPU if GPU preprocessing is enabled
        if self.config.gpu_preprocessing:
            try:
                import torch

                if torch.cuda.is_available():
                    hardware["gpu"] = "cuda"
                    hardware["gpu_name"] = torch.cuda.get_device_name(0)
                elif torch.backends.mps.is_available():
                    hardware["gpu"] = "mps"
                    hardware["gpu_name"] = "Apple Silicon MPS"
                else:
                    warnings.append(
                        "GPU preprocessing enabled but no GPU detected. "
                        "Will fall back to CPU."
                    )
                    hardware["gpu"] = None
            except ImportError:
                warnings.append(
                    "GPU preprocessing enabled but PyTorch not installed. "
                    "Install PyTorch for GPU support."
                )
                hardware["gpu"] = None

        # Check worker count
        if isinstance(self.config.workers, int):
            import multiprocessing

            cpu_count = multiprocessing.cpu_count()
            hardware["cpu_count"] = cpu_count

            if self.config.workers > cpu_count:
                warnings.append(
                    f"Configured {self.config.workers} workers but only "
                    f"{cpu_count} CPUs available. Consider reducing workers."
                )

        # Check if advanced solvers are requested
        if self.config.solver_mode != "baseline":
            warnings.append(
                f"Advanced solver mode '{self.config.solver_mode}' requested "
                "but not yet implemented. Using baseline solver."
            )

        # Log warnings
        for warning in warnings:
            logger.warning(warning)

        return {
            "valid": True,
            "warnings": warnings,
            "hardware": hardware,
        }

    def get_solver_info(self) -> Dict[str, Any]:
        """
        Get information about the configured solver.

        Returns:
            Dictionary with solver information:
                - mode: Solver mode
                - workers: Number of workers
                - gpu_enabled: Whether GPU is enabled
                - device: Geospatial device
                - implemented: Whether solver is fully implemented
        """
        return {
            "mode": self.config.solver_mode,
            "workers": self.config.workers,
            "gpu_enabled": self.config.gpu_preprocessing,
            "device": self.config.geospatial_device,
            "implemented": self.config.solver_mode == "baseline",
            "fallback_to_baseline": self.config.solver_mode != "baseline",
        }


def create_solver_from_config(
    config: Optional[OptimizationConfig] = None,
) -> Callable[[tlp.TradeLPModel], Any]:
    """
    Convenience function to create solver from configuration.

    This is a shorthand for:
        factory = SolverFactory(config)
        solver = factory.create_solver()

    Args:
        config: Optimization configuration. If None, uses defaults.

    Returns:
        Solver function that takes TradeLPModel and returns result
    """
    factory = SolverFactory(config)
    return factory.create_solver()


def get_available_solver_modes() -> list[str]:
    """
    Get list of available solver modes.

    Returns:
        List of solver mode names
    """
    return ["baseline", "parallel", "gasplan", "parallel_gasplan"]


def is_solver_mode_implemented(mode: str) -> bool:
    """
    Check if a solver mode is fully implemented.

    Args:
        mode: Solver mode to check

    Returns:
        True if implemented, False if placeholder/fallback
    """
    # Only baseline is currently implemented
    return mode == "baseline"
