"""Parse per-run runtime/memory breakdowns out of a solver-benchmark run's captured
log, so comparing highs vs. gurobi vs. gurobi+clustering doesn't require anyone to
manually grep logs/*.err.

Reads each run's task.err (run_solver_benchmark.sbatch copies its own --error log
there under a job-ID-independent name -- see that script's tail). Two log sources,
both already emitted by existing code with no simulation changes needed here:

  - `/usr/bin/time -v`'s own summary lines (wraps every run in
    run_solver_benchmark.sbatch): wall_clock_s, peak_rss_mb for the whole process.
  - `operation=<name> [year=Y] duration_s=T` INFO log lines (logging.basicConfig's
    default handler writes to stderr, landing in the same task.err) from
    plant_agent.py and trade_lp_modelling.py, summed across all years into four
    additive, non-overlapping buckets:
      - lp_build_s: operation=allocation_setup -- set_up_steel_trade_lp() building the
        Pyomo model (TradeLPModel.build_lp_model() and everything around it), before
        the solver call.
      - lp_solve_s: operation=trade_optimization -- solve_lp_model()'s solver.solve()
        call specifically (logged without a year field; summed across the whole run
        the same as the other buckets).
      - npv_plant_decision_s: operation=pam_evaluate_plants +
        operation=pam_evaluate_expansions -- the actual per-plant NPV renovate/switch/
        close/expand decision logic, no LP or geospatial work involved.
      - geospatial_s: operation=geospatial_model -- siting new plants. Kept separate
        rather than folded into another bucket: this doesn't touch the LP at all, and
        can dominate runtime in later years (see CLUSTER_RUNBOOK.md's clustering
        speedup notes) -- lumping it in elsewhere would misattribute a geospatial-
        driven slowdown to the solver/clustering choice.
    agent_module_s is reported separately as the *measured* operation=plant_agents_model
    total (the whole per-year economic-model step), not computed as the sum of the four
    buckets above. other_s = agent_module_s - (lp_build_s + lp_solve_s +
    npv_plant_decision_s + geospatial_s) is the residual -- carbon-cost calc,
    allocation postprocessing/export, and any untimed overhead. It's a sanity check as
    much as a bucket: it should stay small; a large other_s means the four buckets
    above aren't actually explaining most of where the time goes.
    Also captures memory_checkpoint rss_mb at the after_lp_setup/after_lp_solve
    phases (utilities/memory_profiling.py's MemoryTracker) as
    peak_lp_build_rss_mb/peak_lp_solve_rss_mb -- the max RSS logged at that
    checkpoint across all years (RSS trends upward over a run since it's one
    long-lived process across the annual loop, not restarted per year, so the max is
    effectively "how big did the process get by the time it reached this phase"). No
    equivalent checkpoint exists for npv_plant_decision/geospatial, so there's no
    memory column for those.

Usage:
    uv run python -m scripts.sensitivity.parse_stage_timings \\
        --sweep-root outputs/sensitivity/solver_benchmark \\
        --configs highs gurobi gurobi_clustering \\
        --out-csv outputs/sensitivity/solver_benchmark/stage_timing_summary.csv
"""

import argparse
import csv
import json
import re
from pathlib import Path

_DURATION_WITH_YEAR_RE = re.compile(r"operation=(\w+) year=(\d+) duration_s=([\d.]+)")
_TRADE_OPTIMIZATION_RE = re.compile(r"operation=trade_optimization duration_s=([\d.]+)")
_MEMORY_CHECKPOINT_RE = re.compile(r"operation=memory_checkpoint (?:year=\d+ )?phase=(\S+) rss_mb=([\d.]+)")
_TIME_V_WALL_CLOCK_RE = re.compile(r"Elapsed \(wall clock\) time.*?: (\S+)")
_TIME_V_PEAK_RSS_KB_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")

# operation= names summed into each bucket -- see module docstring for what each
# bucket means and why agent_module_s is measured directly rather than derived as
# their sum.
_AGENT_MODULE_OPS = {"plant_agents_model"}
_LP_BUILD_OPS = {"allocation_setup"}
_NPV_PLANT_DECISION_OPS = {"pam_evaluate_plants", "pam_evaluate_expansions"}
_GEOSPATIAL_OPS = {"geospatial_model"}


