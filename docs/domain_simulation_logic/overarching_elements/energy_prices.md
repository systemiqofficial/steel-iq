# Energy Prices of Existing and New Plants

Electricity and hydrogen are the two energy carriers whose prices differ between existing plants and the new plants the model opens at geospatial sites. Every other carrier comes from the master's input costs for both. This page follows the two prices step by step through the code as it stands, and names the stage of the energy-price trace that watches each step. The [Logging Guide](../../user_guide/LOGGING_GUIDE.md#energy-price-trace) explains how to switch the trace on and read its output.

LCOE below is the levelised cost of electricity of a site's own baseload supply, and LCOH the levelised cost of hydrogen.

## Units

- **Electricity**: USD/kWh in the site rasters and in furnace groups. The baseload LCOE files hold USD/MWh and are converted on reading.
- **Hydrogen**: USD/kg in the site raster and in the country series; USD/t in furnace groups and in the cost data of business opportunities, which multiply the USD/kg price by 1,000.

## Existing Plants

- **Electricity**: the master's input cost for the plant's geo key, the province row when the master has one, else the country row. Bootstrap writes it into every furnace group, and the end of each year writes the next year's price (`Environment.set_input_cost_in_furnace_groups`).
- **Hydrogen**: bootstrap precomputes a capped price per geo key for every year from the start year to the last input-cost year (`Environment.initiate_capped_hydrogen_costs_by_year`).
  - *Electrolysis power* is priced at the run's power mix: `(1 - c) × grid + c × LCOE`, where c is the baseload coverage (0.85, 0.9 or 0.95), the grid price is the geo key's input-cost electricity price, and the LCOE is a percentile of the LCOE of the geo key's cells in the promoted BOA file new sites read (step 1). The percentile is `hydrogen_lcoe_percentile`, 50 (the median) by default. A country's cells include its provinces'; a province's cells are those inside its admin-1 shape, for the provinces `geo_hierarchy.json` declares. A geo key without a cell of its own that has an LCOE takes the mean of its 8 nearest cells that have one, around its cells or the representative point of its admin-1 shape (`hydrogen_lcoe.baseload_lcoe_by_geo_key_series`). The baseload share is priced at the LCOE even where the grid is cheaper. With the "Grid only" or "Not included" mix, electrolysis power is the grid price alone and no LCOE is read.
  - *LCOH*: the electrolyser's electricity use times that power price, plus the electrolyser's capital and operating cost per kg, from the province row where the master has one, else from the country row.
  - *Ceiling and cap*: a region's ceiling is a percentile of its countries' LCOH (`hydrogen_ceiling_percentile`), and a province takes its country's region. The capped price is the lower of the LCOH and the ceiling; with intraregional trade on, also of the cheapest partner region's ceiling plus pipeline transport. At the 100th percentile, the default, the ceiling is raised to the highest LCOH of all the region's geo keys, provinces included, so no price is capped by its own region; with intraregional trade on, a partner region's ceiling then counts its provinces too. Below 100 the ceiling stays a percentile of the countries' LCOH only, so a province priced above it is capped at it.
  - At the start of each year, the geo key's capped price × 1,000 replaces the hydrogen price of every existing plant's furnace group (`Plant.update_furnace_hydrogen_costs`); new sites keep their own (steps 7 and 8). Data preparation also writes a grid-based country hydrogen price into the input costs, which bootstrap copies into every furnace group and its reductant pick reads; the year-start refresh replaces it, and the reductant pick runs again, before the first trade model, so no price paid uses it.
- **Subsidies**: energy subsidies for the furnace group's technology then apply on top. The prices without subsidy stay in `energy_costs_no_subsidy`.
- **Output**: bootstrap writes each geo key's hydrogen price and every input behind it, for the simulated years the input costs cover, to `data/LCOH/lcoh_existing_plants.csv`. See [Outputs and Post-Processing](outputs_and_postprocessing.md#geospatial-statistics-lcoe-and-lcoh-files).

**Trace:** at bootstrap, once per year of the series, `hydrogen_lcoe` checks each listed country's LCOE against the percentile of its cells, and `country_hydrogen` checks every geo key's electricity against the blend of its input-cost grid price and its LCOE, its LCOH against the electrolyser formula on that electricity, and its capped price against `min(LCOH, ceiling)`. `brownfield` checks every active furnace group's prices without subsidy each year, after the subsidies and before the trade model.

