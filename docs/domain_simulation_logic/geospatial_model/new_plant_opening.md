# New Plant Opening Logic

## Overview

The new plant opening system transforms candidate locations into actual steel and iron plants through a multi-year business opportunity lifecycle. Companies identify promising locations, track their economic viability over time, announce viable projects, and eventually construct new facilities -all while accounting for uncertainty, capacity constraints, and changing market conditions.

Business opportunities progress through the following stages:
- **CONSIDERED**: Top location-technology pairs are identified based on NPV calculations and selected using weighted random sampling. Its NPV is recalculated annually with updated costs for several years; opportunities with consistently positive NPV advance to announcement (subject to probability filter), while consistently negative NPV leads to discard.
- **ANNOUNCED**: Project waits for construction start; dynamic costs continue to be updated annually; advancement to construction depends on technology remaining allowed, capacity limits, and probability filter.
- **CONSTRUCTION**: Plant is being built over several years (handled by the plant agent model).
- **OPERATING**: Plant is operational and removed from business opportunity tracking (fully handled by the plant agent model).
- **DISCARDED**: Opportunity is abandoned due to negative economics or banned technology.

The system updates the costs and status of business opportunities each simulation year:
1. **Update Dynamic Costs**
   - Refresh CAPEX, cost of debt, electricity, and hydrogen prices
   - Apply subsidies based on earliest construction start year
   - Update bill of materials with new energy prices

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
        | - Update CAPEX, cost of debt, electricity & hydrogen prices (with subsidies)
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
        | - Update CAPEX, cost of debt, electricity & hydrogen prices (with subsidies) |
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

## Business Opportunity Identification

The identification process evaluates potential new plant locations and technologies through five sequential steps, selecting the most promising opportunities for detailed multi-year tracking.

### Step 1: Technology Filtering

**Function:** `get_list_of_allowed_techs_for_target_year()`

Filters technologies based on what will be allowed at the earliest possible construction start year (target year = current year + consideration time + 1), not what's currently allowed.

**Purpose:** Prevents companies from considering plants using technologies that would be illegal to build by the time construction begins. For example, if BF-BOF will be banned in 2034, it won't be considered as an opportunity in 2030 even though it's currently legal.

**Process:**
- Calculate target year when the earliest possible construction would start 
- Check which technologies are allowed in that future year
- Filter opportunities to only include permitted technologies

### Step 2: Location Sampling

**Function:** `select_location_subset()`

Randomly samples a subset of top priority locations to reduce computational burden, since NPV calculations are resource-intensive.

**Configuration:**
- `calculate_npv_pct`: Percentage of locations to evaluate (default: 10%) out of the top X% extracted by the location priority selection (default: 5% of the world; see related documentation in [Priority Location Selection](priority_location_selection.md)). 

**Purpose:** Balance computational efficiency with coverage of good opportunities. Sampling 10% of 1000 candidate locations means evaluating 100 instead of all 1000.

### Step 3: Cost Data Preparation

**Function:** `prepare_cost_data_for_business_opportunity()`

Gathers all cost inputs needed for NPV calculation for each location-technology pair. Location-technology combinations with missing or invalid critical data are skipped and a warning is logged.

**Required Inputs:**

| Input Category | Components | Source |
|----------------|-----------|---------|
| Energy costs | Electricity, hydrogen, gas, coal prices | Country-level data + site-specific renewable calculations for energy costs |
| Financial parameters | Cost of debt, cost of equity | Country-level financial data |
| CAPEX | Capital expenditure per tonne capacity | Regional technology-specific estimates |
| OPEX | Fixed operating costs per tonne | Country and technology-specific |
| Infrastructure | Railway buildout cost | From priority location selection |
| Production | Bill of materials, utilization rate, reductant type | Technology-specific averages |
| Subsidies | CAPEX, debt, and OPEX subsidies | Country and technology-specific policies |
| Carbon pricing | Carbon cost time series | Country-level projections |

### Step 4: NPV Calculation

**Function:** `calculate_business_opportunity_npvs()`

Calculates Net Present Value for each business opportunity using an **adjusted NPV metric** that accounts for future subsidies. This metric uses subsidies from the target construction year rather than current year subsidies.

**Why Adjusted NPV?**

Subsidies are often announced years before plants are built. Standard NPV using current-year subsidies would make subsidized technologies appear less attractive until subsidies activate. The adjusted NPV assumes subsidies announced for the target year will be available, preventing artificial delays in subsidized technology adoption. This adjusted NPV is only used for the decision to create a business opportunity. Once a plant is constructed, it uses actual year-by-year costs, not the adjusted values.

**NPV Components:**

