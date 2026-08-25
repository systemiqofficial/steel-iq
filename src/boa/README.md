# boa

Baseload Optimisation Atlas: optimal solar/wind/battery systems for a fixed baseload
demand, world-wide. Model core in `model/`, model-input loading in `inputs/`, geography in
`geo/`, input-data pipeline in `cds/`, assumptions in `config/`.

## Preparing cost inputs

`boa-data-prepare` (a steelo-side command) extracts the four sheets boa reads from a
master excel workbook into a costs scenario:

```bash
boa-data-prepare                                              # S3 master-input package -> costs/default/
boa-data-prepare --input-file wb.xlsx --scenario cheap_renewables
boa-data-prepare --scenario cheap_renewables --full           # + cost cache for all sheet years
boa-data-prepare --scenario cheap_renewables --year_start 2025 --year_end 2050 --year_step 5
```

- `--input-file` — source workbook; omitted → the `master-input` DataManager package (S3),
  the same source `steelo-data-prepare` uses.
- `--scenario` — costs-set name (default `default`). A scenario is a whole hand-edited
  workbook variant; the extracted copy doubles as the provenance record of what a run used.
- `--full` — also pre-build the per-year cost cache for every year column in the
  RES CAPEX projections sheet.
- `--year_start` / `--year_end` / `--year_step` — narrow the cache years (defaults:
  earliest/latest in the sheet, step 1); each implies `--full`.

Re-running is an idempotent upsert: unchanged data is a no-op (cache kept); changed data
replaces the workbook and clears the scenario's cost cache.

Data lands under the boa data root (`$BOA_DATA_ROOT` → `$STEELO_HOME/boa` → `~/.steelo/boa`):

```
costs/<scenario>/
├── boa_cost_data.xlsx    the four extracted sheets (RES CAPEX projections, RES OPEX,
│                         Cost of capital, Country mapping)
├── source.json           scenario, prepared_at, source workbook path + sha256
└── cache_costs/          cost_of_renewables_<year>_investment_year.nc, one per year
```

Pre-building the cache is optional — a simulation builds any missing year on the fly from
`boa_cost_data.xlsx`. Run against a scenario with `run_boa ... --costs <scenario>`.

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
