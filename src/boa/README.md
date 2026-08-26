# BOA: Baseload Optimisation Atlas

Baseload Optimisation Atlas: optimal solar/wind/battery systems for a fixed baseload
demand, world-wide. Model core in `model/`, model-input loading in `inputs/`, geography in
`geo/`, input-data pipeline in `cds/`, assumptions in `config/`.

## End-to-end pipeline

The full pipeline is three commands — costs, weather stores, run — each detailed in its
own section below:

```bash
# 1. Cost side: static geo data + cost workbook          -> costs/default/
boa-data-prepare

# 2. Weather side: profile + max-capacity stores          -> inputs/cds-2024/
#    (stops and names the boa-cds-download command if the raw data is missing)
boa-cds-prepare --weather_year 2024

# 3. Sanity-check the pairing, then run at production settings
boa-run --demand 1000 --coverage 0.95 --samples 2000 --dry-run
boa-run --demand 1000 --coverage 0.95 --samples 2000 --promote-lcoe
```

Steps 1 and 2 are idempotent and independent — rerun either at any time; existing
artefacts are reused (both can also run inline via `boa-run --cds-prepare 2024
--data-prepare wb.xlsx rev2`). The run defaults (`--start-year 2025 --end-year 2060
--frequency 1`, `--weather-input cds-2024`, `--workers fast`) suit a production sweep;
drop to `--samples 1000` and a single year (`--start-year 2030 --end-year 2030`) for a
faster exploratory run. To change only cost assumptions afterwards, skip the rebuild
entirely: `boa-data-prepare --input-file edited.xlsx --scenario rev2` then
`boa-run query --cost-input rev2 ...` reuses the design caches and re-derives the
NetCDFs in minutes per year.

## Preparing input data

`boa-data-prepare` (a steelo-side command) prepares everything a run needs: the static
geo data plus a costs scenario extracted from a master excel workbook:

```bash
boa-data-prepare                                              # S3 master-input package -> costs/default/
boa-data-prepare --input-file wb.xlsx --scenario cheap_renewables
boa-data-prepare --scenario cheap_renewables --year_start 2025 --year_end 2050 --year_step 5
```

