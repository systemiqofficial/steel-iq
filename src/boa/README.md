# boa

Baseload Optimisation Atlas: optimal solar/wind/battery systems for a fixed baseload
demand, world-wide. Model core in `model/`, model-input loading in `inputs/`, geography in
`geo/`, input-data pipeline in `cds/`, assumptions in `config/`.

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
└── cache_designs/                    design cache, built by run_boa
costs/<scenario>/
├── boa_cost_data.xlsx    the four extracted sheets (RES CAPEX projections, RES OPEX,
│                         Cost of capital, Country mapping)
├── source.json           scenario, prepared_at, source workbook path + sha256
└── cache_costs/          cost_of_renewables_<year>_investment_year.nc, one per year
```

Run against a scenario with `run_boa ... --costs <scenario>`.

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
