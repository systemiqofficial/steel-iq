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

The CO2-ramp scenario is authored directly into a clone of the master Excel's
"Carbon cost" sheet (`scripts/sensitivity/author_carbon_cost_scenario.py`),
then prepared through the normal `steelo-data-prepare` pipeline — no separate
post-prep patch step, unlike earlier versions of this workflow.

**Run `steelo-data-prepare` on the cluster, not on Windows.** Its console
output has a pre-existing Windows-only crash (`UnicodeEncodeError` on rich's
checkmark glyphs under the cp1252 codepage) that can hit *after* all fixture
files are written, making the run look fully successful even when it wasn't.
That's a real trap: `recreate_fallback_material_costs` in
`src/steelo/data/recreation_functions.py` catches any read failure and
silently writes an empty `fallback_material_costs.json` with only a
`[yellow]` console warning — easy to miss, especially if the console then
crashes on a later print before you'd scroll back to see it. It only
surfaced once, hours into a real cluster run, as `Run failed: No fallback
material costs loaded from fixtures`. Preparing on the cluster's Linux
console sidesteps the crash entirely, and also means only the ~50MB authored
Excel needs to cross the network, not the ~900MB prepared fixture tree.

**Step 1 — author the scenario workbook**, on whichever machine holds the
real master Excel (this only reads it; the original stays untouched as the
source of truth):
```bash
uv run python -m scripts.sensitivity.author_carbon_cost_scenario \
    --master-excel-path <path-to-real-master-excel> \
    --scenario scripts/sensitivity/scenarios/co2_ramp.yaml \
    --output-path ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx
```

**Step 2 — get the authored workbook onto the cluster's NFS home** (the real
master Excel never needs to leave the machine that holds it):
```bash
rsync -av ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx \
    <cluster-host>:~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx
```

**Step 3 — prepare fixtures on the cluster**, from the authored workbook now
sitting there. `--no-skip-existing` matters if `--output-dir` was ever
populated before (e.g. by an earlier, possibly-broken run) -- by default
`steelo-data-prepare` treats any file already present at the destination as
done and leaves it alone (`skip_existing=True` in
`src/steelo/data/preparation.py`), so a stale/broken fixture from a prior
run silently survives a "successful" re-prep otherwise:
```bash
uv run steelo-data-prepare \
    --master-excel ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx \
    --output-dir ~/.steelo/preparation_cache/co2_ramp_authored/data \
    --no-skip-existing
```
Spot-check afterward:
- `carbon_costs.json`'s per-ISO3 `carbon_cost` series should read
  `{"2025": 0.0, "2030": 60.0, "2035": 120.0, "2040": 180.0, "2045": 180.0,
  "2050": 180.0}` uniformly.
- `fixtures/fallback_material_costs.json` must **not** be a bare `[]`/`{}` —
  see the warning above.

(Step 1 needs S3 credentials only if you don't already have the real master
Excel locally — see `docs/data_management/`. If you do, it runs offline.)

Either way: **prepared input data goes on NFS home** (small, read-mostly,
survives across jobs). **Per-run scratch/output goes on `/local/$USER` on
labgpu01** — 5 concurrent processes writing logs/plots/CSVs to NFS
simultaneously risks metadata contention; local disk avoids that. `/local`
has 20TB available and is not guaranteed to persist across nodes or be
visible from the login node, so copy final results back to NFS at job end
(the sbatch script below already does this).

## 3. Smoke test before the real submission

Grab a short interactive session and run one tiny simulation end-to-end
before trusting a 36h+ batch job to an unverified setup:
```bash
salloc --partition=<PARTITION> --nodelist=labgpu01 --cpus-per-task=1 --mem=8G --time=00:30:00
srun --pty bash
source ~/steel-iq/.venv/bin/activate
cd ~/steel-iq
python -m scripts.sensitivity.run_one \
    --data-dir ~/.steelo/preparation_cache/co2_ramp_authored/data \
    --master-excel ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx \
    --output-dir /tmp/smoke_test --start-year 2025 --end-year 2026 \
    --params-json '{"probabilistic_agents": false, "random_seed": 1}'
```
Confirms the whole pipeline (imports, data paths, `/local` writability) works
on this node/filesystem. While it runs, check `top`/`htop` — CPU usage should
stay near 1 core, confirming HiGHS/numpy/BLAS actually honour single-threaded
execution on this platform rather than oversubscribing.

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
single-threaded (the annual simulation loop is strictly sequential, more
cores doesn't speed one run up). `--time=48:00:00` / `--mem=32G` come from
job `27923`'s real ~36.4-39.0h/seed runtime (`sacct`) and a ~20-25G
doc-guidance ballpark for memory — see the sbatch file's own header comment
for the full numbers, including why `sacct` can't supply a real `MaxRSS` on
this cluster (`JobAcctGatherType=jobacct_gather/none`, confirmed via
`scontrol show config`, cluster-wide and not fixable from this repo).

**Partition**: confirm `long` really caps at 6h and `verylong` at 4d before
trusting `--partition=verylong` — `sinfo -o "%P %l"`.

### Capturing fine-grained runtime/memory data

`run_seed_sweep.sbatch` now wraps the run in `/usr/bin/time -v` (real
peak-RSS, independent of `--log-level` and this cluster's broken accounting)
and passes `--log-level INFO` (was `WARNING`) to capture `plant_agent.py`'s
per-stage `operation=<stage> year=<Y> duration_s=<T>` timers and
`simulation.py`'s `_log_memory_usage` snapshots every year — a year-by-year
trend instead of one end-of-run number, piggybacked onto the real sweep
rather than a separate diagnostic job.

**Caution**: `--log-level INFO` also surfaces ~260 other INFO-level log call
sites across the codebase. `logging_config.yaml`'s `function_overrides` can't
narrow this to just these two sources — the CLI `--log-level` sets a single
shared handler threshold (`src/steelo/logging_config.py`), and Python drops
INFO records before the YAML filter ever runs when the floor is `WARNING`;
`function_overrides` can only suppress below that floor, never promote above
it. **Before submitting the real sweep**, rule out logging overhead as a
confound with a cheap side-by-side (a few minutes, not the real 36h+):
```bash
salloc --partition=<PARTITION> --nodelist=labgpu01 --cpus-per-task=1 --mem=8G --time=00:30:00
srun --pty bash
source ~/steel-iq/.venv/bin/activate && cd ~/steel-iq
for LEVEL in WARNING INFO; do
  /usr/bin/time -v python -m scripts.sensitivity.run_one \
    --data-dir ~/.steelo/preparation_cache/co2_ramp_authored/data \
    --master-excel ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx \
    --output-dir /tmp/logcheck_$LEVEL --start-year 2025 --end-year 2027 \
    --log-level $LEVEL \
    --params-json '{"probabilistic_agents": false, "random_seed": 1}' \
    2> /tmp/logcheck_$LEVEL.err
done
grep "Elapsed (wall clock)" /tmp/logcheck_*.err   # compare wall time
wc -l /tmp/logcheck_*.err                          # compare log volume
```
If `INFO` adds meaningful wall-time overhead or produces an unmanageably
large `.err` file, revert `run_seed_sweep.sbatch`'s `--log-level` to
`WARNING` and rely on `/usr/bin/time -v` alone for this round.

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
