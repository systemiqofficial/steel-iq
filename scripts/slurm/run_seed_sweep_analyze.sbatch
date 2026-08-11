#!/bin/bash
# Post-processing step for run_seed_sweep.sbatch's array job: merges each task's
# manifest fragment into one manifest.csv, copies results to the persistent home-dir
# tree, then runs analyze_seed_sensitivity.py.
#
# Submit only after the array job (all 5 tasks) has finished, successfully or not --
# analyze_seed_sensitivity.py's manifest reader already skips non-"success" rows, so
# partial results (e.g. 4/5 seeds) still produce a valid, smaller analysis:
#   sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_seed_sweep_analyze.sbatch
#
# If you later retry a failed seed with `sbatch --array=<TASK_ID> run_seed_sweep.sbatch`,
# re-run this analyze job afterward (depending on the retry's job ID) to fold the fix in.

#SBATCH --job-name=steelo-seed-sweep-analyze
#SBATCH --partition=short
#SBATCH --nodelist=labgpu01
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=jan.tautorus@it-u.at
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
