# Furnace Group Strategy Documentation

## Overview

The technology strategy system for furnace groups operates through a two-function architecture that separates **economic analysis** from **strategic decision-making**:

### Economic Analysis Engine: `optimal_technology_name()`
The `optimal_technology_name` function in the `FurnaceGroup` class is a critical economic analysis component that evaluates the financial viability of technology transitions in steel production facilities. It is a pure analytical function that returns economic metrics without making decisions or modifying any state. It performs a comprehensive Net Present Value (NPV) analysis by:
- Calculating the Cost of Stranded Assets (COSA) for abandoning current technology
- Evaluating NPV for each allowed technology transition
- Accounting for subsidies, carbon costs, and operational differences
- Comparing greenfield (new) vs brownfield (retrofit) installations

### Strategic Decision Orchestrator: `evaluate_furnace_group_strategy()`
The `evaluate_furnace_group_strategy` function in the `Plant` class is the strategic decision-maker that uses NPV analysis to make investment decisions, thereby modifying the state of furnace groups. It:
- Calls `optimal_technology_name()` to obtain economic analysis
- Applies business logic (affordability checks, capacity limits, risk assessment)
- Makes strategic decisions based on economic and operational constraints
- Generates actionable commands (renovate, switch technologies, or close facilities)
- Updates plant balance sheets to reflect investment costs


## Architecture: How the Functions Interact

```
evaluate_furnace_group_strategy (Plant)
  │
  ├─> Stage 1-4: Pre-flight checks
  │   ├─> Check plant financial health
  │   ├─> Check furnace group status
  │   ├─> Evaluate forced closure
  │   └─> Filter allowed transitions
  │
  ├─> Stage 5: Call optimal_technology_name (FurnaceGroup)
  │   │
  │   │   [Economic Analysis - No Decisions Made]
  │   │   ├─> Calculate COSA
  │   │   ├─> Evaluate NPV for each allowed technology
  │   │   │   ├─> Brownfield (renovation): reduced capex
  │   │   │   └─> Greenfield (new tech): full capex + COSA penalty
  │   │   └─> Return: NPV dict, CAPEX dict, COSA, BOM dict
  │   │
  │   └─< Returns economic metrics
  │
  ├─> Stage 6-8: Analyze results
  │   ├─> Check if any option is profitable
  │   ├─> Identify optimal technology
  │   └─> Select technology (weighted random or deterministic)
  │
  ├─> Stage 9-10: Execute decision path
  │   ├─> Path A: Renovation (same tech, lifetime expired)
  │   └─> Path B: Technology switch (different tech)
  │
  ├─> Stage 11-12: Final checks
  │   ├─> Probabilistic adoption filter
  │   └─> Capacity limit enforcement
  │
  └─> Stage 13: Return command
      ├─> RenovateFurnaceGroup
      ├─> ChangeFurnaceGroupTechnology
      ├─> CloseFurnaceGroup
      └─> None (no action)
```


# Part 1: Economic Analysis

## Overview

**Function**: `FurnaceGroup.optimal_technology_name`

**Purpose**: Pure NPV calculation and economic evaluation - determines the optimal technology by comparing economic returns across all allowed technology transitions.

**Key Characteristics**:
- **No decisions made** - only returns economic metrics
- **No side effects** - does not modify any state
- **Comprehensive evaluation** - calculates NPV for all allowed transitions
- **COSA-adjusted** - accounts for stranded asset costs when switching

**High-Level Workflow**:

```
Start
  │
  ├─> Stage 1: Initialize and import dependencies
  │
  ├─> Stage 2: Prepare Operating Expenses List and Calculate Operating Subsidies
  │
  ├─> Stage 3: Calculate Carbon Costs for Current Technology and Combine with Subsidised OPEX
  │
  ├─> Stage 4: Calculate Cost of Stranded Assets (COSA)
  │
  ├─> Stage 5: Check for allowed technology transitions
  │     └─> If no transitions allowed: Return empty results
  │
  ├─> Stage 6-9: For each allowed technology:
  │     ├─> Check if technology has capex data
  │     ├─> Check special requirements (e.g., BOF needs smelter)
  │     ├─> Branch A (Current Tech): Brownfield evaluation
  │     │     ├─> Apply brownfield capex reduction
  │     │     ├─> Use existing BOM and emissions
  │     │     └─> Calculate carbon costs
  │     ├─> Branch B (New Tech): Greenfield evaluation
  │     │     ├─> Fetch average BOM
  │     │     ├─> Match business cases
  │     │     ├─> Calculate new emissions
  │     │     └─> Calculate carbon costs
  │     ├─> Stage 8: Calculate NPV if valid BOM exists
  │     │     ├─> Apply subsidies (capex and opex)
  │     │     ├─> Calculate full NPV
  │     │     └─> Store results
  │     └─> Stage 9: Adjust NPV for COSA (if switching)
  │
  └─> Stage 10: Return results (NPVs, capex dict, COSA, BOMs)
```

