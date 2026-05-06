# Outputs and Post-Processing

This page describes the artefacts a simulation produces — in-memory traces during the run, plot files written by `SteelPlotter`, and the post-processed CSVs assembled at the end. The model also writes geospatial statistics (LCOE / LCOH / overbuild factors) per-year and aggregated.

For where individual costs and emissions originate, see [Cost Calculation Functions](plant_agent_model/calculate_costs.md). For how the trade LP feeds these traces, see [Trade Model Overview](trade_model/overview_trade_model.md) and [TM-PAM Connector](plant_agent_model/trade_model_connector.md).

---

## DataCollector traces

`DataCollector` (`src/steelo/domain/datacollector.py`) is invoked at the end of every simulation year and aggregates per-FG state into in-memory traces consumed by `SteelPlotter` and the post-processor.

| Attribute | Shape | Source |
|-----------|-------|--------|
| `trace_capacity` | `{year: {tech: capacity_t}}` | All active FGs |
| `trace_price` | `{year: {product: price_$/t}}` (incl. `steel`, `iron`, optional `scrap`, `iron_weighted_avg`) | `Environment.cost_curve` |
| `trace_production` | `{year: total_t}` | All active FGs |
| `trace_production_by_product` | `{year: {iron|steel: tonnes}}` | Active FGs (collected alongside emissions) |
| `trace_utilisation_rate` | `{year: {fg_id: rate}}` | Active FGs |
| `trace_capex` | `{year: {tech: {iso3: capex_usd}}}` | New FGs created that year |
| `trace_emissions` | `{boundary: {year: {tech: {scope: tCO2e}}}}` | Active FGs, **all available boundaries**, scopes `direct_ghg`, `direct_with_biomass_ghg`, `indirect_ghg` |
| `trace_iron_ore` | `{year: {quality: tonnes}}` | Iron-ore allocations |
| `trace_metallic_charges` | `{year: {charge_type: tonnes}}` | Iron-bearing inputs to steelmaking |
| `trace_international_iron_trade` | `{year: {iron_product: tonnes}}` | Cross-ISO3 flows of `IRON_PRODUCTS` from `Allocations.allocations` |

### Emissions reshape and overcounting fix

`trace_emissions` was previously a flat `{year: {tech: total}}` that summed `direct_ghg + direct_with_biomass_ghg + indirect_ghg` for the configured carbon-cost boundary only. That sum double-counted direct emissions — the two direct views are alternatives, not separate scopes — and inflated 2025 totals by ~64%. The current shape stores every available boundary and keeps each scope separate, so:

- charts can be produced per boundary without re-walking the plant graph;
- the chart layer chooses which scopes to combine (e.g. `direct_ghg + indirect_ghg`) without forcing a global decision at collection time;
- the same iteration accumulates `trace_production_by_product` for free, used as the denominator for intensity charts.

### International iron trade

`collect_international_iron_trade(year, trade_allocations)` walks the LP allocations, filters to commodities in `IRON_PRODUCTS`, drops intra-country flows (`from_iso3 == to_iso3`), and accumulates per-product cross-border tonnes. Logged at info level with per-product totals; consumed by `SteelPlotter.plot_international_iron_trade()`.

---

## SteelPlotter

`SteelPlotter` (`src/steelo/utilities/steeliq_plotter.py`) is the unified plotting class that has progressively replaced the standalone functions in `src/steelo/utilities/plotting.py`. It centralises styling (footers, legends, color schemes), output-path resolution via `PlotPaths`, and per-chart CSV export.

**Class-level conventions:**

- Each plot method takes a `trace_*` dict and an optional `iso3_filter` / region grouping argument.
- Methods return the saved `Path`, or `None` when there is no data.
- `export_csv=True` (the default on most plots) writes a sibling `.csv` alongside the `.png` via `_save_chart_data_to_csv()`. The CSV has the same data the plot was built from — every furnace, every year — so capacity inventory and breakdowns survive even when the chart truncates or aggregates.
- Plot output subdirectory is selected per call (`subdir="plots_dir"`, `"pam_plots_dir"`, etc.) via `_save_figure()`.

**Plot catalogue:**

| Method | Trace consumed | Output subdir |
|--------|----------------|---------------|
| `plot_capex_by_technology` | `trace_capex` | `pam_plots_dir` |
| `plot_emissions_by_technology` | `trace_emissions` + `trace_production_by_product` | `EMISSIONS_SUBDIR` (`plots/emissions`) |
| `plot_iron_ore_by_quality` | `trace_iron_ore` | `pam_plots_dir` |
| `plot_metallic_charges` | `trace_metallic_charges` | `pam_plots_dir` |
| `plot_international_iron_trade` | `trace_international_iron_trade` | `pam_plots_dir` |
| `plot_steel_cost_curve` / `plot_cost_curve_per_region` / `plot_cost_curve_for_commodity` / `plot_cost_curve_with_breakdown` / `plot_cost_curve_step` | `Environment.cost_curve` | `COST_CURVES_SUBDIR` (`plots/cost_curves`) |
| `plot_capacity_development_by_technology` / `plot_area_chart_by_region_or_technology` | `trace_capacity` | `pam_plots_dir` |

### Plot folder layout

Emissions and cost-curve plots write to top-level sibling folders rather than under `plots/PAM/…`:

```
output/
  plots/
    PAM/        # plant-agent plots (capacity, capex, charges, prices)
    GEO/        # geospatial / new-plant plots
    TM/         # trade-model plots
    emissions/  # SteelPlotter.plot_emissions_by_technology
    cost_curves/  # SteelPlotter cost-curve methods
```

