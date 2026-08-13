#!/bin/bash
# 5-seed PAM noise-floor sweep (co2_ramp_seed_sweep.yaml) on SLURM, as a job array:
# one independent SLURM task per seed (task_id -> seed = task_id + 1), instead of 5
# subprocesses sharing one job's resource allocation. This makes per-seed memory/time
# sizing tractable (one process's footprint, not a guessed aggregate for 5 concurrent
# growing processes) and means a single seed timing out or OOMing doesn't take the
# other 4 down with it.
#
# Submit with:
#   sbatch scripts/slurm/run_seed_sweep.sbatch
# Note the printed job ID (call it <ARRAY_JOBID>), then queue the analysis step to run
# once all 5 tasks are done (successfully or not -- see run_seed_sweep_analyze.sbatch,
# whose manifest reader already skips non-"success" rows, so e.g. 4/5 seeds still
# produces a valid, smaller analysis):
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_seed_sweep_analyze.sbatch
#
# Retrying a single failed/timed-out seed (task IDs are 0-based, seed = task_id + 1):
#   sbatch --array=<TASK_ID> scripts/slurm/run_seed_sweep.sbatch
# This reuses the same fixed scratch output dir, so it only overwrites that seed's
# run_000N directory -- the other already-succeeded runs are untouched. Re-run the
# analyze job afterward (new dependency on the retry's job ID) to pick up the fix.
#
# One-time setup before first submission (on the login node, needs internet):
#   git clone -b pam-benchmark-cluster https://github.com/systemiqofficial/steel-iq.git ~/steel-iq && cd ~/steel-iq && uv sync
#   rsync the already-prepared co2_ramp_scenario data dir to
#   ~/.steelo/preparation_cache/co2_ramp_scenario/data (and the master Excel to
#   ~/.steelo/data_cache/master-input-2026-08-07/) rather than re-running
#   apply_scenario_overrides.py on the cluster.
#
# Sizing rationale (updated with real job 27923 data, all 5 seeds succeeded):
# real elapsed runtime was ~36h/seed for the full 25-year run (2025-2050) -- this
# replaces the old 44h naive-linear-extrapolation guess. --time=48:00:00 below keeps
# ~33% margin over that; tighten further only once multiple full runs confirm 36h
# holds across all 5 seeds and the per-year cost curve doesn't accelerate late in
# the run (unconfirmed -- see CLUSTER_RUNBOOK.md's "Capturing fine-grained
# runtime/memory data" section for how to check).
#
# --mem=32G is a ballpark, not a measurement: it sits above the ~20-25G range the
# model docs suggest for a full run, but job 27923's own logs ran at the default
# --log-level WARNING, so the in-app peak_rss_mb snapshots (simulation.py's
# _log_memory_usage) were never emitted -- see SWEEP_DIAGNOSTICS_HANDOFF.md. Pull a
# real number instead of trusting this ballpark: SLURM's own accounting sampled
# memory independently of app log level, so
#   sacct -j 27923 --format=JobID,Elapsed,MaxRSS,MaxVMSize,State --units=G
# gives a real peak-RSS reading for all 5 already-completed tasks with no rerun
# needed. The /usr/bin/time -v wrapper added below captures the same thing (Maximum
# resident set size) for every future run as a second, log-level-independent check.
#
# Partition: this cluster's tiers (short<=1h/medium<=3h/long<=6h/verylong<=4d) mean
# --time=48:00:00 only fits verylong -- long's 6h cap is nowhere near enough.
# Confirm the real limits before trusting this: sinfo -o "%P %l".

#SBATCH --job-name=steelo-seed-sweep
#SBATCH --partition=verylong       # labgpu01 is the cluster's only node; partitions are time-limit
#SBATCH --nodelist=labgpu01        # tiers (short<=1h/medium<=3h/long<=6h/verylong<=4d) -- confirm on
#SBATCH --array=0-4                # the real cluster with `sinfo -o "%P %l"`. One task per seed (task_id -> seed = task_id + 1)
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1          # each seed run is single-threaded -- see run_one.py
#SBATCH --mem=32G                  # ballpark above the ~20-25G doc guidance -- see sacct note above
#SBATCH --time=48:00:00            # ~33% margin over the real ~36h/seed measured in job 27923
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=jan.tautorus@it-u.at
#SBATCH --output=logs/seed-sweep-%A_%a.out
#SBATCH --error=logs/seed-sweep-%A_%a.err

set -euo pipefail
# `module` is only defined as a shell function in interactive/login shells on this
# cluster; sbatch runs non-interactively, so it's absent here regardless of array vs
# single-job. This workflow has no environment-module dependency (plain Python/uv
# venv), so skip purge entirely when the command isn't available instead of assuming
# it must succeed.
command -v module >/dev/null 2>&1 && module purge || true

export MPLBACKEND=Agg
export STEELO_HOME="/local/$USER/steelo_home_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p logs "$STEELO_HOME"

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

SEED=$((SLURM_ARRAY_TASK_ID + 1))
RUN_ID=$(printf "run_%04d" "$SLURM_ARRAY_TASK_ID")

# Fixed (not job-ID-suffixed): a retry of a single failed task must land in the same
# tree as the other already-succeeded seeds, not a fresh directory.
SCRATCH_OUT="/local/$USER/outputs/co2_ramp_seed_sweep"
RUN_DIR="$SCRATCH_OUT/$RUN_ID"
mkdir -p "$RUN_DIR" "$SCRATCH_OUT/manifest_fragments"

echo "Job $SLURM_ARRAY_JOB_ID task $SLURM_ARRAY_TASK_ID (seed=$SEED) starting on $(hostname)"

PARAMS_JSON="{\"probabilistic_agents\": false, \"random_seed\": $SEED}"

T0=$(date +%s)
STATUS="failed"
# /usr/bin/time -v writes "Maximum resident set size" (KB) plus CPU/wall stats to
# stderr, already captured by --error above -- a real peak-RSS reading independent
# of the app's own --log-level, so it's captured on every run regardless of whether
# --log-level is raised for the fine-grained operation=/memory_snapshot logs below.
if /usr/bin/time -v python -m scripts.sensitivity.run_one \
    --data-dir "$HOME/.steelo/preparation_cache/co2_ramp_scenario/data" \
    --master-excel "$HOME/.steelo/data_cache/master-input-2026-08-07/master_input.xlsx" \
    --output-dir "$RUN_DIR" \
    --start-year 2025 --end-year 2050 \
    --params-json "$PARAMS_JSON"; then
    STATUS="success"
fi
T1=$(date +%s)
DURATION=$((T1 - T0))

# run_one.py's own status.json is authoritative when present (matches run_sweep.py's
# logic) -- covers the case where the process exits 0 but the run itself failed, or
# vice versa. If SLURM hard-kills the process (timeout/OOM) before it can write
# status.json at all, the file won't exist and STATUS stays "failed" from above.
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
