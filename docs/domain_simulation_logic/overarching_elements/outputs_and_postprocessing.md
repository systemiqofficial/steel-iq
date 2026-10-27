# Outputs and Post-Processing

This page describes the artefacts a simulation produces — in-memory traces during the run, plot files written by `SteelPlotter`, the post-processed CSVs assembled at the end, and the scenario viewer's files in `viewer/`. The model also writes LCOE and LCOH files covering every simulation year.

For where individual costs and emissions originate, see [Cost Calculation Functions](../plant_agent_model/calculate_costs.md). For how the trade LP feeds these traces, see [Trade Model Overview](../trade_model/overview_trade_model.md) and [TM-PAM Connector](../plant_agent_model/trade_model_connector.md).

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
| `greenfield_status_rows` | `[{year, furnace_group_id, …}]` | One flat row per year and greenfield FG, written to `data/greenfield_status_timeseries.csv` (see below) |
| `switch_decisions` | `{(fg_id, switch_year): row}` | One row per technology-switch decision, brownfield and greenfield, written to `data/pam_switch_decisions.csv` (see below) |
| `pipeline_status_rows` | `[{year, furnace_group_id, …}]` | One flat row per year and not-yet-operating furnace group of the existing fleet, written to `data/pipeline_status_timeseries.csv` (see below) |

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

`DataCollector` also writes `data/greenfield_status_timeseries.csv` at the end of the run: one row per year and greenfield furnace group, candidates that are never built included. A run that creates no greenfield groups writes no file.

