# PlantAgentsModel Orchestration

## Overview

`PlantAgentsModel` is the main orchestrator that coordinates all PAM decision-making within a simulation year. It processes plants and plant groups in sequence, issuing commands for technology switches, renovations, closures, and capacity expansions.

The model executes the following workflow each year:
1. Calculate market conditions (current and future prices of steel and iron)
2. Evaluate each plant's furnace groups for potential actions
3. Evaluate plant groups for plant expansion opportunities
4. Track and enforce capacity limits (for steel and iron buildout separately)

## Role in Simulation

PAM is called once per simulation year from `simulation.py`:

```python
Simulation(bus=bus, economic_model=PlantAgentsModel()).run_simulation()
```

**Before PAM runs:**

1. **Trade Module** allocates production:
   - Sets `furnace_group.allocated_volumes`
   - Sets `furnace_group.utilization_rate`

2. **TM-PAM Connector** updates costs:
   - Sets `furnace_group.bill_of_materials`
   - Sets `furnace_group.emissions`

**After PAM runs:**

1. **Handlers** execute commands:
   - Update furnace group attributes
   - Create new furnace groups
   - Record domain events

2. **Checkpoint** saves state:
   - Serializes all plants, furnace groups, plant groups
   - Records issued commands for replay

## Orchestration Workflow

PAM executes the following steps each simulation year:

### Step 1: Calculate Market Conditions

Establishes the economic environment for all decisions:

- **Carbon costs**: Updates carbon costs for all furnace groups based on current carbon price
- **Current prices**: Extracts market prices from cost curves (frozen for consistent evaluation)
- **Future price series**: Builds time series of expected prices for NPV and COSA calculations (covering construction time + plant lifetime)

### Step 2: Evaluate Furnace Group Strategies

For each plant (in random order):

1. **Update balances**: Aggregates furnace group balances to plant level and resets furnace group balances to zero
2. **Evaluate each furnace group** (in random order) using `plant.evaluate_furnace_group_strategy()`:
   - **Renovate**: Current technology remains optimal at end-of-life
   - **Switch**: Different technology offers superior NPV (accounting for COSA)
   - **Close**: Historic losses exceed acceptable threshold
   - **Continue**: No action needed
3. **Issue commands**: Approved decisions are sent via message bus

### Step 3: Evaluate Plant Group Expansions

For each plant group (in random order):

1. **Collect total balance**: Sums all plant balances to calculate available capital for investment
2. **Evaluate expansion** using `plant_group.evaluate_expansion()`:
   - Assesses all plants and available technologies
   - Selects the option with highest NPV (using future price forecasts based on demand)
   - Verifies affordability against accumulated balance
   - Checks compliance with capacity limits (separate for steel and iron)
3. **Issue commands**: Approved expansions are sent via message bus

### Step 4: Track and Enforce Capacity Limits

Throughout the evaluation process:

- Monitors cumulative capacity additions by product type (steel/iron)
- Rejects expansion and switch commands that would exceed annual limits
- Capacity limits exclude new plants (handled separately by GeospatialModel)

## Data Flow and Key Parameters

### Inputs

#### From Unit of Work (`bus.uow`)
- `bus.uow.plants.list()` → List of all `Plant` objects
  - Each plant contains `furnace_groups` list
- `bus.uow.plant_groups.list()` → List of all `PlantGroup` objects
  - Aggregated by ownership (parent company)

#### From Environment (`bus.env`)
**Demand Signals**:
- `current_demand` → Current year steel/iron demand (dict by product)
- `iron_demand` → Iron-specific demand
- `future_demand(year)` → Demand forecast for future years

**Market Data**:
- `extract_price_from_costcurve()` → Derives market prices from cost curve
- `carbon_costs_for_emissions` → Carbon price time series

**Cost Data**:
- `name_to_capex` → CAPEX by technology and region
- `capex_renovation_share` → Brownfield cost multipliers
- `industrial_cost_of_debt` → Interest rates by country
- `industrial_cost_of_equity` → Required returns by country