def _parse_time_v_wall_clock(value: str) -> float:
    """Convert /usr/bin/time -v's "Elapsed (wall clock) time" value -- "m:ss.ss" or
    "h:mm:ss" -- to seconds."""
    parts = value.split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_log(log_path: Path) -> dict:
    """Extract stage-level runtime/memory totals from one task's captured log."""
    agent_module_s = 0.0
    lp_build_s = 0.0
    lp_solve_s = 0.0
    npv_plant_decision_s = 0.0
    geospatial_s = 0.0
    peak_lp_build_rss_mb = 0.0
    peak_lp_solve_rss_mb = 0.0
    wall_clock_s: float | None = None
    peak_rss_mb: float | None = None

    for line in log_path.read_text(errors="replace").splitlines():
        if m := _DURATION_WITH_YEAR_RE.search(line):
            op, _year, duration = m.group(1), m.group(2), float(m.group(3))
            if op in _AGENT_MODULE_OPS:
                agent_module_s += duration
            elif op in _LP_BUILD_OPS:
                lp_build_s += duration
            elif op in _NPV_PLANT_DECISION_OPS:
                npv_plant_decision_s += duration
            elif op in _GEOSPATIAL_OPS:
                geospatial_s += duration
            continue
        if m := _TRADE_OPTIMIZATION_RE.search(line):
            lp_solve_s += float(m.group(1))
            continue
        if m := _MEMORY_CHECKPOINT_RE.search(line):
            phase, rss_mb = m.group(1), float(m.group(2))
            if phase == "after_lp_setup":
                peak_lp_build_rss_mb = max(peak_lp_build_rss_mb, rss_mb)
            elif phase == "after_lp_solve":
                peak_lp_solve_rss_mb = max(peak_lp_solve_rss_mb, rss_mb)
            continue
        if m := _TIME_V_WALL_CLOCK_RE.search(line):
            wall_clock_s = _parse_time_v_wall_clock(m.group(1))
            continue
        if m := _TIME_V_PEAK_RSS_KB_RE.search(line):
            peak_rss_mb = int(m.group(1)) / 1024.0
            continue

    other_s = agent_module_s - (lp_build_s + lp_solve_s + npv_plant_decision_s + geospatial_s)

    return {
        "wall_clock_s": wall_clock_s,
        "peak_rss_mb": peak_rss_mb,
        "agent_module_s": agent_module_s,
        "lp_build_s": lp_build_s,
        "lp_solve_s": lp_solve_s,
        "npv_plant_decision_s": npv_plant_decision_s,
        "geospatial_s": geospatial_s,
        "other_s": other_s,
        "peak_lp_build_rss_mb": peak_lp_build_rss_mb,
        "peak_lp_solve_rss_mb": peak_lp_solve_rss_mb,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sweep-root", type=Path, required=True, help="Contains one subdir per config")
    parser.add_argument("--configs", nargs="+", required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    args = parser.parse_args()

    fieldnames = [
        "config",
        "seed",
        "wall_clock_s",
        "peak_rss_mb",
        "agent_module_s",
        "lp_build_s",
        "lp_solve_s",
        "npv_plant_decision_s",
        "geospatial_s",
        "other_s",
        "peak_lp_build_rss_mb",
        "peak_lp_solve_rss_mb",
    ]
    rows = []
    for config in args.configs:
        manifest_path = args.sweep_root / config / "manifest.csv"
        if not manifest_path.exists():
            print(f"Skipping {config}: no manifest.csv")
            continue
        with manifest_path.open(newline="") as f:
            for manifest_row in csv.DictReader(f):
                seed = json.loads(manifest_row["params_json"])["random_seed"]
                log_path = Path(manifest_row["output_dir"]) / "task.err"
                if not log_path.exists():
                    print(f"Skipping {config} seed={seed}: no task.err at {log_path}")
                    continue
                stats = parse_log(log_path)
                rows.append({"config": config, "seed": seed, **stats})

    with args.out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {args.out_csv}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
