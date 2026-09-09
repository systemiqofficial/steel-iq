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
| `status_counts` | `{product: {year: {tech: {status: count}}}}` | Greenfield (GEO-origin) FGs, every status, every year |
| `new_plant_locations` | `{product: {year: [{lat, lon}, …]}}` | Greenfield FGs in the year they start operating |
| `greenfield_plants` | `{fg_id: record}` | One record per greenfield FG: identity and location on first sighting, plus the first year each status was observed and the initial versus final technology, reductant and capacity, refreshed yearly |

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
| `plot_greenfield_plants_by_status` | `status_counts` | `GREENFIELD_SUBDIR` (`plots/greenfield`) |
| `plot_greenfield_plants_map` | `new_plant_locations` | `GREENFIELD_SUBDIR` (`plots/greenfield`) |
| `export_greenfield_plants_csv` | `greenfield_plants` | `GREENFIELD_SUBDIR` (`plots/greenfield/greenfield_plants.csv`) |

### Greenfield outputs

Plants that originate in the geospatial model (greenfield, GEO-origin) get their own outputs under `plots/greenfield/`, drawn by `SteelPlotter` at the end of the run:

- `<product>_greenfield_status.png` — yearly counts of greenfield plants by status, one stacked bar chart per product.
- `<product>_greenfield_map.png` — world map of newly operating greenfield plants coloured by 5-year operational-start class, plus one map per decade (`<product>_greenfield_map_<y0>-<y1>.png`) coloured by exact year.
- `greenfield_plants.csv` — one row per greenfield furnace group that was actually built: the year it entered each lifecycle status (blank where unobserved), the scheduled lifetime end, current status, location (region, geo key, lat/lon), identity, and initial versus final technology, reductant and capacity. Candidates that never reached construction are excluded. For plants still under construction at the end of the run, `year_operating` holds the scheduled operating year; such rows have `status == "construction"`.

### Plot folder layout

Emissions, cost-curve, greenfield and capacity-pool plots write to top-level sibling folders rather than under `plots/PAM/…`:

```
output/
  plots/
    PAM/        # plant-agent plots (capacity, capex, charges, prices)
    GEO/        # opt-in (--plot-geo): geospatial plots (priority maps on milestone years; power-price histogram and mine map final-year-only)
    TM/         # opt-in (--plot-tm): per-year trade maps, replaced by the interactive trade viewers
    emissions/  # SteelPlotter.plot_emissions_by_technology
    cost_curves/  # SteelPlotter cost-curve methods
    greenfield/   # SteelPlotter greenfield status charts, maps and greenfield_plants.csv (see above)
    capacity_pool/  # CapacityPoolPlotter charts, policy-ON runs only (see below)
    interactive/  # InteractivePlotter viewers (self-contained HTML, see below)
```

`plots/GEO/` and `plots/TM/` are written only when `run_simulation` is given `--plot-geo` / `--plot-tm`; without the flag the folder is not created. The trade maps are replaced by the `trade_matrix.html`, `trade_network.html` and `trade_allocations.html` viewers below, which read the per-year `TM/steel_trade_allocations_<year>.csv` files written on every run.

Diagnostics exports under `output/diagnostics/` are off by default; set `STEEL_DIAGNOSTICS=1` to write them (`STEEL_DIAGNOSTICS_DETAIL` and `STEEL_DIAGNOSTICS_PATH` are described in `steelo.domain.diagnostics`).

Cost-curve filenames follow `cost_curve_{product}_by_{aggregation}_{year}.png` (e.g. `cost_curve_iron_by_region_2025.png`); the legacy ordering `{product}_cost_curve_by_{aggregation}_{year}.png` is no longer produced by the new methods. `plot_cost_curve_with_breakdown` retains its previous filename convention.

---

## Interactive viewers

`InteractivePlotter` (`src/steelo/utilities/interactive/`) writes a set of interactive plotly viewers to `plots/interactive/` at the end of every run. Each viewer is a single self-contained HTML file with the run's data embedded — open it in any browser, no server or network access needed.

All viewers share one shell (`common.js` / `common.css`): a run selector, a geography filter (countries, sub-national geo units, trade blocs, regions), an opt-in technology filter, and the shared colour schemes. Sub-national units are labelled from the prepared `fixtures/geo_hierarchy.json` (codes when absent). Chart titles carry the run name — `--run-name`, defaulting to the `sim_<timestamp>` output directory name.

