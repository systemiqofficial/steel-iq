# boa

Baseload Optimisation Atlas: optimal solar/wind/battery systems for a fixed baseload
demand, world-wide. Model core in `model/`, model-input loading in `inputs/`, geography in
`geo/`, input-data pipeline in `cds/`, assumptions in `config/`.

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
