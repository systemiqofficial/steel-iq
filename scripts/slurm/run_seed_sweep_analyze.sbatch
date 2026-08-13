#!/bin/bash
# Post-processing for run_seed_sweep.sbatch's array job: merges each task's manifest
# fragment into one manifest.csv, runs analyze_seed_sensitivity.py, and syncs results
# to NFS home. Submit only after all 5 array tasks finish, successfully or not --
# the manifest reader skips non-"success" rows, so partial results still work:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_seed_sweep_analyze.sbatch
# Re-run after retrying any failed seed to fold the fix into the analysis.

#SBATCH --job-name=steelo-seed-sweep-analyze
#SBATCH --partition=short
#SBATCH --nodelist=labgpu01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=logs/seed-sweep-analyze-%j.out
#SBATCH --error=logs/seed-sweep-analyze-%j.err

set -euo pipefail
# See run_seed_sweep.sbatch: `module` isn't defined in sbatch's non-interactive shell
# on this cluster, and this workflow has no environment-module dependency.
command -v module >/dev/null 2>&1 && module purge || true
export MPLBACKEND=Agg

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

SCRATCH_OUT="/local/$USER/outputs/co2_ramp_seed_sweep"

{
    echo "run_id,output_dir,status,duration_s,params_json"
    cat "$SCRATCH_OUT"/manifest_fragments/*.csv
} > "$SCRATCH_OUT/manifest.csv"

echo "Manifest:"
cat "$SCRATCH_OUT/manifest.csv"

python -m scripts.sensitivity.analyze_seed_sensitivity \
    --sweep-dir "$SCRATCH_OUT" \
    --out-dir "$SCRATCH_OUT/analysis"

mkdir -p "$HOME/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep"
rsync -a "$SCRATCH_OUT/" "$HOME/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep/"

echo "Analysis complete, results synced to $HOME/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep"