| Viewer | Shows | Data source |
|--------|-------|-------------|
| `emissions.html` | Furnace-group emissions with direct / indirect / incl.-biogenic scope tickboxes, for every emissions boundary in the table, stacked by technology or region, annual or cumulative-to-year | `post_processed_<timestamp>.csv` |
| `capacity_and_production.html` | Capacity and production over time, with a capacity-vs-production compare mode and a steel demand overlay | `post_processed_<timestamp>.csv` + `fixtures/demand_centers.json` |
| `cost_curves.html` | Per-commodity cost curves with the engine's market-clearing rule (clearing shares and price buffers from the run config) | `post_processed_<timestamp>.csv` + `data/market_prices_<start>_<end>.csv` |
| `trade_matrix.html` | Steel, iron products, iron ore (mine-labelled origins) and scrap shipped between geographies, per year, each product selectable individually | `TM/steel_trade_allocations_<year>.csv` |
| `trade_network.html` | The same trade flows as a chord diagram with a map layout | `TM/steel_trade_allocations_<year>.csv` |
| `trade_allocations.html` | Every year's trade-LP allocations as commodity arcs over a world map, with a year slider and commodity toggles | `TM/steel_trade_allocations_<year>.csv` |
| `supply_demand.html` | Supply and demand for steel, scrap, iron ore, CO2 storage and biomass | `TM/` allocations + `fixtures/suppliers.json` + `fixtures/biomass_availability.json` |
| `reductant_use.html` | Iron production and absolute reductant use per reductant | `post_processed_<timestamp>.csv` + `fixtures/primary_feedstocks.json` |
| `metallic_charge_use.html` | Metallic charges fed into steel (scrap, hot metal, pig iron, DRI/HBI) and iron (ore grades), per charge / technology / region, with a local scrap supply overlay | `post_processed_<timestamp>.csv` + `fixtures/primary_feedstocks.json` + `fixtures/suppliers.json` |
| `decision_flows.html` | Furnace-group decision flows as a Sankey: each group's state when it first acts (its technology, or NEW for capacity that does not exist yet) flowing through decision rounds grouped into Renovate / Switch / Retire / Pipeline / Expand / Greenfield bands with technology sub-nodes; link width is the capacity entering the decision, in Mt | `data/pam_motions.csv` |

A missing input file skips that viewer with a warning instead of failing the plot stage.

---

## Fleet motions and capacity-policy artefacts

### `data/pam_motions.csv` (every run)

`steelo.motions` records every fleet mutation of the run — closures, renovations, technology switches, expansions, greenfield builds and pipeline arrivals (announced or under-construction units from the input data starting operation) — for all countries, and writes them to `output/data/pam_motions.csv` at the end of the run (header only when nothing moved).

| Column | Meaning |
|--------|---------|
| `year` | Year the motion took effect |
| `kind` | `close`, `renovate`, `switch`, `expansion`, `greenfield` or `pipeline` |
| `source` | `pam` for a model decision; `input_data` for capacity the input data scheduled — pipeline arrivals, and end-of-life closures whose lifetime no model action had reset |
| `plant_id`, `furnace_group_id`, `geo_key`, `owner_id`, `product` | The furnace group that moved, its location key and its owning plant group |
| `old_technology`, `new_technology`, `old_capacity_t`, `new_capacity_t`, `reductant` | Technology and capacity (tonnes) before and after the motion, and the reductant in use |

This file feeds the `decision_flows.html` viewer above.

### `data/policy/` (policy-ON runs only)

When China's capacity-replacement policy is enabled (`run_simulation --enable-capacity-policy`), `CapacityPolicyRecorder` additionally writes four CSVs to `output/data/policy/`. A policy-OFF run creates no `data/policy/` directory.

| File | One row per | Columns |
|------|-------------|---------|
| `capacity_pool_ledger.csv` | Pool operation: `seed`, `deposit_close`, `deposit_close_end_of_life`, `deposit_replace`, `withdraw_expansion`, `withdraw_greenfield`, `expired`, `expired_unowned`, `refunded`, `blocked_expansion`, `blocked_greenfield`, `greenfield_discard` | `year`, `operation`, `amount_t`, `region_tag`, `owner_id`, `product`, `vintage_year`, `credits_consumed`, `blocked_reason`, `attributed_owner_id`, `geo_key`, `furnace_group_id` |
| `capacity_pool_state.csv` | Year-end pool balance per `(region_tag, owner_id, product)` | `year`, `region_tag`, `owner_id`, `product`, `remaining_t`, `oldest_vintage` |
| `capacity_pool_gate_decisions.csv` | Replacement-gate evaluation of a switch or renovation candidate | `year`, `furnace_group_id`, `geo_key`, `product`, `old_technology`, `old_reductant`, `new_technology`, `new_reductant`, `decision` (`ratio` or `blocked_utilization`), `ratio`, `capacity_t`, `permitted_t`, `old_used_conservative_fallback`, `new_used_conservative_fallback` |
| `pam_motions_china.csv` | Chinese fleet motion — the `CHN` slice of `data/pam_motions.csv` | Same as `pam_motions.csv` |

The `plots/capacity_pool/` charts (`CapacityPoolPlotter`) are drawn from these files. Per product: the drawable pool by build location, the whole pool as a band and each cluster's pot as a line (`<product>_pool_nested.png`); the pool by region tag as a stacked area (`<product>_pool_area.png`); the capacity the policy refused, stacked by reason (`<product>_capacity_refused.png`); and the pool's yearly deposits and withdrawals (`<product>_capacity_flows.png`). Across products, `technology_mix.png` shows the capacity moved into each technology, stacked by the motion that moved it. Each chart has a sibling CSV.

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
