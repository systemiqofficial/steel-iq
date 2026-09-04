# Baseload Optimisation Atlas (BOA)
This module calculates global optimal Levelized Cost of Energy (LCOE) at pixel level (high resolution: 0.25 degrees, i.e., 50 km or better). 
It is computed in two steps: 
1) Simulating renewable energy supply (using weather data and Atlite)
2) Finding the optimum overbuilding factors for solar, wind, and battery. 

Due to long runtimes this module runs as a standalone package (`src/boa`, its own pipeline of
CLI commands) rather than as part of the main model run, and its precomputed outputs are read
by the main model (steelo). The full pipeline — cost data, weather stores, then the search
itself — is documented in `src/boa/README.md`; this page covers the search methodology.

### 1. Prerequisites and running the simulation

See `src/boa/README.md`'s "End-to-end pipeline" section for the full command sequence
(`boa-data-prepare` for costs, `boa-cds-prepare` for weather stores, then `boa-run`). Once
both sides are prepared:

```bash
# Full run with default parameters (2025-2060, 1000 MW demand, cds-2024 weather)
boa-run

# Full run with custom parameters
boa-run --demand 800 --coverage 0.95

# Prepare the weather stores and cost set inline, then run
boa-run --cds-prepare 2024 --data-prepare master.xlsx test_scenario

# Build only the year- and baseload-independent frontier caches
boa-run build-cache

# Re-derive optimal-solution NetCDFs from pre-built caches
boa-run query --start-year 2030 --end-year 2030

# Single-point run (region auto-derived from coordinates)
boa-run point --lat 52.5 --lon 13.4

# See all available options
boa-run --help
```

### 2. Available parameters

Full reference: `boa-run --help` (options differ slightly per subcommand). Summary:

**Temporal Parameters** (full run, `query`, `point`):
- `-s`/`--start-year`: Starting investment year (default: 2025)
- `-e`/`--end-year`: Ending investment year (default: 2060)
- `-f`/`--frequency`: Years between simulations (default: 1)

**Scenario Parameters:**
- `-d`/`--demand`: Baseload demand in MW (default: 1000.0, typical range: 150-1000). Not part
  of the frontier cache's key — the search is baseload-invariant, so this only names the
  per-year query output.
- `-c`/`--coverage`: Required demand coverage fraction, e.g., 0.85 means 85% coverage
  (default: 0.85). Part of the frontier cache's key.

**Data Selection:**
- `--weather-input`: Input set under the data root's `inputs/` (profile + max-capacity
  stores; the weather year is read off the store filenames; default: `cds-2024`). The
  frontier cache lives alongside it but is keyed on the weather year alone, not the full
  input set, so every land-availability layer set built on the same weather shares it.
- `--cost-input`: Cost set under `costs/` (workbook + per-year cost cache; default: `default`)
- `--run`: Run name for outputs (default: `<weather-input>__<cost-input>`)
- `--cds-prepare YEAR`: Run `boa-cds-prepare` for YEAR first, building the weather-input
  set's missing stores (the weather-input default then becomes `cds-<YEAR>`)
- `--data-prepare XLSX SCENARIO`: Run `boa-data-prepare` first, extracting the cost workbook
  XLSX into cost set SCENARIO (the cost-input default then becomes SCENARIO)

**Optional Parameters:**
- `-w`/`--workers`: Threads for parallel grid-point optimisation (integer or preset small/normal/fast)
- `--verbose`: Enable detailed logging output
- `--plots`: Generate map plots during the run (off by default)
- `--dry-run` (full run only): Resolve paths and run the preflight check without simulating
- `--force` (`build-cache` and `query` only): Rebuild the targeted artifacts even if present
- `--promote-lcoe` (full run and `query`): Combine the per-year GLOBAL NetCDFs into the single
  LCOE file the steel simulation reads (same as running `boa-promote-lcoe` afterwards)

**The capacity ceiling is not yet applied at query time.** Every query currently reports the
*unconstrained* optimum for its coverage target, regardless of `--demand`, and logs a warning
saying so. Do not promote results from a run in this state.

### 3. Output
The simulation will:
- Run the baseload power simulation for the selected years
- Process all regions in parallel
- Generate optimal renewable energy system designs for each grid point
- Save results as one NetCDF per region-year under `runs/<run>/outputs/<demand>MW/cov<coverage>/nc/<REGION>/`
- With `--plots`, create visualization plots under `runs/<run>/outputs/<demand>MW/cov<coverage>/plots/<REGION>/`

Results include:
- LCOE (Levelized Cost of Energy) in USD/MWh
- Solar overscale factor (relative to baseload demand)
- Wind overscale factor (relative to baseload demand)
- Battery overscale factor (relative to baseload demand)
- Total installation cost in USD

### 4. Handing the LCOE to the steel simulation