**Technology Configuration**:
- `allowed_furnace_transitions` → Valid technology switches
- `dynamic_feedstocks` → Feedstock options by technology
- `allowed_techs` → Technologies allowed by year

**Constraints**:
- `capacity_limit_steel` → Max steel capacity additions per year
- `capacity_limit_iron` → Max iron capacity additions per year
- `new_capacity_share_from_new_plants` → Reserved capacity share for new plants

**Subsidies** (optional):
- `tech_capex_subsidies` → Capital subsidies by country/technology
- `tech_opex_subsidies` → Operating subsidies
- `tech_debt_subsidies` → Interest rate subsidies

### Intermediate Values

**Price Snapshots**:
- `freeze_market_price` → Market price at start of PAM run (dict by product)
  - Used for balance sheet updates of furnace groups
- `current_price` → Refreshed price for each evaluation
- `future_price` → Future prices based on future demand and current cost curves for all furnace evaluations

**Tracking Variables**:
- `counter` → Number of commands issued this year
- `added_capacity` → Total capacity added by PAM this year (tracked in `bus.env`)

**Regional Mapping**:
- `region_capex` → CAPEX data indexed by plant's region, changes as new capacity is added following the learning curve logic
- `renovation_share` → Renovation cost ratios by technology

### Outputs

#### Commands Dispatched
All commands sent via `bus.handle(cmd)`:

1. **`ChangeFurnaceGroupStatusToSwitchingTechnology`** → Scheduling technology switch
   - Allows to produce with the old technology, unitl new technology is installed
2. **`ChangeFurnaceGroupTechnology`** → Technology switch
3. **`RenovateFurnaceGroup`** → Renovate at end-of-life
4. **`CloseFurnaceGroup`** → Shut down unprofitable furnace
5. **`AddFurnaceGroup`** → Expand capacity at existing plant

#### Logged Metrics
- `counter` → Total commands issued
- `bus.env.added_capacity` → Total capacity added

## Key Design Patterns

### 1. Snapshot Market Prices
Freeze prices at start of PAM run and use frozen prices for all furnace evaluations to prevent mid-run price changes from affecting decisions and ensure all furnace groups evaluate under consistent market conditions.

### 2. Command-Based Updates
All changes issued as commands via message bus to:
- Decouple decision-making from state mutation
- Enable event sourcing and undo/replay
- Handler can enforce validation and logging

## Debugging Tips

### Tracing Decisions

Enable furnace group breakdown in `logging_config.yaml`:
```yaml
features:
  furnace_group_breakdown: true  # Set to false to disable
```

### Key Metrics to Monitor

1. **Command counts**: How many switches, renovations, closures, expansions?
2. **Price stability**: Are prices fluctuating year-to-year?
3. **Balance sheet health**: What % of plant groups have positive balance?
4. **Capacity utilization**: Are new plants being utilized?
5. **Subsidy uptake**: How many commands benefited from subsidies?

### Common Issues

**Issue**: No technology switches happening
- Check: allowed transitions Excel tab allows switches between expected technologies
- Check: NPV calculations include correct CAPEX and OPEX
- Check: COSA isn't too high (check remaining debt)

**Issue**: Expansions not happening
- Check: Plant group balances (need positive balance)
- Check: New capacity limits reached (increase limits, decrease expansion capacity, or decrease the share of new capacity reserved for new plants)
- Check: Future demand forecasts (need growth)

---

## Related Documentation

- **[Agent Definitions](agent_definitions.md)**: Class structure (Plant, FurnaceGroup, PlantGroup)
- **[Furnace Group Strategy](furnace_group_strategy.md)**: Furnace group strategy evaluation details
- **[Plant Expansions](plant_expansions.md)**: Plant group expansion evaluation details
- **[Market Price Calculation](market_price_calculation.md)**: How market prices are derived
- **[Trade Model Connector](trade_model_connector.md)**: Pre-PAM data preparation from Trade Module
