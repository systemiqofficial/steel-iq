#!/bin/bash
# Trade-LP solver benchmark: same 3 seeds run under 4 configs -- (1) highs (baseline,
# no clustering, hipo/IPM), (2) gurobi (no clustering, barrier), (3) gurobi +
# furnace-group clustering (barrier), (4) gurobi + clustering + dual-simplex warm-start
# -- as a 12-task SLURM job array, one task per (config, seed) combination. Reusing the
# same seeds across configs isolates solver-choice/clustering effects from seed noise.
#
# Configs 1-3 use each backend's own default solver_options (see
# trade_lp_modelling.py's _default_solver_options) -- each config's fastest realistic
# setup, not a like-for-like algorithm comparison. Config 4 only pairs warm-start with
# clustering: warm-starting only works with simplex-family methods, not barrier/IPM,
# and meta-furnace-group IDs (technology+location+reductant) are stable year to year,
# making the clustered LP the plausible case for it. Unclustered-gurobi+warm-start and
# highs+clustering aren't tested here.
#
# Each config gets its own self-contained output tree (manifest.csv + run_000N dirs),
# matching run_seed_sweep.sbatch's output layout, so
# run_solver_benchmark_analyze.sbatch runs analyze_seed_sensitivity.py once per config
# unmodified, plus a runtime_summary.csv comparing configs.
#
# Prerequisite: `uv sync --extra gurobi` (once, on the login node) and a working
# Gurobi license reachable from labgpu01. Without a license, the highs tasks (0-2)
# still succeed; the gurobi/gurobi_clustering/gurobi_clustering_warmstart tasks (3-11)
# fail with a GurobiError.
#
# Submit:  sbatch scripts/slurm/run_solver_benchmark.sbatch
# Then, once all 12 tasks finish (success or not), queue analysis:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_solver_benchmark_analyze.sbatch
# Retry a single (config, seed) task (reuses the same scratch dir, only overwrites that
# task's run_000N):
#   sbatch --array=<TASK_ID> scripts/slurm/run_solver_benchmark.sbatch
#
# Sizing: --mem=32G / --time=36:00:00, sized for the slowest config (highs, no
# clustering/gurobi) with ~40% headroom over its measured peak wall-clock/RSS. Not
# right-sized for a gurobi_clustering-only run -- use run_gurobi_clustered_sweep.sbatch
# for that. Partition must stay verylong -- long caps at 6h; confirm cluster tiers
# with `sinfo -o "%P %l"`.

#SBATCH --job-name=steelo-solver-benchmark
#SBATCH --partition=verylong
#SBATCH --nodelist=labgpu01        # cluster's only node
#SBATCH --array=0-11               # 4 configs x 3 seeds, see task->(config,seed) mapping below
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1          # single-threaded run, see run_one.py
#SBATCH --mem=32G
#SBATCH --time=36:00:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=jan.tautorus@it-u.at
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

CONFIGS=(highs gurobi gurobi_clustering gurobi_clustering_warmstart)
SEEDS=(101 102 103)
N_SEEDS=${#SEEDS[@]}

CONFIG_IDX=$((SLURM_ARRAY_TASK_ID / N_SEEDS))
SEED_IDX=$((SLURM_ARRAY_TASK_ID % N_SEEDS))
CONFIG=${CONFIGS[$CONFIG_IDX]}
SEED=${SEEDS[$SEED_IDX]}

case "$CONFIG" in
    highs)
        SOLVER_PARAMS='"optimization_solver": "highs", "enable_furnace_group_clustering": false' ;;
    gurobi)
        SOLVER_PARAMS='"optimization_solver": "gurobi", "enable_furnace_group_clustering": false' ;;
    gurobi_clustering)
        SOLVER_PARAMS='"optimization_solver": "gurobi", "enable_furnace_group_clustering": true' ;;
    gurobi_clustering_warmstart)
        SOLVER_PARAMS='"optimization_solver": "gurobi", "enable_furnace_group_clustering": true'
        # 1 = dual simplex -- see trade_lp_modelling.py's _default_solver_options(). Only
        # this config sets it; the plain gurobi/gurobi_clustering configs above keep the
        # barrier default (Method=2, hardcoded fallback when this env var is unset).
        export STEELO_GUROBI_METHOD=1 ;;
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
