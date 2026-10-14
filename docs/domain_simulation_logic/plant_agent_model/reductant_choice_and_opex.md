# Reductant Choice and Investment OPEX

## Overview

Many technologies can run on more than one reductant: a DRI furnace can use natural gas, hydrogen or coal. The reductant drives energy cost, direct emissions and by-product value, so the model chooses it on cost and prices every investment with the reductant the asset would actually run on.

This page covers two linked mechanisms:

- **Operating choice**: the annual reductant pick that each operating furnace group makes.
- **Investment pricing**: the year-wise reductant score series that supplies the energy, carbon and by-product terms of every investment NPV (net present value).

Both use the same scoring rule, so an investment NPV forecasts what the committed asset will do once it operates.

## The Reductant Score

`score_reductants_for_business_cases()` in `calculate_costs.py` scores every (metallic charge, reductant) combination of a technology's business cases. The score is a cost per tonne of product:

```
score = energy VOPEX + carbon price × direct emission factor + secondary-output adjustment
```

- **Energy VOPEX**: energy requirements (including energy carriers listed as secondary feedstocks) times input-side energy prices.
- **Carbon term**: the carbon price times the direct GHG emission factor for (technology, reductant, metallic charge), taken from the emissions boundary set in `chosen_emissions_boundary_for_carbon_costs`. The lookup is strict: a missing emission factor row raises an error rather than scoring carbon as zero, which would bias the choice towards high-emission reductants.
- **Secondary-output adjustment**: by-product revenue, disposal costs and carbon storage costs per tonne, priced with output-side energy prices.

The score carries no material term. The current master data authors the same materials for every reductant of a charge, so materials cannot change the choice. If a metallic charge's material requirements do differ across reductants, the model logs a `[REDUCTANT SCORE]` data-drift warning and ignores the difference.

**Carbon price by year.** Both the annual pick and the score series read the carbon price with `carbon_price_from_series()`: zero for a country with no carbon price series, the last value of the series for years beyond its end, and an error for a year missing inside it.

## Annual Reductant Pick

### When it runs

`Environment.set_primary_feedstocks_in_furnace_groups()` runs once at bootstrap and then at the start of each year's trade model setup, before the LP (linear programme) is built. Every furnace group that is not closed or discarded re-picks its reductant. Closed and discarded furnace groups keep their last choice.

### How a furnace group picks

`FurnaceGroup.generate_energy_vopex_by_reductant()` scores every (metallic charge, reductant) combination of the furnace group's technology. It uses the furnace group's current input-side and output-side energy prices, after the energy subsidies for its own technology, and the country carbon price for the current year. It then:

1. takes the reductant with the lowest total score for each metallic charge
2. sets `chosen_reductant` to the most common of those per-charge winners

Because the pick uses the total score, a higher carbon price can move a furnace group to a cleaner reductant even when that reductant's energy cost is higher. The reporting fields (`energy_vopex_by_input`, `energy_vopex_breakdown_by_input`, `energy_vopex_by_carrier`) stay pure energy VOPEX for the chosen reductant.

At DEBUG level each furnace group logs a `[REDUCTANT PICK]` line with the new choice, the previous choice and every score.

### Fleet-wide most common reductant

After all furnace groups re-pick, the model counts `chosen_reductant` per technology and stores the most common one in `Environment.most_common_reductant_by_tech`. `PlantGroup.most_common_reductant()` does the same within one plant group. Only furnace groups with a status in `active_statuses` vote. By default these are `operating`, `operating pre-retirement` and `operating switching technology`, so closed, discarded and announced capacity does not sway the result. A `[REDUCTANT FLEET]` debug line logs the result each year.

The fleet and plant-group results are only a starting hint. Switch, expansion and new-plant evaluations use them to build a first candidate BOM, then commit the reductant from the score series (see below) and rebuild the BOM if the two differ.

### Candidate pricing for switches and expansions

A candidate technology is priced from unsubsidised energy prices plus the energy subsidies scoped to that candidate and active in its operating start year. Technology switches, expansions and new plants all follow this rule. The incumbent furnace group's own energy subsidies do not leak into a candidate's BOM or by-product pricing: a hydrogen subsidy scoped to the incumbent's technology does not cheapen hydrogen for a candidate technology the subsidy does not cover. The price refresh at the start of each year also refreshes the unsubsidised snapshot (`energy_costs_no_subsidy`), so a lapsed subsidy cannot leave a stale price behind.

