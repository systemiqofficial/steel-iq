# Running the CO2-ramp PAM seed-sensitivity sweep on SLURM

This branch (`pam-benchmark-cluster`) carries **only** the CO2-ramp
seed-sensitivity sweep framework, layered directly on top of `develop`. It
does not carry the `pam-benchmark` branch's BOA-benchmark tooling,
HiGHS/Gurobi solver-switch option, or working-notes docs — those were
deliberately left behind as unrelated to this task. `develop`'s
`probabilistic_agents=False` behavior is already deterministic (see
`Plant.evaluate_furnace_group_strategy`, `src/steelo/domain/models.py`) —
there is no bug fix bundled here, nothing to patch before running.

## Goal

Run the same full simulation (2025-2050) 5 times, varying only `random_seed`
(1-5) under `probabilistic_agents: false` and a shared CO2-ramp price
scenario, then compare outcomes across seeds. The question this answers: how
much does randomness that `probabilistic_agents=False` *cannot* eliminate
(capacity/CO2-limit allocation order when a shared constraint binds,
geospatial siting lottery for new plants) still move the final technology
mix, once tech-selection randomness itself is removed?

Context worth knowing: a prior single run (n=1, not part of this branch)
showed a technology-mix discontinuity around 2045, right as the CO2 price
plateaus after its ramp — nicknamed "the 2045 price jump" in prior
investigation. Part of what this sweep checks is whether that's a real,
seed-independent effect or an artifact of one particular run's randomness.

## 1. Environment setup (login node — needs internet)

```bash
git clone -b pam-benchmark-cluster <repo-url> ~/steel-iq
cd ~/steel-iq
curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if `uv --version` already works
uv sync
```

`geopandas`/`fiona`/`cartopy`/`osmnx`/`highspy` all have C-extension
dependencies (GDAL/GEOS/PROJ). Manylinux wheels usually cover this, but
before trusting a long batch job to it: confirm `uv sync` finishes without
falling back to a from-source build, and that the geospatial imports actually
work:
```bash
uv run python -c "import geopandas, fiona, cartopy, osmnx, highspy; print('ok')"
```

## 2. Data

Two options — pick whichever matches the cluster's network posture:

**(a) Reuse already-prepared data** (faster, avoids network/S3 dependency on
the cluster): `rsync`/`scp` these two directories from the machine that
already prepared them to the cluster's NFS home:
- `~/.steelo/preparation_cache/co2_ramp_scenario/data` (the CO2-ramp scenario
  fixtures)
- `~/.steelo/data_cache/master-input-v2.0.0/master_input.xlsx` (the master
  Excel)

**(b) Regenerate from scratch** on the login node (needs S3 credentials
configured there — see `docs/data_management/`):
```bash
uv run steelo-data-prepare --master-excel <path-to-master-excel> \
    --output-dir ~/.steelo/preparation_cache/co2_ramp_baseline/data
uv run python -m scripts.sensitivity.apply_scenario_overrides \
    --data-dir ~/.steelo/preparation_cache/co2_ramp_baseline/data \
    --scenario scripts/sensitivity/scenarios/co2_ramp.yaml \
    --output-dir ~/.steelo/preparation_cache/co2_ramp_scenario/data
```
Spot-check a couple of countries afterward — `carbon_costs.json`'s per-ISO3
`carbon_cost` series should read `{"2025": 0.0, "2030": 60.0, "2035": 120.0,
"2040": 180.0, "2045": 180.0, "2050": 180.0}` uniformly.

Either way: **prepared input data goes on NFS home** (small, read-mostly,
survives across jobs). **Per-run scratch/output goes on `/local/$USER` on
labgpu01** — 5 concurrent processes writing logs/plots/CSVs to NFS
simultaneously risks metadata contention; local disk avoids that. `/local`
has 20TB available and is not guaranteed to persist across nodes or be
visible from the login node, so copy final results back to NFS at job end
(the sbatch script below already does this).

## 3. Smoke test before the real submission

