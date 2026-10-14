"""Run one simulation as a benchmark that mimics a one-core cluster job.

Usage (from any directory; the script changes into the output directory itself):

    PYTHONHASHSEED=0 uv run python scripts/perf/bench_run.py \
        --data-dir <prepared data dir> --end-year 2026 --scope plant --out-dir <dir> --cpus 2

What it does:

- Pins the process to the given logical CPUs (default: one CPU). Solver and BLAS threads then
  share that CPU, so the CPU time equals the core cost of one cluster job.
- Sets the BLAS/OpenMP thread counts to 1 and the log level to WARNING (override with --log-level).
- Runs from the output directory, because checkpoints go to the working directory.
- Writes ``bench_summary.json`` (total wall and CPU time, peak memory) and prints a per-stage table
  built from ``stage_timing.csv``. Compare CPU time between runs; wall time depends on the load of
  the machine.

PYTHONHASHSEED must be set before the interpreter starts. The script warns when it is not "0".
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
os.environ.setdefault("MPLBACKEND", "Agg")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

LOG_LEVELS = {name: getattr(logging, name) for name in ("DEBUG", "INFO", "WARNING", "ERROR")}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Prepared data directory")
    parser.add_argument("--master-excel", type=Path, default=None, help="Default: <data-dir>/master_input.xlsx")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--scope", default="plant", choices=["plant", "plant_group", "iso3"])
    parser.add_argument("--no-clustering", action="store_true")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--solver", default=None, help="optimization_solver value; omit for the branch default")
    parser.add_argument("--log-level", default="WARNING", choices=sorted(LOG_LEVELS))
    parser.add_argument(
        "--cpus",
        default="2",
        help="Comma-separated logical CPUs to pin the process to; 'all' disables pinning (default: 2)",
    )
    parser.add_argument("--profile", action="store_true", help="Run under cProfile (adds 1.5-2x overhead)")
    parser.add_argument(
        "--config", action="append", default=[], metavar="KEY=VALUE", help="Extra SimulationConfig field (JSON value)"
    )
    return parser.parse_args()


def pin_cpus(spec: str) -> list[int] | None:
    import psutil

    if spec == "all":
        return None
    cpus = [int(c) for c in spec.split(",")]
    psutil.Process().cpu_affinity(cpus)
    return cpus


def peak_memory_mb() -> float:
    import psutil

    info = psutil.Process().memory_info()
    return getattr(info, "peak_wset", info.rss) / 2**20


def print_stage_table(stage_csv: Path) -> None:
    wall: dict[tuple[str, str], float] = defaultdict(float)
    cpu: dict[tuple[str, str], float] = defaultdict(float)
    years: list[str] = []
    stages: list[str] = []
    with open(stage_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["year"], row["stage"])
            wall[key] += float(row["wall_s"])
            cpu[key] += float(row["cpu_s"])
            if row["year"] not in years:
                years.append(row["year"])
            if row["stage"] not in stages:
                stages.append(row["stage"])
    print("stage timing (wall_s / cpu_s)")
    print(f"{'stage':<28}" + "".join(f"{y:>20}" for y in years))
    for stage in stages:
        cells = "".join(f"{wall[(y, stage)]:>10.1f}/{cpu[(y, stage)]:<9.1f}" for y in years)
        print(f"{stage:<28}{cells}")


def main() -> None:
    args = parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("WARNING: PYTHONHASHSEED is not 0; set iteration order may differ between runs", file=sys.stderr)

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(out_dir)
    cpus = pin_cpus(args.cpus)

    from steelo.bootstrap import bootstrap_simulation
    from steelo.domain import Year
    from steelo.simulation import SimulationConfig

    extra = {}
    if args.solver is not None:
        extra["optimization_solver"] = args.solver
    for item in args.config:
        key, _, value = item.partition("=")
        extra[key] = json.loads(value)

    config = SimulationConfig.from_data_directory(
        start_year=Year(args.start_year),
        end_year=Year(args.end_year),
        data_dir=args.data_dir,
        output_dir=out_dir / "run",
        master_excel_path=args.master_excel,
        enable_furnace_group_clustering=not args.no_clustering,
        geographical_clustering_scope=args.scope,
        probabilistic_agents=False,
        random_seed=args.seed,
        log_level=LOG_LEVELS[args.log_level],
        **extra,
    )

    profiler = None
    if args.profile:
        import cProfile

        profiler = cProfile.Profile()

    wall_start, cpu_start = time.perf_counter(), time.process_time()
    status = "failed"
    try:
        if profiler:
            profiler.enable()
        bootstrap_simulation(config).run()
        status = "ok"
    finally:
        if profiler:
            profiler.disable()
            profiler.dump_stats(str(out_dir / "profile.prof"))
        summary = {
            "status": status,
            "wall_s": round(time.perf_counter() - wall_start, 1),
            "cpu_s": round(time.process_time() - cpu_start, 1),
            "peak_memory_mb": round(peak_memory_mb()),
            "cpus": cpus,
            "profiled": args.profile,
            "args": {k: str(v) for k, v in vars(args).items()},
        }
        (out_dir / "bench_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in summary.items() if k != "args"}))
        stage_csv = out_dir / "run" / "stage_timing.csv"
        if stage_csv.exists():
            print_stage_table(stage_csv)


if __name__ == "__main__":
    main()
