"""
Benchmarking and performance profiling for Steel-IQ solvers.

This package provides tools to measure and compare performance of different
solver implementations (baseline, parallel, GASPLAN decomposition).
"""

from .performance_profiler import SolverBenchmark, BenchmarkResults
from .hardware_detector import HardwareInfo, detect_apple_silicon

__all__ = [
    "SolverBenchmark",
    "BenchmarkResults",
    "HardwareInfo",
    "detect_apple_silicon",
]
