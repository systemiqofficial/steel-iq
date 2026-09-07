# BOA Changelog: `origin/boa-refactor` → `review/boa-bisection-search`

Scope: `src/boa/`, `tests/boa/`, the BOA-facing hunks of `src/steelo/entrypoints/cli.py` and
`src/steelo/adapters/geospatial/geospatial_layers.py`, and `src/steelo/entrypoints/boa_data_cli.py`.
Compared against the merge-base (`30bc740`), not `origin/boa-refactor`'s current tip — the two
branches diverged there, and `origin/boa-refactor`'s later commits are a separate, independent
implementation plus unrelated feature work (see the review conversation for detail); this
changelog covers only what `review/boa-bisection-search` itself changed since the fork point.

## Input assumptions

- **`--coverage` is now a plain coverage fraction, not an uncovered percentile.** Previously a
  value like `15` meant "85% coverage" (percentile of hours *not* met); now `0.85` means 85%
  coverage directly. `SearchParams`/`b_min_at` reject anything outside `(0, 1)`
  (`bisection.py`'s `_check_coverage`), so an old-style percentile (e.g. `15` or `85`) now fails
  fast instead of silently running at the wrong target.
- **`boa-run` is GLOBAL-only with subcommands**: bare `boa-run`, plus `build-cache`, `query`,
  and `point`. Replaces whatever ad hoc entrypoints existed before this package was vendored.
- **New data-selection flags**: `--weather-input` (now `nargs="+"`, so one invocation can sweep
  several weather years), `--cost-input`, `--run`, `--cds-prepare YEAR`, `--data-prepare XLSX
  SCENARIO`. `--weather-input` defaults to `cds-2024`; `--cost-input` defaults to `default`.
- **`--run`'s default changed shape mid-branch**: it now resolves to `<cost-input>` (not
  `<weather-input>__<cost-input>`), and the on-disk directory is always `<label>_<hash>`, where
  `<hash>` is a digest of the physical constants + full `SearchParams` (`non_scenario_params_hash`)
  — changing any search-tuning parameter automatically forks a new run directory rather than
  silently reusing an incompatible one. **This hashing is applied only inside `boa-run`'s own
  CLI** (see `BOA_REVIEW_ISSUES.md` §1.2/1.3/5.1) — `boa-promote-lcoe --run <label>` and the
  docs still assume the pre-hash convention.
- **`--plots` is now opt-in** (previously `--no-plots` to disable; plots were on by default).
- **`--workers`** accepts an integer or a preset (`small`/`normal`/`fast`, computed from
  `os.cpu_count()`).
- **New standalone data-prep CLIs**: `boa-data-prepare` (cost workbook + geo shapefiles/iso3
  grid, steelo-side, `src/steelo/entrypoints/boa_data_cli.py`) and `boa-cds-prepare`/
  `boa-cds-download` (weather profile + max-capacity Zarr stores, `src/boa/cli/run_cds.py`).
  Both are idempotent upserts and can also run inline via `boa-run --data-prepare`/
  `--cds-prepare`.
- **New `boa-promote-lcoe` CLI** (`src/boa/cli/promote_lcoe.py`) and `boa-run --promote-lcoe`
  flag, combining a run's per-year GLOBAL NetCDFs into one file (see Promoted outputs).
- **New steelo-side `run_simulation --boa-run <run>` / `--boa-load-density <mw_km2>` flags**
  (`src/steelo/entrypoints/cli.py`), selecting a local BOA run to price baseload power from
  instead of the per-year files shipped with the geo data. Currently non-functional against a
  real promoted run — see `BOA_REVIEW_ISSUES.md` §1.1.
- **Optional layered land-availability ceiling**: `boa-cds-prepare --layers lulc,cds_exclusion`
  builds a max-capacity store scaled by ESA-CCI land-cover suitability and a CDS exclusion mask
  (protected areas, slope, elevation, distance to shore), landing in its own input set (e.g.
  `cds-2024-lulc+excl`) rather than overwriting the geometry-only stores. **Built but not yet
  enforced as a search constraint** — every query still reports the unconstrained optimum
  regardless of this ceiling (logged as a warning; "Grid 2", tracked for a follow-up PR).
- **`REGION_COORDS` went from 9 to 17 regions** (commit `58e8eae`, "Dedupe/split REGION_COORDS,
  add missed islands, fix region names") — old regions were split (Africa → `SUB_SAHARAN_AFRICA`
  + `NORTH_AFRICA`, Europe/Middle East separated, North Asia split west/east, Canada separated
  from North America) and four small island boxes were added (`HAWAII`, `GALAPAGOS`,
  `MASCARENE`, `KERGUELEN`). Downstream code (`combine_regional_datasets_into_global_dataset`)
  already expects 17; prose claiming "9 regions" was not updated (see
  `BOA_REVIEW_ISSUES.md` §1.4).
- **`run.json` manifest schema bumped to v3**, now recording the full `SearchParams` (not just a
  settings summary) plus an `availability_signature` guarding against a stale/mismatched
  land-availability ceiling being reused under a warm cache.

## Internal logic

