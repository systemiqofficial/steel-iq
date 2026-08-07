#!/bin/bash
# Baseline single-node CPU test run of the steel model on SLURM.
# Fill in the placeholders below for your cluster, then submit with:
#   sbatch scripts/slurm/run_baseline.sbatch

#SBATCH --job-name=steelo-baseline
#SBATCH --partition=<PARTITION>          # e.g. cpu, batch -- NOT the GPU partition
#SBATCH --account=<ACCOUNT>              # remove if the cluster has no accounting
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4                # bump to 1/8/etc. across repeat submissions to benchmark
#SBATCH --mem=16G                        # simulations can be memory-intensive; adjust after first run
#SBATCH --time=02:00:00
#SBATCH --output=logs/steelo-baseline-%j.out
#SBATCH --error=logs/steelo-baseline-%j.err

set -euo pipefail

module purge
# module load <python-module-if-needed>  # only if uv can't manage its own Python on this cluster

export MPLBACKEND=Agg                              # headless plotting, no X server on compute nodes
export STEELO_HOME="${STEELO_HOME:-$PWD/.steelo_home}"  # point at scratch/project space, not $HOME quota
mkdir -p logs "$STEELO_HOME"

# Assumes `uv sync` was already run once on the login node (needs internet) and
# `.venv` / $STEELO_HOME data cache are present, since compute nodes may be offline.
source .venv/bin/activate

echo "Job $SLURM_JOB_ID starting on $(hostname), $SLURM_CPUS_PER_TASK cpus"

/usr/bin/time -v run_simulation \
    --start-year 2025 \
    --end-year 2027 \
    --log-level INFO \
    --output-dir "$STEELO_HOME/output/baseline_${SLURM_JOB_ID}"

echo "Job $SLURM_JOB_ID finished"
