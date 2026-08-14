#!/bin/bash
# Trade-LP solver benchmark: same 3 seeds run under 3 configs -- (1) highs (baseline,
# no clustering), (2) gurobi (no clustering), (3) gurobi + furnace-group clustering --
# as a 9-task SLURM job array, one task per (config, seed) combination. Reusing the
# same seeds across configs isolates solver-choice/clustering effects from seed noise
# (see analyze_seed_sensitivity.py's own seed-sweep for the noise-floor question this
# benchmark deliberately holds fixed).
#
# Each config gets its own self-contained output tree (manifest.csv + run_000N dirs),
# structured exactly like run_seed_sweep.sbatch's output, so
# run_solver_benchmark_analyze.sbatch can run analyze_seed_sensitivity.py once per
# config unmodified, plus a runtime_summary.csv comparing configs.
#
# Prerequisite: `uv sync --extra gurobi` (once, on the login node) and a working
# Gurobi license reachable from labgpu01 -- see GUROBI_CLUSTER_LICENSE_SETUP.md.
# Without a license, the highs task (0-2) still succeeds; the gurobi/gurobi_clustering
# tasks (3-8) will fail with a GurobiError, which is expected until licensing is done.
#
# Submit:  sbatch scripts/slurm/run_solver_benchmark.sbatch
# Then, once all 9 tasks finish (success or not), queue analysis:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_solver_benchmark_analyze.sbatch
# Retry a single (config, seed) task (reuses the same scratch dir, only overwrites that
# task's run_000N):
#   sbatch --array=<TASK_ID> scripts/slurm/run_solver_benchmark.sbatch
#
# Sizing: 2025-2040 (16 years) is ~62% of the 26-year 2025-2050 range job 27923
# benchmarked at 36.4-39.0h/seed on highs with no clustering (see
# run_seed_sweep.sbatch's header) -- extrapolating linearly gives ~24h for this
# range's slowest (highs, no clustering) task; --time=36:00:00 below adds buffer on
# top of that extrapolation, since it isn't a real measurement for this shorter range.
# Gurobi and gurobi+clustering tasks are expected to finish well inside that same
# budget (~4.6-4.9x LP-stage speedup for gurobi alone, ~2.6x total per-year speedup
# for clustering, per CLAUDE.md's benchmark notes) -- rerun this comment's math once
# real sacct numbers exist for this range and update the estimate instead of guessing
# twice. --mem=32G carries over from the seed sweep's proven sizing (shorter horizon
# shouldn't increase peak memory). Partition must stay verylong -- long caps at 6h;
# confirm cluster tiers with `sinfo -o "%P %l"`.

#SBATCH --job-name=steelo-solver-benchmark
#SBATCH --partition=verylong
#SBATCH --nodelist=labgpu01        # cluster's only node
#SBATCH --array=0-8                # 3 configs x 3 seeds, see task->(config,seed) mapping below
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1          # single-threaded run, see run_one.py
#SBATCH --mem=32G
#SBATCH --time=36:00:00
#SBATCH --output=logs/solver-benchmark-%A_%a.out
#SBATCH --error=logs/solver-benchmark-%A_%a.err

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
# Option B) -- sourced unconditionally since it's harmless for the highs task and
# required for the gurobi/gurobi_clustering ones.
[ -f "$HOME/.gurobi_credentials.env" ] && source "$HOME/.gurobi_credentials.env"

CONFIGS=(highs gurobi gurobi_clustering)
SEEDS=(101 102 103)
N_SEEDS=${#SEEDS[@]}

CONFIG_IDX=$((SLURM_ARRAY_TASK_ID / N_SEEDS))
SEED_IDX=$((SLURM_ARRAY_TASK_ID % N_SEEDS))
CONFIG=${CONFIGS[$CONFIG_IDX]}
SEED=${SEEDS[$SEED_IDX]}

case "$CONFIG" in
    highs)             SOLVER_PARAMS='"optimization_solver": "highs", "enable_furnace_group_clustering": false' ;;
    gurobi)             SOLVER_PARAMS='"optimization_solver": "gurobi", "enable_furnace_group_clustering": false' ;;
    gurobi_clustering)  SOLVER_PARAMS='"optimization_solver": "gurobi", "enable_furnace_group_clustering": true' ;;
    *) echo "Unknown config index $CONFIG_IDX for task $SLURM_ARRAY_TASK_ID" >&2; exit 1 ;;
esac
PARAMS_JSON="{\"probabilistic_agents\": false, \"random_seed\": $SEED, $SOLVER_PARAMS}"

RUN_ID=$(printf "run_%04d" "$SEED_IDX")

# Fixed (not job-ID-suffixed) so a retry lands in the same tree as already-succeeded
# (config, seed) combinations.
SCRATCH_OUT="/local/$USER/outputs/solver_benchmark/$CONFIG"
RUN_DIR="$SCRATCH_OUT/$RUN_ID"
mkdir -p "$RUN_DIR" "$SCRATCH_OUT/manifest_fragments"

echo "Job $SLURM_ARRAY_JOB_ID task $SLURM_ARRAY_TASK_ID (config=$CONFIG, seed=$SEED) starting on $(hostname)"

T0=$(date +%s)
STATUS="failed"
# /usr/bin/time -v writes peak RSS to stderr (captured by --error), independent of
# --log-level.
if /usr/bin/time -v python -m scripts.sensitivity.run_one \
    --data-dir "$HOME/.steelo/preparation_cache/co2_ramp_authored/data" \
    --master-excel "$HOME/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx" \
    --output-dir "$RUN_DIR" \
    --start-year 2025 --end-year 2040 \
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

python - "$RUN_ID" "$RUN_DIR" "$STATUS" "$DURATION" "$PARAMS_JSON" "$SCRATCH_OUT/manifest_fragments/${SEED_IDX}.csv" <<'PYEOF'
import csv
import sys

run_id, run_dir, status, duration, params_json, out_path = sys.argv[1:7]
with open(out_path, "w", newline="") as f:
    csv.writer(f).writerow([run_id, run_dir, status, duration, params_json])
PYEOF

# Copy this task's own --error log (operation=... duration/memory lines, plus
# /usr/bin/time -v's peak-RSS summary) into RUN_DIR under a job-ID-independent name,
# so run_solver_benchmark_analyze.sbatch can find it by (config, seed) alone --
# %A_%a in the --error path changes across resubmits/retries, RUN_DIR does not.
# Copied, not moved: SLURM still owns and may still be writing to the original path.
cp "logs/solver-benchmark-${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.err" "$RUN_DIR/task.err" 2>/dev/null || true

echo "Task $SLURM_ARRAY_TASK_ID (config=$CONFIG, seed=$SEED) finished: $STATUS (${DURATION}s)"
[ "$STATUS" = "success" ]
