"""Parse per-run runtime/memory breakdowns out of a solver-benchmark run's captured
log, so comparing highs vs. gurobi vs. gurobi+clustering doesn't require anyone to
manually grep logs/*.err.

Reads each run's task.err (run_solver_benchmark.sbatch copies its own --error log
there under a job-ID-independent name -- see that script's tail). Two log sources,
both already emitted by existing code with no simulation changes needed here:

  - `/usr/bin/time -v`'s own summary lines (wraps every run in
    run_solver_benchmark.sbatch): wall_clock_s, peak_rss_mb for the whole process.
  - `operation=<name> [year=Y] duration_s=T` INFO log lines (logging.basicConfig's
    default handler writes to stderr, landing in the same task.err).

The annual cycle runs three independent, SIBLING economic-model stages each year
(src/steelo/economic_models/plant_agent.py: GeospatialModel, AllocationModel,
PlantAgentsModel -- three separate classes, none nested inside another). Reported as
three directly-measured totals, summed across all years:

  - trade_module_s: operation=allocation_model (AllocationModel.run()'s own total --
    builds and solves the trade LP, then exports/plots the allocation).
  - geospatial_s: operation=geospatial_model (GeospatialModel.run()'s own total --
    siting new plants). Can dominate runtime in later years as more plants exist.
  - pam_s: operation=plant_agents_model (PlantAgentsModel.run()'s own total -- per-plant
    renovate/switch/close/expand NPV decisions only; no LP or geospatial work).

Within trade_module_s and pam_s, two further sub-parts are broken out (additive, sum
towards but not necessarily exactly equal to their parent total -- allocation
postprocessing/export and carbon-cost calc aren't separately bucketed, so some residual
is normal, not a bug):
  - lp_build_s: operation=allocation_setup -- set_up_steel_trade_lp() building the
    Pyomo model, before the solver call. Sub-part of trade_module_s.
  - lp_solve_s: operation=trade_optimization -- solve_lp_model()'s solver.solve() call
    specifically (logged without a year field; summed across the whole run same as the
    other buckets). Sub-part of trade_module_s.
  - npv_plant_decision_s: operation=pam_evaluate_plants + operation=pam_evaluate_expansions
    -- the actual per-plant NPV decision logic. Sub-part of pam_s.

Also captures memory_checkpoint rss_mb at the after_lp_setup/after_lp_solve phases
(utilities/memory_profiling.py's MemoryTracker) as peak_lp_build_rss_mb/
peak_lp_solve_rss_mb -- the max RSS logged at that checkpoint across all years (RSS
trends upward over a run since it's one long-lived process across the annual loop, not
restarted per year, so the max is effectively "how big had the process gotten by the
time it reached this phase"). No equivalent checkpoint exists for npv_plant_decision/
geospatial, so there's no memory column for those.

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

# operation= names summed into each bucket -- see module docstring for the three-
# sibling-stage structure and which buckets are sub-parts of which stage total.
_TRADE_MODULE_OPS = {"allocation_model"}
_GEOSPATIAL_OPS = {"geospatial_model"}
_PAM_OPS = {"plant_agents_model"}
_LP_BUILD_OPS = {"allocation_setup"}
_NPV_PLANT_DECISION_OPS = {"pam_evaluate_plants", "pam_evaluate_expansions"}
# Sub-parts of geospatial_s: new-plant-opening candidate identification (Steps 1-5 of
# docs/domain_simulation_logic/geospatial_model/new_plant_opening.md, includes
# select_location_subset's calculate_npv_pct sampling) and the separate per-year status
# update of already-considered/announced opportunities.
_NEW_PLANT_OPENING_OPS = {"geo_identify_opportunities"}
_GEO_UPDATE_STATUS_OPS = {"geo_update_status"}


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
    trade_module_s = 0.0
    geospatial_s = 0.0
    pam_s = 0.0
    lp_build_s = 0.0
    lp_solve_s = 0.0
    npv_plant_decision_s = 0.0
    new_plant_opening_s = 0.0
    geo_update_status_s = 0.0
    priority_tiebreak_count = 0
    peak_lp_build_rss_mb = 0.0
    peak_lp_solve_rss_mb = 0.0
    wall_clock_s: float | None = None
    peak_rss_mb: float | None = None

    with log_path.open(errors="replace") as f:
        for line in f:
            if m := _DURATION_WITH_YEAR_RE.search(line):
                op, _year, duration = m.group(1), m.group(2), float(m.group(3))
                if op in _TRADE_MODULE_OPS:
                    trade_module_s += duration
                elif op in _GEOSPATIAL_OPS:
                    geospatial_s += duration
                elif op in _PAM_OPS:
                    pam_s += duration
                elif op in _LP_BUILD_OPS:
                    lp_build_s += duration
                elif op in _NPV_PLANT_DECISION_OPS:
                    npv_plant_decision_s += duration
                elif op in _NEW_PLANT_OPENING_OPS:
                    new_plant_opening_s += duration
                elif op in _GEO_UPDATE_STATUS_OPS:
                    geo_update_status_s += duration
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
            if "operation=priority_tiebreak" in line:
                priority_tiebreak_count += 1
                continue

    return {
        "wall_clock_s": wall_clock_s,
        "peak_rss_mb": peak_rss_mb,
        "trade_module_s": trade_module_s,
        "geospatial_s": geospatial_s,
        "pam_s": pam_s,
        "lp_build_s": lp_build_s,
        "lp_solve_s": lp_solve_s,
        "npv_plant_decision_s": npv_plant_decision_s,
        "new_plant_opening_s": new_plant_opening_s,
        "geo_update_status_s": geo_update_status_s,
        "priority_tiebreak_count": priority_tiebreak_count,
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
        "trade_module_s",
        "geospatial_s",
        "pam_s",
        "lp_build_s",
        "lp_solve_s",
        "npv_plant_decision_s",
        "new_plant_opening_s",
        "geo_update_status_s",
        "priority_tiebreak_count",
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
