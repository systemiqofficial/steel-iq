# Running the CO2-ramp PAM seed-sensitivity sweep on SLURM

Runs the full steel-industry simulation (2025-2050) under a shared CO2-ramp
price scenario across multiple random seeds, to measure how much randomness
`probabilistic_agents: false` doesn't eliminate (capacity/CO2-limit
allocation order, geospatial siting lottery for new plants) still moves the
final technology mix.

The trade-LP solver backend is switchable between HiGHS (default) and
Gurobi, and furnace-group clustering can be enabled to speed up the LP —
region/technology aggregates are unaffected, exact plant-level flows become
approximate. §7 below is the recommended production config (Gurobi +
clustering); §6 is the tool that compares configs against each other.

`develop`'s `probabilistic_agents=False` behavior is already deterministic
(see `Plant.evaluate_furnace_group_strategy`,
`src/steelo/domain/models.py`) — nothing needs patching before running.

Also worth checking in any result: whether a known technology-mix
discontinuity around 2045, as the CO2 price plateaus after its ramp, holds
consistently across seeds or is seed-dependent.

## 1. Environment setup (login node — needs internet)

```bash
git clone -b pam-benchmark-cluster <repo-url> ~/steel-iq
cd ~/steel-iq
curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if `uv --version` already works
uv sync
```

`geopandas`/`fiona`/`cartopy`/`osmnx`/`highspy` all have C-extension
dependencies (GDAL/GEOS/PROJ). Before trusting a long batch job to it,
confirm `uv sync` finished without falling back to a from-source build, and
that the geospatial imports actually work:
```bash
uv run python -c "import geopandas, fiona, cartopy, osmnx, highspy; print('ok')"
```

For the Gurobi-backed configs (§6/§7): `uv sync --extra gurobi`, plus a
working Gurobi license reachable from `labgpu01`.

## 2. Data

The CO2-ramp scenario is authored directly into a clone of the master
Excel's "Carbon cost" sheet
(`scripts/sensitivity/author_carbon_cost_scenario.py`), then prepared
through the normal `steelo-data-prepare` pipeline.

**Run `steelo-data-prepare` on the cluster, not on Windows.** Its console
output has a Windows-only crash (`UnicodeEncodeError` on rich's checkmark
glyphs under the cp1252 codepage) that can hit after fixture files are
written, making a broken run look successful.
`recreate_fallback_material_costs`
(`src/steelo/data/recreation_functions.py`) catches read failures and
silently writes an empty `fallback_material_costs.json` with only a console
warning — check for this explicitly (see spot-check below), don't rely on
the console output alone. Preparing on the cluster's Linux console avoids
the crash, and only the ~50MB authored Excel needs to cross the network
instead of the ~900MB prepared fixture tree.

**Step 1 — author the scenario workbook**, on whichever machine holds the
real master Excel (only reads it; the original stays untouched):
```bash
uv run python -m scripts.sensitivity.author_carbon_cost_scenario \
    --master-excel-path <path-to-real-master-excel> \
    --scenario scripts/sensitivity/scenarios/co2_ramp.yaml \
    --output-path ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx
```
(Needs S3 credentials only if you don't already have the real master Excel
locally — see `docs/data_management/`.)

**Step 2 — get the authored workbook onto the cluster's NFS home**:
```bash
rsync -av ~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx \
    <cluster-host>:~/.steelo/data_cache/master-input-co2-ramp/master_input.xlsx
```

**Step 3 — prepare fixtures on the cluster**. `--no-skip-existing` matters
if `--output-dir` was ever populated before — by default
`steelo-data-prepare` treats any file already present as done and leaves it
alone (`skip_existing=True` in `src/steelo/data/preparation.py`), so a
stale/broken fixture from a prior run would otherwise silently survive a
"successful" re-prep:
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
- `fixtures/fallback_material_costs.json` must **not** be a bare `[]`/`{}`.

