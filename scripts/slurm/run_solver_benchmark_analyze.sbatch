#!/bin/bash
# Post-processing for run_solver_benchmark.sbatch's 12-task array job. Each of the 4
# configs (highs, gurobi, gurobi_clustering, gurobi_clustering_warmstart) gets its own
# manifest.csv merged from that config's task fragments, then analyze_seed_sensitivity.py
# runs against it unmodified -- that script keys everything by seed and the 3 seeds are
# reused identically across configs, so each config's analysis dir is a same-seeds
# noise-floor check on its own terms; comparing the 4 configs' final_tech_share_by_seed.csv
# etc. by hand is how you check solver/clustering/warm-start didn't change *what* gets
# built, not just how fast.
#
# Also writes runtime_summary.csv (config, seed, duration_s, status) across all 12 runs
# from the manifests directly, plus stage_timing_summary.csv (parse_stage_timings.py) --
# wall_clock_s/peak_rss_mb from each run's /usr/bin/time -v output; lp_build_s/
# lp_solve_s/npv_plant_decision_s/geospatial_s/other_s (additive, sum to
# agent_module_s) and peak_lp_build_rss_mb/peak_lp_solve_rss_mb, parsed out of the
# operation=.../memory_checkpoint log lines run_solver_benchmark.sbatch already copies
# into each run dir as task.err -- the actual point of this benchmark, answering "how
# much faster/leaner is each config than highs on this range, and where does the
# difference come from."
#
# Submit only after all 12 array tasks finish, successfully or not -- the manifest
# reader skips non-"success" rows, so partial results still work:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_solver_benchmark_analyze.sbatch
# Re-run after retrying any failed (config, seed) task to fold the fix in.

#SBATCH --job-name=steelo-solver-benchmark-analyze
#SBATCH --partition=short
#SBATCH --nodelist=labgpu01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=logs/solver-benchmark-analyze-%j.out
#SBATCH --error=logs/solver-benchmark-analyze-%j.err

set -euo pipefail
# See run_solver_benchmark.sbatch: `module` isn't defined in sbatch's non-interactive
# shell on this cluster, and this workflow has no environment-module dependency.
command -v module >/dev/null 2>&1 && module purge || true
export MPLBACKEND=Agg

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

SCRATCH_ROOT="/local/$USER/outputs/solver_benchmark"
CONFIGS=(highs gurobi gurobi_clustering gurobi_clustering_warmstart)

for CONFIG in "${CONFIGS[@]}"; do
    SCRATCH_OUT="$SCRATCH_ROOT/$CONFIG"
    if [ ! -d "$SCRATCH_OUT/manifest_fragments" ]; then
        echo "Skipping $CONFIG: no manifest_fragments dir (no tasks ran for this config yet)"
        continue
    fi

    {
        echo "run_id,output_dir,status,duration_s,params_json"
        cat "$SCRATCH_OUT"/manifest_fragments/*.csv
    } > "$SCRATCH_OUT/manifest.csv"

    echo "=== $CONFIG manifest ==="
    cat "$SCRATCH_OUT/manifest.csv"

    python -m scripts.sensitivity.analyze_seed_sensitivity \
        --sweep-dir "$SCRATCH_OUT" \
        --out-dir "$SCRATCH_OUT/analysis"
done

python - "$SCRATCH_ROOT" "${CONFIGS[@]}" <<'PYEOF'
import csv
import json
import sys
from pathlib import Path

scratch_root, *configs = sys.argv[1:]
out_path = Path(scratch_root) / "runtime_summary.csv"
rows = []
for config in configs:
    manifest_path = Path(scratch_root) / config / "manifest.csv"
    if not manifest_path.exists():
        continue
    with manifest_path.open(newline="") as f:
        for row in csv.DictReader(f):
            seed = json.loads(row["params_json"])["random_seed"]
            rows.append([config, seed, row["status"], row["duration_s"]])

with out_path.open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["config", "seed", "status", "duration_s"])
    writer.writerows(rows)

print(f"Wrote {out_path}")
for row in rows:
    print(row)
PYEOF

python -m scripts.sensitivity.parse_stage_timings \
    --sweep-root "$SCRATCH_ROOT" \
    --configs "${CONFIGS[@]}" \
    --out-csv "$SCRATCH_ROOT/stage_timing_summary.csv"

mkdir -p "$HOME/steel-iq/outputs/sensitivity/solver_benchmark"
rsync -a "$SCRATCH_ROOT/" "$HOME/steel-iq/outputs/sensitivity/solver_benchmark/"

echo "Analysis complete, results synced to $HOME/steel-iq/outputs/sensitivity/solver_benchmark"
