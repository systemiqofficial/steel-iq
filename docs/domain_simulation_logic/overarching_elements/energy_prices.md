# Energy Prices of Existing and New Plants

Electricity and hydrogen are the two energy carriers whose prices differ between existing plants and the new plants the model opens at geospatial sites. Every other carrier comes from the master's input costs for both. This page follows the two prices step by step through the code as it stands, and names the stage of the energy-price trace that watches each step. The [Logging Guide](../../user_guide/LOGGING_GUIDE.md#energy-price-trace) explains how to switch the trace on and read its output.

LCOE below is the levelised cost of electricity of a site's own baseload supply, and LCOH the levelised cost of hydrogen.

## Units

- **Electricity**: USD/kWh in the site rasters and in furnace groups. The baseload LCOE files hold USD/MWh and are converted on reading.
- **Hydrogen**: USD/kg in the site raster and in the country series; USD/t in furnace groups and in the cost data of business opportunities, which multiply the USD/kg price by 1,000.

## Existing Plants

- **Electricity**: the master's input cost for the plant's geo key, the province row when the master has one, else the country row. Bootstrap writes it into every furnace group, and the end of each year writes the next year's price (`Environment.set_input_cost_in_furnace_groups`).
- **Hydrogen**: bootstrap precomputes a capped price per geo key for every year from the start year to the last input-cost year (`Environment.initiate_capped_hydrogen_costs_by_year`). A geo key's LCOH is the electrolyser's electricity use times the geo key's input-cost electricity price, plus the country's electrolyser capital and operating cost per kg. A region's ceiling is a percentile of its countries' LCOH, and a province takes its country's region. The capped price is the lower of the LCOH and the ceiling; with intraregional trade on, also of the cheapest partner region's ceiling plus pipeline transport. At the start of each year, the geo key's capped price × 1,000 replaces the hydrogen price of every furnace group (`Plant.update_furnace_hydrogen_costs`).
- **Subsidies**: energy subsidies for the furnace group's technology then apply on top. The prices without subsidy stay in `energy_costs_no_subsidy`.

**Trace:** `country_hydrogen` checks the precomputed series at bootstrap. `brownfield` checks every active furnace group's prices without subsidy each year, after the subsidies and before the trade model.

## New Plants, Step by Step

A new plant starts as a candidate site, becomes a considered business opportunity, is announced, enters construction and then operates. Its `parent_gem_id` starts with `indi`.

### 1. The site rasters

Each year the geospatial model prices every cell of a global 0.25° grid (`get_candidate_locations_for_opening_new_plants`):

- **Grid price**: the country's input-cost electricity price. Province rows do not reach the grid cells. A feasible cell whose country has no price takes the highest grid price.
- **Baseload LCOE**: read from the prepared data's per-year files under `outputs/GEO/baseload_power_simulation/`, one folder per coverage. A year between two files is interpolated linearly; a year outside their span reuses the nearest file.
- **Power price**: `power_price = (1 - c) × grid + LCOE`, where c is the baseload coverage of the power mix (0.85 or 0.95). The per-year files hold the baseload system's cost per MWh of demand, so the LCOE enters in full. With the "Grid only" or "Not included" mix, the power price is the grid price.
- **Hydrogen**: a cell's LCOH is the electrolyser's electricity use times the cell's power price, plus the country's electrolyser capital and operating cost per kg. A region's ceiling is a percentile of its cells' LCOH (`hydrogen_ceiling_percentile`, 100 by default, which leaves prices uncapped). `capped_lcoh` is the lower of the LCOH and the ceiling, and with intraregional trade on, also of the cheapest partner region's ceiling plus pipeline transport. A cell inside a region but without an LCOH takes the region's ceiling.

**Trace:** `site_power` checks every priced cell against the blend its LCOE file's basis implies. `site_hydrogen` checks every priced cell against its region's ceiling, and counts the cells priced without an LCOH.

### 2. The ranking

The priority location key performance indicator (KPI) ranks cells by an outgoing cash flow proxy whose power term is the power price times the technology's electricity use, capacity and lifetime. The hydrogen price does not enter the ranking. See [Priority Location Selection](../geospatial_model/priority_location_selection.md).

**Trace:** no stage; the ranking reads the raster `site_power` checks.

### 3. The NPV at selection