**Prepared input data goes on NFS home** (small, read-mostly, survives
across jobs). **Per-run scratch/output goes on `/local/$USER` on labgpu01**
— concurrent processes writing logs/plots/CSVs to NFS simultaneously risks
metadata contention. `/local` (20TB) is not guaranteed to persist across
nodes or be visible from the login node, so every sbatch script below
copies final results back to NFS at job end.

## 3. Smoke test before a real submission

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
Confirms the pipeline (imports, data paths, `/local` writability) works on
this node/filesystem. While it runs, check `top`/`htop` — CPU usage should
stay near 1 core; HiGHS/numpy/BLAS should not oversubscribe threads.

## 4. Baseline seed sweep (HiGHS, no clustering)

`run_seed_sweep.sbatch` runs 5 seeds (1-5) as a SLURM **job array**
(`--array=0-4`, one independent task per seed, task_id -> seed = task_id +
1) — each task's `--mem`/`--time` covers one process's footprint rather than
a guessed aggregate for 5 concurrent growing processes, and one seed
timing out or OOMing doesn't take the other 4 down with it.

```bash
sbatch scripts/slurm/run_seed_sweep.sbatch
```

Once all 5 tasks finish (success or not) — `analyze_seed_sensitivity.py`'s
manifest reader skips non-`"success"` rows, so partial results still work:

```bash
sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_seed_sweep_analyze.sbatch
```

**Retry a single seed** (task IDs are 0-based, seed = task_id + 1) — reuses
the same scratch dir, only overwrites that seed's `run_000N`:

```bash
sbatch --array=<TASK_ID> scripts/slurm/run_seed_sweep.sbatch
# then re-run the analyze job, depending on the retry's job ID, to fold the fix in
```