### Commit-time behaviour

When a switch or a new furnace group is committed, the handler rebuilds the reporting fields and then sets `chosen_reductant` to the reductant the NPV evaluated. The committed reductant holds until the next annual pick, which can change it if prices have moved.

## Year-wise Score Series for Investment NPVs

### What the series is

`Environment.reductant_score_series()` returns a `ReductantScoreSeries` for one candidate technology at one location. It holds one score and one reductant pick per operating year, from the operating start year (inclusive) to the end of the plant lifetime (exclusive).

For each operating year the series:

1. **Prices energy for that year.** `Environment.candidate_energy_costs_for_year()` reads the exogenous input-cost trajectory for that year, holds it at the last data year for later years, and raises an error on a year missing inside the trajectory. Hydrogen comes from the precomputed capped LCOH (levelised cost of hydrogen) series. Only energy subsidies scoped to the candidate technology and active in that year apply.
2. **Prices carbon for that year** with `Environment.carbon_price_for_year()`, following the carbon price rule above.
3. **Scores every (charge, reductant) combination** with the reductant score above.
4. **Picks the reductant** with the same rule as the annual pick: the cheapest reductant for each metallic charge, then the most common of those across charges.
5. **Records the year's value**: the score of the picked reductant, weighted by the candidate's output shares per metallic charge.

The series lets an NPV follow changing prices over the asset life. A DRI plant can score on natural gas in its early years and on hydrogen once carbon prices rise or hydrogen costs fall, and the NPV reflects both phases.

### Site prices for geospatial sites

Sites from the geospatial model carry their own power and hydrogen prices, which differ from the country average. For these sites the series keeps the site price level but follows the country trajectory:

```
site price in year t = site price today × country price in year t / country price today
```

Both country prices on the right are unsubsidised, so a candidate subsidy active today does not distort the ratio. Candidate subsidies for year t apply after scaling. This scaling is temporary: a later version will read each site's own price series from the rasters.

### How the NPVs use the series

Each candidate's yearly unit OPEX is:

```
OPEX in year t = materials-only VOPEX + fixed OPEX per tonne of production + score in year t
```

OPEX subsidies then apply year by year. Energy, carbon and by-products enter the NPV only through the score, so the NPV no longer adds a separate carbon cost list or a single secondary-output adjustment.

Four investment decisions use the series:

| Decision | Where | Output shares used to weight the score |
|----------|-------|----------------------------------------|
| Technology switch | `FurnaceGroup.optimal_technology_name()`, Branch B | Average bill of materials (BOM) for the candidate |
| Renovation of the current technology | `FurnaceGroup.optimal_technology_name()`, Branch A | The furnace group's own BOM material demands |
| Expansion at an existing plant | `PlantGroup.evaluate_expansion_options()` | Average BOM for the candidate |
| New plant at a geospatial site | `prepare_cost_data_for_business_opportunity()` and `FurnaceGroup.track_business_opportunities()` | Average BOM for the candidate |

Renovation has no construction lag: it happens in place, so its operating window, and with it the score series and carbon horizon, starts in the decision year. Switch, expansion and new-plant candidates start operating after construction.

### Fixed OPEX

Fixed OPEX inputs (`technology_unit_fopex`) are quoted per tonne of **capacity** per year. An NPV multiplies unit costs by expected **production** (expected utilisation × capacity). `scale_fopex_to_production()` therefore converts the input to a cost per tonne of production:

```
fixed OPEX per tonne of production = fixed OPEX per tonne of capacity / expected utilisation
```

Multiplied back by production, this counts the full annual fixed cost at any utilisation:

```
fixed OPEX / utilisation × utilisation × capacity = fixed OPEX × capacity
```

Unlike the operating unit cost (`FurnaceGroup.unit_fopex`), the investment scaling applies no utilisation floor. The operating floor (`production_threshold.low`, default 0.1) keeps the unit cost of a barely used furnace group from inflating the cost curve. The NPV must not use it: for a furnace group at 2% utilisation, a 0.1 floor would count `fixed OPEX / 0.1 × 0.02 × capacity`, only 20% of the true fixed cost. The NPV would understate fixed costs most for exactly the furnace groups closest to closure.

**Which utilisation each decision uses**