`prepare_cost_data_for_business_opportunity` prices each sampled site for its net present value (NPV). It takes the input costs of the site's geo key for the current year, then replaces electricity with the site's `power_price` and hydrogen with its `capped_lcoh` × 1,000. Each candidate technology's energy subsidies active in the operating start year apply on top. The year-wise reductant score series receives the same two site prices as overrides, with the current year as reference, and scales them along the country trajectory:

```
site price in year t = site price now × country price in year t / country price now
```

See [Site prices for geospatial sites](../plant_agent_model/reductant_choice_and_opex.md#site-prices-for-geospatial-sites).

**Trace:** `greenfield_select` writes each site's prices, its geo key's input-cost prices, the override reference year and the operating start year. It checks nothing.

### 4. Each year while considered

The year-start hydrogen refresh writes the regional price into every furnace group, opportunities included. Later in the year, after the plant agent model, `PlantGroup.update_dynamic_costs_for_business_opportunities` re-reads the site's `power_price` and `capped_lcoh` from the year's rasters. It writes them as the opportunity's electricity and hydrogen without subsidy, with the energy subsidies of the operating start year on top. It writes `capped_lcoh` as read, in USD/kg, where the selection converted it to USD/t. Where a raster value is missing, it keeps the stored price. The yearly NPV re-valuation (`track_business_opportunities`) then passes the stored electricity and hydrogen without subsidy to the score series as site prices, with the current year as reference.

**Trace:** `greenfield_revalue` checks that the hydrogen written equals the site's `capped_lcoh` × 1,000, and keeps the site's raster prices of the year. On the current code the check fails for every opportunity, because of the unit above.

### 5. Announcement

An opportunity whose NPVs over the consideration window are all positive is announced with probability `probability_of_announcement`. Its prices do not change at announcement, and step 4 keeps re-valuing it while it is announced.

**Trace:** `greenfield_revalue`, as in step 4.

### 6. Construction start

`update_status_of_furnace_group` moves an announced opportunity into construction. It sets the operating start year, the construction start plus the construction time (4 years by default), and the utilisation to zero. The prices stay as the last re-valuation wrote them.

**Trace:** `greenfield_build` writes the stored prices and freezes the site's raster prices from the last re-valuation as the furnace group's reference. It also records the geo key's capped regional hydrogen price of that year, for reading only.

### 7. Construction

The furnace group is no longer re-valued. At the end of each year, `Environment.set_input_cost_in_furnace_groups` refreshes every carrier from the input costs, but keeps a new site's electricity and hydrogen without subsidy. At the start of each year, the hydrogen refresh replaces the hydrogen price of every furnace group, new sites included, with the geo key's capped regional price × 1,000.

**Trace:** no stage.

### 8. Operation

At the start of its operating start year, before the trade model, the furnace group turns operating. Its electricity is still the site's power price from the last re-valuation, in the construction-start year. Its hydrogen is the geo key's capped regional price of the year, which the year-start refresh writes every year.

**Trace:** `greenfield_operating` checks both prices without subsidy against the references frozen at construction start. The electricity check passes. On the current code the hydrogen check fails, since the regional price has replaced the site's own.

### 9. The yearly reductant pick

At bootstrap and at the start of each year's trade model setup, every furnace group re-picks its reductant from its current input-side and output-side prices, subsidies included. A new site therefore picks with its own electricity price and the regional hydrogen price. See [Annual Reductant Pick](../plant_agent_model/reductant_choice_and_opex.md#annual-reductant-pick).

**Trace:** no stage of its own; the pick reads the prices `brownfield` and `greenfield_operating` check just before it.

### 10. A switch or renovation

`FurnaceGroup.optimal_technology_name` prices a switch candidate from the furnace group's prices without subsidy, plus the candidate's own energy subsidies. For a new site, the score series takes the furnace group's electricity and hydrogen without subsidy as site prices, scaled along the country trajectory from the decision year. For an existing plant, the series follows the country prices. Expansion candidates at a new site's plant are priced the same way, from the plant's last furnace group.

**Trace:** no stage.

## Related Documentation

- [Priority Location Selection](../geospatial_model/priority_location_selection.md): the site rasters and the ranking
- [New Plant Opening Logic](../geospatial_model/new_plant_opening.md): business opportunities from selection to construction
- [Reductant Choice and Investment OPEX](../plant_agent_model/reductant_choice_and_opex.md): the score series and the yearly reductant pick
- [Sub-national Geography](sub_national_geography.md): province rows in the input costs
- [Logging Guide](../../user_guide/LOGGING_GUIDE.md#energy-price-trace): switching the trace on and reading its output