| Column | Meaning |
|--------|---------|
| `year` | Snapshot year |
| `furnace_group_id`, `plant_id`, `plant_group_id` | The furnace group, its plant and its owning plant group |
| `product`, `technology`, `reductant` | As of the snapshot year, so technology and reductant switches show up as changes between rows |
| `status` | Lifecycle status in the snapshot year |
| `geo_key`, `region`, `lat`, `lon` | Location |
| `capacity` | Capacity in tonnes; in a year the group produced, the capacity that year's allocation used |
| `production`, `utilization_rate` | Zero unless the group produced that year: closing a furnace group leaves its last utilisation rate behind, which would otherwise book production for groups closed in earlier years. A group the plant agents close reads `closed` with its final production in its closing year (see [Which furnace groups get a row](#which-furnace-groups-get-a-row)) |
| `opportunity_npv` | The business-opportunity NPV evaluated that year while the group is a considered candidate; blank afterwards |

This file feeds the `greenfield_status.html` and `greenfield_map.html` viewers below.

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
  viewer/       # scenario viewer files, written last (see below)
```

`plots/GEO/` and `plots/TM/` are written only when `run_simulation` is given `--plot-geo` / `--plot-tm`; without the flag the folder is not created. The trade maps are replaced by the `trade_matrix.html`, `trade_network.html`, `supply_chain.html` and `trade_allocations.html` viewers below, which read the per-year `TM/steel_trade_allocations_<year>.csv` files written on every run.

Diagnostics exports under `output/diagnostics/` are off by default; set `STEEL_DIAGNOSTICS=1` to write them (`STEEL_DIAGNOSTICS_DETAIL` and `STEEL_DIAGNOSTICS_PATH` are described in `steelo.domain.diagnostics`).

Cost-curve filenames follow `cost_curve_{product}_by_{aggregation}_{year}.png` (e.g. `cost_curve_iron_by_region_2025.png`); the legacy ordering `{product}_cost_curve_by_{aggregation}_{year}.png` is no longer produced by the new methods. `plot_cost_curve_with_breakdown` retains its previous filename convention.

---

## Interactive viewers

`InteractivePlotter` (`src/steelo/utilities/interactive/`) writes a set of interactive plotly viewers to `plots/interactive/` at the end of every run. Each viewer is a single self-contained HTML file with the run's data embedded — open it in any browser, no server or network access needed.

All viewers except the two capacity maps share one shell (`common.js` / `common.css`): a run selector, a geography filter (countries, sub-national geo units, trade blocs, regions), an opt-in technology filter, and the shared colour schemes. Tick-box filters show as a row of ticks up to six options and fold into a dropdown with removable chips above that. Each shared dropdown has a search box and its own All/None buttons, which act on the rows the search shows; the countries dropdown lists the major producers (CHN, IND, USA, JPN, KOR, SAU, ARE, when present) first, above a separator, and the rest alphabetically. Sub-national units are labelled from the prepared `fixtures/geo_hierarchy.json` (codes when absent). Chart titles carry the run name — `--run-name` from the CLI or the run form's name from the app, with `_2`, `_3` added when another run holds it; without one, the `sim_<timestamp>` output directory name (CLI) or "Model Run #<id>" (app). The capacity maps (`capacity_world_map.html`, `capacity_china_map.html`) are pure SVG with their own toolbar, light theme only and no run selector; they take the shared CSS, the technology colours, and the country and geo-unit names from the same config.

| Viewer | Shows | Data source |
|--------|-------|-------------|
| `emissions.html` | Furnace-group emissions with direct / indirect / incl.-biogenic scope tickboxes, for every emissions boundary in the table, stacked by technology or region, annual or cumulative-to-year | `post_processed_<timestamp>.csv` |
| `capacity_and_production.html` | Capacity and production over time, with a capacity-vs-production compare mode and a steel demand overlay | `post_processed_<timestamp>.csv` + the run's demand scenario's `demand_centers.json` (found through `fixtures/scenarios.json`) |
| `cost_curves.html` | Per-commodity cost curves with the engine's market-clearing rule (clearing shares and price buffers from the run config) | `post_processed_<timestamp>.csv` + `data/market_prices_<start>_<end>.csv` |
| `trade_matrix.html` | Steel, iron products, iron ore and scrap shipped between geographies, per year, each product selectable individually | `TM/steel_trade_allocations_<year>.csv` |
| `trade_network.html` | The same trade flows as a chord diagram with a map layout | `TM/steel_trade_allocations_<year>.csv` |
| `supply_chain.html` | The whole chain behind the steel demand of one country or region, per year: the steel plants serving it, the iron products and scrap those plants took in, and the ore behind that iron, as tiers, a circle or a map. Upstream volumes are attributed pro rata to the share of each furnace group's output that went down the chain | `TM/steel_trade_allocations_<year>.csv` |
| `trade_allocations.html` | Every year's trade-LP allocations as commodity arcs over a world map, with a year slider and commodity toggles | `TM/steel_trade_allocations_<year>.csv` |
| `embedded_emissions_map.html` | The emissions embedded in steel trade as a world map with a year slider: each furnace group's emissions — direct / indirect / incl.-biogenic scope tickboxes, for every emissions boundary in the table, opening on direct (excl. biogenic) in the run's chosen boundary — are carried along the realised allocations, iron furnace group → steel furnace group → demand centre, pro rata to volume, giving production-based and consumption-based emissions per country, the emissions embedded in imports and exports, net imports (also as % of production-based) and the embedded intensity of the steel consumed. Clicking a country shows its series and partners; the geography filter treats a selection as one economy, netting out trade inside it. Indirect emissions are grid electricity at the plant's location plus the technology's indirect factor | post-processed CSV + `TM/steel_trade_allocations_<year>.csv` |
| `supply_demand.html` | Supply and demand for steel, scrap, iron ore, CO2 storage and biomass | `TM/` allocations + the run's demand scenario's `demand_centers.json` and scrap scenario's `suppliers.json` (found through `fixtures/scenarios.json`) + `fixtures/biomass_availability.json` |
| `reductant_use.html` | Iron production and absolute reductant use per reductant | `post_processed_<timestamp>.csv` + `fixtures/primary_feedstocks.json` |
| `metallic_charge_use.html` | Metallic charges fed into steel (scrap, hot metal, pig iron, DRI/HBI) and iron (ore grades), per charge / technology / region, with a local scrap supply overlay | `post_processed_<timestamp>.csv` + `fixtures/primary_feedstocks.json` + the run's scrap scenario's `suppliers.json` (found through `fixtures/scenarios.json`) |
| `decision_flows.html` | Furnace-group decision flows as a Sankey: each group's state when it first acts (its technology, or NEW for capacity that does not exist yet) flowing through decision rounds grouped into Renovate / Switch / Retire / Pipeline / Expand / Greenfield bands with technology sub-nodes; link width is the capacity entering the decision, in Mt; the Retire band keeps the closures the input data scheduled (`source` = `input_data`) and states their share in the hovers and the summary; a year-range slider narrows the flows by the year each motion took effect (the motions table's per-kind convention: a switch shows in its execution year, not its decision year) | `data/pam_motions.csv` |
| `greenfield_status.html` | The static greenfield status charts made interactive: greenfield furnace groups per year stacked by lifecycle status or by technology, as plant count, capacity or production, as the stock in each status or the flow entering it | `data/greenfield_status_timeseries.csv` |
| `greenfield_map.html` | The static greenfield maps made interactive: every greenfield furnace group as a capacity-sized dot on a world map with a year slider, filters for status, technology, product, reductant and metallic charge, and a per-site history (status entry years, technology and reductant switches, production and charge allocations); light and dark themes | `data/greenfield_status_timeseries.csv` + `post_processed_<timestamp>.csv` + `fixtures/primary_feedstocks.json` |
| `capacity_world_map.html` | One pie per plant on a world map (area = the plant's iron plus steel capacity, wedges = technology) with a year slider, filters for technology, status (operating, on by default; construction, off by default, which besides the model's new builds, expansions and rebuilds holds the units the input data lists as announced or under construction), plant origin (existing fleet, greenfield) and geography (regions, countries, China by province), a statistics side panel and a PNG export; pies that overlap at the current zoom merge. Groups being rebuilt for a technology switch show as under construction in the new technology. The change highlight rings a plant in its first operating year (opened), in its first year under construction, when its technology mix changes and when it closes | `post_processed_<timestamp>.csv` (operating groups) + `data/greenfield_status_timeseries.csv` (new builds under construction) + `data/pam_switch_decisions.csv` (rebuild years) + `data/pipeline_status_timeseries.csv` (the existing fleet's groups that are not operating yet: the input data's pipeline units and the PAM's expansions) + `data/pam_motions.csv` (fallback for runs without the pipeline table: expansions under construction from the motion year to their first operating year) + the run's live plants (coordinates, origin) |
| `capacity_china_map.html` | The capacity world map focused on China: Chinese plants only, provinces as the geography unit, an equirectangular view fitted to the country, province names shown. On policy-ON runs the capacity pool's province groups (Jing-Jin-Ji, Yangtze River Delta, Fenwei Plain) are selectable regions and hovers name a province's group and type. Skipped with a warning when the run has no Chinese plants | As `capacity_world_map.html` + `fixtures/capacity_pool_provinces.json` (policy-ON runs) |

A missing input file skips that viewer with a warning instead of failing the plot stage. The greenfield map drops its metallic-charge filter, with a warning, when only the post-processed table or the Bill of Materials is missing. The capacity world and China maps are still written, with a warning, without the greenfield table (no new builds), the switch decisions (no rebuilds; older runs lack the file) the pipeline table (older runs lack the file: the input data's announced and under-construction units then only appear once they operate) or the motions (no fallback for expansions under construction); any other failure skips the map with a warning. The maps' provenance text names only the inputs the run really had. Their plant names and source line come from the master's Furnace units sheet, read at start-up.

---

## Scenario viewer files (`viewer/`)

As its last step, every `run_simulation` and app run writes `viewer/` into its folder: the files the [Scenario Viewer](../../user_guide/scenario_viewer.md) reads (`write_viewer_files` in `src/steelo/viewer/extract.py`). They come from the last `post_processed_*.csv` in sorted order, `simulation_config.json`, `preparation_metadata.json`, the master workbook the run recorded and the pages in `plots/interactive/`; nothing is read from the prepared data.

| File | Content | Source |
|------|---------|--------|
| `timeseries.csv` | One row per year, geo key, product and technology: capacity, production and the nine emissions accountings (three boundaries, each direct, direct with biogenic and indirect), in kt | Post-processed CSV |
| `greenfield.csv` | New furnace groups and their capacity in kt, by build year, geo key, product and technology | Post-processed CSV |
| `annual_costs.csv` | Cost components in million USD by year, geo key, product, technology and reductant, with capacity and production | Post-processed CSV; `plant_lifetime` and `equity_share` from `simulation_config.json`; the master's "Cost of capital" and "Techno-economic details" sheets |
| `charge.csv`, `reductant.csv`, `trade.csv` | Metallic charges, reductant use and trade flows, as the plot pages show them | The data embedded in `metallic_charge_use.html`, `reductant_use.html` and `trade_matrix.html` |
| `supply.csv` | Use and availability of steel, scrap, iron ore, CO2 storage and biomass by year, geography, commodity and grade, in Mt | `supply_demand.html`; steel demand from the master's "Demand and scrap availability" sheet for the run's demand scenario |
| `geo.json` | Trade blocs, country and geo-unit names, region budgets | The configuration embedded in `emissions.html` and `supply_demand.html` |
| `inputs.json` | The run's View data rows: 11 topics over 9 master sheets, for the run's demand, scrap and grid emissions scenarios | The master workbook |
| `results/` | The Download results tables in the folders Run, Data, LCOE, LCOH, Trade and Policy, each table as `<name>.csv.gz` and in pages of 5,000 rows, listed in `index.json`. A yearly family `<name>_<year>.csv` becomes one table with a `year` column, unless a stacked `<name>_<y1>_<y2>.csv` sits beside it; a folder with no table is left out with a warning | `post_processed_*.csv`, `data/*.csv`, `data/LCOE/`, `data/LCOH/`, `TM/`, `data/policy/` |
| `run.json` | The run's name and folder, where it was made (`made_in`), its start (the config file's modification time, UTC) and end, the package version, its input data (master file name, master source, master name, preparation name, the SHA-256 of the master's bytes), its plot pages, the files above, and `schema` | `simulation_config.json`, `preparation_metadata.json`, the master workbook (the SHA-256 of its bytes) |

`run.json` is written last, so a `viewer/` folder holding it is complete, and the viewer lists only such runs. A plot page the run did not write skips the file taken from it, with a warning. `schema` is `SCHEMA_VERSION`, 1, also written into `inputs.json`; no reader checks it yet. The step cannot fail the run: an error is logged as "viewer files incomplete for this run" and the run still finishes, without `run.json`. `steelo-viewer regenerate` writes the folder again (see [CLI Commands](../../user_guide/cli_commands.md)); unlike the run's own step, it requires `emissions.html`.

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

### `data/pam_switch_decisions.csv` (every run)

A furnace group that decides to switch technology keeps operating its old technology, may then sit in `construction switching technology` (no post-processed row, as that status is not active) and surfaces in `pam_motions.csv` only in the year the new technology starts. `DataCollector` therefore records each switch decision once, with its timing and the NPV evaluation behind it, and writes `output/data/pam_switch_decisions.csv` at the end of the run (header only when no switch was decided). Rows cover all switches, brownfield and greenfield, and are keyed on (`furnace_group_id`, `switch_year`), so a group that switches twice has two rows.

| Column | Meaning |
|--------|---------|
| `decision_year` | Year the PAM decided the switch (the first year the collector sees it scheduled) |
| `switch_year` | Year the new technology starts: `decision_year` + `construction_time` |
| `construction_start_year` | First year the group was observed in `construction switching technology`; blank if never (the old lifetime outlasted the construction period) |
| `executed` | `True` once the group is observed at or after `switch_year` running `new_technology`, outside a switching status unless that status already belongs to the group's next decision (the PAM can re-decide a group in its switch year, before the collector looks); decisions whose `switch_year` lies beyond the horizon stay `False` |
| `origin` | `greenfield` when the plant's `parent_gem_id` starts with `indi_`, else `brownfield` |
| `plant_id`, `furnace_group_id`, `plant_group_id`, `geo_key`, `product` | The deciding furnace group, the plant group owning its plant at decision time, its location key and product |
| `old_technology`, `new_technology` | Technology before and after the switch |
| `old_capacity_t`, `new_capacity_t` | The group's capacity at decision time and the capacity the new technology is built at (smaller when the capacity policy shrank the replacement), in tonnes |
| `reductant` | Reductant chosen for the new technology |
| `winning_npv`, `cosa` | NPV of the selected technology and the cost of stranded assets of the evaluation |
| `incumbent_npv` | NPV of keeping the old technology; blank when the incumbent did not take part (renovation not an allowed transition) |
| `competing_npvs` | JSON object `{technology: npv}` of the finite NPVs the selection ran over (challengers COSA-adjusted); blank, with `incumbent_npv` and `selection_probabilities`, when the command carries none — the row is kept |
| `selection_probabilities` | JSON object `{technology: max(npv, 0) / sum of those weights}` over the same technologies — the technology draw only, not the acceptance draw that follows it; blank when `probabilistic_agents` is off, because the maximum NPV is then taken without a draw |

### `data/pipeline_status_timeseries.csv` (every run)

A furnace group of the existing fleet that is not operating yet has no post-processed row (its status is not active) and is not in the greenfield table (its plant is not GEO-origin). `DataCollector` therefore records one row per year for every such group and writes `output/data/pipeline_status_timeseries.csv` at the end of the run (header only when there is none). The groups are the units the input data lists as announced or under construction, and the expansions the PAM builds at existing plants. At the start of the simulation the input data's `announced` units are turned into `construction`: they start operating in their start year with certainty, unlike an announced greenfield opportunity, so they are booked as a firm CO2 storage commitment and read as committed capacity in every output. Their start year is unchanged.

| Column | Meaning |
|--------|---------|
| `year` | Simulation year of the snapshot |
| `furnace_group_id`, `plant_id`, `plant_group_id`, `geo_key` | The group, its plant, the plant group owning it that year and its location key |
| `product`, `technology`, `reductant` | What the group will produce and with what |
| `status` | `construction` (`announced` cannot occur for input-data units after the start-of-run rename) |
| `capacity` | Capacity in tonnes |
| `start_year` | Year the group turns operating; for input-data units without one in the master it is the model's draw |
| `created_by_pam` | `True` for an expansion the PAM decided, `False` for a unit of the input data |

This file feeds the `capacity_world_map.html` and `capacity_china_map.html` viewers above.

### `data/policy/` (policy-ON runs only)

When China's capacity-replacement policy is enabled (`run_simulation --enable-capacity-policy`, or the app's "Capacity-replacement policy (China)" switch), `CapacityPolicyRecorder` additionally writes four CSVs to `output/data/policy/`. A policy-OFF run creates no `data/policy/` directory.

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

### Which furnace groups get a row

Data collection runs at the end of each simulated year, after the trade allocation, the plant agents and the geospatial model. A furnace group gets a row for a year when it is in an active status (`operating`, `operating pre-retirement`, `operating switching technology`) at that point, or when the plant agents closed it during that year. A closing group is recognised by this year's entry in its utilisation history, which the allocation writes for exactly the groups it sets production for; its status or production cannot serve, as closing leaves the last utilisation rate, bill of materials and emissions behind. Its row carries the year's production (equal to its allocated outflow), bill of materials, emissions and costs, and its capacity.

Capacity in a year therefore reads as the capacity that year's allocation could draw on, and production as the tonnes it allocated. A renovation that shrinks a group under the capacity policy is decided after the allocation, so the row of the renovation year keeps the pre-shrink capacity and the full production; the shrunk capacity shows from the next year, when the allocation first uses it. Both kinds of closure behave alike: a group that reaches its end of life, or that the plant agents close, in year Y has its last row in Y and none from Y+1. A group that sat idle in the year it was closed has a row with zero production, like any idle active group. The same rule governs the emissions, production-by-product, iron ore and metallic charge traces behind the static plots.

Before this rule, a group closed by the plant agents had no row in its closing year although it had produced and shipped: up to about 2% of yearly production and capacity and about 4% of yearly emissions were missing, from the second simulated year on. A group shrunk by a renovation reported the shrunk capacity, and two thirds of its production at a 1.5 replacement ratio, in the renovation year already (capacity policy runs only, about 0.3% of yearly production). Reported production, emissions, capacity and production-weighted cost averages are not comparable between runs from before and after the change; the simulation's decisions are unaffected.

### Fixes that affect comparability between runs

The fixes below change results. Runs from before and after a fix are not directly comparable on the outputs listed.

| Fix | What changed | Outputs affected | Decisions affected |
|---|---|---|---|
| **Indirect emissions counted once** (#181) | `calculate_emissions()` added a furnace group's grid-electricity emissions to `indirect_ghg` once per metallic charge, so a group with several charges (an EAF on scrap, DRI and pig iron; a BOF on hot metal and scrap) counted them several times. They are now added once per emissions boundary. A charge without an indirect emission factor row also no longer drops the grid emissions. | Indirect (Scope 2) emissions in the post-processed CSV, the emissions plots and the emissions viewer; lower than before for multi-charge groups | No: carbon cost and the carbon border charge use direct emissions |
| **Every iron ore mine has an ISO-3 code** (#181) | Mines in 13 countries missing from the region mapping had an empty ISO-3 code, so about 22% of iron ore shipped without transport cost or tariff in the trade LP. Every mine now carries its country code, and an unmapped region stops data preparation. See [Iron ore mines](../../user_guide/master_input_reference.md#iron-ore-mines). | Ore sourcing and trade flows, iron and steel costs, prices | Yes: costs feed prices, NPVs and every plant decision |
| **End-year technology switch runs once** (#181) | The final simulated year is finalised twice. A switch scheduled for that year was replayed on the second pass, which booked the new technology's own debt again as legacy debt and recorded the switch twice. A scheduled switch now runs only while the group still holds a switching status. | Final year only, for groups switching that year: debt, unit cost and switch records | No: the replay happened after the year's decisions |
| **One hot-metal reach rule** (#190) | Under the default plant scope the trade LP allowed hot metal only within one plant, while clustering kept and sized a BOF from any in-country BF within `hot_metal_radius`, and the plant agents' access flag counted any BF of the same owner, whatever its status. The LP now allows hot metal between plants within the radius, and the access flag, clustering and the LP share one reach rule set by `geographical_clustering_scope`. See [Hot-metal producers](../trade_model/overview_trade_model.md#hot-metal-producers). | BOF production and utilisation where a neighbouring plant's BF feeds the BOF, hot metal and pig iron flows, and the costs, prices and emissions that follow | Yes: a BOF fed by another owner's BF keeps access at renovation and expansion, and a BOF whose only nearby BF is retired, mothballed or announced loses it |
| **Bio-PCI availability in tonnes** (#145) | `Biomass availability` rows with a unit in kg (`kg charcoal-equivalent/y`) were read as tonnes, so the bio-PCI limits in the trade LP were 1000 times too high and never bound. They are now divided by 1000. See [Biomass availability](../../user_guide/master_input_reference.md#biomass-availability). | Bio-PCI use and the furnace groups' charge mix, costs and emissions where the limit now binds | Yes: through costs and prices |

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

## Geospatial statistics: LCOE and LCOH files

Five files hold the power and hydrogen prices of every simulation year, one file each, under `output/data/`:

| File | Open it for | One row per |
|---|---|---|
| `LCOE/lcoe_summary.csv` | A quick look at power prices: what renewables cost, what the grid costs, what new sites pay | area, year |
| `LCOH/lcoh_summary.csv` | A quick look at hydrogen prices: new sites against existing plants | area, year |
| `LCOE/lcoe_detailed.csv` | Analysis of power prices: the full distribution of each price type | area, year, `price_type` (`pure_lcoe`, `grid`, `grid_mix`) |
| `LCOH/lcoh_new_sites_detailed.csv` | Analysis of new sites' hydrogen prices, before and after the ceiling | area, year, `price_type` (`uncapped`, `capped`) |
| `LCOH/lcoh_existing_plants.csv` | Existing plants' hydrogen price and every input behind it | country or geo unit, year |

Start with the two summaries. The geospatial step appends the four cell-based files every simulation year (`geospatial_statistics.export_lcoe_lcoh_outputs`); the start year writes them afresh, so a rerun into the same folder starts clean. A failure to write them stops the run. `lcoh_existing_plants.csv` is written once at bootstrap (`Environment.export_lcoh_existing_plants`), from the start year to the end year.

New-site statistics are taken over the 0.25° cells that carry a country, a power price and a capped hydrogen price; all price types of an area use the same cells. **Every cell weighs the same**, and cells cover less ground towards the poles, so an area's average is a cell average, not an area-weighted one.

Runs made before these files existed have per-year `LCOE/lcoe_stats_{year}.csv` and `LCOH/lcoh_stats_{year}.csv` files (every five years, with `*_{start}_{end}.csv` roll-ups) instead.

### Areas (rows)

| `level` | `country` | `subregion` | Cells |
|---|---|---|---|
| `world` | `World` | empty | all cells |
| `irena_region` | `irena_<IRENA region>` | empty | cells of countries in that IRENA region; countries without one are in no IRENA row |
| `country` | ISO3 | empty | the country's cells |
| `geo_unit_region` | ISO3 | Natural Earth region, e.g. `East China` | cells of the country's geo units in that region |
| `geo_unit` | ISO3 | ISO 3166-2 code, e.g. `CN-XJ` | cells inside that geo unit |

- Cells outside every geo unit polygon count in the world, IRENA and country rows only.
- Only China has declared geo units today, so only China has `geo_unit_region` and `geo_unit` rows. Natural Earth groups China into six regions; BOA uses seven.
- Without `geo_hierarchy.json` there are no `geo_unit_region` or `geo_unit` rows.
- The summaries cover areas with at least one cell. `lcoh_existing_plants.csv` covers every country and geo unit the model prices hydrogen for, including those without cells.

### Columns shared by all five files

| Column | Meaning |
|---|---|
| `level` | Type of area: `world`, `irena_region`, `country`, `geo_unit_region` or `geo_unit` |
| `country` | `World`, `irena_<IRENA region>` or the ISO3 country code |
| `subregion` | Empty for world, IRENA and country rows; Natural Earth region name for `geo_unit_region` rows; ISO 3166-2 code for `geo_unit` rows |
| `year` | Simulation year |
| `baseload_coverage_pct` | Share of a site's electricity demand that the BOA renewable system is sized to cover, in %: `c × 100`. 85 for "85% baseload + 15% grid"; 0 for "Grid only" and "Not included" |
| `grid_share_pct` | Share of the priced electricity bought from the grid, in %: `(1 - c) × 100`. 0 on `pure_lcoe` rows |
| `n_grid_points` | Number of 0.25° cells in the area that carry a price. Statistics weight every cell equally. Not in `lcoh_existing_plants.csv`, which has no cell statistics |

**Statistic columns.** `avg` is the mean over the area's cells. `pN` is the Nth percentile (`np.percentile`, linear interpolation, the same method as the BOA summaries). `p0` is the cheapest cell and `p100` the most expensive. Detailed files carry `avg`, `p0`, `p10`, `p20`, `p25`, `p30`, `p40`, `p50`, `p60`, `p70`, `p75`, `p80`, `p90` and `p100`.

### `lcoe_detailed.csv`

Shared columns, `price_type`, then `<stat>_usd_per_mwh` (USD/MWh), then `n_grid_points`.

| `price_type` | Per-cell value |
|---|---|
| `pure_lcoe` | BOA LCOE of the optimal renewable system at the cell, in USD per MWh delivered by that system, interpolated to the year. Not written when the coverage is 0 |
| `grid` | Grid electricity price at the cell: the master's `Input costs` electricity for the cell's geo unit where it has a row, otherwise for its country |
| `grid_mix` | Price of 1 MWh of a new site's electricity demand: `grid_share × grid + coverage × pure_lcoe`. New plants pay this for electricity and for electrolysis power. Equals `grid` when the coverage is 0 |

### `lcoe_summary.csv`

Shared columns, then the following (USD/MWh), each equal to the matching cell in `lcoe_detailed.csv`.

| Column | Meaning |
|---|---|
| `pure_lcoe_avg_usd_per_mwh`, `pure_lcoe_p50_usd_per_mwh` | Mean and median BOA LCOE across the area's cells |
| `grid_avg_usd_per_mwh` | Mean grid price across the area's cells. For a geo unit this is its grid price. For a country with geo unit prices it is the cell-weighted mean of those prices, not the national price |
| `grid_mix_avg_usd_per_mwh`, `grid_mix_p10_usd_per_mwh`, `grid_mix_p50_usd_per_mwh`, `grid_mix_p90_usd_per_mwh` | Mean, 10th, 50th and 90th percentile of what new sites pay for power |

### `lcoh_new_sites_detailed.csv`

Shared columns, `hydrogen_ceiling_pct`, `intraregional_trade_allowed`, `price_type`, `ceiling_usd_per_kg`, then `<stat>_usd_per_kg` (USD/kg), then `n_grid_points`.

| Column | Meaning |
|---|---|
| `hydrogen_ceiling_pct` | Percentile of hydrogen costs within each hydrogen trade region (TIAM-UCL) used as that region's price ceiling. For new sites the percentile is taken across the region's cells. 100 means no ceiling |
| `intraregional_trade_allowed` | `True` if a region may import hydrogen from linked regions at their ceiling plus the long-distance pipeline cost |
| `price_type` = `uncapped` | Electrolyser energy (kWh/kg) × the cell's `grid_mix` price + electrolyser CAPEX/OPEX for the cell's geo unit, otherwise its country |
| `price_type` = `capped` | `min(uncapped, ceiling)`: what a new plant pays for hydrogen |
| `ceiling_usd_per_kg` | Effective ceiling applied to the area's cells, trade-adjusted when trade is allowed. Empty when the area's cells span several hydrogen trade regions, as World and most IRENA rows do |

A cell whose geo unit and country both lack an electrolyser CAPEX/OPEX row has no uncapped price but is still priced at its region's ceiling on the `capped` row. `n_grid_points` counts it on both rows, and the `uncapped` statistics skip it. No cell is in this case with the current master data.

### `lcoh_existing_plants.csv`

`level` (`country` or `geo_unit`), `country`, `subregion`, `year`, `baseload_coverage_pct`, `grid_share_pct`, `hydrogen_ceiling_pct`, `intraregional_trade_allowed`, `hydrogen_lcoe_percentile`, then the following.

| Column | Meaning |
|---|---|
| `baseload_coverage_pct`, `grid_share_pct` | As above, at the run's power mix |
| `hydrogen_lcoe_percentile` | Percentile of the area's cell LCOEs taken as existing plants' renewable price; default 50 (median) |
| `grid_usd_per_mwh` | Grid electricity price (USD/MWh) for the geo unit where the master has a row, otherwise for the country |
| `pure_lcoe_usd_per_mwh` | That percentile of the area's BOA cell LCOEs (USD/MWh). A country's value spans all its cells, including those in its geo units. Areas without an LCOE cell of their own take the mean of their 8 nearest cells. Empty when the coverage is 0 |
| `grid_mix_usd_per_mwh` | `grid_share × grid + coverage × pure_lcoe` (USD/MWh): the electrolysis power price for existing plants |
| `electrolyser_kwh_per_kg` | Electrolyser energy use in the year (master `Hydrogen efficiency` × 1000; the same everywhere) |
| `capex_opex_usd_per_kg` | Electrolyser CAPEX/OPEX (USD/kg) for the geo unit where the master has a row, otherwise for the country |
| `uncapped_usd_per_kg` | `electrolyser_kwh_per_kg × grid_mix + capex_opex` (USD/kg) |
| `ceiling_usd_per_kg` | Effective ceiling (USD/kg), trade-adjusted when allowed. For existing plants the percentile is taken across the region's country-level prices, not cells, and a geo unit inherits its country's ceiling |
| `capped_usd_per_kg` | `min(uncapped, ceiling)` (USD/kg): what existing plants pay for hydrogen |

The file stops at the last year of the input costs when the run ends later.

### `lcoh_summary.csv`

`level`, `country`, `subregion`, `year`, `baseload_coverage_pct`, `grid_share_pct`, `hydrogen_ceiling_pct`, `intraregional_trade_allowed`, then the following.

| Column | Meaning |
|---|---|
| `new_site_avg_usd_per_kg`, `new_site_p10_usd_per_kg`, `new_site_p50_usd_per_kg`, `new_site_p90_usd_per_kg` | Mean, 10th, 50th and 90th percentile of the capped new-site hydrogen price across the area's cells (USD/kg); equal to the matching cells in `lcoh_new_sites_detailed.csv` |
| `existing_plant_usd_per_kg` | What existing plants in the country or geo unit pay for hydrogen that year, the `capped_usd_per_kg` of `lcoh_existing_plants.csv`; beyond the last input-cost year, the last covered year's price, as plants pay it. Empty on world, IRENA and `geo_unit_region` rows, and on a geo unit row whose geo unit has no row of its own in the master (its existing plants pay the country's price) |
| `n_grid_points` | As above |

---

## Run config and metadata files

Both entry points, `run_simulation` and the Django/Electron app, write two files into the run's output directory before the run starts:

- `simulation_config.json` — the run's `SimulationConfig`, one key per public field in declaration order: paths as strings, `geo_config` and `capacity_policy` as nested objects keyed by their field names, `technology_settings` as one object per technology code (`allowed`, `from_year`, `to_year`), and `disposal_cost_outputs` as a sorted list. Private fields are left out.
- `preparation_metadata.json` — where the run's data came from. Both writers record `preparation_time`, `cache_used`, `master_excel` (the workbook's path; null for an app run without a ready data preparation), `made_in` (`cli` or `app`) and `master_source` (`default` for the packaged master, else `custom`). A CLI run adds `cached_from` on a cache hit, or `preparation_duration`, `files_prepared` and `temp_prep_dir` when it prepared the data, and its `demand_scenario` and `scrap_scenario`. An app run adds `cached_from` (its preparation's data folder), `master_name` and `preparation_name`; its scenarios are recorded only in `simulation_config.json`.

This makes app runs and CLI runs leave the same on-disk shape, simplifying downstream analysis tooling that walks output directories regardless of how the run was started.
