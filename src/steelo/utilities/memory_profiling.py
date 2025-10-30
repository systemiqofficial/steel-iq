"""Memory profiling utilities for tracking simulation memory usage.

This module provides lightweight memory tracking using psutil to identify
memory consumption patterns and potential leaks during simulation runs.

Usage:
    from steelo.utilities.memory_profiling import MemoryTracker

    tracker = MemoryTracker()
    tracker.checkpoint("simulation_start")
    # ... do work ...
    tracker.checkpoint("after_work", year=2025)
"""

import logging
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


class MemoryTracker:
    """Track memory usage at key simulation points.

    Logs RSS (Resident Set Size) at checkpoints and calculates deltas
    to identify memory growth patterns and potential leaks.

    Attributes:
        process: psutil.Process instance for current process
        last_checkpoint_rss: RSS in MB at last checkpoint (for delta calculation)
    """

    def __init__(self):
        """Initialize memory tracker."""
        self.process = psutil.Process()
        self.last_checkpoint_rss = 0.0

    def checkpoint(self, phase: str, year: Optional[int] = None) -> float:
        """Log memory usage at a checkpoint.

        Args:
            phase: Name of the checkpoint phase (e.g., "year_start", "after_lp_solve")
            year: Optional year number for year-specific checkpoints

        Returns:
            Current RSS in MB

        Logs:
            operation=memory_checkpoint year=YYYY phase=NAME rss_mb=X.X delta_mb=X.X
        """
        mem_info = self.process.memory_info()
        rss_mb = mem_info.rss / (1024 * 1024)
        delta_mb = rss_mb - self.last_checkpoint_rss

        year_str = f"year={year} " if year is not None else ""
        logger.info(f"operation=memory_checkpoint {year_str}phase={phase} rss_mb={rss_mb:.1f} delta_mb={delta_mb:.1f}")

        self.last_checkpoint_rss = rss_mb
        return rss_mb

    def get_current_rss_mb(self) -> float:
        """Get current RSS without logging a checkpoint.

        Returns:
            Current RSS in MB
        """
        mem_info = self.process.memory_info()
        return mem_info.rss / (1024 * 1024)