Cost-curve filenames follow `cost_curve_{product}_by_{aggregation}_{year}.png` (e.g. `cost_curve_iron_by_region_2025.png`); the legacy ordering `{product}_cost_curve_by_{aggregation}_{year}.png` is no longer produced by the new methods. `plot_cost_curve_with_breakdown` retains its previous filename convention.

---

## Post-processed CSV columns

`extract_and_process_stored_dataCollection()` in `src/steelo/adapters/dataprocessing/postprocessing/post_process_datacollection.py` assembles a per-FG-per-year DataFrame from the stored pickle data. The column set is no longer hardcoded — keys come from runtime arguments computed once at simulation start.

### Header columns (deterministic order)

| Column | Source |
|--------|--------|
| `iso3` | `Plant.location.iso3` |
| `country` | `iso3_to_country_map[iso3]` |
| `region` | `iso3_to_region_map[iso3]` |
| `year`, `commands`, `materials`, `energy`, `cost_breakdown` | Per-FG state |

Headers are placed first via explicit reordering so downstream consumers can rely on a stable schema.

### Dynamic feedstock / carrier columns

Wide-form columns are emitted for each canonical feedstock or carrier key. Three families share this pattern:

| Family | Key source | Example column |
|--------|-----------|----------------|
| `cost_breakdown - <key>` | `Environment.cost_breakdown_keys` (built from dynamic feedstocks via `normalize_energy_key`) | `cost_breakdown - hydrogen` |
| `carbon_breakdown - <feedstock>` | `Environment.carbon_breakdown_columns` | `carbon_breakdown - coal` |
| `unit_subsidy_<carrier>` | `plant.columns[startswith("unit_subsidy_")]` plus a fixed-order header set, then `unit_subsidy_total` | `unit_subsidy_hydrogen` |

The previous hardcoded `STANDARD_COST_BREAKDOWN_COLUMNS` list and the `fluxes` / `lime` → `burnt lime` rename map have been removed; new energy carriers and feedstocks now appear in the post-processed CSV automatically without code edits. Missing keys are zero-padded so the schema is the same across runs.

### Optional columns

| Column | Present when |
|--------|-------------|
| `unit_secondary_output_costs` | FG records secondary-output cost adjustments |
| `unit_carbon_cost` | Carbon-cost calculation produced a value |
| `unit_carbon_cost_contribution - co2_slip` | FG has a non-zero `co2_slip × carbon_price` contribution (calculated on `FurnaceGroup`, collected each year) |
| `emissions_<boundary>_<scope>` | Wide-form columns expanded from per-FG `emissions[boundary][scope]` for every available boundary/scope pair |

### Per-carrier subsidy tracking

The data collector records each FG's per-carrier subsidy (`unit_subsidy_<carrier>`) and a roll-up `unit_subsidy_total`. The post-processor picks these up by prefix, places the fixed-order set first, then appends any additional carriers found at runtime, then `unit_subsidy_total`. This keeps existing dashboards stable while letting new carriers flow through.

---

## Tabular price outputs

`SimulationRunner` writes a per-year price CSV at the end of the run, keyed off `data_collector.trace_price`:

```
output/data/steel_iron_prices.csv
```

Columns: `year`, `steel_price_usd_per_t`, `iron_price_usd_per_t`, optional `scrap_price_usd_per_t`, optional `iron_weighted_avg_cost_usd_per_t`.

A matching matplotlib chart is also produced.

---

## Geospatial statistics: LCOE, LCOH, overbuild factors

`src/steelo/adapters/geospatial/geospatial_statistics.py` exports per-country statistics every modelled geospatial year (typically every 5 years where the geospatial pipeline runs):

- `output/data/LCOE/lcoe_stats_{year}.csv` — average / min / max / p10 / p20 / p25 / p50 LCOE in **USD/MWh** plus `n_grid_points`. Source LCOE is the `power_price` variable in `energy_prices` (USD/kWh, multiplied ×1000 on export).
- `output/data/LCOH/lcoh_stats_{year}.csv` — same statistics in **USD/kg** for the `capped_lcoh` variable, plus a `hydrogen_ceiling_pct` column reflecting the configured `GeoConfig` percentile cap.
- `output/data/{factor}_factors/{factor}_stats_{year}.csv` — overbuild-factor statistics (e.g. `solar_factor`, `wind_factor`, `battery_factor`) at LCOE percentile points (`avg`, `min`, `max`, `p10`, `p20`, `p25`, `p50`). Each value is the mean factor across grid points within ±5 % of the LCOE percentile in that country (with a closest-point fallback when the band is empty).

At end-of-run, `aggregate_lcoe_lcoh_statistics(output_dir, start_year, end_year)` concatenates the per-year files into:

- `output/data/LCOE/lcoe_stats_{start_year}_{end_year}.csv`
- `output/data/LCOH/lcoh_stats_{start_year}_{end_year}.csv`

sorted by `(year, country)`, formatted to 4 decimals. Per-year files are kept; aggregated files are recognised and excluded from the next aggregation pass via the `_<digits>` filename suffix check.

---

## Per-app-run config artefacts

When a simulation is launched from the Django/Electron app, `SimulationRunner` mirrors the CLI output layout by writing two artefacts into the run's output directory:

- `simulation_config.json` — the resolved `SimulationConfig` (after defaults, parameter merges and validation).
- `preparation_metadata.json` — metadata about the data preparation that fed this run (cache hash, source master-input version, etc.).

This makes app runs and CLI runs leave the same on-disk shape, simplifying downstream analysis tooling that walks output directories regardless of how the run was started.