Sizing: `--cpus-per-task=1` (the annual loop is strictly sequential, more
cores don't speed one run up), `--mem=32G`, `--time=48:00:00` — see the
sbatch file's header for the measurement behind these numbers. Confirm
`--partition=verylong` is still the right tier with `sinfo -o "%P %l"`
(`long` caps at 6h).

### Fine-grained runtime/memory logging

`run_seed_sweep.sbatch` wraps the run in `/usr/bin/time -v` (peak RSS) and
passes `--log-level INFO`, which also captures `plant_agent.py`'s per-stage
`operation=<stage> year=<Y> duration_s=<T>` timers and yearly memory
snapshots. `--log-level INFO` surfaces ~260 other INFO-level log call sites
across the codebase too — `logging_config.yaml`'s `function_overrides` can't
narrow the CLI's shared handler threshold down to just these two sources.
Before trusting a real 36h+ run, rule out logging overhead as a confound
with a cheap side-by-side:
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
If `INFO` adds meaningful overhead or an unmanageable `.err` file, switch
`run_seed_sweep.sbatch`'s `--log-level` to `WARNING` and rely on
`/usr/bin/time -v` alone.

## 5. Once it finishes

`run_seed_sweep_analyze.sbatch` merges each task's manifest fragment into
`manifest.csv`, runs `analyze_seed_sensitivity.py`, and `rsync`s results to
`~/steel-iq/outputs/sensitivity/co2_ramp_seed_sweep/` on NFS home. Check:

- **All 5 seeds actually diverge** (not identical to the decimal) — if they
  match exactly, `probabilistic_agents=False` isn't behaving as expected or
  the scenario data wasn't varied correctly.
- **Spread in final-year technology shares and plant-location shares across
  seeds** — report range/std, not just "they differ."
- **The 2045 price-jump pattern** — consistent across all 5 seeds, or only
  some.
- If a matching local Windows run of the same seeds exists (`pam-benchmark`
  branch), cross-check the same seed number across platforms — small
  floating-point divergence between machines/OS/BLAS builds is expected for
  the same seed unless it changes qualitative conclusions.

## 6. Solver benchmark (highs vs. gurobi vs. gurobi + clustering [+ warm-start])

`run_solver_benchmark.sbatch` runs 3 seeds (101-103) under each of 4
configs — `highs` (baseline), `gurobi`, `gurobi_clustering`, and
`gurobi_clustering_warmstart` (dual-simplex warm-start between years,
clustering only, since warm-start needs simplex-family methods) — as a
12-task job array, 2025-2040. The first 3 configs use each backend's own
default `solver_options` (each config's fastest realistic setup, not a
like-for-like algorithm comparison). Same 3 seeds across all 4 configs
isolates solver/clustering/warm-start effects from seed-to-seed noise.
Answers two questions: does switching solver/clustering/warm-start change
*what* gets built (technology mix, plant locations, emissions), and how
much faster is it.

Without a Gurobi license, the `highs` tasks (array indices 0-2) still
succeed; `gurobi`/`gurobi_clustering`/`gurobi_clustering_warmstart` (3-11)
fail with a `GurobiError`.

```bash
sbatch scripts/slurm/run_solver_benchmark.sbatch
```

Once all 12 tasks finish (success or not):

```bash
sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_solver_benchmark_analyze.sbatch
```

Produces, under `~/steel-iq/outputs/sensitivity/solver_benchmark/`:

- `<config>/analysis/` — one `analyze_seed_sensitivity.py` output tree per
  config. Compare the same file (e.g. `final_tech_share_by_seed.csv`)
  across configs' `analysis/` dirs to see whether switching
  solver/clustering/warm-start changed actual decisions, not just runtime.
- `runtime_summary.csv` — `config, seed, status, duration_s` for all 12
  runs.
- `stage_timing_summary.csv` — one row per `(config, seed)` with
  `wall_clock_s`/`peak_rss_mb` (from `/usr/bin/time -v`) and three
  independent sibling-stage totals: `trade_module_s` (builds + solves the
  trade LP, plus export/plot), `geospatial_s` (new-plant siting, can
  dominate in later years), `pam_s` (per-plant renovate/switch/close/expand
  NPV decisions). These are siblings, not nested — they don't sum to one
  "agent module" total. `lp_build_s`/`lp_solve_s` are sub-parts of
  `trade_module_s`; `npv_plant_decision_s` is a sub-part of `pam_s`. Plus
  `peak_lp_build_rss_mb`/`peak_lp_solve_rss_mb`. Parsed by
  `parse_stage_timings.py` from each run's `operation=.../memory_checkpoint`
  log lines (`--log-level INFO`) — see that script's docstring for the
  full column-to-log-line mapping.

Retry a single `(config, seed)` task (task IDs are 0-based; mapping is in
the sbatch file's header) — reuses the same scratch dir, only overwrites
that one run:

```bash
sbatch --array=<TASK_ID> scripts/slurm/run_solver_benchmark.sbatch
# then re-run the analyze job, depending on the retry's job ID, to fold the fix in
```

## 7. Production runs: gurobi + clustering

The recommended config for production runs where exact plant-level
precision isn't needed (region/tech aggregates are unaffected by
clustering). `run_gurobi_clustered_sweep.sbatch` is sized for this config
specifically — `run_solver_benchmark.sbatch`'s `--mem`/`--time` are sized
for its slowest config (`highs`) and would over-request resources for a
single-config run. Otherwise structured like §4 (5-seed array, full
2025-2050 range, `probabilistic_agents: false`), with
`optimization_solver=gurobi` and `enable_furnace_group_clustering=true`
locked in and no warm-start.

```bash
sbatch scripts/slurm/run_gurobi_clustered_sweep.sbatch
```

Once all 5 tasks finish (success or not):

```bash
sbatch --dependency=afterany:<ARRAY_JOBID> scripts/slurm/run_gurobi_clustered_sweep_analyze.sbatch
```

Retry a single seed the same way as §4:

```bash
sbatch --array=<TASK_ID> scripts/slurm/run_gurobi_clustered_sweep.sbatch
# then re-run the analyze job, depending on the retry's job ID, to fold the fix in
```

Sizing: `--mem=8G`, `--time=12:00:00` — see the sbatch file's header for the
basis and the safety margins applied.
