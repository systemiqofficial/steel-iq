#!/usr/bin/env python3
"""
Steel-IQ Optimization Setup and Test Script

This script sets up the environment and runs comprehensive tests for all
optimization implementations.

Usage:
    python scripts/setup_and_test.py --all
    python scripts/setup_and_test.py --check-only
    python scripts/setup_and_test.py --benchmark
"""

import argparse
import logging
import platform
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Color:
    """ANSI color codes for terminal output."""

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_header(text: str):
    """Print a formatted header."""
    logger.info(f"\n{Color.BOLD}{Color.BLUE}{'=' * 70}{Color.END}")
    logger.info(f"{Color.BOLD}{Color.BLUE}{text:^70}{Color.END}")
    logger.info(f"{Color.BOLD}{Color.BLUE}{'=' * 70}{Color.END}\n")


def print_success(text: str):
    """Print a success message."""
    logger.info(f"{Color.GREEN}✓ {text}{Color.END}")


def print_warning(text: str):
    """Print a warning message."""
    logger.warning(f"{Color.YELLOW}⚠ {text}{Color.END}")


def print_error(text: str):
    """Print an error message."""
    logger.error(f"{Color.RED}✗ {text}{Color.END}")


def check_python_version() -> bool:
    """Check if Python version is compatible."""
    print_header("Checking Python Version")

    version = sys.version_info
    logger.info(f"Python version: {version.major}.{version.minor}.{version.micro}")

    if version.major == 3 and version.minor >= 9:
        print_success(f"Python {version.major}.{version.minor} is compatible")
        return True
    else:
        print_error(f"Python 3.9+ required, found {version.major}.{version.minor}")
        return False


def check_hardware() -> Dict[str, any]:
    """Detect and report hardware configuration."""
    print_header("Detecting Hardware")

    try:
        from steelo.benchmarking.hardware_detector import detect_apple_silicon

        hw = detect_apple_silicon()
        logger.info(f"Hardware detected: {hw}")

        if hw.is_apple_silicon:
            print_success(f"Apple Silicon detected: {hw.chip_model}")
            logger.info(f"  - Cores: {hw.performance_cores}P + {hw.efficiency_cores}E")
            logger.info(f"  - Memory: {hw.total_memory_gb:.1f} GB")
            logger.info(f"  - Optimal workers (parallel): {hw.optimal_workers('parallel')}")
        else:
            print_warning("Not Apple Silicon, will use CPU optimizations")
            logger.info(f"  - Cores: {hw.total_cores}")
            logger.info(f"  - Memory: {hw.total_memory_gb:.1f} GB")

        return {
            "is_apple_silicon": hw.is_apple_silicon,
            "chip_model": hw.chip_model,
            "total_cores": hw.total_cores,
            "performance_cores": hw.performance_cores,
            "workers": hw.optimal_workers('parallel'),
        }

    except ImportError as e:
        print_error(f"Failed to import hardware detector: {e}")
        return {"error": str(e)}


def check_pytorch() -> Tuple[bool, str]:
    """Check if PyTorch is installed with Metal support."""
    print_header("Checking PyTorch Installation")

    try:
        import torch

        version = torch.__version__
        logger.info(f"PyTorch version: {version}")

        # Check Metal (MPS) support
        if torch.backends.mps.is_available():
            print_success("PyTorch with Metal Performance Shaders (MPS) is available")
            logger.info("  - MPS device can be used for GPU acceleration")
            return True, "mps"

        # Check CUDA support
        elif torch.cuda.is_available():
            print_success(f"PyTorch with CUDA is available (GPU: {torch.cuda.get_device_name(0)})")
            return True, "cuda"

        # CPU only
        else:
            print_warning("PyTorch installed but no GPU support detected")
            logger.info("  - Will use CPU for all operations")
            return True, "cpu"

    except ImportError:
        print_error("PyTorch not installed")
        logger.info("\nTo install PyTorch:")
        logger.info("  pip install torch>=2.0.0")
        return False, "none"


def check_dependencies() -> bool:
    """Check all required dependencies."""
    print_header("Checking Dependencies")

    required = {
        "pyomo": "Pyomo",
        "pandas": "Pandas",
        "numpy": "NumPy",
        "scipy": "SciPy",
    }

    optional = {
        "torch": "PyTorch (for GPU acceleration)",
    }

    all_ok = True

    # Check required
    logger.info("Required dependencies:")
    for module, name in required.items():
        try:
            __import__(module)
            print_success(f"{name} installed")
        except ImportError:
            print_error(f"{name} NOT installed")
            all_ok = False

    # Check optional
    logger.info("\nOptional dependencies:")
    for module, name in optional.items():
        try:
            __import__(module)
            print_success(f"{name} installed")
        except ImportError:
            print_warning(f"{name} NOT installed (optional)")

    return all_ok


