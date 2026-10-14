"""Per-stage wall and CPU timing that does not depend on the log level.

The simulation logs ``operation=<stage> duration_s=...`` lines at INFO. A run with a quieter log
level (for example WARNING, as used for sweeps) drops them. This module records the same stages
in memory, with CPU time next to wall time, and writes them to ``stage_timing.csv``.

CPU time is the process CPU time (user + system, all threads). It stays stable when the machine
is busy, and it equals the core cost of a run on a scheduler that gives one core per run, as long as
the solver runs with one thread. Wall time is kept for comparison: the gap between the two shows
waiting (I/O, a busy machine) or parallel work (solver threads).

Size counters of the same stages (clusters, LP rows and columns) go to ``stage_metrics.csv``.

The recorder is process-wide. A run starts with ``reset_stage_timings``; the year loop calls
``set_stage_year`` once per year.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path

STAGE_TIMING_FILE = "stage_timing.csv"
STAGE_METRICS_FILE = "stage_metrics.csv"
_FIELDS = ("year", "stage", "wall_s", "cpu_s")
_METRIC_FIELDS = ("year", "metric", "value")


@dataclass(frozen=True)
class StageRecord:
    year: int | None
    stage: str
    wall_s: float
    cpu_s: float


@dataclass(frozen=True)
class MetricRecord:
    year: int | None
    metric: str
    value: float


_records: list[StageRecord] = []
_metrics: list[MetricRecord] = []
_current_year: int | None = None


class StageTimer:
    """Stopwatch for wall time and process CPU time, started on construction."""

    __slots__ = ("_wall_start", "_cpu_start")

    def __init__(self) -> None:
        self._wall_start = time.perf_counter()
        self._cpu_start = time.process_time()

    def elapsed(self) -> tuple[float, float]:
        """Return (wall_s, cpu_s) since the timer started."""
        return time.perf_counter() - self._wall_start, time.process_time() - self._cpu_start


def reset_stage_timings() -> None:
    """Clear all records. Call once at the start of a run."""
    global _current_year
    _records.clear()
    _metrics.clear()
    _current_year = None


def set_stage_year(year: int | None) -> None:
    """Set the simulation year attached to the stages recorded from now on."""
    global _current_year
    _current_year = None if year is None else int(year)


def stop_stage(timer: StageTimer, stage: str) -> float:
    """Record the stage with the time since ``timer`` started. Return the wall seconds."""
    wall_s, cpu_s = timer.elapsed()
    _records.append(StageRecord(_current_year, stage, wall_s, cpu_s))
    return wall_s


def record_metric(metric: str, value: float) -> None:
    """Record a size counter (for example LP columns) for the current year."""
    _metrics.append(MetricRecord(_current_year, metric, float(value)))


def get_stage_records() -> list[StageRecord]:
    return list(_records)


def get_metric_records() -> list[MetricRecord]:
    return list(_metrics)


def write_stage_timings(output_dir: Path) -> Path:
    """Write all records to ``<output_dir>/stage_timing.csv`` and ``stage_metrics.csv``, replacing both files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / STAGE_TIMING_FILE
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_FIELDS)
        for r in _records:
            writer.writerow([r.year if r.year is not None else "", r.stage, f"{r.wall_s:.3f}", f"{r.cpu_s:.3f}"])
    with open(output_dir / STAGE_METRICS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_METRIC_FIELDS)
        for m in _metrics:
            writer.writerow([m.year if m.year is not None else "", m.metric, f"{m.value:.10g}"])
    return path