## Detailed Stage Breakdown

### Stage 1: Initialize and Import Dependencies
- **Purpose**: Set up logging and import required calculation functions
- **Debug prefix**: `[OPTIMAL TECHNOLOGY]:`
- **Key actions**:
  - Log current furnace group state
  - Import cost and emissions calculation functions
  - Initialize return dictionaries

### Stage 2: Prepare Operating Expenses List and Calculate Operating Subsidies
- **Purpose**: Apply OPEX subsidies to the current OPEX value
- **Output**: List of subsidised OPEX values by year for the period of remaining lifetime

### Stage 3: Calculate Carbon Costs for Current Technology and Combine with Subsidised OPEX
- **Purpose**: Determine carbon cost series for existing operations
- **Key decision**: Use current emissions and chosen boundary
- **Inputs**: Current emissions, carbon price series, emission boundary
- **Output**: List of carbon costs over facility lifetime combined with list of subsidised OPEX values

### Stage 4: Calculate Cost of Stranded Assets (COSA)
- **Purpose**: Determine the cost of abandoning current technology
- **Key calculation**: NPV of remaining debt + operating costs
- **Decision**: COSA = max(calculated_COSA, remaining_debt)
- **Rationale**: Must at minimum pay off remaining debt

### Stage 5: Check for Allowed Technology Transitions
- **Decision point**: Are transitions defined for current technology?
  - If no: Return empty results (no switch possible)
  - If yes: Continue to evaluation
- **Key check**: `allowed_furnace_transitions` dictionary

### Stage 6: Evaluate Each Allowed Technology
- **For each technology in allowed transitions**:
  - **Decision 1**: Does technology have capex data?
    - If no: Skip this technology
  - **Decision 2**: Special requirements check
    - BOF requires smelter furnace
    - If not met: Skip this technology

### Stage 7: Branch Based on Technology Type

#### Branch A: Current Technology (Brownfield)
- **Characteristics**:
  - Retrofit of existing facility
  - Reduced capex (typically 20% of greenfield)
  - Uses existing BOM and utilization rate
  - Uses existing emissions profile
- **Key adjustments**:
  - Apply brownfield_capex_share multiplier
  - Validate existing BOM structure

#### Branch B: New Technology (Greenfield)
- **Characteristics**:
  - Complete replacement with new technology
  - Full capex cost
  - Requires fetching average BOM
  - Must calculate new emissions profile
- **Process**:
  1. Fetch average BOM for technology
  2. Match business cases with BOM
  3. Calculate emissions for new technology
  4. Determine carbon costs based on new emissions

### Stage 8: Calculate NPV
- **Prerequisite**: Valid BOM must exist
- **Process**:
  1. Store BOM for technology
  2. Get market price for product type
  3. Calculate operating subsidies
  4. Apply capex subsidies
  5. Calculate full NPV including:
     - Capex (after subsidies)
     - Operating costs and revenues
     - Carbon costs
     - Debt service
- **Key parameters**: Capacity, utilization, lifetime, financing costs

### Stage 9: Adjust NPV for COSA
- **Decision**: Is this a technology switch?
  - If yes (tech != current): Subtract COSA from NPV
  - If no (same tech): No COSA adjustment
- **Rationale**: Switching technologies incurs stranded asset cost

### Stage 10: Return Results
- **Outputs**:
  1. `npv_dict`: NPV values for each technology (COSA-adjusted)
  2. `npv_capex_dict`: Effective capex after subsidies
  3. `cosa`: Cost of Stranded Assets value
  4. `bom_dict`: Bill of Materials for each technology

