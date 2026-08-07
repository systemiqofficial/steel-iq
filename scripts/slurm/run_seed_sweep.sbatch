#!/bin/bash
# 5-seed PAM noise-floor sweep (co2_ramp_seed_sweep.yaml) on SLURM.
# Fill in the placeholders below for your cluster, then submit with:
#   sbatch scripts/slurm/run_seed_sweep.sbatch
#
# One-time setup before first submission (on the login node, needs internet):
#   git clone -b pam-benchmark <repo-url> ~/steel-iq && cd ~/steel-iq && uv sync
#   rsync the already-prepared co2_ramp_scenario data dir to
#   ~/.steelo/preparation_cache/co2_ramp_scenario/data (and the master Excel to
#   ~/.steelo/data_cache/master-input-v2.0.0/) rather than re-running
#   apply_scenario_overrides.py on the cluster.
#
# Resumable: if this job is killed (walltime, node failure), just `sbatch` it
# again -- run_sweep.py skips any run_id whose status.json already says success.

#SBATCH --job-name=steelo-seed-sweep
#SBATCH --partition=<PARTITION for labgpu01>
#SBATCH --nodelist=labgpu01
#SBATCH --account=<ACCOUNT>
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=5          # 1 core per concurrent seed run (each is single-threaded)
#SBATCH --mem=64G                  # observed peak ~9-10.7GB/run locally x5 + headroom
#SBATCH --time=16:00:00            # observed ~9.5-10h/seed locally; padded
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=<your-email>
#SBATCH --output=logs/seed-sweep-%j.out
#SBATCH --error=logs/seed-sweep-%j.err

set -euo pipefail
module purge

export MPLBACKEND=Agg
export STEELO_HOME="/local/$USER/steelo_home_${SLURM_JOB_ID}"
mkdir -p logs "$STEELO_HOME"

source "$HOME/steel-iq/.venv/bin/activate"
cd "$HOME/steel-iq"

echo "Job $SLURM_JOB_ID starting on $(hostname), $SLURM_CPUS_PER_TASK cpus"

SCRATCH_OUT="/local/$USER/outputs/co2_ramp_seed_sweep_${SLURM_JOB_ID}"

python -m scripts.sensitivity.run_sweep \
    --grid-file scripts/sensitivity/grids/co2_ramp_seed_sweep.yaml \
    --data-dir "$HOME/.steelo/preparation_cache/co2_ramp_scenario/data" \
    --master-excel "$HOME/.steelo/data_cache/master-input-v2.0.0/master_input.xlsx" \
    --out-dir "$SCRATCH_OUT" \
    --jobs "$SLURM_CPUS_PER_TASK" \
    --threads-per-job 1

python -m scripts.sensitivity.analyze_seed_sensitivity \
    --sweep-dir "$SCRATCH_OUT" \
    --out-dir "$SCRATCH_OUT/analysis"

mkdir -p "$HOME/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep"
rsync -a "$SCRATCH_OUT/" "$HOME/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep/"

echo "Job $SLURM_JOB_ID finished"