Grab a short interactive session and run one tiny simulation end-to-end
before trusting a 10-16 hour batch job to an unverified setup:
```bash
salloc --partition=<PARTITION> --nodelist=labgpu01 --cpus-per-task=1 --mem=8G --time=00:30:00
srun --pty bash
source ~/steel-iq/.venv/bin/activate
cd ~/steel-iq
python -m scripts.sensitivity.run_one \
    --data-dir ~/.steelo/preparation_cache/co2_ramp_scenario/data \
    --master-excel ~/.steelo/data_cache/master-input-v2.0.0/master_input.xlsx \
    --output-dir /tmp/smoke_test --start-year 2025 --end-year 2026 \
    --params-json '{"probabilistic_agents": false, "random_seed": 1}'
```
Confirms the whole pipeline (imports, data paths, `/local` writability) works
on this node/filesystem. While it runs, check `top`/`htop` for that process —
CPU usage should stay near 1 core. That confirms `OMP_NUM_THREADS=1` (etc.,
set by `run_sweep.py --threads-per-job`) is actually being honored by
HiGHS/numpy/BLAS on this platform — otherwise 5 concurrent runs in the real
job would thread-thrash against each other even though SLURM's cgroup caps
the job at 5 cores total.

## 4. Submit the sweep

`scripts/slurm/run_seed_sweep.sbatch` runs the 5 seeds as a SLURM **job
array** (`--array=0-4`, one independent task per seed, task_id -> seed =
task_id + 1) rather than as 5 subprocesses sharing one job's resource
allocation. That makes per-seed `--mem`/`--time` sizing tractable (one
process's footprint, not a guessed aggregate for 5 concurrent growing
processes) and means one seed timing out or OOMing doesn't take the other 4
down with it.

```bash
sbatch scripts/slurm/run_seed_sweep.sbatch
```

Note the printed job ID (`<ARRAY_JOBID>`), then queue the analysis step to
run once all 5 tasks are done, successfully or not — `analyze_seed_sensitivity.py`'s
manifest reader already skips non-`"success"` rows, so e.g. 4/5 seeds still
produces a valid, smaller analysis:

```bash
sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_seed_sweep_analyze.sbatch
```

**Retrying a single failed/timed-out seed** (task IDs are 0-based, seed =
task_id + 1) reuses the same fixed scratch output dir, so it only overwrites
that seed's `run_000N` — the other already-succeeded runs are untouched:

```bash
sbatch --array=<TASK_ID> scripts/slurm/run_seed_sweep.sbatch
# then re-run the analyze job, depending on the retry's job ID, to fold the fix in
```

Sizing: each array task requests `--cpus-per-task=1` — each run is
single-threaded since the annual simulation loop is strictly sequential, more
cores per run doesn't speed one run up. Budgeted from the smoke test (single
process, 1 cpu, 2 years, 16G peak, 3h32m) with margin: `--mem=48G` (~3x, since
memory likely grows with accumulated plants/locations over the full 25 years)
and `--time=90:00:00` (~2x the naive 44h/seed linear extrapolation from the
smoke test's per-year rate, since per-year cost likely isn't flat either).
These are conservative estimates, not measurements — tighten them if you get
a real `peak_rss_mb` reading from a full-length run.

## 5. Once it finishes

`run_seed_sweep_analyze.sbatch` merges each task's manifest fragment into one
`manifest.csv`, runs `analyze_seed_sensitivity.py`, and `rsync`s everything
back to `~/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep/` on NFS home.
Sanity-check the output:

- **All 5 seeds actually diverge** (not identical to the decimal) — if they
  match exactly, either `probabilistic_agents=False` isn't behaving as
  expected or the scenario data wasn't varied correctly.
- **Spread in final-year technology shares and plant-location shares across
  seeds** — this is the actual answer to the experiment's question. Report
  range/std, not just "they differ."
- **The 2045 price-jump pattern** (see Goal, above) — does it show up
  consistently across all 5 seeds, or only some? That distinguishes a real
  effect from a single-run artifact.
- If a matching local Windows run of the same seeds exists (`pam-benchmark`
  branch, `outputs/sensitivity/co2_ramp_seed_sweep/`), cross-check the same
  seed number across both platforms — small floating-point divergence
  between machines/OS/BLAS builds for the *same* seed is expected, not a bug,
  unless it's large enough to change qualitative conclusions.