The steel simulation reads exactly one variable off a BOA run — `lcoe` — so a finished run
is promoted into a single combined file before it is used. Promotion stacks every year into
one `(year, lat, lon)` float32 variable and stores the cost keys and status codes once,
which turns 5.49 GB of per-year files (36 years) into about 26 MB:

```bash
boa-promote-lcoe --run cds-2024__china_test    # or: boa-run ... --promote-lcoe
```

The result lands in
`<boa data root>/lcoe-for-steel-iq/<run>/optimal_lcoe_<bl>MW_cov<c>_<first>_<last>.nc`
and carries its own provenance (run name, input/cost sets, workbook hash, versions, scenario
settings), so the file alone identifies what produced it.

Point the steel simulation at a promoted run:

```bash
run_simulation --boa-run cds-2024__china_test
```

- `--boa-run`: local BOA run to price baseload power from. Omitted — the default — the
  simulation reads the per-year files shipped with the geo data, exactly as before
- `--boa-demand`: baseload demand in MW, needed only when the run holds more than one; requires `--boa-run`

The coverage is not a flag: it follows `GeoConfig.included_power_mix` (`simulation.py`), the
setting the simulation prices power at. `85% baseload + 15% grid` reads the run's p15 file,
`95% baseload + 5% grid` its p5 file — change the setting and the matching file is used. If
the run has no file at that percentile, the simulation stops before any data preparation and
names the mix that required it, rather than quietly pricing power off whatever sits in the
data directory. A grid-only mix has no baseload component at all, so pairing it with
`--boa-run` is rejected instead of resolving a file that would never be read.

The per-year files stay in place — they carry the overbuild factors and the cost breakdown —
and `--baseload-power-sim-dir` still points at a directory of them.

## Methodology:
1. Project investment costs for solar and wind technologies for each country and year
    - Use SSP-RCP projected renewable buildouts until 2100 from IAASA (SSP1-2.6 (Sustainability), SSP2-4.5 (Middle of the Road), and SSP3-Baseline
    (Business-as-usual)).
    - Correct those projections with historical installed capacity data from IRENA.
    - Apply technology-specific learning curves to project CAPEX across time and space for solar and wind technologies.

2. Simulate hourly solar PV and onshore wind generation potential
    - Use high spatial resolution (30 km or higher) based on reanalysis weather data (ERA5 from Copernicus), including variables like radiation, temperature,
    and wind speed.
    - Use the Atlite package to simulate solar PV panels and onshore wind turbines at each location.

3. Determine the installation limits for solar and wind at each grid point
    - A pure-geometry ceiling: area per grid cell at a given latitude x an areal power density
    (accounting for turbine/panel spacing) is available by default.
    - A layered land-availability ceiling is also available: the geometric ceiling further
    scaled by an ESA-CCI land-cover suitability fraction and a CDS-derived exclusion mask
    (protected areas, slope, elevation, distance to shore). See `src/boa/README.md` for how to
    build it. Not yet enforced as a search constraint — see "Available parameters" above.

4. Identify grid points eligible for renewable system deployment
    - Filter out water bodies (oceans and seas).
    - Set a maximum altitude and slope.
    - Exclude grid points with zero potential for both solar and wind.
    - Note: this is BOA's own eligibility filter, separate from the steelo siting feasibility mask (which additionally allows land at or below sea level).

5. For each eligible grid point, search deterministically rather than sample
    - The search runs in solar/wind **overscale** units (installed capacity relative to
    baseload demand), which is what makes the result reusable across every baseload demand
    and, together with a closed-form LCOE (below), across every investment year.
    - A coarse grid ranks basins across the whole feasible box, then one or more dense
    patches refine around the best basin(s) — grid-bisection, not stochastic sampling. At
    every node, the smallest battery meeting the hourly coverage target is found by
    bisection on the battery's state-of-charge simulation (ideal efficiency, empty at year
    start), plus a handful of larger "rungs" above that minimum, because dividing LCOE by
    the energy actually delivered means the cheapest battery does not always sit exactly at
    the coverage minimum.
    - Battery CAPEX is corrected for modular installation (larger batteries are cheaper per
    unit of storage), folded algebraically into a single power-law term rather than applied
    as a separate correction pass.
    - What the search caches per grid point is pure dispatch physics — no cost, no
    investment year. LCOE for one (country, year) is then a closed-form combination of
    four scalar coefficients (from that year's CAPEX, OPEX and cost of capital) against the
    cached physics, with no re-simulation — the same cached search result reprices instantly
    for every requested investment year and cost scenario.
    - The reported design is the cheapest cached node/rung meeting the coverage target,
    ranked and reported on the same LCOE (energy-delivered, curtailment-aware).

6. Extrapolate in time and space
    - Repeat the above for each eligible grid point and each requested investment year (2025
    to 2060 by default, see "Available parameters" above).
    - Combine all optimal designs and LCOEs into a set of global maps.