- **Search method replaced**: the Monte Carlo sampler (random design draws + heuristic battery
  sizing) is gone, replaced by a deterministic grid-bisection search
  (`src/boa/model/bisection.py`). For each pixel: a coarse grid ranks basins across the whole
  feasible `(solar, wind)` overscale box, then dense patches refine around the best basin(s);
  at every node, the smallest battery meeting the coverage target is found by bisection on the
  state-of-charge simulation, plus a small ladder of larger "rungs" above the minimum (since
  dividing LCOE by delivered energy means the cheapest battery isn't always exactly at the
  coverage minimum). Deterministic — two builds of the same pixel are bit-identical, no RNG.
- **Frontier cache schema v2 → v3**: v2 was ragged (CSR-style, variable surviving-design count
  per pixel, from the Monte Carlo sampler); v3 is dense/fixed-shape (`Gc×Gc` coarse grid +
  `K×P×P×R` padded patch arrays), indexed directly with no offset table. No migration exists —
  a v2 store is rejected outright and must be rebuilt.
- **The frontier cache stores no capacity ceiling.** It depends only on weather (profiles +
  ERA5 land-sea mask), never on land-availability assumptions, so every layer-set build sharing
  the same weather year reuses one cache. The ceiling is read separately at query time from the
  max-capacity store (and, currently, not applied — see Input assumptions above).
- **Anchor-based multi-year/multi-key coverage**: `anchor_cost_coefficients`/`covering_anchors`
  (`src/boa/model/anchors.py`) select a small set of frozen cost-coefficient "anchors" — greedily
  farthest-point-first over the joint (cost key, investment year) space — so a build's seed
  placement stays valid across a whole multi-decade horizon and every country's cost mix,
  without rebuilding the frontier per year. The frontier's cached values are pure dispatch
  physics (no cost, no year), so `argmin_lcoe` reprices the same cache under any year's real
  costs at query time.
- **Containment certificate + truncation diagnostics**: `argmin_lcoe` reports whether the coarse
  grid outside every dense patch is provably no cheaper than the reported optimum
  (`patch_certified`) and whether the winning design sat against a patch edge
  (`argmin_truncated`) — both new query-time signals with no v2 counterpart.
- **Battery CAPEX modular-installation correction folded algebraically into a single power-law
  exponent** (`GAMMA = 1 + BATTERY_UNIT_CAPEX_SCALING_FACTOR`) rather than applied as a separate
  correction pass.
- **`LULC_CODES` rewritten** as a three-stage density derivation (theoretical density × packing
  factor × land-availability fraction) with a full source trail per land-cover class, replacing
  whatever the prior ceiling derivation was.
- **Status codes**: code `4` (the old Monte Carlo "fragile optimum, minimum-survivor cut
  rejected") is retired and never reused; code `6` is reserved (not yet implemented) for the
  Grid-2 capacity-box corner screen.

## Promoted outputs

- **New combined per-run LCOE file**: `boa-promote-lcoe` (or `boa-run --promote-lcoe`) stacks a
  run's per-year GLOBAL optimal-solution NetCDFs into one `(year, lat, lon)` float32 `lcoe`
  variable, storing `cost_key`/`status` once instead of once per year — turned 5.49 GB (36
  years) into ~26 MB on a real GLOBAL run. Filename:
  `optimal_lcoe_wy<weather_year>_<rho>MWkm2_cov<c>_<first_year>_<last_year>.nc`, where `<rho>`/
  `<c>` are dot-free (`format_scenario_number`, e.g. `cov0.85` → `cov0p85`). This is a **breaking
  rename** relative to whatever percentile-based (`p<uncovered-percentile>`) convention preceded
  it; the steelo-side consumer (`resolve_boa_lcoe_file` in `cli.py`) and its tests initially
  weren't updated to match, which shipped two blocking bugs -- since fixed (see
  `BOA_REVIEW_ISSUES.md` §1.1, §3.1).
- **Cost keys and status travel as compact codes, not strings**: the promoted file stores
  `cost_key_id` (int16) plus a `cost_key_legend` attribute (comma-joined), and `status` (int8)
  plus a `status_legend` attribute (semicolon-joined `code=label` pairs) — both year-invariant
  by construction, and promotion refuses to run if a year disagrees with the others on either.
- **New `capacity_ceiling_applied` attribute** (currently always `0`) on every optimal-solution
  NetCDF, flagging that the reported LCOE is the *unconstrained* optimum until Grid 2 lands —
  a machine-checkable counterpart to the `[UNCONSTRAINED]` log warning.
- **Optimal-solution and design-cache float variables are now stored as float32** (previously
  presumably float64), halving on-disk size with no meaningful precision loss for values that
  are themselves model estimates.
- **The promoted file carries its own full provenance**: run name, input/cost sets, cost
  workbook sha256, `boa` package version, git SHA, and the resolved scenario settings — it
  identifies what produced it without needing the run directory alongside it.
- **`optimal_sol`/scenario output paths were restructured** to
  `outputs/wy<weather_year>/<rho>MWkm2/cov<coverage>/nc/<REGION>/optimal_sol_wy<weather_year>_
  <rho>MWkm2_cov<coverage>_<REGION>_<investment_year>.nc` — nested by weather year first (so one
  run can hold a full weather-year sweep without the years overwriting each other), then by
  load density and coverage, both dot-free-encoded. Replaces whatever the prior
  percentile-keyed path layout was.
