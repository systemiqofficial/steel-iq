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
# real elapsed runtime for the full 25-year run (2025-2050) ranged 36.4h-39.0h across
# all 5 seeds (worst case: 39h01m) -- this replaces the old 44h naive-linear-
# extrapolation guess. --time=48:00:00 below keeps ~23% margin over the observed
# worst case, not a huge cushion -- if the --log-level INFO change below (not yet
# measured for overhead) pushes wall time up meaningfully, bump toward 60:00:00
# rather than trust a tight margin. Tighten further only once multiple full runs
# confirm this range holds and the per-year cost curve doesn't accelerate late in
# the run -- this run is now the one collecting that data, see --log-level note below.
#
# --mem=32G is a ballpark, not a measurement. `sacct -j 27923 --format=JobID,Elapsed,
# MaxRSS,MaxVMSize,State --units=G` came back with MaxRSS blank for every task --
# this cluster's SLURM accounting isn't sampling memory at all -- confirmed via
# `scontrol show config`: JobAcctGatherType=jobacct_gather/none, cluster-wide, not
# just this job. sacct will never supply MaxRSS here (past or future) unless
# whoever administers the cluster changes that slurm.conf setting -- not something
# to route around in this script. The /usr/bin/time -v wrapper below is the
# permanent real peak-RSS source instead, independent of both app --log-level and
# this cluster's accounting config.
#
# Partition: this cluster's tiers (short<=1h/medium<=3h/long<=6h/verylong<=4d) mean
# --time=48:00:00 only fits verylong -- long's 6h cap is nowhere near enough.
# Confirm the real limits before trusting this: sinfo -o "%P %l".
#
# --log-level INFO (below, was WARNING) turns on plant_agent.py's per-stage
# operation=<stage> year=<Y> duration_s=<T> timers and simulation.py's
# _log_memory_usage peak_rss_mb snapshots every year -- the year-by-year trend
# CLUSTER_RUNBOOK.md's "Capturing fine-grained runtime/memory data" section asks
# for, piggybacked onto this real sweep rather than a separate diagnostic run.
# CAUTION: this also surfaces every other INFO-level log in the codebase (~260
# call sites across geo/pam/tm), not just these two sources -- logging_config.yaml's
# function_overrides can only suppress a function, never promote one above the CLI
# floor, so there's no way to isolate just these two without also seeing the rest.
# Before trusting this run's numbers, rule out logging itself as a confound with a
# cheap short check first (see CLUSTER_RUNBOOK.md): a 2-3 year salloc run at
# --log-level WARNING vs --log-level INFO, comparing wall time and .err log size.
# If INFO turns out to add meaningful overhead or unmanageable log volume, revert
# this to WARNING and fall back to the /usr/bin/time -v numbers only.

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
    --log-level INFO \
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
