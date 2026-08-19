#!/bin/bash
# calculate_npv_pct gating benchmark (scripts/sensitivity/grids/npv_pct_tuning.yaml).
# 3 scenarios x (3 seeds + 1 same-seed repeat) = 12 combinations, one SLURM array task
# each, locked to gurobi + furnace-group clustering -- structured identically to
# run_gurobi_clustered_sweep.sbatch, just with an explicit combination table instead of
# a uniform seed sweep. The task-index -> combination arrays below MUST match
# npv_pct_tuning.yaml's `scenarios:` list exactly; that file is the source of truth.
#
# Submit:  sbatch scripts/slurm/run_npv_pct_tuning.sbatch
# Then, once all 12 tasks finish (success or not), queue analysis:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_npv_pct_tuning_analyze.sbatch
# Retry a single combination (reuses the same scratch dir, only overwrites that task's run):
#   sbatch --array=<TASK_ID> scripts/slurm/run_npv_pct_tuning.sbatch
#
# Prerequisite: `uv sync --extra gurobi` plus a working Gurobi license reachable from
# labgpu01 (see GUROBI_CLUSTER_LICENSE_SETUP.md).
#
# Sizing: --mem=8G matches run_gurobi_clustered_sweep.sbatch's 2025-2050 gurobi+clustering
# baseline. --time=24:00:00 is a first-pass estimate with a large safety margin for the
# npv_pct_full/npv_pct_narrowed combinations, whose whole point is exercising the ~7x
# slower path in select_location_subset (docs/domain_simulation_logic/geospatial_model/
# new_plant_opening.md) -- but that 7x figure is for the location-sampling step alone,
# not the whole annual cycle, so the real multiplier on total wall-clock is unknown until
# this script has actually run once. Correct against this script's own sacct/task.err
# numbers afterward. Partition must stay verylong -- long caps at 6h.

#SBATCH --job-name=steelo-npv-pct-tuning
#SBATCH --partition=verylong
#SBATCH --nodelist=labgpu01        # cluster's only node
#SBATCH --array=0-11               # one task per (scenario, seed) combination
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1          # single-threaded run, see run_one.py
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=jan.tautorus@it-u.at
#SBATCH --output=logs/npv-pct-tuning-%A_%a.out
#SBATCH --error=logs/npv-pct-tuning-%A_%a.err

set -euo pipefail
# `module` isn't defined in sbatch's non-interactive shell here; skip purge if
# absent instead of failing (this workflow has no environment-module dependency).
command -v module >/dev/null 2>&1 && module purge || true

export MPLBACKEND=Agg
export STEELO_HOME="/local/$USER/steelo_home_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p logs "$STEELO_HOME"

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

# Private, git-ignored credentials file (GUROBI_CLUSTER_LICENSE_SETUP.md Step 4, Option B).
[ -f "$HOME/.gurobi_credentials.env" ] && source "$HOME/.gurobi_credentials.env"

# Index -> combination table. Must match npv_pct_tuning.yaml's scenarios: list order.
SCENARIOS=(baseline baseline baseline baseline npv_pct_full npv_pct_full npv_pct_full npv_pct_full npv_pct_narrowed npv_pct_narrowed npv_pct_narrowed npv_pct_narrowed)
LABELS=(seed1 seed2 seed3 seed1_repeat seed1 seed2 seed3 seed1_repeat seed1 seed2 seed3 seed1_repeat)
SEEDS=(1 2 3 1 1 2 3 1 1 2 3 1)
NPV_PCTS=(0.1 0.1 0.1 0.1 1.0 1.0 1.0 1.0 1.0 1.0 1.0 1.0)
PRIORITY_PCTS=(5 5 5 5 5 5 5 5 0.5 0.5 0.5 0.5)

I=$SLURM_ARRAY_TASK_ID
SCENARIO=${SCENARIOS[$I]}
LABEL=${LABELS[$I]}
SEED=${SEEDS[$I]}
NPV_PCT=${NPV_PCTS[$I]}
PRIORITY_PCT=${PRIORITY_PCTS[$I]}

# Per-scenario subdirectory (config/ layout parse_stage_timings.py expects: one
# manifest.csv per scenario, not one flat manifest across all three).
SCRATCH_OUT="/local/$USER/outputs/npv_pct_tuning/$SCENARIO"
RUN_ID="run_${LABEL}"
RUN_DIR="$SCRATCH_OUT/$RUN_ID"
mkdir -p "$RUN_DIR" "$SCRATCH_OUT/manifest_fragments"

echo "Job $SLURM_ARRAY_JOB_ID task $I: scenario=$SCENARIO label=$LABEL seed=$SEED calculate_npv_pct=$NPV_PCT priority_pct=$PRIORITY_PCT starting on $(hostname)"

PARAMS_JSON="{\"probabilistic_agents\": false, \"optimization_solver\": \"gurobi\", \"enable_furnace_group_clustering\": true, \"random_seed\": $SEED, \"calculate_npv_pct\": $NPV_PCT, \"geo_config.priority_pct\": $PRIORITY_PCT}"

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

python - "$RUN_ID" "$RUN_DIR" "$STATUS" "$DURATION" "$PARAMS_JSON" "$SCRATCH_OUT/manifest_fragments/${I}.csv" <<'PYEOF'
import csv
import sys

run_id, run_dir, status, duration, params_json, out_path = sys.argv[1:7]
with open(out_path, "w", newline="") as f:
    csv.writer(f).writerow([run_id, run_dir, status, duration, params_json])
PYEOF

# Copy this task's own --error log into RUN_DIR under a job-ID-independent name, so
# retries/analysis can find it by scenario+label alone -- %A_%a in the --error path
# changes across resubmits, RUN_DIR does not. Copied, not moved: SLURM still owns the
# original. Also named task.err to match parse_stage_timings.py's expected filename.
cp "logs/npv-pct-tuning-${SLURM_ARRAY_JOB_ID}_${I}.err" "$RUN_DIR/task.err" 2>/dev/null || true

echo "Task $I (scenario=$SCENARIO label=$LABEL) finished: $STATUS (${DURATION}s)"
[ "$STATUS" = "success" ]