## Key Dependencies and Interactions

### External Functions Called
- `calculate_emissions_cost_series`: Converts emissions to monetary costs
- `calculate_opex_subsidies`: Determines operating subsidy values
- `stranding_asset_cost`: Calculates COSA
- `get_bom_from_avg_boms`: Retrieves average BOMs for technologies
- `materiall_bill_business_case_match`: Matches feedstocks to BOMs
- `calculate_emissions`: Computes emission profiles
- `calculate_capex_with_subsidies`: Applies capital subsidies
- `calculate_variable_opex`: Computes variable operating costs
- `calculate_npv_full`: Performs complete NPV calculation

### Data Dependencies
- `self.bill_of_materials`: Current technology's BOM
- `self.emissions`: Current emission profile
- `self.lifetime`: Facility lifetime information
- `self.debt_repayment_per_year`: Debt service schedule
- `self.utilization_rate`: Current capacity utilization
- `self.production`: Current production volume

## Input Parameters

### Required Market Data
- `market_price_series`: Product prices by type (steel, iron, etc.)
- `carbon_cost_series`: Carbon prices over time
- `cost_of_debt`: Interest rate on debt
- `cost_of_equity`: Required return on equity

### Technology Configuration
- `capex_dict`: Capital costs by technology
- `capex_renovation_share`: Retrofit cost multipliers (brownfield)
- `technology_fopex_dict`: Fixed operating costs
- `allowed_furnace_transitions`: Valid technology switches
- `dynamic_business_cases`: Feedstock configurations

### Subsidy Information
- `tech_capex_subsidies`: Capital subsidies by technology
- `tech_opex_subsidies`: Operating subsidies by technology
- `tech_debt_subsidies`: Debt subsidies by technology

## Common Issues and Debugging Tips

### Issue 1: No Transitions Found
- **Symptom**: Function returns empty NPV dictionary
- **Check**: `allowed_furnace_transitions` contains current technology
- **Debug**: Look for `"NO TRANSITIONS ALLOWED"` in logs

### Issue 2: Technology Skipped
- **Symptom**: Expected technology not in results
- **Possible causes**:
  - Missing capex data
  - BOF without smelter
  - Failed BOM retrieval
- **Debug**: Look for `"SKIPPING"` messages in logs

### Issue 3: Invalid BOM Structure
- **Symptom**: Technology evaluation fails
- **Check**: BOM contains both "materials" and "energy" keys
- **Debug**: Look for `"Invalid or missing BOM"` warnings

### Issue 4: Negative NPV After COSA
- **Symptom**: All technologies show negative NPV
- **Explanation**: COSA exceeds benefits of switching
- **Debug**: Check COSA calculation and remaining debt

# Part 2: Strategic Decision-Making

## Overview

**Function**: `Plant.evaluate_furnace_group_strategy`

**Purpose**: Strategic decision orchestrator that uses NPV analysis to make investment decisions

**Key Characteristics**:
- **Makes decisions** - determines which action to take
- **Has side effects** - updates plant balance sheet
- **Business logic** - applies affordability checks, capacity limits, probabilistic adoption
- **Command generation** - returns executable commands for the simulation

**Decision Workflow**

The function follows a 13-stage workflow from pre-flight checks through command generation:

```
Stage 1-4: Pre-Flight Checks
  ├─> Plant financial health
  ├─> Furnace group status
  ├─> Forced closure evaluation
  └─> Technology transition filtering

Stage 5: Economic Analysis
  └─> Call optimal_technology_name() → Get NPV analysis

Stage 6-8: Results Analysis
  ├─> Check profitability (any NPV > 0?)
  ├─> Identify optimal technology
  └─> Technology selection (weighted random or deterministic)

Stage 9-10: Decision Execution
  ├─> Renovation path (if best tech = current tech)
  └─> Technology switch path (if best tech ≠ current tech)

Stage 11-13: Final Checks & Command Generation
  ├─> Probabilistic adoption filter
  ├─> Capacity limit enforcement
  └─> Generate command (Renovate/Switch/Close/None)
```

## Detailed Stage Breakdown

### Stage 1: Check Plant Financial Health
**Decision**: Can the plant afford to invest?
- **Check**: `plant.balance >= 0`
- **If negative**: Return `None` (no action possible)
- **Rationale**: Negative balance = no capital available for investment