## New Plants, Step by Step

A new plant starts as a candidate site, becomes a considered business opportunity, is announced, enters construction and then operates. Its `parent_gem_id` starts with `indi`.

### 1. The site rasters

Each year the geospatial model prices every cell of a global 0.25° grid (`get_candidate_locations_for_opening_new_plants`):

- **Grid price**: the input-cost electricity price of the cell's geo key. Every cell is tagged with the province it lies in, by point-in-polygon against the admin-1 layer for the provinces `geo_hierarchy.json` declares (`hydrogen_lcoe.build_geo_key_grid`). A cell takes its province's row where the master has one, else its country's (`add_grid_power_price`). A cell whose centre lies outside every province polygon, such as some coastal cells, stays at country level. A feasible cell whose country has no price takes the highest country-level grid price, never a province's.
- **Baseload LCOE**: read from one promoted BOA file per coverage, which holds every investment year. The file comes from the folder `run_simulation --boa-run` names, else from the prepared data's `outputs/GEO/baseload_lcoe/`, where geo-data 3.0.0 ships the default files for 85%, 90% and 95% coverage. It is picked by the coverage token in its name (`cov0p85` for 0.85) and must state schema version 1, LCOE per MWh delivered and the coverage priced; bootstrap opens it once, so a missing or refused file stops the run before its first year. A year the file does not hold is interpolated linearly, and a year outside its span reuses the nearest year.
- **Power price**: `power_price = (1 - c) × grid + c × LCOE`, where c is the baseload coverage of the power mix (0.85, 0.9 or 0.95). The LCOE is a cost per MWh the baseload system delivers, and that system covers the share c of demand. With the "Grid only" or "Not included" mix, the power price is the grid price and no LCOE file is read.
- **Hydrogen**: a cell's LCOH is the electrolyser's electricity use times the cell's power price, plus the electrolyser's capital and operating cost per kg from its province's row where the master has one, else its country's. A region's ceiling is a percentile of its cells' LCOH (`hydrogen_ceiling_percentile`, 100 by default, which leaves prices uncapped), lowered with intraregional trade on to the cheapest partner region's ceiling plus pipeline transport. `capped_lcoh` is the lower of the LCOH and that effective ceiling. A cell without an LCOH, because it has no power price or no electrolyser cost row, stays unpriced, so it drops out of the candidate sites.
- **The ceilings of every lock year**: in its first year, before any site is priced, the geospatial step computes each region's effective ceiling for every year a new site can lock its stack (`geospatial_layers.site_hydrogen_ceilings`, stored as `Environment.site_hydrogen_ceilings`). It prices the cells of each year as above, from the year's grid prices, the LCOE file and the electrolyser inputs, without plots or trace stages, and logs its wall time at INFO ("Precomputed site hydrogen ceilings"). The years run from the first year to the last year every input covers (the electrolyser's use and cost, the input costs and the LCOE file), and no further than `end_year + max(consideration_time, 2)`, the last construction start a run can reach; a later year reads the last stored year's ceilings. Each year's raster is capped at the stored ceilings of its year, so the raster and every lock read one source.

**Output:** every year, the geospatial step appends each area's statistics of these rasters to `data/LCOE/lcoe_summary.csv`, `lcoe_detailed.csv`, `data/LCOH/lcoh_summary.csv` and `lcoh_new_sites_detailed.csv`.

**Trace:** `lcoe_source` records the file, its BOA run and version at bootstrap, and each listed country's median LCOE every year. `site_grid` checks every cell's grid price against the input costs at its geo key, else its country, and every cell without either against the highest country-level price. `site_power` checks every priced cell against the blend its LCOE file's basis implies. `site_hydrogen` checks every priced cell against its region's stored ceiling, and every cell with a power price and an electrolyser cost against the LCOH formula at its geo key; it counts the cells that carry a price without an LCOH, none since such cells stay unpriced. It also checks each region's stored ceiling against the one it computes from the year's raster, so the precompute and the raster cannot drift apart.

### 2. The ranking

The priority location key performance indicator (KPI) ranks cells by an outgoing cash flow proxy whose power term is the power price times the technology's electricity use, capacity and lifetime. The hydrogen price does not enter the ranking. See [Priority Location Selection](../geospatial_model/priority_location_selection.md).

**Trace:** no stage; the ranking reads the raster `site_power` checks.

### 3. The NPV at selection

`prepare_cost_data_for_business_opportunity` prices each sampled site for its net present value (NPV) as a system built at its earliest construction start b, the target year (the current year plus `max(consideration_time, 2)`). It locks the site's stack for b (`Environment.captive_energy_at`, a `CaptiveEnergy`):

- **Power:** the coverage c of the power mix and the LCOE of the site's cell for a system built in b, read from the LCOE file, which bootstrap loads into memory once (`boa_lcoe.load_lcoe_grid`), by the year rule of the raster (`boa_lcoe.lcoe_at_cell`). Under "Grid only" and "Not included", c and the LCOE are 0.
- **Electrolyser:** its electricity use e(b) and its capital and operating cost k(b) at the site's geo key, else its country.
- **Contract:** the region's stored ceiling of b (step 1), held as a contract when the site's own hydrogen price in b, e(b) × (c × L + (1 − c) × g(b)) + k(b), is above it.

The year-wise reductant score series prices every operating year y from the stack and the grid price g(y) at the site's geo key (`Environment.site_energy_costs_for_year`):

```
electricity(y) = c × L(site, b) + (1 − c) × g(y)
hydrogen(y)    = (e(b) × electricity(y) + k(b)) × 1,000, or the contract × 1,000
```

Each candidate technology's energy subsidies of year y apply on top. See [Site prices for geospatial sites](../plant_agent_model/reductant_choice_and_opex.md#site-prices-for-geospatial-sites). The site's three price dicts keep the cell's `power_price` and `capped_lcoh` × 1,000 of the current year, which the creation bill of materials reads, and the furnace group keeps the stack (`FurnaceGroup.captive_energy`). A cell without a finite LCOE in b, or a site without an electrolyser cost row, stops the run. The electrolyser inputs and the ceiling of a year past their data take the last data year's values.

**Trace:** `greenfield_select` writes each site's prices, its geo key's input-cost prices, the operating start year and the stack. It checks the stack's LCOE against the LCOE grid at the cell and b, and the NPV's electricity and hydrogen of the first operating year, before subsidies, against the formulas above, computed from the LCOE grid, the input costs, the electrolyser inputs and the stored ceiling.

### 4. Each year while considered

The year-start hydrogen refresh skips opportunities, as it skips every new site. Later in the year, after the plant agent model, `PlantGroup.update_dynamic_costs_for_business_opportunities` re-reads the site's `power_price` and `capped_lcoh` from the year's rasters. It writes them as the opportunity's electricity and hydrogen without subsidy, with the energy subsidies of the operating start year on top. It converts `capped_lcoh` from USD/kg to USD/t, as the selection does. Where a raster value is missing, it keeps the stored price. It also locks a new stack for the opportunity's earliest construction start, `max(current year + consideration_time − years considered, current year + 1)`, and stores it, even in a year whose prices, capex and cost of debt repeat the last year's. The yearly NPV re-valuation (`track_business_opportunities`) then prices the opportunity from that stack, as in step 3; an opportunity without one stops the run.

**Trace:** `greenfield_revalue` checks that the hydrogen written equals the site's `capped_lcoh` × 1,000 and the stack's LCOE against the LCOE grid at its lock year, and keeps the site's raster prices of the year.

### 5. Announcement

An opportunity whose NPVs over the consideration window are all positive is announced with probability `probability_of_announcement`. Its prices do not change at announcement, and step 4 keeps re-valuing it while it is announced. While announced, the re-valuation dates its capex and debt subsidies at the current year, since the construction draw later in the same year can start it, and its energy subsidies at that year plus the construction time, the operating start step 6 sets. Its stack is locked at the current year too.

**Trace:** `greenfield_revalue`, as in step 4. For an announced opportunity the stack's lock year is the year of the rasters, so the stack priced at the year's grid price must also equal the cell's `power_price` and `capped_lcoh` × 1,000 where the cell's raster geo key is the plant's. A coastal cell outside every province polygon is keyed to its country in the raster while its plant resolves a province, so its lock is recorded unchecked and counted.

### 6. Construction start

`update_status_of_furnace_group` moves an announced opportunity into construction. It sets the operating start year, the construction start plus the construction time (4 years by default), and the utilisation to zero. The prices stay as the last re-valuation wrote them, and so does the stack that re-valuation locked for the construction-start year. From now on the stack is fixed until a renovation or a switch re-locks it (step 10).

**Trace:** `greenfield_build` freezes the furnace group's stack as its reference and checks that its lock year is the construction-start year. It records the stored prices, the site's raster prices from the last re-valuation and the geo key's capped regional hydrogen price of that year, for reading only.

### 7. Construction

The furnace group is no longer re-valued. At the end of each year, `Environment.set_input_cost_in_furnace_groups` gives each furnace group its own copy of the next year's input costs and prices its electricity and hydrogen without subsidy from its stack and that year's grid price g at the plant's geo key, else its country:

```
electricity = c × L + (1 − c) × g
hydrogen    = (e × electricity + k) × 1,000, or the contract × 1,000
```

The grid share follows the grid every year; the LCOE, the electrolyser and the contract stay those of the lock year: the construction-start year, or the start of the latest renovation, switch or expansion. Every other carrier comes from the input costs. A considered or announced opportunity keeps its stored electricity and hydrogen, which the next re-valuation replaces. Any other new-site furnace group without a stack stops the run, since every build, switch, renovation and expansion locks one. The year-start hydrogen refresh skips new sites.

**Trace:** no stage.

### 8. Operation

At the start of its operating start year, before the trade model, the furnace group turns operating. It pays the electricity and hydrogen the year-end refresh set from its stack and the year's grid price, with the year's energy subsidies on top.

**Trace:** `greenfield_operating` checks both prices without subsidy against c × L + (1 − c) × g and the hydrogen rule, computed from the stack frozen at construction start and the year's grid price at the plant's geo key.

### 9. The yearly reductant pick

At bootstrap and at the start of each year's trade model setup, every furnace group re-picks its reductant from its current input-side and output-side prices, subsidies included. A new site therefore picks with its own electricity and hydrogen prices. See [Annual Reductant Pick](../plant_agent_model/reductant_choice_and_opex.md#annual-reductant-pick).

**Trace:** no stage of its own; the pick reads the prices `brownfield` and `greenfield_operating` check just before it.

### 10. A switch, renovation or expansion

`Plant.evaluate_furnace_group_strategy` prices every candidate, the incumbent's renovation and each switch, with the score series, plus the candidate's own energy subsidies. For a new site it first locks one stack for the current year s, the start of the new investment (`Environment.captive_energy_at`), and every candidate, the CO2 storage gate and the capacity-policy pick are priced from it as in step 3. For an existing plant, the series follows the country prices and no stack is built.

- **Renovation:** it runs at once in year s. The `RenovateFurnaceGroup` command carries the stack, and `Plant.renovate_furnace_group` installs it, so from the next year-end refresh the site pays the LCOE, electrolyser and contract of s.
- **Switch:** decided in s, it executes in s + `construction_time`. The `ChangeFurnaceGroupTechnology` command carries the stack of s; the old technology keeps its old stack while the new one is built, and `execute_scheduled_technology_switch` installs the new stack with the new technology.
- **Expansion:** `PlantGroup.evaluate_expansion_options` prices each new site's expansion candidates, the CO2 storage gate included, from the stack the plant would lock in the current year. `evaluate_expansion` puts that stack on the `AddFurnaceGroup` command, and `add_furnace_group_to_plant` gives the new furnace group the stack, its electricity and hydrogen without subsidy from the stack and the year's grid price, and every other carrier at its sibling's price without subsidy. No subsidy enters the new group's base prices, so the year-start loop applies each subsidy once. An existing plant's new group still starts from the plant's prices, which the year-end refresh re-bases on the input costs.

**Trace:** `greenfield_relock` records the old and new stacks at a renovation, a switch decision, a switch execution and an expansion of a new site; an expansion's new group has no old stack. It checks the new stack's lock year against the investment's start year, and its LCOE and electrolyser use and cost against the LCOE grid and the electrolyser inputs of that year. At a renovation, a switch's execution and an expansion, the new stack becomes the furnace group's reference `greenfield_operating` checks against.

## Related Documentation

- [Priority Location Selection](../geospatial_model/priority_location_selection.md): the site rasters and the ranking
- [New Plant Opening Logic](../geospatial_model/new_plant_opening.md): business opportunities from selection to construction
- [Reductant Choice and Investment OPEX](../plant_agent_model/reductant_choice_and_opex.md): the score series and the yearly reductant pick
- [Sub-national Geography](sub_national_geography.md): province rows in the input costs
- [Logging Guide](../../user_guide/LOGGING_GUIDE.md#energy-price-trace): switching the trace on and reading its output