def test_hardware_detection():
    """Test hardware detection module."""
    print_header("Testing Hardware Detection")

    try:
        from steelo.benchmarking.hardware_detector import (
            detect_apple_silicon,
            HardwareInfo,
        )

        hw = detect_apple_silicon()

        assert isinstance(hw, HardwareInfo), "Should return HardwareInfo"
        assert hw.total_cores > 0, "Should detect cores"
        assert hw.total_memory_gb > 0, "Should detect memory"

        # Test worker calculation
        workers_parallel = hw.optimal_workers('parallel')
        workers_max = hw.optimal_workers('max')

        assert workers_parallel > 0, "Should calculate parallel workers"
        assert workers_max > 0, "Should calculate max workers"

        print_success("Hardware detection tests passed")
        return True

    except Exception as e:
        print_error(f"Hardware detection tests failed: {e}")
        return False


def test_benchmarking_framework():
    """Test benchmarking framework."""
    print_header("Testing Benchmarking Framework")

    try:
        from steelo.benchmarking.performance_profiler import (
            SolverBenchmark,
            BenchmarkResults,
        )

        # Test profiling
        benchmark = SolverBenchmark()

        def dummy_function(x):
            return x * 2

        result = benchmark.profile(
            func=dummy_function,
            component="test",
            operation="multiply",
            args=(5,),
            iterations=3,
        )

        assert isinstance(result, BenchmarkResults), "Should return BenchmarkResults"
        assert result.component == "test", "Should track component"
        assert result.operation == "multiply", "Should track operation"
        assert result.iterations == 3, "Should track iterations"
        assert result.duration_ms > 0, "Should measure time"

        print_success("Benchmarking framework tests passed")
        return True

    except Exception as e:
        print_error(f"Benchmarking tests failed: {e}")
        return False