### Stage 2: Check Furnace Group Status
**Decision**: Is the furnace group eligible for strategy evaluation?
- **Check**: Status is not "operating pre-retirement"
- **If pre-retirement**: Return `None` (already scheduled to close)
- **Rationale**: Don't invest in facilities about to close

### Stage 3: Check for Forced Closure
**Decision**: Have accumulated losses exceeded write-off threshold?
- **Threshold calculation**: `CAPEX × capacity`
- **Check**: `furnace_group.historic_balance < -threshold`
- **If exceeded**: Return `CloseFurnaceGroup` command
- **Rationale**: Losses too great - better to close than continue operating

**Example**:
- CAPEX: $800/tonne
- Capacity: 5,000,000 tonnes (5 Mt)
- Threshold: -$4,000,000,000
- Historic balance: -$4,500,000,000 → **Close**

### Stage 4: Filter Allowed Technology Transitions
**Purpose**: Narrow down technology options based on what's allowed in the current year
- **Process**: Intersect `allowed_techs[current_year]` with `allowed_furnace_transitions[current_tech]`
- **Example**:
  - Current tech: BF-BOF
  - All possible transitions: [BF-BOF, EAF, DRI-EAF, H2-DRI-EAF]
  - Allowed in 2030: [BF-BOF, EAF, DRI-EAF] (H2-DRI-EAF not yet available)
  - Filtered transitions: [BF-BOF, EAF, DRI-EAF]

### Stage 5: Calculate NPV for All Technology Options
**Key Action**: Call `furnace_group.optimal_technology_name()` to get economic analysis

**Inputs passed to analysis engine**:
- Market price forecasts
- Technology costs (CAPEX, OPEX, financing)
- Bill of materials function
- Carbon cost projections
- Subsidy configurations

**Outputs received**:
- `tech_npv_dict`: NPV for each technology (already COSA-adjusted)
- `npv_capex_dict`: Subsidized CAPEX for each technology
- `cosa`: Cost of stranded assets
- `bom_dict`: Bills of materials for each technology

### Stage 6: Check if Any Technology Option is Profitable
**Decision**: Is investing worthwhile?
- **Check**: `max(tech_npv_dict.values()) > 0`
- **If all NPVs ≤ 0**: Return `None` (no profitable option exists)
- **Rationale**: Don't invest if all options lose money

### Stage 7: Identify Optimal Technology
**Process**: Find technology with highest NPV
- **Calculation**: `optimal_tech = max(tech_npv_dict, key=tech_npv_dict.get)`
- **Check**: Is current technology already optimal?
  - `is_current_best = (current_tech == optimal_tech)`

### Stage 8: Technology Selection
**Decision Mode**: Weighted random (realistic) or deterministic (pure optimization)

#### If Current Tech is NOT Optimal:
**Weighted Random Selection** (when `probabilistic_agents=True`):
- **Purpose**: Model real-world decision uncertainty and strategic preferences
- **Process**:
  1. Filter out invalid NPVs (NaN, infinite)
  2. Calculate weights: `weight = max(NPV, 0)` (negative NPVs get zero weight)
  3. Randomly select technology with probability ∝ NPV
- **Example**:
  - BF-BOF: NPV = $5M → weight = 5M → 37% selection probability
  - EAF: NPV = $8.5M → weight = 8.5M → 63% selection probability
  - DRI-EAF: NPV = -$1M → weight = 0 → 0% selection probability

#### If Current Tech IS Optimal:
- **Selection**: Keep current technology

### Stage 9: Handle Renovation Scenario
**Condition**: Best technology = current technology AND lifetime expired

**Decision Path**:
1. **Calculate renovation cost**:
   - CAPEX = subsidized renovation CAPEX from NPV analysis
   - Cost = `CAPEX × capacity × equity_share`

2. **Affordability check**:
   - If `renovation_cost > plant.balance` → Return `CloseFurnaceGroup`
   - Rationale: Can't afford to renovate, must close

3. **Execute renovation**:
   - Deduct cost from plant balance: `plant.balance -= renovation_cost`
   - Return `RenovateFurnaceGroup` command