| Decision | Expected utilisation |
|----------|----------------------|
| Technology switch | Fleet-average utilisation of the candidate technology (`Environment.avg_utilization`: fleet production divided by fleet capacity; 0.6 when the technology has no fleet) |
| Renovation | The furnace group's own realised utilisation from the trade LP |
| Expansion | The higher of the technology's fleet average and the plant's own historical average utilisation for that product; the same figure sets the production in the NPV |
| New plant | The utilisation stored in the opportunity's cost data (fleet average) |

Renovation is the only decision priced on a furnace group's own utilisation. A furnace group that the trade LP gave little production this year is valued as if it will run at that rate for its whole next lifetime, while switch candidates use the fleet average.

**Zero utilisation**

A candidate with no expected production cannot be priced. `scale_fopex_to_production()` raises `ZeroUtilisationError` for a utilisation of zero or below, and each decision drops only that candidate:

- **Switch and renovation**: the candidate is skipped with an `[OPTIMAL TECH] SKIPPING` warning. In practice this hits the incumbent when the trade LP allocated the furnace group no production this year. The incumbent then has no renovation NPV. At an expired lifetime boundary with no profitable alternative, the furnace group closes.
- **Expansion**: the candidate is skipped with a `[PG EXPANSION] SKIPPING` warning.
- **New plant**: the site and technology pair receives an NPV of minus infinity, so it is never chosen.

### The committed reductant

The pick for the first operating year (`picks[0]`) is the reductant the investment commits to. When it differs from the reductant used to build the candidate BOM, the model rebuilds the BOM for the committed reductant. Material rows do not depend on the reductant; only the energy rows change. The committed reductant then flows to:

- **The commit commands**: `ChangeFurnaceGroupTechnology` and `AddFurnaceGroup` carry it, and new plants store it in their cost data, so the built asset starts on the reductant the NPV evaluated.
- **The CO2 storage gates**: the switch and expansion gates size a CCS (carbon capture and storage) candidate's storage need with this pick. See [CO2 Storage Gate](../co2_storage_gate.md).

### Debugging

At DEBUG level each candidate logs a `[REDUCTANT NPV]` line with the committed reductant and the picks compressed into year ranges, for example `natural_gas:2028-2033, hydrogen:2034-2047`.

## Data Checks at Load Time

The reductant score looks up emission factors strictly, and the score series needs hydrogen prices for every future operating year. Without checks at load time, a data gap would stop the run in whichever simulation year first reached it. Bootstrap therefore checks the inputs once, before the first year.

### Checks that stop the run

**Emission factor coverage.** `Environment.initiate_technology_emission_factors()` raises an error when:

- business cases exist but the technology emission factor table is empty
- the boundary set in `chosen_emissions_boundary_for_carbon_costs` has no rows in the table
- any (technology, reductant, metallic charge) combination in the Bill of Materials lacks an emission factor row in any one boundary present in the table, not only the configured one

The error message gives the number of missing combinations and up to five examples. To fix it, add the missing rows to the "Technology emission factors" sheet.

**Capped hydrogen price series.** `Environment.initiate_capped_hydrogen_costs_by_year()` computes the capped LCOH for every year from the simulation start year to the last year of the input-cost data. Every input to it is exogenous, so the full series is known at bootstrap. It raises an error when the input costs are empty, or when a region has input costs for a year but no electricity price in that year. During the run, each year's price refresh and the score series read from this stored series. Years beyond the end of the data reuse the last year, and a year missing inside the covered range raises an error.

### Checks that warn only

**Reductant coverage across charges.** `Environment.validate_reductant_material_invariance()` logs a `[REDUCTANT DATA]` warning for each affected (technology, metallic charge) when:

- a metallic charge is authored for only some of a technology's reductants
- a metallic charge's material requirements (required quantity per tonne or non-energy secondary feedstocks) differ across reductants

The run continues, but the reductant score assumes every reductant shares the same charges and materials. The pick can then favour a reductant whose material side differs from what the score assumed. The check covers every technology in the data, including candidate technologies that no existing furnace group uses yet. Fix the warning in the master's Bill of Materials sheet.

## Related Documentation

- [Furnace Group Strategy](furnace_group_strategy.md): switch and renovation NPVs
- [Plant Expansions](plant_expansions.md): expansion NPVs
- [New Plant Opening](../geospatial_model/new_plant_opening.md): new-plant NPVs
- [Cost Calculation Functions](calculate_costs.md): OPEX, subsidy and NPV functions
- [Economic Considerations](economic_considerations.md): carbon pricing and subsidies
