#!/bin/bash
# Production 5-seed PAM noise-floor sweep (co2_ramp_seed_sweep.yaml), full 2025-2050
# range, locked to gurobi + furnace-group clustering, no warm-start -- the recommended
# config for runs where exact plant-level precision isn't needed (region/tech
# aggregates are unaffected by clustering; see CLUSTER_RUNBOOK.md §7). Structured
# identically to run_seed_sweep.sbatch (one independent SLURM array task per seed,
# task_id -> seed = task_id + 1), just with the solver/clustering params locked in and
# right-sized --mem/--time for this specific config -- run_solver_benchmark.sbatch's
# sizing is for its slowest config (highs) and would over-request resources here.
#
# Submit:  sbatch scripts/slurm/run_gurobi_clustered_sweep.sbatch
# Then, once all 5 tasks finish (success or not), queue analysis:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_gurobi_clustered_sweep_analyze.sbatch
# Retry a single seed (reuses the same scratch dir, only overwrites that seed's run_000N):
#   sbatch --array=<TASK_ID> scripts/slurm/run_gurobi_clustered_sweep.sbatch
#
# Prerequisite: `uv sync --extra gurobi` plus a working Gurobi license reachable from
# labgpu01.
#
# Sizing: --mem=8G / --time=12:00:00, extrapolated from run_solver_benchmark.sbatch's
# gurobi_clustering results (2025-2040) to this script's longer 2025-2050 range, with a
# safety margin on top of the straight-line estimate since later years (siting more
# plants) likely cost more per year than earlier ones. Correct against this script's
# own sacct/task.err numbers once it has completed a real run. Partition must stay
# verylong -- long caps at 6h; confirm cluster tiers with `sinfo -o "%P %l"`.

#SBATCH --job-name=steelo-gurobi-clustered-sweep
#SBATCH --partition=verylong
#SBATCH --nodelist=labgpu01        # cluster's only node
#SBATCH --array=0-4                # one task per seed (task_id -> seed = task_id + 1)
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1          # single-threaded run, see run_one.py
#SBATCH --mem=8G
#SBATCH --time=12:00:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=jan.tautorus@it-u.at
#SBATCH --output=logs/gurobi-clustered-sweep-%A_%a.out
#SBATCH --error=logs/gurobi-clustered-sweep-%A_%a.err

set -euo pipefail
# `module` isn't defined in sbatch's non-interactive shell here; skip purge if
# absent instead of failing (this workflow has no environment-module dependency).
command -v module >/dev/null 2>&1 && module purge || true

export MPLBACKEND=Agg
export STEELO_HOME="/local/$USER/steelo_home_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p logs "$STEELO_HOME"

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

# Private, git-ignored credentials file (GUROBI_CLUSTER_LICENSE_SETUP.md Step 4,
# Option B).
[ -f "$HOME/.gurobi_credentials.env" ] && source "$HOME/.gurobi_credentials.env"

SEED=$((SLURM_ARRAY_TASK_ID + 1))
RUN_ID=$(printf "run_%04d" "$SLURM_ARRAY_TASK_ID")

# Fixed (not job-ID-suffixed) so a retry lands in the same tree as already-succeeded seeds.
SCRATCH_OUT="/local/$USER/outputs/co2_ramp_gurobi_clustered_sweep"
RUN_DIR="$SCRATCH_OUT/$RUN_ID"
mkdir -p "$RUN_DIR" "$SCRATCH_OUT/manifest_fragments"

echo "Job $SLURM_ARRAY_JOB_ID task $SLURM_ARRAY_TASK_ID (seed=$SEED) starting on $(hostname)"

PARAMS_JSON="{\"probabilistic_agents\": false, \"random_seed\": $SEED, \"optimization_solver\": \"gurobi\", \"enable_furnace_group_clustering\": true}"

T0=$(date +%s)
STATUS="failed"
# /usr/bin/time -v writes peak RSS to stderr (captured by --error), independent of
# --log-level.
if /usr/bin/time -v python -m scripts.sensitivity.run_one \
    --data-dir "$HOME/.steelo/preparation_cache/co2_ramp_authored/data" \
    --master-excel "$HOME/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx" \
    --output-dir "$RUN_DIR" \
    --start-year 2025 --end-year 2050 \
    --log-level INFO \
    --params-json "$PARAMS_JSON"; then
    STATUS="success"
fi
T1=$(date +%s)
DURATION=$((T1 - T0))

# status.json (if present) is authoritative -- covers the process exiting 0 while
# the run itself failed, or vice versa. Absent if SLURM hard-kills the process
# (timeout/OOM) before it can write one, in which case STATUS stays "failed".
if [ -f "$RUN_DIR/status.json" ]; then
    STATUS=$(python -c "import json; print(json.load(open('$RUN_DIR/status.json'))['status'])")
fi

python - "$RUN_ID" "$RUN_DIR" "$STATUS" "$DURATION" "$PARAMS_JSON" "$SCRATCH_OUT/manifest_fragments/${SLURM_ARRAY_TASK_ID}.csv" <<'PYEOF'
import csv
import sys

run_id, run_dir, status, duration, params_json, out_path = sys.argv[1:7]
with open(out_path, "w", newline="") as f:
    csv.writer(f).writerow([run_id, run_dir, status, duration, params_json])
PYEOF

# Copy this task's own --error log into RUN_DIR under a job-ID-independent name, so
# retries/analysis can find it by seed alone -- %A_%a in the --error path changes
# across resubmits, RUN_DIR does not. Copied, not moved: SLURM still owns the original.
cp "logs/gurobi-clustered-sweep-${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.err" "$RUN_DIR/task.err" 2>/dev/null || true

echo "Task $SLURM_ARRAY_TASK_ID (seed=$SEED) finished: $STATUS (${DURATION}s)"
[ "$STATUS" = "success" ]
