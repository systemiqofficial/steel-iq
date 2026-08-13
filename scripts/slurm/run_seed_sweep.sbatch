#!/bin/bash
# 5-seed PAM noise-floor sweep (co2_ramp_seed_sweep.yaml) as a SLURM job array --
# one independent task per seed (task_id -> seed = task_id + 1) instead of 5
# subprocesses sharing one job's resources. See CLUSTER_RUNBOOK.md for setup,
# data prep, and full sizing detail.
#
# Submit:  sbatch scripts/slurm/run_seed_sweep.sbatch
# Then, once all 5 tasks finish (success or not), queue analysis:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_seed_sweep_analyze.sbatch
# Retry a single seed (reuses the same scratch dir, only overwrites that seed's run_000N):
#   sbatch --array=<TASK_ID> scripts/slurm/run_seed_sweep.sbatch
#
# One-time setup, full data-prep steps, and sizing detail: see CLUSTER_RUNBOOK.md §2/§4.
#
# Sizing: --time=48:00:00 / --mem=32G, from job 27923's real 36.4-39.0h/seed runtime
# (`sacct`) and a ~20-25G doc-guidance ballpark for memory -- this cluster's SLURM
# accounting doesn't sample memory (JobAcctGatherType=jobacct_gather/none), so the
# /usr/bin/time -v wrapper below is the real peak-RSS source instead. --log-level
# INFO captures the year-by-year operation=/memory_snapshot trend; run the
# WARNING-vs-INFO precheck in CLUSTER_RUNBOOK.md before trusting a full run's
# numbers. Partition must stay verylong -- long caps at 6h; confirm cluster tiers
# with `sinfo -o "%P %l"`.

#SBATCH --job-name=steelo-seed-sweep
#SBATCH --partition=verylong
#SBATCH --nodelist=labgpu01        # cluster's only node
#SBATCH --array=0-4                # one task per seed (task_id -> seed = task_id + 1)
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1          # single-threaded run, see run_one.py
#SBATCH --mem=32G
#SBATCH --time=48:00:00
#SBATCH --output=logs/seed-sweep-%A_%a.out
#SBATCH --error=logs/seed-sweep-%A_%a.err

set -euo pipefail
# `module` isn't defined in sbatch's non-interactive shell here; skip purge if
# absent instead of failing (this workflow has no environment-module dependency).
command -v module >/dev/null 2>&1 && module purge || true

export MPLBACKEND=Agg
export STEELO_HOME="/local/$USER/steelo_home_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p logs "$STEELO_HOME"

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

SEED=$((SLURM_ARRAY_TASK_ID + 1))
RUN_ID=$(printf "run_%04d" "$SLURM_ARRAY_TASK_ID")

# Fixed (not job-ID-suffixed) so a retry lands in the same tree as already-succeeded seeds.
SCRATCH_OUT="/local/$USER/outputs/co2_ramp_seed_sweep"
RUN_DIR="$SCRATCH_OUT/$RUN_ID"
mkdir -p "$RUN_DIR" "$SCRATCH_OUT/manifest_fragments"

echo "Job $SLURM_ARRAY_JOB_ID task $SLURM_ARRAY_TASK_ID (seed=$SEED) starting on $(hostname)"

PARAMS_JSON="{\"probabilistic_agents\": false, \"random_seed\": $SEED}"

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

echo "Task $SLURM_ARRAY_TASK_ID (seed=$SEED) finished: $STATUS (${DURATION}s)"
[ "$STATUS" = "success" ]