- `--input-file` — source workbook; omitted → the `master-input` DataManager package (S3),
  the same source `steelo-data-prepare` uses. The workbook must contain the four sheets boa
  reads: RES CAPEX projections, RES OPEX, Cost of capital, Country mapping (a "missing
  sheet(s)" failure means the source predates them).
- `--scenario` — costs-set name (default `default`). A scenario is a whole hand-edited
  workbook variant; the extracted copy doubles as the provenance record of what a run used.
- `--year_start` / `--year_end` / `--year_step` — narrow the cost-cache years (defaults:
  earliest/latest year column in the RES CAPEX projections sheet, step 1).

Geo side: the pinned Natural Earth shapefiles (1:50m map subunits, 1:10m admin-1) and the
ERA5 land-sea mask are installed from the `boa-data` DataManager package (S3), and the
per-pixel iso3 grid is built locally from the 1:50m shapefile (`geo/iso3_grid_builder.py`).
The shapefiles are pinned on S3 rather than fetched from naciscdn.org because Natural Earth
releases change polygons, which would silently change the iso3 grid.

The first run downloads ~16 MB and builds the iso3 grid in about a minute; re-runs finish
in seconds. Re-running is an idempotent upsert: files already present are kept, unchanged
cost data is a no-op; changed cost data replaces the workbook and rebuilds the scenario's
cost cache. The iso3 grid carries a fingerprint of its source shapefile and is rebuilt
automatically if the NE 1:50m shapefile ever changes.

Data lands under the boa data root (`$BOA_DATA_ROOT` → `$STEELO_HOME/boa` → `~/.steelo/boa`):

```
data/
├── ne_50m_admin_0_map_subunits/      NE 1:50m shapefile (source of the iso3 grid)
├── ne_10m_admin_1_states_provinces/  NE 1:10m admin-1 shapefile (sub-national cost keys)
├── lsm_025_deg.nc                    ERA5 0.25 deg land-sea mask
├── iso3_grid.nc                      per-pixel ISO3 grid, built locally
└── cds/                              raw CDS NetCDFs (+ global_zarr/ build cache)
inputs/<set>/                         e.g. cds-2024, tagged by weather year
├── cds-zarr/                         live profile + max-capacity stores the model reads
├── staging/                          freshly built stores (transient; emptied on install)
└── cache_designs/                    design cache, built by boa-run
costs/<scenario>/
├── boa_cost_data.xlsx    the four extracted sheets (RES CAPEX projections, RES OPEX,
│                         Cost of capital, Country mapping)
├── source.json           scenario, prepared_at, source workbook path + sha256
└── cache_costs/          cost_of_renewables_<year>_investment_year.nc, one per year
```

Run against a scenario with `boa-run ... --cost-input <scenario>`.

## CDS input stores

`boa-cds-prepare` builds the profile + max-capacity Zarr stores for one input set from raw
CDS NetCDFs (dataset sis-energy-global-reanalysis) and installs them into
`inputs/<set>/cds-zarr/`, the live dir the model reads:

```bash
boa-cds-prepare --weather_year 2024            # build + install what is missing -> inputs/cds-2024/
boa-cds-prepare --weather_year 2024 --force    # rebuild everything
boa-cds-download --year 2025                   # fetch raw NetCDFs for another year
```

The input set is tagged automatically as `cds-<weather_year>`; pass `--inputs` only to
override. Re-running is idempotent: regions whose stores already exist in the live dir are
reused; `--force` rebuilds them. If the raw files for the requested year are missing, prepare stops
and names the `boa-cds-download` command to run (which needs a CDS account, `~/.cdsapirc`
with the dataset licence accepted, and `uv sync --extra cds` for the client). Raw files land
in `data/cds/` (~6 GB per year); the convert stage builds a shared global intermediate at
`data/cds/global_zarr/` (~12 GB per year, deletable — it rebuilds in about a minute), after
which each region converts in seconds. Max-capacity stores are geometry-only (pixel area x
density; no land-use term for now).

Already have the raw data (from another machine or an earlier checkout)? Drop the
*extracted* per-year directories — 12 monthly NetCDFs each — into `data/cds/` under the
boa data root (`$BOA_DATA_ROOT` → `$STEELO_HOME/boa` → `~/.steelo/boa`) and prepare will
use them without downloading:

```
data/cds/
├── cds_solar_cf_ic6hh135_0_25_degree_2024/        *.nc, one per month
└── cds_wind_onshore_cf_ic6hh135_0_25_degree_2024/ *.nc, one per month
```

A zip dropped on its own is not enough: the downloader treats an existing zip as
already-handled and never extracts it, so unzip into the sibling directory named after the
zip stem. `boa-cds-download` also skips any (technology, year) whose extracted directory
already exists, so partial reuse works too.

## Running the model

`boa-run` is always GLOBAL (all 9 regions); the one exception is the single-point mode:

```bash
boa-run --demand 1000 --coverage 0.95            # full run: build caches if missing, query every year
boa-run build-cache --samples 2000               # year- and baseload-independent design caches only
boa-run query --start-year 2030 --end-year 2030  # NetCDFs from pre-built caches (--force to re-derive)
boa-run point --lat 52.5 --lon 13.4              # single point; region auto-derived
boa-run --weather-input cds-2023 --cost-input rev3 --dry-run  # resolve paths + preflight, run nothing
boa-run --cds-prepare 2024 --data-prepare wb.xlsx rev2        # prepare both sides inline, then run
```

`--weather-input` alone identifies the weather side (stores + design cache; the weather
year is read off the store filenames, never passed; default `cds-2024`), `--cost-input`
the cost side (xlsx + per-year cost cache), and `--run` names the output pairing (default
`<weather-input>__<cost-input>`). A preflight
check fails fast with the exact `boa-cds-prepare` / `boa-data-prepare` command when the
selected sets are incomplete. The full run never rebuilds an existing design cache; use
`build-cache --force` or `query --force` for targeted rebuilds. Design caches are
baseload-independent: one cache per (coverage, samples, weather year) serves every
`--demand`, with the capacity ceiling applied as a query-time mask; pixels the mask
starves or leaves sparsely sampled are re-searched by a query-time top-up (supported
baseload: up to 20,000 MW). Expect hours
for a full multi-year GLOBAL run at production settings (`--samples 2000`); a `query`
against warm caches is minutes per year.

## Handing LCOE to the steel simulation

The steel simulation reads exactly one variable off a run — `lcoe` — so a finished run is
promoted into one combined file per scenario. Promotion stacks every year into a single
`(year, lat, lon)` float32 variable and stores the cost keys and status codes once (they are
year-invariant, and promotion refuses to run if they are not), which turned 5.49 GB of
per-year files into 25.7 MB on a 36-year GLOBAL run:

```bash
boa-promote-lcoe --run cds-2024__china_test   # every scenario in a finished run
boa-run --demand 1000 --coverage 0.95 --promote-lcoe   # or inline, right after the query
```

Output: `lcoe-for-steel-iq/<run>/optimal_lcoe_<bl>MW_p<p>_<first>_<last>.nc`, chunked one
year at a time so a single-year read stays cheap. The file carries its own provenance — run
name, input/cost sets, workbook sha256, boa version, git sha, and the scenario settings —
so it identifies what produced it without the run directory. Cost keys travel as int16 ids
plus a `cost_key_legend` attribute; `status` keeps the `STATUS_CODES` values, legend in
`status_legend`.

The per-year NetCDFs stay put: they hold the overbuild factors and the cost breakdown, and
remain the artefacts for crash recovery, plots and forensics. The steel side opts into a local
run with `run_simulation --boa-run <run>` (add `--boa-demand` only when a run holds several
demands); without `--boa-run` it reads the per-year files shipped with the geo data as before.
Which percentile it reads is not a flag — it follows steelo's `GeoConfig.included_power_mix`,
so an 85% baseload mix takes the p15 file and a 95% one the p5 file. A run missing that
percentile stops the simulation before any data preparation and lists what is available.

## Sources for model assumptions

References behind the numeric assumptions in `config/settings.py`.

### Technology lifetimes (`LIFETIMES`)

- **Solar, 25 years** — IEA, *The world needs more diverse solar panel supply chains*, 2022, gives 25–30 years.
  https://www.iea.org/news/the-world-needs-more-diverse-solar-panel-supply-chains-to-ensure-a-secure-transition-to-net-zero-emissions
- **Wind, 25 years** — IRENA, *Leveraging Local Capacity for Onshore Wind*, Executive Summary, 2017, p. 20. Other sources give 20 years.
  https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2017/Jun/IRENA_Leveraging_for_Onshore_Wind_Executive_Summary_2017.pdf
- **Battery, 25 years** — aligned with solar/wind so no technology is reinstalled within the investment horizon.

### Deterioration rates (`YEARLY_DETERIORATION_RATES`)

- **Battery, 1.5 %/year** — NREL, *Battery Lifespan*.
  https://www2.nrel.gov/transportation/battery-lifespan

## Relationship to steelo

`boa` imports nothing from `steelo`; the dependency runs one way (steelo → boa). Glue that
needs both — data-root resolution, downloading reference data, selecting a run for a steel
simulation — lives on the steelo side.

`geo/geo_hierarchy.py`, `geo/geo_hierarchy_overrides.py` and `geo/iso3_finder.py` overlap
with older copies in `steelo` (`data/recreation_functions.build_geo_hierarchy`,
`data/geo_hierarchy_overrides.py`, `adapters/dataprocessing/preprocessing/iso3_finder.py`).
The `boa` versions are the newer ones; the intent is for steelo to import them from here so
the repo carries a single province taxonomy and ISO3 lookup.

## TODO

- CLI command to ingest pre-downloaded CDS capacity-factor data (today the extracted
  directories must be dropped into `data/cds/` by hand, see above).
- Record the CDS data version in the store metadata, and rebuild/add newly processed
  stores when the version differs for the same weather year.
- Multi-year weather-data runs (an input set currently holds exactly one weather year).
- Model: battery optimisation improvements.
- Save run log and config file
- Output short status while running in terminal
