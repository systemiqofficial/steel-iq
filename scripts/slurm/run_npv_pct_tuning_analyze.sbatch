#!/bin/bash
# Post-processing for run_npv_pct_tuning.sbatch's array job: merges each scenario's
# manifest fragments into a per-scenario manifest.csv, runs
# analyze_npv_pct_tuning.py, and syncs results to NFS home. Submit only after all 12
# array tasks finish, successfully or not:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_npv_pct_tuning_analyze.sbatch
# Re-run after retrying any failed task to fold the fix into the analysis.
#
# Sizing: --mem=8G (copied from run_gurobi_clustered_sweep_analyze.sbatch's single-sweep
# pattern) OOM-killed on the first attempt (2026-08-20, job 28446). Unlike that script,
# analyze_npv_pct_tuning.py runs analyze_seed_sensitivity.py as three sequential
# subprocesses (one per scenario, each loading full 2025-2050 post_processed_*.csv data
# under SLURM's cgroup memory limit, which sums the whole process tree, not just this
# script's own process) plus its own file-hashing pass for the determinism check.
# Bumped to 24G with no real profiling behind that number beyond "comfortably above the
# single-scenario 8G baseline for 3x the concurrent-tree memory pressure" -- correct
# against sacct's MaxRSS for this job after it succeeds.

#SBATCH --job-name=steelo-npv-pct-tuning-analyze
#SBATCH --partition=short
#SBATCH --nodelist=labgpu01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --time=00:30:00
#SBATCH --output=logs/npv-pct-tuning-analyze-%j.out
#SBATCH --error=logs/npv-pct-tuning-analyze-%j.err

set -euo pipefail
command -v module >/dev/null 2>&1 && module purge || true
export MPLBACKEND=Agg

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

SCRATCH_OUT="/local/$USER/outputs/npv_pct_tuning"

for SCENARIO in baseline npv_pct_full npv_pct_narrowed; do
    {
        echo "run_id,output_dir,status,duration_s,params_json"
        cat "$SCRATCH_OUT/$SCENARIO"/manifest_fragments/*.csv
    } > "$SCRATCH_OUT/$SCENARIO/manifest.csv"
    echo "Manifest for $SCENARIO:"
    cat "$SCRATCH_OUT/$SCENARIO/manifest.csv"
done

python -m scripts.sensitivity.analyze_npv_pct_tuning \
    --sweep-root "$SCRATCH_OUT" \
    --out-dir "$SCRATCH_OUT/analysis"

mkdir -p "$HOME/steel-iq/outputs/sensitivity/npv_pct_tuning"
rsync -a "$SCRATCH_OUT/" "$HOME/steel-iq/outputs/sensitivity/npv_pct_tuning/"

echo "Analysis complete, results synced to $HOME/steel-iq/outputs/sensitivity/npv_pct_tuning"
