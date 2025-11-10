"""
Hardware detection and optimization for Apple Silicon.

Detects M-series chips and provides core topology information for optimal
thread allocation and performance tuning.
"""

import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class HardwareInfo:
    """Hardware configuration information."""

    is_apple_silicon: bool
    chip_model: Optional[str]  # e.g., "Apple M4 Pro"
    total_cores: int
    performance_cores: int
    efficiency_cores: int
    total_memory_gb: float
    architecture: str  # e.g., "arm64"

    def optimal_workers(self, mode: str = "parallel") -> int:
        """
        Calculate optimal number of worker threads.

        Args:
            mode: Optimization mode:
                - "parallel": For multi-year parallel solving
                - "single": For single-threaded with background tasks
                - "max": Maximum parallelism

        Returns:
            Recommended number of worker threads
        """
        if mode == "parallel":
            # Use performance cores for compute-heavy parallel solving
            # Reserve 1-2 cores for OS and background tasks
            return max(1, self.performance_cores - 1)
        elif mode == "single":
            # Use efficiency cores for background tasks
            return self.efficiency_cores
        elif mode == "max":
            # Use all cores
            return self.total_cores
        else:
            return self.total_cores // 2

    def __repr__(self) -> str:
        if self.is_apple_silicon:
            return (
                f"HardwareInfo(chip={self.chip_model}, "
                f"cores={self.performance_cores}P+{self.efficiency_cores}E, "
                f"memory={self.total_memory_gb:.1f}GB)"
            )
        else:
            return f"HardwareInfo(cores={self.total_cores}, memory={self.total_memory_gb:.1f}GB)"


def detect_apple_silicon() -> HardwareInfo:
    """
    Detect Apple Silicon hardware and return configuration.

    Returns:
        HardwareInfo with detected hardware specifications
    """
    arch = platform.machine()
    is_apple_silicon = arch == "arm64" and platform.system() == "Darwin"

    total_cores = os.cpu_count() or 1
    total_memory_gb = _get_total_memory_gb()

    if is_apple_silicon:
        chip_model = _get_apple_chip_model()
        perf_cores, eff_cores = _get_apple_core_topology(chip_model)
    else:
        chip_model = None
        perf_cores = total_cores
        eff_cores = 0

    return HardwareInfo(
        is_apple_silicon=is_apple_silicon,
        chip_model=chip_model,
        total_cores=total_cores,
        performance_cores=perf_cores,
        efficiency_cores=eff_cores,
        total_memory_gb=total_memory_gb,
        architecture=arch,
    )


def _get_apple_chip_model() -> Optional[str]:
    """Get Apple chip model string (e.g., 'Apple M4 Pro')."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return "Apple Silicon (unknown model)"


def _get_apple_core_topology(chip_model: Optional[str]) -> tuple[int, int]:
    """
    Determine performance and efficiency core counts for Apple Silicon.

    Args:
        chip_model: Chip model string from sysctl

    Returns:
        (performance_cores, efficiency_cores)
    """
    try:
        # Try to get actual core counts from system
        perf_result = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
            capture_output=True,
            text=True,
            check=False,
        )
        eff_result = subprocess.run(
            ["sysctl", "-n", "hw.perflevel1.logicalcpu"],
            capture_output=True,
            text=True,
            check=False,
        )

        if perf_result.returncode == 0 and eff_result.returncode == 0:
            perf_cores = int(perf_result.stdout.strip())
            eff_cores = int(eff_result.stdout.strip())
            return perf_cores, eff_cores
    except (ValueError, subprocess.SubprocessError):
        pass

    # Fallback: Estimate based on known chip models
    if chip_model:
        model_lower = chip_model.lower()
        # M4 Pro: 10P + 4E = 14 cores
        if "m4 pro" in model_lower:
            return 10, 4
        # M4 Max: 12P + 4E = 16 cores
        elif "m4 max" in model_lower:
            return 12, 4
        # M4: 4P + 6E = 10 cores
        elif "m4" in model_lower and "pro" not in model_lower and "max" not in model_lower:
            return 4, 6
        # M3 Pro: 6P + 6E = 12 cores
        elif "m3 pro" in model_lower:
            return 6, 6
        # M3 Max: 12P + 4E = 16 cores
        elif "m3 max" in model_lower:
            return 12, 4
        # M3: 4P + 4E = 8 cores
        elif "m3" in model_lower:
            return 4, 4
        # M2 Pro: 8P + 4E = 12 cores
        elif "m2 pro" in model_lower:
            return 8, 4
        # M2 Max: 8P + 4E = 12 cores
        elif "m2 max" in model_lower:
            return 8, 4
        # M2: 4P + 4E = 8 cores
        elif "m2" in model_lower:
            return 4, 4
        # M1 Pro/Max: 8P + 2E = 10 cores
        elif "m1 pro" in model_lower or "m1 max" in model_lower:
            return 8, 2
        # M1: 4P + 4E = 8 cores
        elif "m1" in model_lower:
            return 4, 4

    # Ultimate fallback: assume all cores are performance cores
    total = os.cpu_count() or 1
    return total, 0


def _get_total_memory_gb() -> float:
    """Get total system memory in GB."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
            )
            memory_bytes = int(result.stdout.strip())
            return memory_bytes / (1024**3)
        else:
            # Linux fallback
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        memory_kb = int(line.split()[1])
                        return memory_kb / (1024**2)
    except (subprocess.SubprocessError, FileNotFoundError, ValueError, IOError):
        pass

    # Fallback: estimate from available memory
    return 8.0


if __name__ == "__main__":
    # CLI for testing hardware detection
    hw = detect_apple_silicon()
    print(hw)
    print(f"\nOptimal workers for parallel mode: {hw.optimal_workers('parallel')}")
    print(f"Optimal workers for max mode: {hw.optimal_workers('max')}")