**Example Calculation**:
- Subsidized renovation CAPEX: $160/tonne
- Capacity: 5 Mt
- Equity share: 30%
- Renovation cost: $160 × 5,000,000 × 0.30 = $240,000,000
- Plant balance: $300,000,000 → **Affordable, renovate**

### Stage 10: Handle Technology Switch Scenario
**Condition**: Best technology ≠ current technology

**Process**:
1. **Calculate switching cost**:
   - CAPEX = subsidized greenfield CAPEX from NPV analysis
   - Cost = `CAPEX × capacity × equity_share`

2. **Affordability check**:
   - If `switch_cost > plant.balance` → Return `None`
   - Rationale: Can't afford to switch

3. **Continue to probabilistic adoption** (Stage 11)

**Example Calculation**:
- Subsidized greenfield CAPEX: $650/tonne
- Capacity: 5 Mt
- Equity share: 30%
- Switch cost: $650 × 5,000,000 × 0.30 = $975,000,000
- Plant balance: $1,200,000,000 → **Affordable, proceed**

### Stage 11: Probabilistic Adoption Decision
**Purpose**: Model real-world hesitation in technology adoption (financing risk, permit delays, market uncertainty)

**Formula**:
```
acceptance_probability = exp(-switch_cost / NPV)
```

**Interpretation**:
- Higher cost relative to benefit → Lower probability
- Cost << NPV → High probability (close to 100%)
- Cost ≈ NPV → Moderate probability (≈37%)
- Cost >> NPV → Low probability (close to 0%)

**Example**:
- Switch cost: $975M
- NPV: $8.5M
- Ratio: 975 / 8.5 = 114.7
- Acceptance probability: exp(-114.7) ≈ 0% (very unlikely)

**Alternative Example** (more favorable):
- Switch cost: $100M
- NPV: $200M
- Ratio: 100 / 200 = 0.5
- Acceptance probability: exp(-0.5) ≈ 61%

**Decision Process**:
1. Calculate acceptance probability
2. Draw random number (0-1)
3. If `random_draw < acceptance_probability` AND furnace has no CCS/CCU → Proceed to Stage 12
4. Otherwise → Return `None` (probabilistic rejection)

**Special Case**: Furnaces with CCS/CCU equipment are never switched (representing significant sunk investment)

### Stage 12: Check Capacity Limits
**Purpose**: Enforce annual capacity expansion limits (supply chain constraints)

**Context**:
- Total annual capacity is limited (default: 100 Mt for both steel and iron)
- Limit is split between:
  - **New plants**: Greenfield facilities (handled by Geospatial Model)
  - **Expansions & switches**: Capacity added to existing plants (handled by PAM)

**Calculation**:
```
total_installed_capacity = installed_capacity_in_year(product)  # All new capacity this year
new_plant_capacity = new_plant_capacity_in_year(product)        # Greenfield only
expansion_and_switch_capacity = total - new_plant_capacity      # Existing plants

if expansion_and_switch_capacity + furnace_capacity > expansion_limit:
    BLOCKED
```

**Example**:
- Product: Steel
- Total expansion limit for steel: 100 Mt/year
- New plant share: 40% → New plant limit: 40 Mt, Expansion/switch limit: 60 Mt
- Current new expansion/switch capacity already installed this year: 55 Mt
- Furnace capacity to switch: 8 Mt
- Total after switch: 55 + 8 = 63 Mt > 60 Mt → **BLOCKED**

**If within limits**: Proceed to Stage 13

### Stage 13: Execute Technology Switch
**Final Actions**:
1. **Update plant balance**: `plant.balance -= switch_cost`
2. **Generate command**: Return `ChangeFurnaceGroupTechnology`

**Command includes**:
- Technology change details (old → new)
- Economic metrics (NPV, COSA)
- Cost details (CAPEX with/without subsidies, cost of debt)
- Technical details (BOM, utilization, capacity)
- Subsidy configurations

## Command Types Returned

### 1. `RenovateFurnaceGroup`
- **When**: Current tech optimal AND lifetime expired AND affordable
- **Includes**: Renovation CAPEX, subsidies, cost of debt

### 2. `ChangeFurnaceGroupTechnology`
- **When**: Different tech selected AND affordable AND passes probability filter AND within capacity limits
- **Includes**: Full technology switch details, NPV, COSA, BOM, subsidies