def test_configuration_system():
    """Test configuration system."""
    print_header("Testing Configuration System")

    try:
        from steelo.config.optimization_config import OptimizationConfig

        # Test default config
        config = OptimizationConfig()
        assert config.lp_solver_mode == "baseline", "Default should be baseline"

        # Test custom config
        config = OptimizationConfig(
            lp_solver_mode="parallel",
            workers=8,
            gpu_preprocessing=True,
        )
        assert config.lp_solver_mode == "parallel"
        assert config.workers == 8
        assert config.gpu_preprocessing is True

        # Test YAML loading
        import tempfile
        import yaml

        test_config = {
            "optimization": {
                "lp_solver": {
                    "mode": "gasplan",
                    "workers": "auto",
                },
                "validation": {
                    "check_accuracy": True,
                    "tolerance": 0.01,
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(test_config, f)
            yaml_path = f.name

        config = OptimizationConfig.from_yaml(yaml_path)
        assert config.lp_solver_mode == "gasplan"
        assert config.check_accuracy is True
        assert config.tolerance == 0.01

        Path(yaml_path).unlink()  # Clean up

        print_success("Configuration system tests passed")
        return True

    except Exception as e:
        print_error(f"Configuration tests failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_gpu_availability():
    """Test GPU module availability and device detection."""
    print_header("Testing GPU Modules")

    try:
        from steelo.domain.trade_modelling.gpu_matrix_builder import GPUMatrixBuilder
        from steelo.adapters.geospatial.gpu_geospatial import GPUGeospatialCalculator

        # Test matrix builder
        builder = GPUMatrixBuilder()
        logger.info(f"GPU Matrix Builder - Device: {builder.device}")
        logger.info(f"  GPU Available: {builder.is_gpu_available()}")

        # Test geospatial calculator
        calc = GPUGeospatialCalculator()
        logger.info(f"GPU Geospatial - Device: {calc.device}")
        logger.info(f"  GPU Available: {calc.is_gpu_available}")

        if builder.is_gpu_available() or calc.is_gpu_available:
            print_success("GPU modules loaded successfully with GPU support")
        else:
            print_warning("GPU modules loaded but using CPU fallback")

        return True

    except ImportError as e:
        print_error(f"GPU modules import failed: {e}")
        logger.info("This is expected if PyTorch is not installed")
        return False
    except Exception as e:
        print_error(f"GPU module tests failed: {e}")
        return False


def run_quick_benchmark():
    """Run a quick benchmark to verify functionality."""
    print_header("Running Quick Benchmark")

    try:
        from steelo.benchmarking.performance_profiler import SolverBenchmark
        import time

        benchmark = SolverBenchmark()

        # Simulate a simple LP solve
        def dummy_lp_solve():
            time.sleep(0.01)  # Simulate 10ms solve
            return {"objective": 1000.0, "status": "optimal"}

        result = benchmark.profile(
            func=dummy_lp_solve,
            component="lp_solver",
            operation="solve_test",
            iterations=5,
            problem_size={"variables": 100, "constraints": 50},
        )

        logger.info(f"Test solve results:")
        logger.info(f"  - Average duration: {result.avg_duration_ms():.2f}ms")
        logger.info(f"  - Peak memory: {result.peak_memory_mb:.1f}MB")
        logger.info(f"  - Iterations: {result.iterations}")

        # Export results
        output_dir = Path("test_benchmarks")
        output_dir.mkdir(exist_ok=True)

        benchmark.export_results("quick_benchmark.csv")
        benchmark.generate_report("quick_benchmark_report.md")

        print_success("Quick benchmark completed")
        return True

    except Exception as e:
        print_error(f"Quick benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def generate_test_report(results: Dict[str, bool]) -> str:
    """Generate a summary test report."""
    print_header("Test Summary")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed

    logger.info(f"Total tests: {total}")
    logger.info(f"Passed: {Color.GREEN}{passed}{Color.END}")
    logger.info(f"Failed: {Color.RED}{failed}{Color.END}")
    logger.info("")

    for test_name, passed_flag in results.items():
        status = f"{Color.GREEN}✓ PASS{Color.END}" if passed_flag else f"{Color.RED}✗ FAIL{Color.END}"
        logger.info(f"  {status} - {test_name}")

    success_rate = (passed / total) * 100 if total > 0 else 0

    if success_rate == 100:
        print_success(f"All tests passed! ({passed}/{total})")
        return "success"
    elif success_rate >= 75:
        print_warning(f"Most tests passed ({passed}/{total})")
        return "warning"
    else:
        print_error(f"Many tests failed ({failed}/{total})")
        return "failure"


def main():
    """Main setup and test routine."""
    parser = argparse.ArgumentParser(
        description="Steel-IQ Optimization Setup and Test Script"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check environment, don't run tests",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run quick benchmark",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all checks and tests",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Default to --all if no flags specified
    if not (args.check_only or args.benchmark or args.all):
        args.all = True

    print_header("Steel-IQ Optimization Setup & Test")
    logger.info("Starting environment setup and testing...")
    logger.info(f"Platform: {platform.system()} {platform.release()}")
    logger.info(f"Architecture: {platform.machine()}")
    logger.info("")

    test_results = {}

    # Always check environment
    test_results["Python Version"] = check_python_version()
    hardware_info = check_hardware()
    test_results["Hardware Detection"] = "error" not in hardware_info
    test_results["Dependencies"] = check_dependencies()
    pytorch_ok, device = check_pytorch()
    test_results["PyTorch"] = pytorch_ok

    if args.check_only:
        logger.info("\nEnvironment check complete (--check-only mode)")
        return

    # Run tests if --all or --benchmark
    if args.all:
        test_results["Hardware Detection Module"] = test_hardware_detection()
        test_results["Benchmarking Framework"] = test_benchmarking_framework()
        test_results["Configuration System"] = test_configuration_system()
        test_results["GPU Modules"] = test_gpu_availability()

    if args.benchmark or args.all:
        test_results["Quick Benchmark"] = run_quick_benchmark()

    # Generate summary
    status = generate_test_report(test_results)

    # Exit with appropriate code
    if status == "success":
        logger.info("\n🎉 Environment is ready for Steel-IQ optimization!")
        logger.info("\nNext steps:")
        logger.info("  1. Review configuration: config/optimization_example.yaml")
        logger.info("  2. Run full benchmark: python -m steelo.benchmarking.run_benchmark --years 2020-2025")
        logger.info("  3. Compare solvers: python -m steelo.benchmarking.run_benchmark --compare-all")
        sys.exit(0)
    elif status == "warning":
        logger.info("\n⚠️ Some tests failed, but core functionality should work")
        sys.exit(0)
    else:
        logger.info("\n❌ Multiple test failures - please review errors above")
        sys.exit(1)


if __name__ == "__main__":
    main()