| Component | Composition | Subsidy Timing | Period |
|-----------|-------------|----------------|---------|
| **CAPEX** | Capital expenditure per tonne × capacity + infrastructure (railway buildout) | CAPEX subsidies: Target year | One-time (construction) |
| **Cost of Debt** | Interest rate on borrowed capital | Debt subsidies: Target year | Financing period |
| **Cost of Equity** | Return required by investors | No subsidies | Financing period |
| **OPEX - Variable** | Materials + energy from bill of materials × unit costs | OPEX subsidies: Operation years | Annual (plant lifetime) |
| **OPEX - Fixed** | Fixed operating costs per tonne | OPEX subsidies: Operation years | Annual (plant lifetime) |
| **Energy Costs** | Electricity and hydrogen prices | No subsidies | Annual (plant lifetime) |
| **Carbon Costs** | Emissions × carbon price trajectory | No subsidies | Annual (plant lifetime) |
| **Revenue** | Production capacity × utilization rate × market price projections | N/A | Annual (plant lifetime) |
| **Discount Rate** | Weighted average cost of capital (WACC = debt share × cost of debt + equity share × cost of equity) | Applied to debt portion only | NPV calculation |

**Notes:**
- If NPV calculation fails (returns NaN due to missing data or invalid inputs), it is set to negative infinity to exclude that location-technology pair from selection.
- For more information on the NPV calculation, see related documentation in [Calculate Cost](../plant_agent_model/calculate_cost.md).

### Step 5: Top Opportunity Selection

**Function:** `select_top_opportunities_by_npv()`

Selects top N location-technology combinations using **weighted random sampling** (instead of pure NPV ranking) to represent some randomness in human decision-making. Pure ranking would always select the absolute highest NPV locations. In reality, companies have geographic preferences, imperfect information, varying risk tolerance, and strategic considerations. Weighted random sampling ensures diversity while still strongly favoring high-NPV options.

**Process:**
- Filter out invalid NPVs (NaN or negative infinity from calculation failures)
- Create weights from NPV values (shift negative NPVs to make all weights non-negative)
- Normalize weights to probabilities
- Randomly select top N opportunities with probability proportional to NPV
- If fewer valid pairs exist than requested, select all valid pairs

**Purpose:** Creates geographic diversity in opportunities while maintaining economic rationality. Higher NPV opportunities have much higher selection probability, but mid-tier opportunities can also be selected.

## Business Opportunity Tracking
**Function:** `update_status_of_business_opportunities()`

Once business opportunities are created, they are tracked annually through cost updates and status decisions until they either advance to construction through several stages or are discarded.

### Step 1: Annual Cost Updates

**Function:** `update_dynamic_costs_for_business_opportunities()`

Updates dynamic costs for all CONSIDERED and ANNOUNCED business opportunities each year to ensure NPV calculations reflect current market conditions.

**Updated Costs:**
- CAPEX (with subsidies for target construction year)
- Cost of debt (with subsidies for target construction year)
- Electricity price (custom power mix: LCOE from baseload power optimization and/or grid price)
- Hydrogen price (calculated from electricity price, including regional cap and intraregional trade, if allowed)
- Bill of materials (updated with new energy prices)

**Note:** For more information on the electricity and hydrogen prices see related documentation [Priority Location Selection](priority_location_selection.md).

**Target Year Calculation:**

The system uses subsidies from the earliest construction start year, reflecting that subsidies are often announced in advance.

| Status | Target Year | Reasoning |
|--------|-------------|-----------|
| CONSIDERED | `current + consideration_time + 1 - years_considered` | Earliest construction start based on consideration progress |
| ANNOUNCED | `current + 1` | Next year (announcement time = 1) |

**Process:**
For each business opportunity:
1. Calculate appropriate target year based on opportunity status
2. Filter subsidies active in target year and calculate new costs
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

Total new capacity (new plants + expansions) is limited annually. Annual capacity limits default to 150 Mt/year for both iron (`capacity_limit_iron`) and steel (`capacity_limit_steel`). The `new_capacity_share_from_new_plants` parameter determines how much of this limit is reserved for new plants versus expansions of existing facilities (default: 20%).

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
| `calculate_npv_pct` | float | 0.1 | Percentage of priority locations to sample for NPV calculation (fixed) |

### Probability Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `probability_of_announcement` | float | 0.7 | Chance viable opportunity is announced |
| `probability_of_construction` | float | 0.9 | Chance announced project starts construction |

### Capacity Limits

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `capacity_limit_iron` | float | 150 Mt/year | Total new iron capacity allowed per year |
| `capacity_limit_steel` | float | 150 Mt/year | Total new steel capacity allowed per year |
| `new_capacity_share_from_new_plants` | float | 0.2 | Share of capacity limit for new plants vs. expansions |

## Related Documentation

- [Priority Location Selection](priority_location_selection.md) - How candidate locations are identified
- [Calculate Cost](../plant_agent_model/calculate_cost.md) - NPV, subsidy, and carbon costs calculation details
- [Baseload Power Optimization](baseload_optimization_atlas.md) - Renewable energy cost calculations
- [Plant Agent Model](../plant_agent_model/PLANT_AGENT_DOCUMENTATION.md) - Overall plant lifecycle management