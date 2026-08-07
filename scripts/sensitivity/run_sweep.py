"""Queue and run a parameter sensitivity sweep of full steel-IQ simulations across
local CPU cores.

Each parameter combination is one long (potentially hours), effectively
single-threaded run: the annual loop in simulation.py is strictly sequential
(year N depends on state left by year N-1), so giving one run more cores doesn't
shorten it. The efficient use of a multi-core workstation is therefore to run
several *independent* combinations in parallel -- one OS process each -- not to
give any single run more cores. This script is that queue: it keeps a worker pool
of size --jobs, launches scripts.sensitivity.run_one as a subprocess per
combination, and replaces finished workers with the next queued combination until
the grid is exhausted.

Combinations come from a YAML grid file, either:
  base_params: {...}            # SimulationConfig overrides applied to every run
  sweep_params: {name: [v1, v2, ...], ...}   # Cartesian product forms the grid
or:
  base_params: {...}
  scenarios: [{...}, {...}, ...]  # hand-picked full override dicts instead of a grid
Provide exactly one of sweep_params or scenarios. See grids/example.yaml.

Resumable: a run whose output dir already has status.json with status="success" is
skipped, so a killed or rebooted sweep can just be restarted with the same command.

Prepare data once beforehand (steelo-data-prepare, or reuse a directory an earlier
`run_simulation` already prepared under ~/.steelo/preparation_cache or
~/.steelo/temp_prep) and point --data-dir/--master-excel at that -- run_one.py
never re-prepares data itself, so every run in the sweep reuses the same fixtures.

Usage:
    uv run python -m scripts.sensitivity.run_sweep \\
        --grid-file scripts/sensitivity/grids/example.yaml \\
        --data-dir ~/.steelo/preparation_cache/<hash>/data \\
        --master-excel ~/.steelo/data_cache/master-input-v2.0.0/master_input.xlsx \\
        --out-dir outputs/sensitivity/example --jobs 4
"""

import argparse
import csv
import itertools
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def build_combinations(grid: dict) -> list[dict]:
    has_sweep = "sweep_params" in grid
    has_scenarios = "scenarios" in grid
    if has_sweep == has_scenarios:
        raise ValueError("Grid file must define exactly one of sweep_params or scenarios")

    base = grid.get("base_params", {})

    if has_scenarios:
        return [{**base, **scenario} for scenario in grid["scenarios"]]

    sweep = grid["sweep_params"]
    names = list(sweep)
    return [{**base, **dict(zip(names, values))} for values in itertools.product(*(sweep[n] for n in names))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid-file", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--master-excel", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2050)
    parser.add_argument(
        "--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="WARNING"
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Max concurrent simulation runs (default: cpu_count - 1). Also check RAM: "
        "peak_rss_mb logged by simulation.py's memory snapshots x --jobs must fit in "
        "physical memory, or the OS will start swapping and every run gets slower.",
    )
    parser.add_argument(
        "--threads-per-job",
        type=int,
        default=1,
        help="OMP_NUM_THREADS/OPENBLAS_NUM_THREADS/MKL_NUM_THREADS/NUMEXPR_NUM_THREADS set "
        "in each worker's environment. Left unset, numpy/BLAS defaults to using ALL cores "
        "per process, which badly oversubscribes once --jobs > 1. Keep at 1 unless you've "
        "measured that a single run benefits from more (see run_baseline.sbatch's approach).",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args()

    grid = yaml.safe_load(args.grid_file.read_text())
    combinations = build_combinations(grid)
    logger.info(f"Grid has {len(combinations)} combination(s), running with --jobs={args.jobs}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.csv"
    manifest_is_new = not manifest_path.exists()
    manifest_file = manifest_path.open("a", newline="")
    manifest_writer = csv.writer(manifest_file)
    if manifest_is_new:
        manifest_writer.writerow(["run_id", "output_dir", "status", "duration_s", "params_json"])
        manifest_file.flush()

    child_env = os.environ.copy()
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        child_env[var] = str(args.threads_per_job)

    queue: list[tuple[str, dict, Path]] = []
    skipped = 0
    for i, combo in enumerate(combinations):
        run_id = f"run_{i:04d}"
        run_dir = args.out_dir / run_id
        status_path = run_dir / "status.json"
        if status_path.exists() and json.loads(status_path.read_text()).get("status") == "success":
            skipped += 1
            continue
        queue.append((run_id, combo, run_dir))

    if skipped:
        logger.info(f"Skipping {skipped} already-completed run(s) (status.json says success)")

    running: dict[str, tuple[subprocess.Popen, dict, Path, float]] = {}

    def launch(run_id: str, combo: dict, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        log_file = (run_dir / "run.log").open("w")
        cmd = [
            sys.executable,
            "-m",
            "scripts.sensitivity.run_one",
            "--data-dir",
            str(args.data_dir),
            "--master-excel",
            str(args.master_excel),
            "--output-dir",
            str(run_dir),
            "--start-year",
            str(args.start_year),
            "--end-year",
            str(args.end_year),
            "--log-level",
            args.log_level,
            "--params-json",
            json.dumps(combo),
        ]
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=child_env)
        running[run_id] = (proc, combo, run_dir, time.time())
        logger.info(f"Launched {run_id} (pid={proc.pid}) params={combo}")

    while queue or running:
        while queue and len(running) < args.jobs:
            run_id, combo, run_dir = queue.pop(0)
            launch(run_id, combo, run_dir)

        time.sleep(args.poll_interval)

        finished = [run_id for run_id, (proc, *_rest) in running.items() if proc.poll() is not None]
        for run_id in finished:
            proc, combo, run_dir, start_time = running.pop(run_id)
            duration = time.time() - start_time
            status = "success" if proc.returncode == 0 else "failed"
            status_path = run_dir / "status.json"
            if status_path.exists():
                status = json.loads(status_path.read_text()).get("status", status)
            logger.info(f"Finished {run_id}: {status} ({duration / 3600:.2f}h, exit={proc.returncode})")
            manifest_writer.writerow([run_id, str(run_dir), status, f"{duration:.1f}", json.dumps(combo)])
            manifest_file.flush()

    manifest_file.close()
    logger.info(f"Sweep complete. Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
