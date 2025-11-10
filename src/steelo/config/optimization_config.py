"""
Optimization configuration for Steel-IQ solvers.

This module provides configuration settings for different solver implementations,
including baseline, parallel, gasplan, and GPU-accelerated variants.
"""

import logging
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union

import yaml

logger = logging.getLogger(__name__)

# Valid solver modes
SolverMode = Literal["baseline", "parallel", "gasplan", "parallel_gasplan"]

# Valid device types
DeviceType = Literal["cpu", "mps", "cuda", "auto"]


@dataclass
class OptimizationConfig:
    """
    Configuration for Steel-IQ optimization solvers.

    This configuration allows switching between different solver implementations
    (baseline, parallel, gasplan, parallel+gasplan) and tuning their behavior.

    Attributes:
        solver_mode: Which solver implementation to use
            - baseline: Standard Pyomo/HiGHS solver (default)
            - parallel: Parallelized LP solve (future)
            - gasplan: Gasplan network solver (future)
            - parallel_gasplan: Combined parallel + gasplan (future)

        workers: Number of parallel workers
            - "auto": Auto-detect based on CPU count (default)
            - int: Specific number of workers

        gpu_preprocessing: Whether to use GPU for preprocessing
            - False: CPU-only (default)
            - True: Use GPU if available

        geospatial_device: Device for geospatial computations
            - "auto": Auto-detect (MPS on M-series, CPU otherwise)
            - "cpu": Force CPU
            - "mps": Force Apple Metal Performance Shaders
            - "cuda": Force NVIDIA CUDA

        check_accuracy: Whether to validate optimized solvers against baseline
        tolerance: Relative tolerance for accuracy validation (default 1%)

        detect_apple_silicon: Auto-detect Apple Silicon hardware
        optimize_for_m4: Enable M4-specific optimizations if available

        solver_options: Additional solver-specific options
    """

    # LP Solver configuration
    solver_mode: SolverMode = "baseline"
    workers: Union[str, int] = "auto"
    gpu_preprocessing: bool = False

    # Geospatial configuration
    geospatial_device: DeviceType = "auto"

    # Validation configuration
    check_accuracy: bool = True
    tolerance: float = 0.01  # 1%

    # Hardware detection
    detect_apple_silicon: bool = True
    optimize_for_m4: bool = True

    # Additional solver options
    solver_options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self._validate()
        self._resolve_auto_settings()

    def _validate(self) -> None:
        """Validate configuration settings."""
        # Validate solver_mode
        valid_modes = ["baseline", "parallel", "gasplan", "parallel_gasplan"]
        if self.solver_mode not in valid_modes:
            raise ValueError(
                f"Invalid solver_mode '{self.solver_mode}'. "
                f"Must be one of: {', '.join(valid_modes)}"
            )

        # Validate workers
        if isinstance(self.workers, str) and self.workers != "auto":
            raise ValueError(
                f"Invalid workers value '{self.workers}'. "
                "Must be 'auto' or a positive integer."
            )
        if isinstance(self.workers, int) and self.workers < 1:
            raise ValueError("workers must be at least 1")

        # Validate geospatial_device
        valid_devices = ["cpu", "mps", "cuda", "auto"]
        if self.geospatial_device not in valid_devices:
            raise ValueError(
                f"Invalid geospatial_device '{self.geospatial_device}'. "
                f"Must be one of: {', '.join(valid_devices)}"
            )

        # Validate tolerance
        if self.tolerance < 0 or self.tolerance > 1:
            raise ValueError("tolerance must be between 0 and 1")

    def _resolve_auto_settings(self) -> None:
        """Resolve 'auto' settings based on hardware detection."""
        # Auto-detect workers
        if self.workers == "auto":
            import multiprocessing

            self.workers = multiprocessing.cpu_count()
            logger.info(f"Auto-detected {self.workers} CPU cores")

        # Auto-detect geospatial device
        if self.geospatial_device == "auto":
            self.geospatial_device = self._detect_best_device()
            logger.info(f"Auto-detected geospatial device: {self.geospatial_device}")

    def _detect_best_device(self) -> DeviceType:
        """
        Detect the best available device for computation.

        Returns:
            Device type: "mps" for Apple Silicon, "cuda" for NVIDIA GPUs, "cpu" otherwise
        """
        # Check for Apple Silicon
        if self.detect_apple_silicon and self._is_apple_silicon():
            try:
                import torch

                if torch.backends.mps.is_available():
                    logger.info("Apple Silicon detected with MPS support")
                    return "mps"
            except ImportError:
                logger.warning("Apple Silicon detected but PyTorch not available")

        # Check for CUDA
        try:
            import torch

            if torch.cuda.is_available():
                logger.info("NVIDIA CUDA GPU detected")
                return "cuda"
        except ImportError:
            pass

        logger.info("No GPU acceleration detected, using CPU")
        return "cpu"

    def _is_apple_silicon(self) -> bool:
        """
        Check if running on Apple Silicon.

        Returns:
            True if running on Apple Silicon (M-series chips)
        """
        if platform.system() != "Darwin":
            return False

        try:
            # Check for ARM architecture
            machine = platform.machine().lower()
            if "arm" in machine or machine == "arm64":
                # Further verify it's Apple Silicon
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                cpu_info = result.stdout.strip().lower()
                if "apple" in cpu_info:
                    # Check for M4 specifically
                    if self.optimize_for_m4 and "m4" in cpu_info:
                        logger.info("Apple M4 chip detected")
                    return True
        except Exception as e:
            logger.debug(f"Error detecting Apple Silicon: {e}")

        return False

    def get_solver_config(self) -> Dict[str, Any]:
        """
        Get solver-specific configuration dictionary.

        Returns:
            Dictionary of solver options suitable for passing to solver factory
        """
        config = {
            "mode": self.solver_mode,
            "workers": self.workers,
            "gpu_preprocessing": self.gpu_preprocessing,
            "geospatial_device": self.geospatial_device,
            **self.solver_options,
        }
        return config

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "OptimizationConfig":
        """
        Create OptimizationConfig from a dictionary.

        Args:
            config_dict: Configuration dictionary

        Returns:
            OptimizationConfig instance
        """
        # Extract nested configuration if present
        if "optimization" in config_dict:
            config_dict = config_dict["optimization"]

        # Flatten nested structure if needed
        flat_config = {}
        if "lp_solver" in config_dict:
            flat_config.update(
                {
                    "solver_mode": config_dict["lp_solver"].get("mode", "baseline"),
                    "workers": config_dict["lp_solver"].get("workers", "auto"),
                    "gpu_preprocessing": config_dict["lp_solver"].get("gpu_preprocessing", False),
                }
            )

        if "geospatial" in config_dict:
            flat_config.update(
                {
                    "geospatial_device": config_dict["geospatial"].get("device", "auto"),
                }
            )

        if "validation" in config_dict:
            flat_config.update(
                {
                    "check_accuracy": config_dict["validation"].get("check_accuracy", True),
                    "tolerance": config_dict["validation"].get("tolerance", 0.01),
                }
            )

        # Merge with top-level config
        for key in ["detect_apple_silicon", "optimize_for_m4"]:
            if key in config_dict:
                flat_config[key] = config_dict[key]

        return cls(**flat_config)

    @classmethod
    def from_yaml(cls, yaml_path: Union[str, Path]) -> "OptimizationConfig":
        """
        Load OptimizationConfig from a YAML file.

        Args:
            yaml_path: Path to YAML configuration file

        Returns:
            OptimizationConfig instance

        Raises:
            FileNotFoundError: If YAML file doesn't exist
            yaml.YAMLError: If YAML is malformed
        """
        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

        with open(yaml_path, "r") as f:
            config_dict = yaml.safe_load(f)

        return cls.from_dict(config_dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert OptimizationConfig to dictionary.

        Returns:
            Configuration dictionary
        """
        return {
            "lp_solver": {
                "mode": self.solver_mode,
                "workers": self.workers,
                "gpu_preprocessing": self.gpu_preprocessing,
            },
            "geospatial": {
                "device": self.geospatial_device,
            },
            "validation": {
                "check_accuracy": self.check_accuracy,
                "tolerance": self.tolerance,
            },
            "detect_apple_silicon": self.detect_apple_silicon,
            "optimize_for_m4": self.optimize_for_m4,
            "solver_options": self.solver_options,
        }

    def to_yaml(self, yaml_path: Union[str, Path]) -> None:
        """
        Save OptimizationConfig to YAML file.

        Args:
            yaml_path: Path to save YAML configuration
        """
        yaml_path = Path(yaml_path)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)

        with open(yaml_path, "w") as f:
            yaml.dump({"optimization": self.to_dict()}, f, default_flow_style=False)

        logger.info(f"Configuration saved to {yaml_path}")


def create_default_config() -> OptimizationConfig:
    """
    Create default optimization configuration.

    Returns:
        OptimizationConfig with default settings
    """
    return OptimizationConfig()


def create_performance_config() -> OptimizationConfig:
    """
    Create performance-optimized configuration.

    This configuration enables all available optimizations:
    - Parallel solving
    - GPU preprocessing
    - Auto hardware detection

    Returns:
        OptimizationConfig optimized for performance
    """
    return OptimizationConfig(
        solver_mode="parallel_gasplan",
        workers="auto",
        gpu_preprocessing=True,
        geospatial_device="auto",
        check_accuracy=True,
        tolerance=0.01,
        detect_apple_silicon=True,
        optimize_for_m4=True,
    )
