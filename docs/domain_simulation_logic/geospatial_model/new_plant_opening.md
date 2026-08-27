# New Plant Opening Logic

## Overview

The new plant opening system transforms candidate locations into actual steel and iron plants through a multi-year business opportunity lifecycle. Companies identify promising locations, track their economic viability over time, announce viable projects, and eventually construct new facilities -all while accounting for uncertainty, capacity constraints, and changing market conditions.

Business opportunities progress through the following stages:
- **CONSIDERED**: Top location-technology pairs are identified based on NPV calculations and selected using weighted random sampling (or, when `probabilistic_agents` is disabled, deterministically by highest NPV, evaluated over every candidate location instead of a random sample — see [Business Opportunity Identification](#business-opportunity-identification)). Its NPV is recalculated annually with updated costs for several years; opportunities with consistently positive NPV advance to announcement (subject to probability filter), while consistently negative NPV leads to discard.
- **ANNOUNCED**: Project waits for construction start; dynamic costs continue to be updated annually; advancement to construction depends on technology remaining allowed, capacity limits, and probability filter.
- **CONSTRUCTION**: Plant is being built over several years (handled by the plant agent model).
- **OPERATING**: Plant is operational and removed from business opportunity tracking (fully handled by the plant agent model).
- **DISCARDED**: Opportunity is abandoned due to negative economics or banned technology.

The system updates the costs and status of business opportunities each simulation year:
1. **Update Dynamic Costs**
   - Refresh CAPEX, cost of debt, and energy prices for all carriers
   - Apply subsidies (CAPEX and debt at the earliest construction start year; energy carriers at the operating start year that follows it)
   - Update bill of materials with new energy prices
   - Refresh the expected utilisation of CONSIDERED opportunities from the current fleet average for the technology (ANNOUNCED ones keep their value)

2. **Update Status**
   - For CONSIDERED: Recalculate NPV and check for status change
   - For ANNOUNCED: Try to convert to CONSTRUCTION

**Key Timing Parameters:**
- `consideration_time`: Minimum years to track NPV before decision (default: 3 years)
- `announcement_time`: Minimum 1 year (fixed)
- `construction_time`: Years to build plant after announcement (default: 4 years)
Both the consideration and announcement times can be considerably longer than their set minimums depending on the capacity and probability filters.

## Process Diagram
The flow from business opportunity to new plant is as follows: 

```text
    NEW (year t)
        |
        | identify_new_business_opportunities_4indi()
        | - Calculate NPVs for good location-technology pairs (business opportunities)
        | - Select top N business opportunities (weighted by NPV)
        | - Create new Plant + FurnaceGroup
        |
        v
    CONSIDERED (years t to t+T for T the consideration time, default: 3 years)
        |
        | [Each year]:
        | update_dynamic_costs_for_business_opportunities()
        | - Update CAPEX, cost of debt, and energy prices for all carriers (with subsidies)
        |
        | track_business_opportunities()
        | - Recalculate NPV with updated costs/prices
        | - Track NPV history over T years
        |
        +------------------------+----------------------------------------------+
        |                        |                                              |
        | NPV > 0                | No clear trend                               | NPV < 0
        | for T consecutive      | (mixed or too few                            | for T consecutive
        | years                  | NPVs recorded)                               | years
        |                        |                                              |
        | Apply probability      |                                              |
        | filter (default: 70%)  |                                              |
        |                        |                                              |
        +-----+---+              |                                              |
        |         |              |                                              |
     passed  not passed          |                                              |
        |         |              v                                              |
        v         +----> (stay CONSIDERED)                                      |
        |                                                                       |
    ANNOUNCED                                                                   |
        |                                                                       |
        | [Each year]:                                                          |
        | update_dynamic_costs_for_business_opportunities()                     |
        | - Update CAPEX, cost of debt, and energy prices for all carriers (with subsidies) |
        |                                                                       |
        | convert_business_opportunity_                                         |
        | into_actual_project()                                                 |
        | - Check technology still allowed                                      |
        | - Check new capacity limit                                            |
        |                                                                       |
        +--------------+------------------+                                     |
        |              |                  |                                     |
    tech banned   new capacity       tech allowed                               |
        |          limit full             |                                     |
        |            |                    | Apply probability                   |
        |            |                    | filter (default: 90%)               |
        |            |                    |                                     |
        |            |                    +-----+---+                           |
        |            |                    |         |                           |
        |            |              not passed    passed                        |
        |            |                    |         |                           |
        |            +--------------------+         |                           |
        |                                 |         |                           |
        |                                 v         |                           |
        |                          (stay ANNOUNCED) |                           |
        |                                           |                           |
        |                                           v                           |
        |                                      CONSTRUCTION                     |
        |                                           |                           |
        |                                           | After construction_time   |
        |                                           |   (default: 4 years)      | 
        |                                           |                           |
        |                                           v                           |
        |                                       OPERATING                       |
        |                                (removed from tracking                 |
        |                                 and handled by PAM)                   |
        |                                                                       |
        v                                                                       |
    DISCARDED <-----------------------------------------------------------------+

```

## Per-ISO3 Plant Group Routing

Every new plant is routed into its per-country group (`indi_{iso3}`, e.g. `indi_CHN`, `indi_AUS`) **at birth** — the moment `add_new_business_opportunities_to_repository` handles the plant. Per-country groups are created lazily on first use. The master `indi` group remains as the dispatch point for candidate generation (`identify_new_business_opportunities_4indi`) but is structurally empty: it never holds plants.

**Why this matters:** The plant agent model evaluates expansion (adding a new furnace group to an existing plant) once per plant group per year. With a single "indi" group, only one expansion could occur per year across all new plants globally. Per-ISO3 groups allow one expansion per country per year. Routing at birth also ensures the plant-group reverse-lookup map (`plant_id_to_plantgroup_id`) is populated for runtime-born plants.

**Lifecycle summary:**

| Status | Plant group | Processed by |
|--------|------------|--------------|
| Considered | `indi_{iso3}` | GEO: dynamic cost updates, status tracking |
| Announced | `indi_{iso3}` | GEO: dynamic cost updates, conversion to construction |
| Construction | `indi_{iso3}` | Simulation loop: time-based transition to operating |
| Operating | `indi_{iso3}` | PAM: balance updates, furnace group strategy, expansion |

**Implementation:** Routing runs inside the `add_new_business_opportunities_to_repository` handler via `PlantGroupRepository.register_plant_in_group`, which creates the per-country group on demand. The handler also overwrites `plant.parent_gem_id` from the `"indi"` placeholder set by `PlantGroup.generate_new_plant` to `indi_{iso3}`. Plants are identified as GEO-originated by `parent_gem_id.startswith("indi")`.

## Business Opportunity Identification

The identification process evaluates potential new plant locations and technologies through five sequential steps, selecting the most promising opportunities for detailed multi-year tracking.

### Step 1: Technology Filtering

**Function:** `get_list_of_allowed_techs_for_target_year()`

Filters technologies based on what will be allowed at the earliest possible construction start year (target year = current year + consideration time + 1), not what's currently allowed.

**Purpose:** Prevents companies from considering plants using technologies that would be illegal to build by the time construction begins. For example, if BF-BOF will be banned in 2034, it won't be considered as an opportunity in 2030 even though it's currently legal.

**Process:**
- Drop technologies listed in `geo_config.excluded_greenfield_technologies` (default `["BOF"]`) before anything else; these are never built greenfield regardless of `technology_settings`
- Calculate target year when the earliest possible construction would start 
- Check which technologies are allowed in that future year
- Filter opportunities to only include permitted technologies

### Step 2: Location Sampling

**Function:** `select_location_subset()`

Randomly samples a subset of top priority locations to reduce computational burden, since NPV calculations are resource-intensive.

**Configuration:**
- `calculate_npv_sites_share`: Fraction of locations to evaluate (default: 0.1, i.e. 10%) out of the top `geo_config.pick_priority_sites_share` fraction extracted by the location priority selection (default: 0.05, i.e. 5% of the world; see related documentation in [Priority Location Selection](priority_location_selection.md)).

**Purpose:** Balance computational efficiency with coverage of good opportunities. Sampling 10% of 1000 candidate locations means evaluating 100 instead of all 1000. When `probabilistic_agents` is False, `calculate_npv_sites_share` is forced to 1.0 (evaluate every candidate) and `geo_config.pick_priority_sites_share` is forced to 0.005 (narrowing the candidate pool the 100% sampling runs over) — full sampling alone measured ~7x slower, but combined with the narrower pool the runtime cost is negligible.

### Step 3: Cost Data Preparation

**Function:** `prepare_cost_data_for_business_opportunity()`

Gathers all cost inputs needed for NPV calculation for each location-technology pair. Sites missing critical data (energy costs, cost of equity or debt) raise a hard error, as do invalid data types; technologies with an incomplete field set are dropped in validation, so only complete entries reach the NPV.

**Required Inputs:**

| Input Category | Components | Source |
|----------------|-----------|---------|
| Energy costs | Electricity, hydrogen, gas, coal prices | Geography-resolved data (province row when the site falls in one, else country) + site-specific renewable calculations for electricity and hydrogen |
| Financial parameters | Cost of debt, cost of equity | Country-level financial data |
| CAPEX | Capital expenditure per tonne capacity | Regional technology-specific estimates |
| OPEX | Fixed operating costs per tonne | Country and technology-specific |
| Infrastructure | Railway buildout cost | From priority location selection |
| Production | Bill of materials, utilization rate, reductant type | Technology-specific averages |
| Subsidies | CAPEX, debt, and OPEX subsidies | Geography- and technology-specific policies (country and province rows apply additively) |
| Carbon pricing | Enters via the per-year reductant score series, not a separate cost field | Country-level projections |

### Step 4: NPV Calculation

**Function:** `calculate_business_opportunity_npvs()`

Calculates Net Present Value for each business opportunity using an **adjusted NPV metric** that accounts for future subsidies. This metric uses subsidies from the target construction year rather than current year subsidies.

**Why Adjusted NPV?**

Subsidies are often announced years before plants are built. Standard NPV using current-year subsidies would make subsidized technologies appear less attractive until subsidies activate. The adjusted NPV assumes subsidies announced for the target year will be available, preventing artificial delays in subsidized technology adoption. This adjusted NPV is only used for the decision to create a business opportunity. Once a plant is constructed, it uses actual year-by-year costs, not the adjusted values.

**Price/cost alignment:** the market-price series handed to the NPV is anchored at the target (construction-start) year, so after the construction-time lag each revenue year prices the same calendar year as its costs — the NPV values the plant's actual operating window. The yearly re-valuation of existing opportunities re-anchors the same way, floored at the soonest path still open (announce now, construct from next year).

**NPV Components:**

| Component | Composition | Subsidy Timing | Period |
|-----------|-------------|----------------|---------|
| **CAPEX** | Capital expenditure per tonne × capacity + infrastructure (railway buildout) | CAPEX subsidies: Target year | One-time (construction) |
| **Cost of Debt** | Interest rate on borrowed capital | Debt subsidies: Target year | Financing period |
| **Cost of Equity** | Return required by investors | No subsidies | Financing period |
| **OPEX - Variable** | Materials + energy from bill of materials × unit costs | OPEX subsidies: Operation years | Annual (plant lifetime) |
| **OPEX - Fixed** | Fixed operating costs per tonne | OPEX subsidies: Operation years | Annual (plant lifetime) |
| **Energy Costs** | Energy prices for all carriers (electricity, hydrogen, gas, etc.) | Energy subsidies: Operating start year | Annual (plant lifetime) |
| **Carbon Costs** | Emissions × carbon price trajectory (inside the reductant score series) | No subsidies | Annual (plant lifetime) |
| **Revenue** | Production capacity × utilization rate × market price projections | N/A | Annual (plant lifetime) |
| **Discount Rate** | Weighted average cost of capital (WACC = debt share × cost of debt + equity share × cost of equity) | Applied to debt portion only | NPV calculation |

**Notes:**
- If NPV calculation fails (returns NaN due to missing data or invalid inputs), it is set to negative infinity to exclude that location-technology pair from selection.
- For more information on the NPV calculation, see related documentation in [Calculate Cost](../plant_agent_model/calculate_costs.md).

### Step 5: Top Opportunity Selection

**Function:** `select_top_opportunities_by_npv()`

When `probabilistic_agents` is enabled (default), selects top N location-technology combinations using **rank-weighted random sampling** (instead of pure NPV ranking) to represent some randomness in human decision-making. Pure ranking would always select the absolute highest NPV locations. In reality, companies have geographic preferences, imperfect information, varying risk tolerance, and strategic considerations. Weighted random sampling ensures diversity while still strongly favoring high-NPV options. When `probabilistic_agents` is disabled, this step instead deterministically picks the top N by NPV — no random draw at all.

**Process (probabilistic_agents enabled):**
- Filter out invalid NPVs (NaN or negative infinity from calculation failures)
- Rank valid candidates by NPV and trim to the best 3N (implausible sites can never be selected, regardless of NPV scale)
- Assign linearly decreasing rank weights over the trimmed pool (best gets weight 3N, worst gets weight 1) and normalize to probabilities
- Randomly draw N opportunities from the trimmed pool without replacement, with probability proportional to rank weight
- If fewer valid pairs exist than requested, select all valid pairs

**Process (probabilistic_agents disabled):**
- Filter out invalid NPVs (NaN or negative infinity from calculation failures)
- Select the top N valid candidates by NPV directly

**Purpose:** Creates geographic diversity in opportunities while maintaining economic rationality. Higher NPV opportunities have much higher selection probability, but mid-tier opportunities can also be selected. Disabling `probabilistic_agents` trades this realism for full determinism and reproducibility across runs/seeds.

## Business Opportunity Tracking
**Function:** `update_status_of_business_opportunities()`

Once business opportunities are created, they are tracked annually through cost updates and status decisions until they either advance to construction through several stages or are discarded.

### Step 1: Annual Cost Updates

**Function:** `update_dynamic_costs_for_business_opportunities()`

Updates dynamic costs for all CONSIDERED and ANNOUNCED business opportunities each year to ensure NPV calculations reflect current market conditions.

**Updated Costs:**
- CAPEX (with subsidies for target construction year)
- Cost of debt (with subsidies for target construction year)
- Energy prices for all carriers, carried as three dicts:
  - `energy_costs`: subsidised input prices (reduced by subsidy) — used for BOM and VOPEX
  - `output_energy_costs`: subsidised output prices (increased by subsidy for physical carriers) — used for by-product revenue
  - `energy_costs_no_subsidy`: original unsubsidised prices — used as baseline for yearly refresh
- Electricity and hydrogen prices sourced from the geospatial layer (custom power mix / capped LCOH); other carriers from the furnace group's existing cost base
- Energy subsidies are collected for the plant's geography (country and province rows apply additively) and filtered at the operating start year (construction start + construction time), matching opportunity creation and the plant agent model
- Bill of materials (updated with new subsidised input energy prices)
- Expected utilisation, refreshed from `Environment.avg_utilization` (the operating fleet's average for the technology, with the same 0.6 fallback as opportunity creation) — CONSIDERED opportunities only. ANNOUNCED ones keep their value: the build decision is already taken, and utilisation is reset to 0 at construction start. A fleet average of zero flows into the zero-utilisation guard in the NPV re-valuation (−inf → discard).

**Note:** For more information on the electricity and hydrogen prices see related documentation [Priority Location Selection](priority_location_selection.md).

**Target Year Calculation:**

The system uses CAPEX and debt subsidies from the earliest construction start year, reflecting that subsidies are often announced in advance. Energy subsidies use the operating start year (construction start + construction time), because subsidised carrier prices are what the plant pays once it is running.

| Status | Target Year (construction start) | Reasoning |
|--------|---------------------------------|-----------|
| CONSIDERED | `max(current + consideration_time + 1 - years_considered, current + 1)` | Earliest construction start based on consideration progress, floored at the soonest path still open (announce now, construct from next year) — the same floor the NPV re-valuation applies |
| ANNOUNCED | `current + 1` | Next year (announcement time = 1) |

**Process:**
For each business opportunity:
1. Calculate the construction start year based on opportunity status
2. Filter CAPEX and debt subsidies at that year, energy subsidies at construction start + construction time, and calculate new costs
3. Update bill of materials with new energy prices
4. Skip updates if costs haven't changed and update modified costs

### Step 2: NPV Tracking and Announcement

**Function:** `track_business_opportunities()`

Tracks CONSIDERED business opportunities by recalculating NPV each year and deciding whether to announce or discard based on sustained NPV trends.

**Decision Rules:**

| Condition | Action | Probability Applied |
|-----------|--------|---------------------|
| NPV > 0 for all `consideration_time` years | Announce | `probability_of_announcement` |
| NPV < 0 for all `consideration_time` years | Discard | 100% (deterministic) |
| Mixed positive/negative NPVs | Keep considering | N/A |

**Why Multi-Year Tracking?**

Single-year NPV could be an outlier from temporary price spikes, one-time events, or data anomalies. Multi-year tracking ensures decisions are based on sustained economic viability.

### Step 3: Converting into Actual Plants under Construction

**Function:** `convert_business_opportunity_into_actual_project()`

Converts ANNOUNCED business opportunities into CONSTRUCTION status, checking technology allowance, new capacity limits, and applying construction probability.

**Decision Sequence:**
1. **Technology Check:** If technology is now banned → Discard immediately
2. **Capacity Check:** If adding this plant would exceed the new annual capacity limit assigned to new plants → Stay announced, retry next year
3. **Probability Filter:** Apply `probability_of_construction` → If fails, stay announced, retry next year
4. **Success:** Begin construction

**Capacity Limit Logic:**

Total new capacity (new plants + expansions) is limited annually. Annual capacity limits default to 100 Mt/year for both iron (`capacity_limit_iron`) and steel (`capacity_limit_steel`). The `new_capacity_share_from_new_plants` parameter determines how much of this limit is reserved for new plants versus expansions of existing facilities (default: 40%).

**Purpose of Probability Filter:**

Models real-world risk factors: financing may fall through, permits may be denied, market conditions may shift, or political/regulatory environments may change. Not all announced projects actually get built.

## Configuration Parameters

### Simulation Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `consideration_time` | int | 3 years | Min years to track NPV before announcement decision |
| `construction_time` | int | 4 years | Years to build plant after starting construction |
| `plant_lifetime` | int | 20 years | Expected operational lifetime of plant |
| `expanded_capacity` | float | 2.5 Mt/year | Standard capacity for new plants (same than for plant expansion) |
| `top_n_loctechs_as_business_op` | int | 5 | Number of opportunities to track per product per year |
| `geo_config.excluded_greenfield_technologies` | list[str] | `["BOF"]` | Technologies removed from the greenfield candidate set before the allowed-technology filter of Step 1. A greenfield plant has a single furnace group, so a BOF built this way has no hot-metal supply of its own. Brownfield switching and renovation are unaffected (they use `technology_settings`) |

### Probability Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `probabilistic_agents` | bool | True | When True, Step 5 draws a rank-weighted mix of top opportunities; when False, Step 5 picks the top N by NPV deterministically. Also gates Step 2's `calculate_npv_sites_share` sampling and `geo_config.pick_priority_sites_share` — see below |
| `probability_of_announcement` | float | 0.7 | Chance viable opportunity is announced. Forced to 1.0 when `probabilistic_agents` is False |
| `probability_of_construction` | float | 0.9 | Chance announced project starts construction. Forced to 1.0 when `probabilistic_agents` is False |
| `calculate_npv_sites_share` | float | 0.1 | Fraction of priority locations sampled for NPV calculation. Forced to 1.0 when `probabilistic_agents` is False |
| `geo_config.pick_priority_sites_share` | float | 0.05 | Fraction of global grid points selected as priority locations (see [Priority Location Selection](priority_location_selection.md)). Forced to 0.005 when `probabilistic_agents` is False, to keep the cost of full `calculate_npv_sites_share` sampling low |

### Capacity Limits

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `capacity_limit_iron` | float | 100 Mt/year | Total new iron capacity allowed per year |
| `capacity_limit_steel` | float | 100 Mt/year | Total new steel capacity allowed per year |
| `new_capacity_share_from_new_plants` | float | 0.4 | Share of capacity limit for new plants vs. expansions |

## Related Documentation

- [Priority Location Selection](priority_location_selection.md) - How candidate locations are identified
- [Calculate Cost](../plant_agent_model/calculate_costs.md) - NPV, subsidy, and carbon costs calculation details
- [Baseload Power Optimization](baseload_optimization_atlas.md) - Renewable energy cost calculations
- [Plant Agent Model](../plant_agent_model/overview_plant_agent_model.md) - Overall plant lifecycle management