### 3. `CloseFurnaceGroup`
- **When**:
  - Forced closure (historic losses exceed threshold)
  - Cannot afford renovation when lifetime expired
- **Includes**: Plant ID and furnace group ID

### 4. `None`
- **When**:
  - Plant has negative balance
  - Furnace group pre-retirement
  - All NPVs negative or zero
  - Cannot afford switch
  - Probabilistic rejection
  - Capacity limit exceeded
  - Current tech optimal but lifetime not expired

## Data Dependencies

### For Strategic Decisions (`evaluate_furnace_group_strategy`)
- `plant.balance`: Available capital for investment
- `furnace_group.status`: Operating status
- `furnace_group.historic_balance`: Cumulative profit/loss
- `furnace_group.lifetime.expired`: Whether renovation is needed
- `furnace_group.has_ccs_or_ccu`: CCS/CCU equipment flag

### External Functions Called
- `furnace_group.optimal_technology_name()`: Gets NPV analysis
- `filter_active_subsidies()`: Filters subsidies by year
- `calculate_debt_with_subsidies()`: Applies debt subsidies

# Function Relationship Summary

## Division of Responsibilities

| Aspect | `optimal_technology_name()` | `evaluate_furnace_group_strategy()` |
|--------|----------------------------|-------------------------------------|
| **Role** | Economic analyst | Strategic decision-maker |
| **Question** | "What are the economics?" | "What should we do?" |
| **Type** | Pure function (no side effects) | Impure function (updates state) |
| **Returns** | NPV dictionaries, COSA, BOMs | Command objects or None |
| **Considers** | Market prices, costs, subsidies | Affordability, risk, capacity limits |
| **Modifies** | Nothing | Plant balance sheet |
| **Calls** | Calculate_costs functions | optimal_technology_name() |
| **Level** | FurnaceGroup level | Plant level |

## Why This Architecture?

### Separation of Concerns
- **Economic analysis** is complex and reusable
- **Strategic decisions** depend on context (plant finances, capacity limits, policy)
- Separating them allows testing economics independently of business rules

### Testability
- Can test NPV calculations without mocking entire decision system
- Can test decision logic with predetermined NPV inputs

### Clarity
- Clear distinction between "what's optimal economically" and "what's feasible practically"
- Makes code easier to understand and maintain

### Flexibility
- Can change decision rules without touching NPV calculations
- Can add new economic factors without changing decision logic


## Key Metrics to Monitor

### Economic Analysis Metrics
1. **COSA vs NPV**: If COSA > NPV, switch is unprofitable
2. **Utilization rates**: Lower utilization reduces NPV
3. **Carbon cost impact**: Rising carbon prices favor cleaner technologies
4. **Subsidy effectiveness**: Track NPV changes with/without subsidies
5. **Brownfield advantage**: Compare brownfield vs greenfield NPVs

### Decision Metrics
1. **Acceptance rate**: % of positive-NPV switches actually executed
2. **Capacity utilization**: How close to annual limits
3. **Rejection reasons**: Categorize why switches don't happen
4. **Technology distribution**: Which technologies are being adopted

### Performance Indicators
1. **Average NPV of executed switches**: Quality of decisions
2. **Renovation vs. switch ratio**: Technology transition pace
3. **Closure rate**: Industry health indicator
4. **Balance sheet health**: Plant financial sustainability

# Glossary

- **COSA**: Cost of Stranded Assets - economic loss from abandoning existing technology (includes remaining debt and foregone profits)
- **NPV**: Net Present Value - present value of future cash flows minus initial investment
- **BOM**: Bill of Materials - resource requirements for production (materials and energy)
- **Brownfield**: Retrofit/upgrade of existing facility (reduced CAPEX)
- **Greenfield**: Complete new installation (full CAPEX)
- **CAPEX**: Capital Expenditure - upfront investment cost
- **OPEX**: Operating Expenditure - ongoing operational costs
- **FOPEX**: Fixed Operating Expenditure - costs independent of production volume
- **VOPEX**: Variable Operating Expenditure - costs that scale with production
- **Equity Share**: Fraction of investment paid from company funds (vs. debt financing)
- **Utilization Rate**: Actual production as percentage of capacity
- **Acceptance Probability**: Likelihood of executing a switch given positive NPV (models risk aversion)
